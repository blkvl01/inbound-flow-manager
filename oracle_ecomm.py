"""WebbyCom Oracle E_COMM source with atomic in-memory snapshots.

The module intentionally contains no password or version-controlled secret.
Credentials are read only from the current process environment.  A refresh
builds and validates a candidate head/detail snapshot first; the live snapshot
is replaced only after both queries and every structural validation succeed.
"""

from __future__ import annotations

import logging
import os
import threading
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Iterable

import pandas as pd


log = logging.getLogger(__name__)

SOURCE_MODE_ENV = "FLOW_ECOMM_SOURCE"
PASSWORD_ENV = "WEBBYCOM_ORACLE_PASSWORD"
HOST_ENV = "WEBBYCOM_ORACLE_HOST"
PORT_ENV = "WEBBYCOM_ORACLE_PORT"
SERVICE_ENV = "WEBBYCOM_ORACLE_SERVICE"
USER_ENV = "WEBBYCOM_ORACLE_USER"
CALL_TIMEOUT_ENV = "WEBBYCOM_ORACLE_CALL_TIMEOUT_MS"
FULL_REFRESH_HOURS_ENV = "WEBBYCOM_ORACLE_FULL_REFRESH_HOURS"
DRIVER_DIR_ENV = "WEBBYCOM_ORACLE_DRIVER_DIR"

DEFAULT_HOST = "192.168.45.180"
DEFAULT_PORT = 1521
DEFAULT_SERVICE = "HGL"
DEFAULT_USER = "ECOMM_READ"
DEFAULT_CALL_TIMEOUT_MS = 120_000
DEFAULT_FULL_REFRESH_HOURS = 6.0
INCREMENTAL_OVERLAP = timedelta(seconds=2)

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
    "REF_DETAIL_CLIENT_ID",
    "CLIENT_NAME",
    "REF_GHA_COMPANY_ID",
    "GHA_COMPANY_NAME",
    "EXPECTED_ARRIVAL",
    "REF_PARCELS_STATUS_ID",
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
    "REF_DEST_COUNTRY_ID",
    "DEST_COUNTRY_NAME",
    "DELIVERY_PLATE_NUMBER",
    "BUD_PALLET_DUMP_NUMBER",
    "GLABS_ID",
    "PLT_OUT",
    "LOCATION_CODES",
    "BUD_PALLET_LOCATION",
    "BUD_PALLET_CHECK_IN",
    "BUD_PALLET_REMARK",
    "BUD_PALLET_RAMP",
    "LAST_CHANGE_DATE",
)

OPTIONAL_DETAIL_COLUMNS = (
    "BUD_PALLET_LOCATION",
    "BUD_PALLET_RAMP",
)
REQUIRED_DETAIL_COLUMNS = tuple(
    column for column in DETAIL_COLUMNS if column not in OPTIONAL_DETAIL_COLUMNS
)

RAW_COLUMNS = (
    "lmp",
    "awb",
    "boxes",
    "weight",
    "parcel_count",
    "poe_raw",
    "carrier_k",
    "expected_arrival_raw",
    "status_n",
    "partial_boxes_raw",
    "partial_weight_raw",
    "uld_raw",
    "ad_raw",
    "ai_raw",
    "ak_raw",
    "noa_raw",
    "am_raw",
    "rendszam_raw",
    "aq_raw",
    "ar_raw",
    "departure_raw",
)

PALLET_COLUMNS = (
    "bud_lmp",
    "awb",
    "plate",
    "eta_note",
    "glabs_id",
    "boxes",
    "weight",
    "driver_checkin",
    "issued_time",
    "rest_raw",
    "kiad_status",
    "pallets_issued",
    "location",
    "load_note",
    "dump_number",
)


class OracleSourceError(RuntimeError):
    """A safe, user-displayable Oracle source failure."""


@dataclass(frozen=True)
class OracleSettings:
    host: str
    port: int
    service: str
    user: str
    password: str
    call_timeout_ms: int
    full_refresh_hours: float
    driver_dir: str | None = None

    @classmethod
    def from_environment(cls) -> "OracleSettings":
        password = os.environ.get(PASSWORD_ENV, "")
        if not password:
            raise OracleSourceError(
                f"Az Oracle jelszó nincs beállítva a {PASSWORD_ENV} folyamat-környezeti változóban."
            )
        try:
            port = int(os.environ.get(PORT_ENV, str(DEFAULT_PORT)))
            timeout = int(os.environ.get(CALL_TIMEOUT_ENV, str(DEFAULT_CALL_TIMEOUT_MS)))
            full_hours = float(os.environ.get(FULL_REFRESH_HOURS_ENV, str(DEFAULT_FULL_REFRESH_HOURS)))
        except ValueError as exc:
            raise OracleSourceError("Hibás numerikus Oracle környezeti beállítás.") from exc
        if port <= 0 or timeout <= 0 or full_hours <= 0:
            raise OracleSourceError("Az Oracle port, timeout és teljes frissítési idő pozitív kell legyen.")
        return cls(
            host=os.environ.get(HOST_ENV, DEFAULT_HOST).strip(),
            port=port,
            service=os.environ.get(SERVICE_ENV, DEFAULT_SERVICE).strip(),
            user=os.environ.get(USER_ENV, DEFAULT_USER).strip(),
            password=password,
            call_timeout_ms=timeout,
            full_refresh_hours=full_hours,
            driver_dir=os.environ.get(DRIVER_DIR_ENV) or None,
        )


@dataclass(frozen=True)
class OracleSnapshot:
    heads: dict[int, dict[str, Any]]
    details: dict[int, dict[str, Any]]
    watermark: datetime
    loaded_at: datetime
    last_full_refresh: datetime
    refresh_kind: str
    changed_heads: int
    changed_details: int
    quality_warnings: tuple[str, ...]
    zero_quantity_details: int
    head_detail_mismatches: int


def get_source_mode() -> str:
    mode = os.environ.get(SOURCE_MODE_ENV, "excel").strip().lower()
    aliases = {"compare": "shadow", "comparison": "shadow"}
    mode = aliases.get(mode, mode)
    return mode if mode in {"excel", "shadow", "oracle"} else "excel"


def _to_int(value: Any) -> int:
    if isinstance(value, Decimal):
        return int(value)
    return int(value)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    result = parsed.to_pydatetime()
    return result.replace(tzinfo=None) if result.tzinfo else result


def _excel_serial(value: Any) -> float | None:
    dt = _as_datetime(value)
    if dt is None:
        return None
    return (dt - datetime(1899, 12, 30)).total_seconds() / 86400.0


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _lmp_label(detail: dict[str, Any], head: dict[str, Any]) -> str:
    """Restore the Excel-style TEMU marker from the Oracle head classification."""
    client = _text(detail.get("CLIENT_NAME"))
    group = _text(head.get("HEAD_MAIN_CLIENT_GROUP"))
    group_key = group.casefold()
    client_key = client.casefold()
    is_temu_group = group_key == "temu" or group_key.startswith("temu ")
    already_marked = client_key == "temu" or client_key.startswith("temu ")
    if is_temu_group and client and not already_marked:
        return f"TEMU {client}"
    return client


def _import_oracledb(driver_dir: str | None = None):
    if driver_dir:
        import sys

        if driver_dir not in sys.path:
            sys.path.insert(0, driver_dir)
    try:
        import oracledb  # type: ignore
    except ImportError as exc:
        raise OracleSourceError(
            "A python-oracledb csomag nem érhető el. Telepítsd a requirements.txt alapján."
        ) from exc
    return oracledb


def _default_connection_factory(settings: OracleSettings):
    oracledb = _import_oracledb(settings.driver_dir)
    params = oracledb.ConnectParams(
        host=settings.host,
        port=settings.port,
        service_name=settings.service,
        tcp_connect_timeout=max(1.0, min(30.0, settings.call_timeout_ms / 1000.0)),
    )
    connection = oracledb.connect(user=settings.user, password=settings.password, params=params)
    connection.call_timeout = settings.call_timeout_ms
    return connection


class OracleEcommStore:
    def __init__(
        self,
        connection_factory: Callable[[OracleSettings], Any] | None = None,
        settings_factory: Callable[[], OracleSettings] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self._connection_factory = connection_factory or _default_connection_factory
        self._settings_factory = settings_factory or OracleSettings.from_environment
        self._now = now_fn or datetime.now
        self._lock = threading.RLock()
        self._snapshot: OracleSnapshot | None = None

    def reset(self) -> None:
        with self._lock:
            self._snapshot = None

    def snapshot(self) -> OracleSnapshot | None:
        with self._lock:
            return self._snapshot

    @staticmethod
    def _query_rows(
        cursor,
        view: str,
        columns: Iterable[str],
        lower_bound: datetime | None,
        upper_bound: datetime,
    ) -> list[dict[str, Any]]:
        if view not in {HEAD_VIEW, DETAIL_VIEW}:
            raise ValueError(f"Nem engedélyezett Oracle view: {view}")
        selected = tuple(columns)
        allowed = set(HEAD_COLUMNS) | set(DETAIL_COLUMNS)
        if not selected or any(column not in allowed for column in selected):
            raise ValueError("Nem engedélyezett Oracle-oszlop.")
        where = "LAST_CHANGE_DATE <= :upper_bound"
        binds: dict[str, Any] = {"upper_bound": upper_bound}
        if lower_bound is not None:
            where += " AND LAST_CHANGE_DATE >= :lower_bound"
            binds["lower_bound"] = lower_bound
        cursor.execute(f"SELECT {', '.join(selected)} FROM {view} WHERE {where}", binds)
        names = [str(description[0]).upper() for description in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    @classmethod
    def _query_detail_rows(
        cls,
        cursor,
        lower_bound: datetime | None,
        upper_bound: datetime,
    ) -> list[dict[str, Any]]:
        """Read new BUD fields when exposed, without breaking the old report view.

        WebbyCom already has these properties, but reporting-view rollout may
        happen later. Only ORA-00904 (missing column) triggers the compatible
        retry; network, permission and other Oracle errors remain hard failures.
        """
        try:
            return cls._query_rows(cursor, DETAIL_VIEW, DETAIL_COLUMNS, lower_bound, upper_bound)
        except Exception as exc:
            if "ORA-00904" not in str(exc).upper():
                raise
            log.warning(
                "Oracle detail view does not expose BUD_PALLET_LOCATION/BUD_PALLET_RAMP yet; "
                "continuing without those optional values"
            )
            rows = cls._query_rows(
                cursor, DETAIL_VIEW, REQUIRED_DETAIL_COLUMNS, lower_bound, upper_bound
            )
            for row in rows:
                for column in OPTIONAL_DETAIL_COLUMNS:
                    row[column] = None
            return rows

    @staticmethod
    def _database_time(cursor) -> datetime:
        cursor.execute("SELECT CAST(SYSTIMESTAMP AS TIMESTAMP) FROM DUAL")
        row = cursor.fetchone()
        upper = _as_datetime(row[0] if row else None)
        if upper is None:
            raise OracleSourceError("Az Oracle rendszeridő nem olvasható.")
        return upper

    @staticmethod
    def _indexed(rows: list[dict[str, Any]], key: str, label: str) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            if row.get(key) is None:
                raise OracleSourceError(f"Hiányzó {label} azonosító.")
            record_id = _to_int(row[key])
            if record_id in result:
                raise OracleSourceError(f"Nem egyedi {label} azonosító: {record_id}")
            result[record_id] = dict(row)
        return result

    @staticmethod
    def _validate(
        heads: dict[int, dict[str, Any]], details: dict[int, dict[str, Any]]
    ) -> tuple[tuple[str, ...], int, int]:
        if not heads or not details:
            raise OracleSourceError("Az Oracle head vagy detail view üres eredményt adott.")
        warnings: list[str] = []
        zero_quantity_details = 0
        detail_sums: dict[int, list[float]] = {}
        for head_id, head in heads.items():
            if _as_datetime(head.get("LAST_CHANGE_DATE")) is None:
                raise OracleSourceError(f"Hiányzó head LAST_CHANGE_DATE: {head_id}")
        for detail_id, detail in details.items():
            if _as_datetime(detail.get("LAST_CHANGE_DATE")) is None:
                raise OracleSourceError(f"Hiányzó detail LAST_CHANGE_DATE: {detail_id}")
            ref = _to_int(detail.get("REF_E_COMM_ID"))
            head = heads.get(ref)
            if head is None:
                raise OracleSourceError(f"Árva detail rekord: {detail_id} -> {ref}")
            if _text(head.get("AIR_WAYBILL")) != _text(detail.get("AIR_WAYBILL")):
                raise OracleSourceError(
                    f"Head/detail AWB eltérés: detail {detail_id}, head {ref}"
                )
            quantities = (
                _number(detail.get("DETAIL_COLLIS")) or 0.0,
                _number(detail.get("DETAIL_WEIGHT_KG")) or 0.0,
                _number(detail.get("DETAIL_PARCELS")) or 0.0,
            )
            if quantities == (0.0, 0.0, 0.0):
                zero_quantity_details += 1
            sums = detail_sums.setdefault(ref, [0.0, 0.0, 0.0])
            for index, value in enumerate(quantities):
                sums[index] += value
        if zero_quantity_details:
            warnings.append(f"{zero_quantity_details} detail rekord minden mennyisége nulla")
        head_detail_mismatches = 0
        for head_id, head in heads.items():
            sums = detail_sums.get(head_id, [0.0, 0.0, 0.0])
            expected = (
                _number(head.get("SUM_OF_COLLIS")) or 0.0,
                _number(head.get("SUM_OF_WEIGHT_KG")) or 0.0,
                _number(head.get("SUM_OF_PARCELS")) or 0.0,
            )
            if (
                abs(sums[0] - expected[0]) > 0.01
                or abs(sums[1] - expected[1]) > 0.11
                or abs(sums[2] - expected[2]) > 0.01
            ):
                head_detail_mismatches += 1
        if head_detail_mismatches:
            warnings.append(
                f"{head_detail_mismatches} head rekord detail összege eltér a kontrollértéktől"
            )
        return tuple(warnings), zero_quantity_details, head_detail_mismatches

    def refresh(self, force_full: bool = False) -> OracleSnapshot:
        """Refresh atomically; retain the previous snapshot on any exception."""
        with self._lock:
            settings = self._settings_factory()
            previous = self._snapshot
            due_full = (
                previous is None
                or force_full
                or (self._now() - previous.last_full_refresh).total_seconds()
                >= settings.full_refresh_hours * 3600.0
            )
            lower = None if due_full else previous.watermark - INCREMENTAL_OVERLAP
            connection = None
            try:
                connection = self._connection_factory(settings)
                cursor = connection.cursor()
                upper = self._database_time(cursor)
                head_rows = self._query_rows(cursor, HEAD_VIEW, HEAD_COLUMNS, lower, upper)
                detail_rows = self._query_detail_rows(cursor, lower, upper)

                changed_heads = self._indexed(head_rows, "E_COMM_ID", "head")
                changed_details = self._indexed(detail_rows, "E_COMM_DETAIL_ID", "detail")
                if due_full:
                    candidate_heads = changed_heads
                    candidate_details = changed_details
                else:
                    candidate_heads = dict(previous.heads)
                    candidate_details = dict(previous.details)
                    candidate_heads.update(changed_heads)
                    candidate_details.update(changed_details)

                warnings, zero_quantity_details, head_detail_mismatches = self._validate(
                    candidate_heads, candidate_details
                )
                loaded_at = self._now()
                candidate = OracleSnapshot(
                    heads=candidate_heads,
                    details=candidate_details,
                    watermark=upper,
                    loaded_at=loaded_at,
                    last_full_refresh=loaded_at if due_full else previous.last_full_refresh,
                    refresh_kind="full" if due_full else "incremental",
                    changed_heads=len(changed_heads),
                    changed_details=len(changed_details),
                    quality_warnings=warnings,
                    zero_quantity_details=zero_quantity_details,
                    head_detail_mismatches=head_detail_mismatches,
                )
                self._snapshot = candidate
                log.info(
                    "Oracle E_COMM %s refresh: head=%d detail=%d changed=%d/%d watermark=%s",
                    candidate.refresh_kind,
                    len(candidate.heads),
                    len(candidate.details),
                    candidate.changed_heads,
                    candidate.changed_details,
                    candidate.watermark.isoformat(),
                )
                for warning in candidate.quality_warnings:
                    log.warning("Oracle E_COMM adatminőség: %s", warning)
                return candidate
            except OracleSourceError:
                raise
            except Exception as exc:
                log.error("Oracle E_COMM refresh failed; previous snapshot kept: %s", exc, exc_info=True)
                raise OracleSourceError(f"Oracle E_COMM lekérdezési hiba: {exc}") from exc
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

    @staticmethod
    def to_raw_dataframe(snapshot: OracleSnapshot) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for detail_id in sorted(snapshot.details):
            detail = snapshot.details[detail_id]
            head = snapshot.heads[_to_int(detail.get("REF_E_COMM_ID"))]
            rows.append({
                "lmp": _lmp_label(detail, head),
                "awb": _text(detail.get("AIR_WAYBILL")),
                "boxes": _number(detail.get("DETAIL_COLLIS")),
                "weight": _number(detail.get("DETAIL_WEIGHT_KG")),
                "parcel_count": _number(detail.get("DETAIL_PARCELS")),
                "poe_raw": None,
                "carrier_k": _text(detail.get("GHA_COMPANY_NAME")),
                "expected_arrival_raw": _excel_serial(detail.get("EXPECTED_ARRIVAL")),
                "status_n": _text(detail.get("PARCEL_STATUS_NAME")),
                "partial_boxes_raw": None,
                "partial_weight_raw": None,
                "uld_raw": _text(detail.get("ULD_NUMBER")),
                "ad_raw": _excel_serial(detail.get("ULD_RETURN_DATE")),
                "ai_raw": None,
                "ak_raw": _excel_serial(detail.get("ATA")),
                "noa_raw": _excel_serial(detail.get("NOA")),
                "am_raw": _excel_serial(detail.get("GOODS_TRANSFER_DATE")),
                "rendszam_raw": _text(detail.get("TRANSFER_PLATE_NUMBER")),
                "aq_raw": _excel_serial(detail.get("CUSTOMS_CLEARANCE_START")),
                "ar_raw": _excel_serial(detail.get("CUSTOMS_CLEARANCE_FINISH")),
                "departure_raw": _excel_serial(detail.get("ITEM_DEPARTURE")),
            })
        return pd.DataFrame(rows, columns=RAW_COLUMNS)

    @staticmethod
    def to_pallets_dataframe(snapshot: OracleSnapshot) -> pd.DataFrame:
        """Build the BUD-Pallets-compatible detail frame used by outbound logic.

        LOCATION_CODES is deliberately not used here: it is the item's warehouse
        storage-code collection, while the BUD Pallets Location value is exposed
        separately as BUD_PALLET_LOCATION.
        """
        rows: list[dict[str, Any]] = []
        for detail_id in sorted(snapshot.details):
            detail = snapshot.details[detail_id]
            rows.append({
                "bud_lmp": _text(detail.get("DEST_COUNTRY_NAME")),
                "awb": _text(detail.get("AIR_WAYBILL")),
                "plate": _text(detail.get("DELIVERY_PLATE_NUMBER")) or None,
                # WebbyCom has no separate BUD-Pallets ETA field in this view.
                # Keep the free remark available to the existing forwarded-note
                # detector while also using it for rest/E-COM parsing below.
                "eta_note": _text(detail.get("BUD_PALLET_REMARK")) or None,
                "glabs_id": _text(detail.get("GLABS_ID")) or None,
                "boxes": _number(detail.get("DETAIL_COLLIS")) or 0.0,
                "weight": _number(detail.get("DETAIL_WEIGHT_KG")) or 0.0,
                "driver_checkin": _as_datetime(detail.get("BUD_PALLET_CHECK_IN")),
                "issued_time": _as_datetime(detail.get("ITEM_DEPARTURE")),
                "rest_raw": _text(detail.get("BUD_PALLET_REMARK")) or None,
                "kiad_status": _text(detail.get("PARCEL_STATUS_NAME")) or None,
                "pallets_issued": _number(detail.get("PLT_OUT")) or 0.0,
                "location": _text(detail.get("BUD_PALLET_LOCATION")) or None,
                "load_note": (
                    _text(detail.get("BUD_PALLET_RAMP"))
                    or _text(detail.get("BUD_PALLET_REMARK"))
                    or None
                ),
                "dump_number": _text(detail.get("BUD_PALLET_DUMP_NUMBER")) or None,
            })
        frame = pd.DataFrame(rows, columns=PALLET_COLUMNS)
        frame.attrs["pallets_source_type"] = "oracle"
        return frame

    @staticmethod
    def metadata(snapshot: OracleSnapshot) -> dict[str, Any]:
        pallets = OracleEcommStore.to_pallets_dataframe(snapshot)
        return {
            "ecomm_source_type": "oracle",
            "oracle_head_count": len(snapshot.heads),
            "oracle_detail_count": len(snapshot.details),
            "oracle_refresh_kind": snapshot.refresh_kind,
            "oracle_changed_heads": snapshot.changed_heads,
            "oracle_changed_details": snapshot.changed_details,
            "oracle_watermark": snapshot.watermark.isoformat(),
            "oracle_last_refresh": snapshot.loaded_at.isoformat(),
            "oracle_last_full_refresh": snapshot.last_full_refresh.isoformat(),
            "oracle_quality_warnings": list(snapshot.quality_warnings),
            "oracle_zero_quantity_details": snapshot.zero_quantity_details,
            "oracle_head_detail_mismatches": snapshot.head_detail_mismatches,
            "oracle_pallet_rows": len(pallets),
            "oracle_pallet_locations": int(pallets["location"].notna().sum()),
        }

    def refresh_raw(self, force_full: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
        snapshot = self.refresh(force_full=force_full)
        return self.to_raw_dataframe(snapshot), self.metadata(snapshot)

    def current_raw(self) -> tuple[pd.DataFrame, dict[str, Any]] | None:
        with self._lock:
            snapshot = self._snapshot
            if snapshot is None:
                return None
            return self.to_raw_dataframe(snapshot), self.metadata(snapshot)

    def refresh_pallets(self, force_full: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
        snapshot = self.refresh(force_full=force_full)
        return self.to_pallets_dataframe(snapshot), self.metadata(snapshot)

    def current_pallets(self) -> tuple[pd.DataFrame, dict[str, Any]] | None:
        with self._lock:
            snapshot = self._snapshot
            if snapshot is None:
                return None
            return self.to_pallets_dataframe(snapshot), self.metadata(snapshot)


_store = OracleEcommStore()


def refresh_raw(force_full: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
    return _store.refresh_raw(force_full=force_full)


def reset_store() -> None:
    _store.reset()


def current_raw() -> tuple[pd.DataFrame, dict[str, Any]] | None:
    return _store.current_raw()


def refresh_pallets(force_full: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
    return _store.refresh_pallets(force_full=force_full)


def current_pallets() -> tuple[pd.DataFrame, dict[str, Any]] | None:
    return _store.current_pallets()


def main() -> int:
    """Read-only operational smoke check; prints metadata, never credentials."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format="%(message)s", force=True)
    try:
        raw, meta = refresh_raw(force_full=True)
    except OracleSourceError as exc:
        print(f"ORACLE_ADAPTER_ERROR: {exc}")
        return 1
    print("ORACLE_ADAPTER_OK")
    print(json.dumps({**meta, "raw_rows": len(raw)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
