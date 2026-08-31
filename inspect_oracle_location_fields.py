"""Read-only evidence check for Oracle outbound location-related fields."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

import data_reader
import oracle_ecomm


OUTPUT = Path("outputs/oracle_location_fields")
COLUMNS = (
    "AIR_WAYBILL",
    "E_COMM_DETAIL_ID",
    "CLIENT_NAME",
    "DEST_COUNTRY_NAME",
    "GLABS_ID",
    "LOCATION_CODES",
    "BUD_PALLET_LOCATION",
    "BUD_PALLET_DUMP_NUMBER",
    "BUD_PALLET_REMARK",
    "BUD_PALLET_RAMP",
    "BUD_PALLET_CHECK_IN",
    "ITEM_DEPARTURE",
    "PLT_OUT",
    "DELIVERY_PLATE_NUMBER",
)


def clean(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return " ".join(str(value).strip().split())


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def main() -> int:
    settings = oracle_ecomm.OracleSettings.from_environment()
    connection = oracle_ecomm._default_connection_factory(settings)
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT " + ", ".join(COLUMNS) +
            f" FROM {oracle_ecomm.DETAIL_VIEW}"
        )
        names = [item[0] for item in cursor.description]
        oracle_rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    finally:
        connection.close()

    pallets = data_reader._read_pallets()
    bud_by_awb: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pallets.to_dict("records"):
        bud_by_awb[clean(row.get("awb"))].append(row)

    oracle_location_nonempty = 0
    matched_awbs = 0
    comparable_locations = 0
    exact_locations = 0
    comparison_samples: list[dict[str, Any]] = []
    dump_samples: list[dict[str, Any]] = []
    location_values: Counter[str] = Counter()
    dump_values: Counter[str] = Counter()
    candidate_fields = (
        "LOCATION_CODES",
        "BUD_PALLET_LOCATION",
        "BUD_PALLET_REMARK",
        "BUD_PALLET_RAMP",
        "BUD_PALLET_DUMP_NUMBER",
        "GLABS_ID",
        "DELIVERY_PLATE_NUMBER",
        "CLIENT_NAME",
        "DEST_COUNTRY_NAME",
    )
    candidate_matches: dict[str, int] = {field: 0 for field in candidate_fields}
    candidate_comparable: dict[str, int] = {field: 0 for field in candidate_fields}
    candidate_samples: dict[str, list[dict[str, Any]]] = {field: [] for field in candidate_fields}

    for row in oracle_rows:
        awb = clean(row.get("AIR_WAYBILL"))
        location = clean(row.get("LOCATION_CODES"))
        dump_number = clean(row.get("BUD_PALLET_DUMP_NUMBER"))
        if location:
            oracle_location_nonempty += 1
            location_values[location] += 1
        if dump_number:
            dump_values[dump_number] += 1
            if len(dump_samples) < 20:
                dump_samples.append({
                    "awb": awb,
                    "detail_id": json_value(row.get("E_COMM_DETAIL_ID")),
                    "glabs_id": clean(row.get("GLABS_ID")),
                    "bud_pallet_dump_number": dump_number,
                    "location_codes": location,
                    "plt_out": json_value(row.get("PLT_OUT")),
                })

        bud_rows = bud_by_awb.get(awb, [])
        if not bud_rows:
            continue
        matched_awbs += 1
        bud_locations = sorted({clean(item.get("location")) for item in bud_rows if clean(item.get("location"))})
        for field in candidate_fields:
            oracle_value = clean(row.get(field))
            if not oracle_value or not bud_locations:
                continue
            candidate_comparable[field] += 1
            found = []
            for bud_location in bud_locations:
                pattern = rf"(?<![A-Z0-9]){re.escape(bud_location.upper())}(?![A-Z0-9])"
                if re.search(pattern, oracle_value.upper()):
                    found.append(bud_location)
            if found:
                candidate_matches[field] += 1
                if len(candidate_samples[field]) < 15:
                    candidate_samples[field].append({
                        "awb": awb,
                        "glabs_id": clean(row.get("GLABS_ID")),
                        "bud_pallets_location": " | ".join(bud_locations),
                        "oracle_value": oracle_value,
                        "matched_locations": found,
                    })
        if not location or not bud_locations:
            continue
        comparable_locations += 1
        oracle_parts = {part.strip().casefold() for part in location.replace(";", ",").split(",") if part.strip()}
        bud_parts = {part.casefold() for part in bud_locations}
        exact = bool(oracle_parts & bud_parts) or location.casefold() in bud_parts
        exact_locations += int(exact)
        if len(comparison_samples) < 30:
            comparison_samples.append({
                "awb": awb,
                "glabs_id": clean(row.get("GLABS_ID")),
                "oracle_location_codes": location,
                "bud_pallets_location": " | ".join(bud_locations),
                "match": exact,
            })

    result = {
        "generated_at": datetime.now().isoformat(),
        "oracle_rows": len(oracle_rows),
        "bud_pallets_rows": len(pallets),
        "oracle_location_nonempty": oracle_location_nonempty,
        "oracle_location_distinct": len(location_values),
        "bud_awb_matches": matched_awbs,
        "location_comparable": comparable_locations,
        "location_matches": exact_locations,
        "location_match_rate": exact_locations / comparable_locations if comparable_locations else None,
        "location_top_values": location_values.most_common(30),
        "location_comparison_samples": comparison_samples,
        "dump_number_nonempty": sum(dump_values.values()),
        "dump_number_distinct": len(dump_values),
        "dump_number_top_values": dump_values.most_common(30),
        "dump_number_samples": dump_samples,
        "candidate_field_checks": {
            field: {
                "comparable": candidate_comparable[field],
                "matches": candidate_matches[field],
                "match_rate": (
                    candidate_matches[field] / candidate_comparable[field]
                    if candidate_comparable[field] else None
                ),
                "samples": candidate_samples[field],
            }
            for field in candidate_fields
        },
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=json_value),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=json_value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
