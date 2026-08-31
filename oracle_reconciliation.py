"""Read-only E_COMM Excel ↔ WebbyCom Oracle reconciliation.

This module does not change either source. It reads the current E_COMM workbook,
queries the two reporting views with SELECT statements, and writes auditable
JSON/CSV intermediates for the mapping review.

The Oracle password is accepted only through WEBBYCOM_ORACLE_PASSWORD.  It is
never accepted as a command-line argument, written to disk, or included in logs.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

import data_reader


DEFAULT_HOST = "192.168.45.180"
DEFAULT_PORT = 1521
DEFAULT_SERVICE = "HGL"
DEFAULT_USER = "ECOMM_READ"
PASSWORD_ENV = "WEBBYCOM_ORACLE_PASSWORD"
DRIVER_DIR_ENV = "WEBBYCOM_ORACLE_DRIVER_DIR"

HEAD_VIEW = "WAREHOUSE_HEAD_REPORT_VW"
DETAIL_VIEW = "WAREHOUSE_DETAIL_REPORT_VW"

HEAD_COLUMNS = (
    "E_COMM_ID",
    "AIR_WAYBILL",
    "SUM_OF_COLLIS",
    "SUM_OF_WEIGHT_KG",
    "SUM_OF_PARCELS",
    "HEAD_MAIN_CLIENT_GROUP",
    "CREATION_DATE",
    "LAST_CHANGE_DATE",
)

DETAIL_COLUMNS = (
    "REF_E_COMM_ID",
    "AIR_WAYBILL",
    "E_COMM_DETAIL_ID",
    "CLIENT_NAME",
    "GHA_COMPANY_NAME",
    "EXPECTED_ARRIVAL",
    "PARCEL_STATUS_NAME",
    "LAST_PARCEL_ST_CHANGE",
    "DETAIL_COLLIS",
    "DETAIL_WEIGHT_KG",
    "DETAIL_PARCELS",
    "ULD_NUMBER",
    "ULD_RETURN_DATE",
    "ATA",
    "NOA",
    "GOODS_TRANSFER_DATE",
    "TRANSFER_PLATE_NUMBER",
    "CUSTOMS_CLEARANCE_START",
    "CUSTOMS_CLEARANCE_FINISH",
    "ITEM_DEPARTURE",
    "DEST_COUNTRY_NAME",
    "DELIVERY_PLATE_NUMBER",
    "BUD_PALLET_DUMP_NUMBER",
    "GLABS_ID",
    "PLT_OUT",
    "LOCATION_CODES",
    "BUD_PALLET_CHECK_IN",
    "BUD_PALLET_REMARK",
    "LAST_CHANGE_DATE",
)

# One normalized detail row is the comparison grain. Head quantities are kept
# separately as controls and must never be repeated into detail KPI rows.
FIELD_MAPPING = (
    ("lmp", "CLIENT_NAME", "text", "Ügyfél / detail ügyfél"),
    ("awb", "AIR_WAYBILL", "text", "AWB"),
    ("boxes", "DETAIL_COLLIS", "number", "Colli"),
    ("weight", "DETAIL_WEIGHT_KG", "number", "Súly"),
    ("parcel_count", "DETAIL_PARCELS", "number", "Parcel"),
    ("carrier_k", "GHA_COMPANY_NAME", "text", "GHA"),
    ("expected_arrival_raw", "EXPECTED_ARRIVAL", "date", "Várható érkezés"),
    ("status_n", "PARCEL_STATUS_NAME", "status", "Státusz"),
    ("uld_raw", "ULD_NUMBER", "uld", "ULD azonosító"),
    ("ad_raw", "ULD_RETURN_DATE", "date", "ULD visszaadás"),
    ("ak_raw", "ATA", "date", "ATA"),
    ("noa_raw", "NOA", "date", "NOA"),
    ("am_raw", "GOODS_TRANSFER_DATE", "date", "Áttár"),
    ("rendszam_raw", "TRANSFER_PLATE_NUMBER", "text", "Áttár rendszám"),
    ("aq_raw", "CUSTOMS_CLEARANCE_START", "date", "Vámkezelés kezdete"),
    ("ar_raw", "CUSTOMS_CLEARANCE_FINISH", "date", "Vámkezelés vége"),
    ("departure_raw", "ITEM_DEPARTURE", "date", "Tétel indulás"),
)

DEFERRED_PARTIAL_MAPPING = (
    ("partial_boxes_raw", "DETAIL_COLLIS", "number", "Részleges colli jelölt"),
    ("partial_weight_raw", "DETAIL_WEIGHT_KG", "number", "Részleges súly jelölt"),
)

DATE_TOLERANCE_SECONDS = 60.0
NUMBER_TOLERANCE = Decimal("0.01")
_PLATE_LIKE_RE = re.compile(r"^[A-Z0-9]{2,5}(?:[- /][A-Z0-9]{2,5})+(?:[/,][A-Z0-9 -]{2,12})*$", re.IGNORECASE)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and value != value:
        return None
    if hasattr(value, "item"):
        try:
            return _json_value(value.item())
        except (TypeError, ValueError):
            pass
    return value


def _empty(value: Any) -> bool:
    return data_reader._empty(value)


def _text(value: Any) -> str:
    if _empty(value):
        return ""
    return " ".join(str(value).strip().split())


def _key_text(value: Any) -> str:
    return _text(value).casefold()


def _number(value: Any) -> Decimal | None:
    if _empty(value):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _excel_datetime(value: Any) -> datetime | None:
    if _empty(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return data_reader._serial_to_dt(value)


def _oracle_datetime(value: Any) -> datetime | None:
    if _empty(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        return None if pd.isna(parsed) else parsed.to_pydatetime()
    except (TypeError, ValueError, OverflowError):
        return None


def _uld_set(value: Any) -> tuple[str, ...]:
    return tuple(sorted(data_reader._parse_uld_numbers(value)))


def comparison_key(awb: Any, client: Any) -> str:
    return f"{_key_text(awb)}|{_key_text(client)}"


def read_excel_source(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read the same 21 semantic E_COMM columns as the production reader."""
    source = str(path)
    tmp_path: str | None = None
    try:
        tmp_path = data_reader._temp_copy(source)
        column_index, header_row = data_reader._resolve_ecomm_column_map(tmp_path)
        ordered = sorted(column_index.items(), key=lambda item: item[1])
        raw = pd.read_excel(
            tmp_path,
            engine="pyxlsb",
            sheet_name="E-comm",
            header=header_row - 1,
            usecols=[index for _name, index in ordered],
            nrows=data_reader._ecomm_data_nrows(header_row),
        )
        raw.columns = [name for name, _index in ordered]
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    result: list[dict[str, Any]] = []
    for row in raw.to_dict("records"):
        awb = _text(row.get("awb"))
        lmp = _text(row.get("lmp"))
        # Retain the guard for old exported comparison inputs whose header may
        # already have been materialized as a data row.
        if not awb or (_key_text(awb) == "awb" and _key_text(lmp) in {"ügyfél", "ugyfel"}):
            continue
        item = {name: _json_value(row.get(name)) for name in data_reader._ECOMM_NAMES}
        item["awb"] = awb
        item["lmp"] = lmp
        item["comparison_key"] = comparison_key(awb, lmp)
        result.append(item)
    return result


def _import_oracledb(driver_dir: str | None = None):
    candidate = driver_dir or os.environ.get(DRIVER_DIR_ENV)
    if candidate:
        sys.path.insert(0, candidate)
    try:
        import oracledb  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "A python-oracledb driver nem érhető el. Telepítsd a futtató Pythonhoz, "
            f"vagy állítsd be a {DRIVER_DIR_ENV} változót."
        ) from exc
    return oracledb


def _query_rows(cursor, view: str, columns: Iterable[str]) -> list[dict[str, Any]]:
    allowed_views = {HEAD_VIEW, DETAIL_VIEW}
    if view not in allowed_views:
        raise ValueError(f"Nem engedélyezett view: {view}")
    selected = tuple(columns)
    known = set(HEAD_COLUMNS) | set(DETAIL_COLUMNS)
    if not selected or any(column not in known for column in selected):
        raise ValueError("Nem engedélyezett Oracle-oszlop a lekérdezésben")
    # Identifiers come only from constants above; values never enter SQL text.
    cursor.execute(f"SELECT {', '.join(selected)} FROM {view}")
    names = [description[0] for description in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def read_oracle_source(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    service: str = DEFAULT_SERVICE,
    user: str = DEFAULT_USER,
    driver_dir: str | None = None,
    call_timeout_ms: int = 30_000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    password = os.environ.get(PASSWORD_ENV)
    if not password:
        raise RuntimeError(
            f"Hiányzik a {PASSWORD_ENV} környezeti változó. A jelszó nem adható meg CLI argumentumként."
        )
    oracledb = _import_oracledb(driver_dir)
    dsn = oracledb.makedsn(host, port, service_name=service)
    with oracledb.connect(user=user, password=password, dsn=dsn) as connection:
        connection.call_timeout = call_timeout_ms
        with connection.cursor() as cursor:
            heads = _query_rows(cursor, HEAD_VIEW, HEAD_COLUMNS)
            details = _query_rows(cursor, DETAIL_VIEW, DETAIL_COLUMNS)
    return heads, details


def prepare_oracle_details(
    heads: list[dict[str, Any]],
    details: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    head_by_id = {str(row.get("E_COMM_ID")): row for row in heads}
    prepared: list[dict[str, Any]] = []
    for detail in details:
        head = head_by_id.get(str(detail.get("REF_E_COMM_ID")))
        item = {key: _json_value(value) for key, value in detail.items()}
        item["HEAD_AIR_WAYBILL"] = _json_value(head.get("AIR_WAYBILL")) if head else None
        item["comparison_key"] = comparison_key(item.get("AIR_WAYBILL"), item.get("CLIENT_NAME"))
        prepared.append(item)

    detail_by_head: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for detail in details:
        detail_by_head[str(detail.get("REF_E_COMM_ID"))].append(detail)

    head_checks: list[dict[str, Any]] = []
    for head in heads:
        head_id = str(head.get("E_COMM_ID"))
        group = detail_by_head.get(head_id, [])
        record = {
            "E_COMM_ID": _json_value(head.get("E_COMM_ID")),
            "AIR_WAYBILL": _text(head.get("AIR_WAYBILL")),
            "DETAIL_ROWS": len(group),
        }
        for head_field, detail_field, label in (
            ("SUM_OF_COLLIS", "DETAIL_COLLIS", "COLLIS"),
            ("SUM_OF_WEIGHT_KG", "DETAIL_WEIGHT_KG", "WEIGHT_KG"),
            ("SUM_OF_PARCELS", "DETAIL_PARCELS", "PARCELS"),
        ):
            head_value = _number(head.get(head_field)) or Decimal("0")
            detail_sum = sum((_number(row.get(detail_field)) or Decimal("0") for row in group), Decimal("0"))
            record[f"HEAD_{label}"] = float(head_value)
            record[f"DETAIL_{label}_SUM"] = float(detail_sum)
            record[f"{label}_DIFF"] = float(detail_sum - head_value)
            record[f"{label}_MATCH"] = abs(detail_sum - head_value) <= NUMBER_TOLERANCE
        head_checks.append(record)
    return prepared, head_checks


def _aggregate(records: list[dict[str, Any]], field: str, kind: str, source: str) -> Any:
    values = [row.get(field) for row in records]
    nonempty = [value for value in values if not _empty(value)]
    if kind == "number":
        numbers = [_number(value) for value in nonempty]
        return sum((value for value in numbers if value is not None), Decimal("0")) if numbers else None
    if kind == "date":
        parser = _excel_datetime if source == "excel" else _oracle_datetime
        parsed = [parser(value) for value in nonempty]
        parsed = [value for value in parsed if value is not None]
        if not parsed:
            return None
        # Multiple different timestamps remain visible as a tuple and will not
        # accidentally compare equal to one Oracle value.
        unique = sorted(set(parsed))
        return unique[0] if len(unique) == 1 else tuple(unique)
    if kind == "uld":
        combined = set()
        for value in nonempty:
            combined.update(_uld_set(value))
        return tuple(sorted(combined))
    if kind == "status":
        normalized = sorted({data_reader._status_key(value) for value in nonempty if data_reader._status_key(value)})
        return normalized[0] if len(normalized) == 1 else tuple(normalized)
    normalized = sorted({_text(value) for value in nonempty if _text(value)})
    return normalized[0] if len(normalized) == 1 else tuple(normalized)


def _equal(left: Any, right: Any, kind: str) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    if isinstance(left, tuple) or isinstance(right, tuple):
        return left == right
    if kind == "number":
        return abs(Decimal(left) - Decimal(right)) <= NUMBER_TOLERANCE
    if kind == "date":
        return abs((left - right).total_seconds()) <= DATE_TOLERANCE_SECONDS
    if kind == "uld":
        return tuple(left) == tuple(right)
    if kind == "status":
        return left == right
    return _key_text(left) == _key_text(right)


def _display(value: Any) -> Any:
    if isinstance(value, tuple):
        return " | ".join(str(_json_value(item)) for item in value)
    return _json_value(value)


def _plate_like(value: Any) -> bool:
    text = _text(value)
    return bool(text and ":" not in text and _PLATE_LIKE_RE.fullmatch(text))


def _profile_plate_candidates(
    excel_by_key: dict[str, list[dict[str, Any]]],
    oracle_by_key: dict[str, list[dict[str, Any]]],
    matched_keys: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profiles: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    for oracle_field in ("TRANSFER_PLATE_NUMBER", "DELIVERY_PLATE_NUMBER"):
        exact = both = excel_nonempty = oracle_nonempty = oracle_plate_like = 0
        distinct_pairs: set[tuple[str, str]] = set()
        for key in matched_keys:
            left = _aggregate(excel_by_key[key], "rendszam_raw", "text", "excel")
            right = _aggregate(oracle_by_key[key], oracle_field, "text", "oracle")
            left_text = _text(left)
            right_text = _text(right)
            excel_nonempty += bool(left_text)
            oracle_nonempty += bool(right_text)
            oracle_plate_like += _plate_like(right_text)
            if left_text and right_text:
                both += 1
                if _equal(left, right, "text"):
                    exact += 1
                distinct_pairs.add((left_text, right_text))
        profiles.append({
            "oracle_field": oracle_field,
            "matched_keys": len(matched_keys),
            "excel_nonempty": excel_nonempty,
            "oracle_nonempty": oracle_nonempty,
            "both_nonempty": both,
            "exact_matches": exact,
            "exact_match_rate_when_both": (exact / both) if both else None,
            "oracle_plate_like_values": oracle_plate_like,
            "oracle_plate_like_rate_when_nonempty": (oracle_plate_like / oracle_nonempty) if oracle_nonempty else None,
        })
        for excel_value, oracle_value in sorted(distinct_pairs)[:50]:
            samples.append({
                "oracle_field": oracle_field,
                "excel_value": excel_value,
                "oracle_value": oracle_value,
                "exact_match": _key_text(excel_value) == _key_text(oracle_value),
                "oracle_plate_like": _plate_like(oracle_value),
            })
    return profiles, samples


def reconcile(
    excel_rows: list[dict[str, Any]],
    heads: list[dict[str, Any]],
    details: list[dict[str, Any]],
) -> dict[str, Any]:
    oracle_rows, head_checks = prepare_oracle_details(heads, details)
    excel_by_key: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    oracle_by_key: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in excel_rows:
        excel_by_key[row.get("comparison_key") or comparison_key(row.get("awb"), row.get("lmp"))].append(row)
    for row in oracle_rows:
        oracle_by_key[row["comparison_key"]].append(row)

    matched_keys = sorted(set(excel_by_key) & set(oracle_by_key))
    unmatched_excel_keys = sorted(set(excel_by_key) - set(oracle_by_key))
    unmatched_oracle_keys = sorted(set(oracle_by_key) - set(excel_by_key))
    comparisons: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    field_counts: Counter = Counter()
    field_matches: Counter = Counter()
    plate_candidates, plate_candidate_samples = _profile_plate_candidates(
        excel_by_key, oracle_by_key, matched_keys
    )

    for key in matched_keys:
        excel_group = excel_by_key[key]
        oracle_group = oracle_by_key[key]
        oracle_first = oracle_group[0]
        row_result: dict[str, Any] = {
            "comparison_key": key,
            "awb": _text(oracle_first.get("AIR_WAYBILL")),
            "client": _text(oracle_first.get("CLIENT_NAME")),
            "excel_rows": len(excel_group),
            "oracle_rows": len(oracle_group),
            "e_comm_detail_ids": ", ".join(str(row.get("E_COMM_DETAIL_ID")) for row in oracle_group),
        }
        row_mismatch_count = 0
        mappings = list(FIELD_MAPPING)
        for excel_field, oracle_field, kind, label in mappings:
            if kind == "status":
                # DUP is an Excel-side technical/history marker for records that
                # already exist in WebbyCom. It is not an Oracle lifecycle status
                # and therefore must not count as a field mismatch.
                meaningful_excel_statuses = sorted({
                    data_reader._status_key(row.get(excel_field))
                    for row in excel_group
                    if data_reader._status_key(row.get(excel_field)) not in {"", "dup"}
                })
                if not meaningful_excel_statuses:
                    continue
                left = meaningful_excel_statuses[0] if len(meaningful_excel_statuses) == 1 else tuple(meaningful_excel_statuses)
            else:
                left = _aggregate(excel_group, excel_field, kind, "excel")
            right = _aggregate(oracle_group, oracle_field, kind, "oracle")
            matches = _equal(left, right, kind)
            field_counts[label] += 1
            if matches:
                field_matches[label] += 1
            else:
                row_mismatch_count += 1
                mismatches.append({
                    "comparison_key": key,
                    "awb": row_result["awb"],
                    "client": row_result["client"],
                    "field": label,
                    "excel_field": excel_field,
                    "oracle_field": oracle_field,
                    "excel_value": _display(left),
                    "oracle_value": _display(right),
                    "excel_rows": len(excel_group),
                    "oracle_rows": len(oracle_group),
                })
        row_result["mismatch_count"] = row_mismatch_count
        row_result["status"] = "EGYEZIK" if row_mismatch_count == 0 else "ELTÉRÉS"
        comparisons.append(row_result)

    field_summary = []
    for label in sorted(field_counts):
        checked = field_counts[label]
        matched = field_matches[label]
        field_summary.append({
            "field": label,
            "checked": checked,
            "matched": matched,
            "mismatched": checked - matched,
            "match_rate": matched / checked if checked else None,
        })

    unmatched_excel = [
        {
            "comparison_key": key,
            "awb": _text(excel_by_key[key][0].get("awb")),
            "client": _text(excel_by_key[key][0].get("lmp")),
            "rows": len(excel_by_key[key]),
        }
        for key in unmatched_excel_keys
    ]
    unmatched_oracle = [
        {
            "comparison_key": key,
            "awb": _text(oracle_by_key[key][0].get("AIR_WAYBILL")),
            "client": _text(oracle_by_key[key][0].get("CLIENT_NAME")),
            "rows": len(oracle_by_key[key]),
            "e_comm_detail_ids": ", ".join(str(row.get("E_COMM_DETAIL_ID")) for row in oracle_by_key[key]),
        }
        for key in unmatched_oracle_keys
    ]

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "summary": {
            "excel_rows": len(excel_rows),
            "oracle_head_rows": len(heads),
            "oracle_detail_rows": len(details),
            "matched_keys": len(matched_keys),
            "fully_matching_keys": sum(1 for row in comparisons if row["status"] == "EGYEZIK"),
            "keys_with_mismatch": sum(1 for row in comparisons if row["status"] == "ELTÉRÉS"),
            "unmatched_excel_keys": len(unmatched_excel_keys),
            "unmatched_oracle_keys": len(unmatched_oracle_keys),
            "mismatch_cells": len(mismatches),
            "head_quantity_check_failures": sum(
                1 for row in head_checks
                if not (row["COLLIS_MATCH"] and row["WEIGHT_KG_MATCH"] and row["PARCELS_MATCH"])
            ),
        },
        "field_summary": field_summary,
        "comparisons": comparisons,
        "mismatches": mismatches,
        "unmatched_excel": unmatched_excel,
        "unmatched_oracle": unmatched_oracle,
        "head_detail_checks": head_checks,
        "plate_candidates": plate_candidates,
        "plate_candidate_samples": plate_candidate_samples,
        "mapping": [
            {
                "excel_field": excel_field,
                "oracle_field": oracle_field,
                "kind": kind,
                "label": label,
            }
            for excel_field, oracle_field, kind, label in FIELD_MAPPING
        ],
        "known_gaps": [
            {"excel_field": "poe_raw", "excel_header": "POE", "oracle_field": None, "impact": "jelenleg nincs downstream fogyasztó"},
            {"excel_field": "ai_raw", "excel_header": "PRE-ALERT", "oracle_field": None, "impact": "Oracle-forrásnál nem szükséges; a jelenlegi Excel-specifikus részleges időablak nem kerül tovább"},
            {"excel_field": "partial_boxes_raw", "excel_header": "=", "oracle_field": None, "impact": "halasztva; csak későbbi konkrét üzleti igénynél vizsgálandó"},
            {"excel_field": "partial_weight_raw", "excel_header": "Részben(kg)", "oracle_field": None, "impact": "halasztva; csak későbbi konkrét üzleti igénynél vizsgálandó"},
        ],
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _json_value(value) for key, value in row.items()})


def write_report_data(report: dict[str, Any], output_dir: str | os.PathLike[str]) -> Path:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "reconciliation.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=target, suffix=".tmp") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=_json_value)
        temp_name = handle.name
    os.replace(temp_name, json_path)
    for key in (
        "field_summary",
        "comparisons",
        "mismatches",
        "unmatched_excel",
        "unmatched_oracle",
        "head_detail_checks",
        "plate_candidates",
        "plate_candidate_samples",
        "mapping",
        "known_gaps",
    ):
        _write_csv(target / f"{key}.csv", list(report.get(key) or []))
    _write_csv(target / "summary.csv", [{"metric": key, "value": value} for key, value in report["summary"].items()])
    return json_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only E_COMM Excel–Oracle reconciliation")
    parser.add_argument("--excel", default=data_reader.ECOMM_FILE, help="E_COMM xlsb path")
    parser.add_argument("--output-dir", default=str(Path("outputs") / "webbycom_oracle_reconciliation"))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument("--service", default=DEFAULT_SERVICE)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--driver-dir", default=os.environ.get(DRIVER_DIR_ENV))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    excel_rows = read_excel_source(args.excel)
    heads, details = read_oracle_source(
        host=args.host,
        port=args.port,
        service=args.service,
        user=args.user,
        driver_dir=args.driver_dir,
    )
    report = reconcile(excel_rows, heads, details)
    json_path = write_report_data(report, args.output_dir)
    print(f"Reconciliation complete: {json_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
