import logging
import os
import re
import shutil
import tempfile
import time
import unicodedata
import warnings
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import openpyxl
import pandas as pd
from pyxlsb import open_workbook

from config import ECOMM_FILE, PALLETS_FILE
import oracle_ecomm

log = logging.getLogger(__name__)
warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported.*",
    category=UserWarning,
)


# OneDrive / Excel resilience for the source copy. A transient share/lock during
# sync is common; a few short retries clears it. A copy that *hangs* (cloud-only
# hydration) is NOT handled here — the data_cache scheduler bounds the whole read
# with a hard timeout and keeps the last good data.
_COPY_RETRIES = 4
_COPY_BACKOFF_S = 0.6
# Windows FILE_ATTRIBUTE flags that mark a OneDrive "Files On-Demand" placeholder
# whose bytes are not fully present locally (opening it triggers a network fetch).
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000


def _is_cloud_only_placeholder(src: str) -> bool:
    """True if src is a OneDrive cloud-only placeholder (not hydrated locally).

    Reading such a file triggers an on-demand download that can block for a long
    time. We can't force hydration safely here, but detecting it lets the caller
    log a precise reason and lets the scheduler flag "sync stuck" instead of a
    mystery hang.
    """
    try:
        attrs = os.stat(src).st_file_attributes  # Windows-only attribute
    except (OSError, AttributeError):
        return False
    return bool(attrs & (_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS | _FILE_ATTRIBUTE_OFFLINE))


def _temp_copy(src: str) -> str:
    """Copy src to a temp file; returns the temp path. Caller must delete it.

    Retries a few times on transient PermissionError/OSError (Excel/OneDrive lock
    during sync). Raises the last error if every attempt fails, so the caller's
    read fails cleanly (and the scheduler keeps the last good data) rather than
    silently returning an empty frame.
    """
    suffix = os.path.splitext(src)[1]
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    if _is_cloud_only_placeholder(src):
        log.warning(
            "Source is a OneDrive cloud-only placeholder (not fully synced locally): %s",
            src,
        )

    last_exc: Exception | None = None
    for attempt in range(_COPY_RETRIES):
        try:
            shutil.copy2(src, tmp)
            return tmp
        except (PermissionError, OSError) as exc:
            last_exc = exc
            log.info(
                "Source copy attempt %d/%d failed (%s): %s",
                attempt + 1, _COPY_RETRIES, os.path.basename(src), exc,
            )
            time.sleep(_COPY_BACKOFF_S * (attempt + 1))

    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise last_exc if last_exc is not None else OSError(f"Could not copy source: {src}")

# Current 0-based column indices in the E_COMM ``E-comm`` sheet. The temporary
# blank T column seen on 2026-07-14 is no longer present in the live 2026-07-22
# workbook. These remain preferred positions only: both readers resolve the real
# columns from semantic headers, so either layout is supported.
#
# B=1 lmp, D=3 awb, E=4 boxes/colli, F=5 weight, G=6 parcels, J=9 poe,
# K=10 carrier_k, L=11 expected arrival, N=13 status_n,
# V=21 uld_raw (ULD identifier), AD=29 ad_raw (ULD return date),
# AH=33 ai_raw (PRE-ALERT), AJ=35 ak_raw (ATA), AK=36 noa_raw (NOA),
# AL=37 am_raw (Áttár/Transfer), AN=39 rendszam_raw,
# AP=41 aq_raw (Vámkez.k. = customs START), AQ=42 ar_raw (Vámkez.v. = customs END),
# AS=44 departure_raw (Tétel indulás / Outbound).
_ECOMM_COLUMN_INDEX = {
    "lmp": 1,
    "awb": 3,
    "boxes": 4,
    "weight": 5,
    "parcel_count": 6,
    "poe_raw": 9,
    "carrier_k": 10,
    "expected_arrival_raw": 11,
    "status_n": 13,
    "partial_boxes_raw": 14,
    "partial_weight_raw": 15,
    "uld_raw": 21,
    "ad_raw": 29,
    "ai_raw": 33,
    "ak_raw": 35,
    "noa_raw": 36,
    "am_raw": 37,
    "rendszam_raw": 39,
    "aq_raw": 41,
    "ar_raw": 42,
    "departure_raw": 44,
}
_ECOMM_NAMES = list(_ECOMM_COLUMN_INDEX)
_ECOMM_COLS = [_ECOMM_COLUMN_INDEX[name] for name in _ECOMM_NAMES]
_ECOMM_ULD_NAMES = ["lmp", "awb", "carrier_k", "uld_raw", "ad_raw", "am_raw"]
_ECOMM_ULD_COLS = [_ECOMM_COLUMN_INDEX[name] for name in _ECOMM_ULD_NAMES]

# Header labels let both readers recover the real indices from the workbook
# instead of silently corrupting the mapping after a future column insertion.
# ``Várhatóérk.`` may occur twice; the candidate nearest the current known index is
# selected, which consistently identifies the operational L-column value.
_ECOMM_HEADER_LABEL = {
    "lmp": "Ügyfél",
    "awb": "AWB",
    "boxes": "Colli",
    "weight": "Súly",
    "parcel_count": "Parcel",
    "poe_raw": "POE",
    "carrier_k": "GHA",
    "expected_arrival_raw": "Várhatóérk.",
    "status_n": "Státusz",
    "partial_boxes_raw": "=",
    "partial_weight_raw": "Részben(kg)",
    "uld_raw": "ULD azonosító",
    "ad_raw": "Vissza",
    "ai_raw": "PRE-ALERT",
    "ak_raw": "ATA",
    "noa_raw": "NOA",
    "am_raw": "Áttár",
    "rendszam_raw": "Rendszám",
    "aq_raw": "Vámkez.k.",
    "ar_raw": "Vámkez.v.",
    "departure_raw": "Tétel indulás",
}

# Only explicitly verified spelling variants are accepted. Keeping aliases
# field-specific avoids the collisions that a global "remove punctuation"
# normalizer could introduce between unrelated workbook headers. The current
# 2026-08-31 workbook uses ``Vámkez.k.`` in AP and ``Vámkez.vége`` in AQ;
# abbreviated labels are retained for older E_COMM revisions.
_ECOMM_HEADER_ALIASES = {
    "aq_raw": ("Vámkez k.", "Vámkez.k", "Vámkez k"),
    "ar_raw": ("Vámkez v.", "Vámkez.v", "Vámkez v", "Vámkez.vége"),
}
_ECOMM_HEADER_SCAN_ROWS = 100


def _normalize_ecomm_header(value) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return re.sub(r"\s+", "", text)


def _ecomm_header_targets(name: str) -> set[str]:
    """Normalized, field-specific accepted labels for one logical column."""
    label = _ECOMM_HEADER_LABEL.get(name)
    if label is None:
        raise KeyError(f"Unknown ECOMM field: {name}")
    return {
        normalized
        for value in (label, *_ECOMM_HEADER_ALIASES.get(name, ()))
        if (normalized := _normalize_ecomm_header(value))
    }


def _is_ecomm_header_row(header_values: list) -> bool:
    """Recognize the semantic header row without assuming fixed B/D positions."""
    normalized = {_normalize_ecomm_header(value) for value in header_values}
    return bool(
        normalized.intersection(_ecomm_header_targets("lmp"))
        and normalized.intersection(_ecomm_header_targets("awb"))
    )


def _resolve_ecomm_column_indices(
    header_values: list,
    names: Optional[list[str]] = None,
) -> dict[str, int]:
    """Resolve logical ECOMM field names against one workbook header row."""
    requested = list(names or _ECOMM_NAMES)
    normalized = [_normalize_ecomm_header(value) for value in header_values]
    resolved: dict[str, int] = {}
    missing: list[str] = []

    for name in requested:
        label = _ECOMM_HEADER_LABEL.get(name)
        targets = _ecomm_header_targets(name)
        candidates = [idx for idx, value in enumerate(normalized) if value in targets]
        if not candidates:
            missing.append(f"{name} ({label})")
            continue
        preferred = _ECOMM_COLUMN_INDEX[name]
        resolved[name] = min(candidates, key=lambda idx: (abs(idx - preferred), idx))

    if missing:
        raise ValueError("Az E_COMM oszlopstruktúrája eltér; hiányzó oszlopok: " + ", ".join(missing))
    if len(set(resolved.values())) != len(resolved):
        raise ValueError(f"Az E_COMM oszlopstruktúrája eltér; többször felismert oszlopok: {resolved}")
    return resolved


def _resolve_ecomm_column_map(
    workbook_path: str,
    names: Optional[list[str]] = None,
) -> tuple[dict[str, int], int]:
    """Find the ECOMM label row and return its header-driven column mapping."""
    with open_workbook(workbook_path) as wb:
        with wb.get_sheet("E-comm") as sheet:
            for scanned, row in enumerate(sheet.rows(), start=1):
                values = [cell.v for cell in row]
                if _is_ecomm_header_row(values):
                    excel_row = (row[0].r + 1) if row else scanned
                    return _resolve_ecomm_column_indices(values, names), excel_row
                if scanned >= _ECOMM_HEADER_SCAN_ROWS:
                    break
    raise ValueError(
        f"Az E_COMM oszlopstruktúrája eltér; a fejléc nem található az első {_ECOMM_HEADER_SCAN_ROWS} sorban"
    )


_ECOMM_MAX_EXCEL_ROW = 25000  # operational rows have grown beyond the legacy 10,000-row KPI export range
ECOMM_STALE_AFTER_MINUTES = 60


def _ecomm_data_nrows(header_row: int) -> int:
    """Rows after the detected header while preserving the Excel row-25000 cap."""
    return max(0, _ECOMM_MAX_EXCEL_ROW - int(header_row))

# AWB 3-digit prefix -> airline label (used by the trend chart "prefix" breakdown).
# Mirrors the Reggeli Riport airline map; unknown prefixes fall back to the raw code.
_AIRLINE_PREFIX = {
    "022": "022", "038": "038 Hungarian Airlines", "057": "057 Air France",
    "065": "065 Saudi Arabian Airlines", "071": "071 Ethiopian Airlines", "074": "074 KLM",
    "077": "077 Egypt Air", "080": "080 LOT Polish Airlines", "112": "112 China Cargo Airlines",
    "157": "157 Qatar", "172": "172 Cargolux", "176": "176 Emirates SkyCargo",
    "180": "180 Korean Airlines", "207": "207 CAMEX Georgia", "227": "227",
    "233": "233 MSC AIR cargo", "235": "235 Turkish", "250": "250 Uzbekistan Airways",
    "317": "317 Air Atlanta Europe", "330": "330 Geosky", "369": "369 Atlas Air USA",
    "416": "416 National Airlines",
}
# How many trailing periods to keep on the trend chart (keeps it readable + the
# store payload small). The chip filter still exposes every series.
_TREND_DAY_CAP  = 30
_TREND_WEEK_CAP = 12


def _serial_to_dt(v) -> Optional[datetime]:
    if v is None or (isinstance(v, float) and v != v):  # NaN check
        return None
    try:
        return datetime(1899, 12, 30) + timedelta(days=float(v))
    except (TypeError, ValueError):
        return None


def _empty(v) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and v != v:  # NaN
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def _to_float(v, default: float = 0.0) -> float:
    if _empty(v):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _source_age_minutes(path: str) -> float | None:
    try:
        return max(0.0, (datetime.now().timestamp() - os.path.getmtime(path)) / 60.0)
    except OSError:
        return None


def _is_dt(v) -> bool:
    return isinstance(v, datetime) and not pd.isna(v)


def _strip_accents(text: str) -> str:
    table = str.maketrans({
        "á": "a", "é": "e", "í": "i", "ó": "o", "ö": "o", "ő": "o",
        "ú": "u", "ü": "u", "ű": "u", "Á": "A", "É": "E", "Í": "I",
        "Ó": "O", "Ö": "O", "Ő": "O", "Ú": "U", "Ü": "U", "Ű": "U",
        "õ": "o", "Õ": "O",
    })
    return text.translate(table)


def _status_key(value) -> str:
    if _empty(value):
        return ""
    text = _strip_accents(str(value)).strip().casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_ACTIVE_STATUS_KEYS = {"felveve"}
_PARTIAL_STATUS_KEY = "reszben"
_READY_STATUS_KEYS = {"kiadhato", "mitortenik", "mi tortenik", "vamkez alatt"}


def _is_ready_status(status) -> bool:
    return _status_key(status) in _READY_STATUS_KEYS


def _is_active_inbound_status(status) -> bool:
    return _status_key(status) in _ACTIVE_STATUS_KEYS


def _is_issued_status(status) -> bool:
    return _status_key(status) == "kiadva"


def _is_pallet_issued(status, issued_time) -> bool:
    return _is_issued_status(status) or _is_dt(issued_time)


def _base_awb(awb: str) -> str:
    parts = str(awb or "").strip().rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) <= 3:
        return parts[0]
    return str(awb or "").strip()


def _parse_rest_text(rest_raw, driver_checkin: Optional[datetime]) -> tuple[bool, Optional[datetime]]:
    """Recognize real rest notes from free-text column M values.

    Only text explicitly indicating rest counts as rest. Plain dates or other
    notes must not be treated as rest.
    """
    if _is_dt(rest_raw):
        return False, None
    if not isinstance(rest_raw, str):
        return False, None

    raw = rest_raw.strip()
    if not raw:
        return False, None

    text = _strip_accents(raw).lower()
    rest_markers = ("pihen", "piheno", "pihenos", "pihin", "szunet")
    if not any(marker in text for marker in rest_markers):
        return False, None

    now = datetime.now()

    month_day = re.search(r"(?P<month>\d{1,2})\.(?P<day>\d{1,2})(?:\.)?(?:-an|-en)?", text)
    time_match = re.search(r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*-?\s*ig", text)
    if not time_match:
        time_match = re.search(r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?", text)

    if not time_match:
        return True, None

    hour = int(time_match.group("hour"))
    minute = int(time_match.group("minute") or 0)
    if hour > 23 or minute > 59:
        return True, None

    if month_day:
        month = int(month_day.group("month"))
        day = int(month_day.group("day"))
        try:
            candidate = datetime(now.year, month, day, hour, minute)
        except ValueError:
            return True, None

        if candidate < now - timedelta(days=200):
            candidate = candidate.replace(year=now.year + 1)
        elif candidate > now + timedelta(days=200):
            candidate = candidate.replace(year=now.year - 1)
        return True, candidate

    base_date = driver_checkin.date() if _is_dt(driver_checkin) else now.date()
    candidate = datetime.combine(base_date, datetime.min.time()).replace(hour=hour, minute=minute)

    if _is_dt(driver_checkin) and candidate < driver_checkin - timedelta(hours=2):
        candidate += timedelta(days=1)
    elif candidate < now - timedelta(hours=12):
        candidate += timedelta(days=1)

    return True, candidate


# Expiry ("Lejárati idő") shown on the card + drives the status colours: AN + 48h.
# Active-list cutoff is separate and LONGER: a ULD only drops out of the active
# board 60h after AN, so a ULD past its 48h deadline still stays visible (with a
# "lejárt!" / overdue treatment) for another 12h instead of silently vanishing.
_ULD_EXPIRY_HOURS = 48.0
_ULD_ACTIVE_CUTOFF_HOURS = 60.0
ULD_DATA_PARSER_VERSION = 10

_ULD_CONFUSABLES = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K",
    "М": "M", "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y",
    "а": "A", "в": "B", "с": "C", "е": "E", "н": "H", "к": "K",
    "м": "M", "о": "O", "р": "P", "т": "T", "х": "X", "у": "Y",
    "Ο": "O", "ο": "O", "Ι": "I", "І": "I", "і": "I",
})


def _normalize_uld_code(value) -> str:
    """Canonical ULD key: ASCII A-Z/0-9, with common Excel/paste confusables fixed."""
    text = str(value or "").strip().upper().translate(_ULD_CONFUSABLES)
    return "".join(ch for ch in text if ch.isascii() and ch.isalnum())


def _normalize_uld_gha(value) -> str:
    """Return a stable display key for GHA values coming from E_COMM/Oracle.

    The operational source has historically contained harmless spelling variants
    (case, accents, ``Kft.`` suffixes).  Canonicalising known handlers keeps the
    filter, stack validation and colour mapping on one key.  Unknown non-empty
    values are deliberately preserved instead of guessed.
    """
    if _empty(value):
        return ""
    text = " ".join(str(value).strip().split())
    folded = "".join(ch for ch in _strip_accents(text).casefold() if ch.isalnum())
    if folded == "as" or folded.startswith("ascargo"):
        return "AS Cargo"
    if "menzies" in folded:
        return "Menzies"
    if "celebi" in folded:
        return "Celebi"
    return text


def _has_valid_uld_prefix(uld: str) -> bool:
    """IATA ULD numbers start with a 3-letter type code (PMC, PAG, LD3, AKE...).
    A purely numeric prefix (e.g. "112-04958634") is an AWB scan, not a ULD.
    Require at least one letter in the first 3 characters.
    """
    if len(uld) < 3:
        return False
    return any(c.isalpha() for c in uld[:3])


def _parse_uld_numbers(raw) -> list[str]:
    """Parse V column: multiple ULD numbers separated by commas.

    Example: "PMD34212MB-69,(05.12)PMD34353MB-61 (05.12)" → ["PMD34212MB", "PMD34353MB"]
    ULD codes are usually 10 characters, but some ECOMM rows contain 11-character
    owner suffixes such as PMC20257YAA. Keep that full code while still ignoring
    dash/space suffixes like "-69".
    Entries whose first 3 characters are purely numeric are filtered out.
    """
    if _empty(raw):
        return []
    parts = str(raw).strip().split(",")
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        part = re.sub(r"\(\d{1,2}\.\d{1,2}\)", "", part).strip()
        # Uppercase + strip non-alphanumeric so JS/server/stack-JSON all agree on the key.
        # ULD numbers from Excel can be mixed-case "pmc12345lh" or contain dashes/spaces; the
        # stack manager uppercases on write but the row data preserved raw form previously,
        # which broke prepared/lookup matching for ULDs in stacks.
        code_part = re.split(r"[-\s;/]+", part, maxsplit=1)[0]
        cleaned = _normalize_uld_code(code_part)
        if len(cleaned) >= 11:
            uld = cleaned[:11]
        else:
            uld = cleaned[:10]
        if len(uld) >= 3 and uld not in seen and _has_valid_uld_prefix(uld):
            result.append(uld)
            seen.add(uld)
    return result


def _uld_status_for_remaining(remaining_h: float) -> str:
    """ULD státusz a 48h-s lejáratig hátralévő idő alapján. Negatív (lejárt, de
    még az aktív listán a 60h-s cutoffig) tételek 'critical'-ként pirosak."""
    if remaining_h <= 6:
        return "critical"
    if remaining_h <= 12:
        return "warning"
    return "ok"


def _extract_uld_data(raw: pd.DataFrame, archive_out: list | None = None) -> list[dict]:
    """Extract open ULD records from raw (unfiltered) E_COMM data.

    Open ULD: ``ULD azonosító`` and ``Áttár`` filled, ``Vissza`` empty.
    Returns one record per unique ULD number, aggregating AWBs/LMPs across rows.
    """
    if raw.empty or "uld_raw" not in raw.columns or "am_raw" not in raw.columns:
        return []
    result, meta = _fast_uld_data_from_records(raw.to_dict("records"))
    if archive_out is not None:
        archive_out.extend(meta.get("uld_archive") or [])
    return result


def _finish_fast_uld_data(
    uld_map: dict[str, dict], returned_ulds: set[str], meta: dict, now: datetime
) -> tuple[list[dict], dict]:
    uld_data = []
    uld_archive: list[dict] = []
    uld_times_full: dict = {}
    for rec in uld_map.values():
        am_dt = rec["am_time"]
        expiry_dt = am_dt + timedelta(hours=_ULD_EXPIRY_HOURS)
        remaining_h = (expiry_dt - now).total_seconds() / 3600.0
        elapsed_h = (now - am_dt).total_seconds() / 3600.0
        uld_times_full[rec["uld_number"]] = {
            "am_time": am_dt.isoformat(),
            "expiry_time": expiry_dt.isoformat(),
            "gha": rec.get("gha", ""),
            "gha_source": rec.get("gha_source", ""),
            "gha_conflict": bool(rec.get("gha_conflict")),
            "gha_candidates": list(rec.get("gha_candidates", [])),
            "awbs": list(rec.get("awbs", [])),
        }
        if elapsed_h >= _ULD_ACTIVE_CUTOFF_HOURS:
            uld_archive.append({
                "uld_number": rec["uld_number"],
                "uld_type": rec["uld_type"],
                "awbs": rec["awbs"],
                "lmps": rec["lmps"],
                "gha": rec["gha"],
                "gha_source": rec.get("gha_source", ""),
                "gha_conflict": bool(rec.get("gha_conflict")),
                "gha_candidates": list(rec.get("gha_candidates", [])),
                "am_time": am_dt.isoformat(),
                "elapsed_hours": round(elapsed_h, 2),
                "is_archived": True,
                "status": "Archív",
            })
            continue
        status = _uld_status_for_remaining(remaining_h)
        uld_data.append({
            "uld_number": rec["uld_number"],
            "uld_type": rec["uld_type"],
            "awbs": rec["awbs"],
            "lmps": rec["lmps"],
            "gha": rec["gha"],
            "gha_source": rec.get("gha_source", ""),
            "gha_conflict": bool(rec.get("gha_conflict")),
            "gha_candidates": list(rec.get("gha_candidates", [])),
            "am_time": am_dt.isoformat(),
            "expiry_time": expiry_dt.isoformat(),
            "expiry_hours": _ULD_EXPIRY_HOURS,
            "remaining_hours": round(remaining_h, 2),
            "elapsed_hours": round(elapsed_h, 2),
            "is_expired": remaining_h <= 0,
            "status": status,
        })
    uld_data.sort(key=lambda r: r["remaining_hours"])
    uld_archive.sort(key=lambda r: r["elapsed_hours"])
    meta["uld_count"] = len(uld_data)
    meta["uld_archive"] = uld_archive
    meta["uld_times_full"] = uld_times_full
    meta["uld_returned"] = sorted(returned_ulds - set(uld_map))
    return uld_data, meta


def _fast_uld_data_from_records(records, meta: dict | None = None) -> tuple[list[dict], dict]:
    """Build the ULD board from a minimal E_COMM record stream.

    GHA is resolved from the ULD row first, then from another row with the exact
    same AWB when the ULD row is blank.  Ambiguous evidence stays blank and is
    explicitly flagged; the parser never invents a handler.  A dated return also
    closes older occurrences of a reused physical ULD code.
    """
    meta = dict(meta or {})
    rows = list(records or [])
    uld_map: dict[str, dict] = {}
    returned_ulds: set[str] = set()
    latest_return: dict[str, datetime] = {}
    gha_by_awb: dict[str, set[str]] = {}
    now = datetime.now()

    for row in rows:
        awb = str(row.get("awb") or "").strip()
        gha = _normalize_uld_gha(row.get("carrier_k"))
        if awb and gha:
            gha_by_awb.setdefault(awb, set()).add(gha)
        uld_numbers = _parse_uld_numbers(row.get("uld_raw"))
        if not uld_numbers or _empty(row.get("ad_raw")):
            continue
        returned_ulds.update(uld_numbers)
        returned_at = _serial_to_dt(row.get("ad_raw"))
        if returned_at is not None:
            for uld_num in uld_numbers:
                previous = latest_return.get(uld_num)
                if previous is None or returned_at > previous:
                    latest_return[uld_num] = returned_at

    for row in rows:
        uld_numbers = _parse_uld_numbers(row.get("uld_raw"))
        if not uld_numbers or not _empty(row.get("ad_raw")):
            continue
        am_dt = _serial_to_dt(row.get("am_raw"))
        if am_dt is None:
            continue
        awb = str(row.get("awb") or "").strip()
        lmp = str(row.get("lmp") or "").strip()
        gha = _normalize_uld_gha(row.get("carrier_k"))
        for uld_num in uld_numbers:
            # A returned occurrence is history.  Only a later Áttár starts a new,
            # active lifecycle for the same reusable physical code.
            returned_at = latest_return.get(uld_num)
            if returned_at is not None and am_dt <= returned_at:
                continue
            rec = uld_map.setdefault(uld_num, {
                "uld_number": uld_num,
                "uld_type": uld_num[:3].upper() if len(uld_num) >= 3 else uld_num,
                "awbs": [],
                "lmps": [],
                "gha": "",
                "gha_source": "",
                "gha_conflict": False,
                "gha_candidates": [],
                "_direct_ghas": set(),
                "am_time": am_dt,
            })
            if awb and awb not in rec["awbs"]:
                rec["awbs"].append(awb)
            if lmp and lmp not in rec["lmps"]:
                rec["lmps"].append(lmp)
            if am_dt < rec["am_time"]:
                rec["am_time"] = am_dt
            if gha:
                rec["_direct_ghas"].add(gha)

    for rec in uld_map.values():
        direct = set(rec.pop("_direct_ghas", set()))
        awb_candidates: set[str] = set()
        for awb in rec.get("awbs", []):
            awb_candidates.update(gha_by_awb.get(awb, set()))
        candidates = direct | awb_candidates
        if len(candidates) == 1:
            rec["gha"] = next(iter(candidates))
            rec["gha_source"] = "direct" if direct else "awb"
        elif len(candidates) > 1:
            rec["gha_conflict"] = True
            rec["gha_candidates"] = sorted(candidates)

    return _finish_fast_uld_data(uld_map, returned_ulds, meta, now)


def _fast_uld_data_from_raw(raw: pd.DataFrame, meta: dict | None = None) -> tuple[list[dict], dict]:
    return _fast_uld_data_from_records(raw.to_dict("records"), meta)


def load_uld_data_fast() -> tuple[list[dict], dict]:
    """Read only the E_COMM columns needed by the ULD board.

    This is intentionally separate from the full inbound/outbound refresh so
    switching to the ULD tab can show fresh ULD data without waiting for GLABS,
    priority and BUD-Pallets processing.
    """
    meta: dict = {}
    tmp_path: str | None = None
    try:
        if oracle_ecomm.get_source_mode() == "oracle":
            current = oracle_ecomm.current_raw()
            raw, oracle_meta = current if current is not None else oracle_ecomm.refresh_raw()
            return _fast_uld_data_from_raw(raw, oracle_meta)

        source_age = _source_age_minutes(ECOMM_FILE)
        meta["ecomm_source_age_minutes"] = source_age
        meta["ecomm_source_mtime"] = datetime.fromtimestamp(os.path.getmtime(ECOMM_FILE)).isoformat() if source_age is not None else None
        if source_age is None:
            meta["ecomm_source_missing"] = True
            return [], meta
        if source_age > ECOMM_STALE_AFTER_MINUTES:
            meta["ecomm_source_stale"] = True
            return [], meta

        tmp_path = _temp_copy(ECOMM_FILE)
        column_index, header_row = _resolve_ecomm_column_map(tmp_path, _ECOMM_ULD_NAMES)
        lmp_idx = column_index["lmp"]
        awb_idx = column_index["awb"]
        gha_idx = column_index["carrier_k"]
        uld_idx = column_index["uld_raw"]
        ad_idx = column_index["ad_raw"]
        am_idx = column_index["am_raw"]
        records: list[dict] = []
        with open_workbook(tmp_path) as wb:
            with wb.get_sheet("E-comm") as sheet:
                for row_idx, row in enumerate(sheet.rows(), start=1):
                    if row_idx <= header_row:
                        continue
                    if row_idx > _ECOMM_MAX_EXCEL_ROW:
                        break
                    lmp_raw = row[lmp_idx].v if len(row) > lmp_idx else None
                    awb_raw = row[awb_idx].v if len(row) > awb_idx else None
                    gha_raw = row[gha_idx].v if len(row) > gha_idx else None
                    uld_raw = row[uld_idx].v if len(row) > uld_idx else None
                    ad_raw = row[ad_idx].v if len(row) > ad_idx else None
                    am_raw = row[am_idx].v if len(row) > am_idx else None
                    # Keep every AWB/GHA pair, even when this particular row has no
                    # ULD value.  The common resolver can safely backfill a blank ULD
                    # row from another row with the exact same AWB.
                    records.append({
                        "lmp": lmp_raw,
                        "awb": awb_raw,
                        "carrier_k": gha_raw,
                        "uld_raw": uld_raw,
                        "ad_raw": ad_raw,
                        "am_raw": am_raw,
                    })

        return _fast_uld_data_from_records(records, meta)
    except Exception as exc:
        log.error("Fast ULD read error: %s", exc, exc_info=True)
        meta["error"] = str(exc)
        return [], meta
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _shift_hour_buckets(am_f, w, start_dt, now_dt, epoch) -> list[dict]:
    """12 one-hour kg buckets from start_dt, by felvétel idő (AN). Hours that have
    not started yet (relative to now_dt) are flagged inactive; the in-progress hour
    is flagged current."""
    buckets = []
    for i in range(12):
        h_start = start_dt + timedelta(hours=i)
        h_end   = h_start + timedelta(hours=1)
        hs = (h_start - epoch).total_seconds() / 86400
        he = (h_end   - epoch).total_seconds() / 86400
        hour_kg = float(w[am_f.between(hs, he, inclusive="left")].sum())
        buckets.append({
            "label":   h_start.strftime("%H"),
            "kg":      round(hour_kg, 1),
            "active":  h_start <= now_dt,
            "current": h_start <= now_dt < h_end,
        })
    return buckets


def _current_shift_window(now_dt: datetime) -> tuple[datetime, datetime, str]:
    if now_dt.hour >= 20:
        start = now_dt.replace(hour=20, minute=0, second=0, microsecond=0)
        end = (now_dt + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        name = "Éjszakai műszak"
    elif now_dt.hour < 8:
        start = (now_dt - timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
        end = now_dt.replace(hour=8, minute=0, second=0, microsecond=0)
        name = "Éjszakai műszak"
    else:
        start = now_dt.replace(hour=8, minute=0, second=0, microsecond=0)
        end = now_dt.replace(hour=20, minute=0, second=0, microsecond=0)
        name = "Nappali műszak"
    return start, end, name


def _shift_hour_buckets_dt(times, weights, start_dt, now_dt) -> list[dict]:
    buckets = []
    for i in range(12):
        h_start = start_dt + timedelta(hours=i)
        h_end = h_start + timedelta(hours=1)
        mask = (times >= h_start) & (times < h_end)
        hour_kg = float(weights[mask].sum())
        buckets.append({
            "label": h_start.strftime("%H"),
            "kg": round(hour_kg, 1),
            "active": h_start <= now_dt,
            "current": h_start <= now_dt < h_end,
        })
    return buckets


# KPI time-of-day bands. Inbound uses ECOMM AN, outbound uses ECOMM AU /
# departure_raw, and nested 1-hour buckets feed the KPI page interaction.
# Napszak (time-of-day) bands for the KPI page bottom section. Each band is a
# 4-hour window of the current calendar day; inbound is bucketed by felvétel idő
_DAY_TIME_BANDS = [(0, 4), (4, 8), (8, 12), (12, 16), (16, 20), (20, 24)]

# Operational shifts: day = 08:00–20:00, night = 20:00–08:00. A 4-hour band is
# tagged by the shift its start hour belongs to, so the KPI page can group the
# six bands into the two shifts.
def _band_shift(h0: int) -> str:
    return "day" if 8 <= h0 < 20 else "night"


def _metric_series_like(reference, values=None) -> pd.Series:
    if values is not None:
        return pd.to_numeric(values, errors="coerce").fillna(0.0)
    return pd.Series(0.0, index=getattr(reference, "index", None), dtype="float64")


def _time_band_buckets(serials, weights, now_dt: datetime, epoch: datetime,
                       boxes=None, parcels=None, day_offset: int = 0) -> list[dict]:
    """Build 4-hour KPI time bands plus nested 1-hour buckets from Excel serials.

    ``day_offset`` shifts the day window back (0 = today, 1 = yesterday). The
    active/current flags compare against ``now_dt``, so a past day is automatically
    fully ``active`` with no ``current`` band."""
    serials = pd.to_numeric(serials, errors="coerce")
    weights = _metric_series_like(serials, weights)
    boxes = _metric_series_like(serials, boxes)
    parcels = _metric_series_like(serials, parcels)
    day0 = now_dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=day_offset)

    def _bucket(h0: int, h1: int) -> dict:
        start_dt = day0 + timedelta(hours=h0)
        end_dt = day0 + timedelta(hours=h1)
        s0 = (start_dt - epoch).total_seconds() / 86400
        s1 = (end_dt - epoch).total_seconds() / 86400
        mask = serials.between(s0, s1, inclusive="left")

        return {
            "key": f"{h0:02d}-{h1:02d}",
            "label": f"{h0:02d}-{h1 % 24:02d}",
            "start": h0,
            "end": h1,
            "shift": _band_shift(h0),
            "kg": round(_sum_masked(weights, mask), 1),
            "count": int(mask.sum()),
            "colli": round(_sum_masked(boxes, mask), 1),
            "parcel": round(_sum_masked(parcels, mask), 1),
            "active": start_dt <= now_dt,
            "current": start_dt <= now_dt < end_dt,
        }

    bands = []
    for h0, h1 in _DAY_TIME_BANDS:
        band = _bucket(h0, h1)
        band["hours"] = [_bucket(h, h + 1) for h in range(h0, h1)]
        bands.append(band)
    return bands


def _time_band_inbound(am_f, w, now_dt: datetime, epoch: datetime,
                       boxes=None, parcels=None, day_offset: int = 0) -> list[dict]:
    return _time_band_buckets(am_f, w, now_dt, epoch, boxes, parcels, day_offset)


def _time_band_outbound(issued, weights, now_dt: datetime,
                        boxes=None, parcels=None) -> list[dict]:
    """Outbound kg/db per time band, by kiadás idő (issued_time), for today."""
    day0 = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if boxes is None:
        boxes = pd.Series(0.0, index=getattr(weights, "index", None), dtype="float64")
    if parcels is None:
        parcels = pd.Series(0.0, index=getattr(weights, "index", None), dtype="float64")
    bands = []
    for h0, h1 in _DAY_TIME_BANDS:
        b_start = day0 + timedelta(hours=h0)
        b_end = day0 + timedelta(hours=h1)
        mask = issued.notna() & (issued >= b_start) & (issued < b_end)
        bands.append({
            "key": f"{h0:02d}-{h1:02d}",
            "label": f"{h0:02d}–{h1 % 24:02d}",
            "start": h0,
            "end": h1,
            "kg": round(_sum_masked(weights, mask), 1),
            "count": int(mask.sum()),
            "colli": round(_sum_masked(boxes, mask), 1),
            "parcel": round(_sum_masked(parcels, mask), 1),
            "active": b_start <= now_dt,
            "current": b_start <= now_dt < b_end,
        })
    return bands


def _time_band_outbound_ecomm(departure_f, weights, now_dt: datetime, epoch: datetime,
                              boxes=None, parcels=None, day_offset: int = 0) -> list[dict]:
    return _time_band_buckets(departure_f, weights, now_dt, epoch, boxes, parcels, day_offset)


def _time_band_day_offsets(now_dt: datetime, epoch: datetime, *serial_groups) -> list[int]:
    """Return every available past/today date offset represented by source serials."""
    today = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).date()
    offsets = {0}
    for serials in serial_groups:
        nums = pd.to_numeric(serials, errors="coerce") if serials is not None else pd.Series(dtype="float64")
        for value in nums.dropna().tolist():
            try:
                event_day = (epoch + timedelta(days=float(value))).date()
            except Exception:
                continue
            offset = (today - event_day).days
            if offset >= 0:
                offsets.add(offset)
    return sorted(offsets, reverse=True)


def _merge_day_bands(inbound: list[dict] | None, outbound: list[dict] | None,
                     day_dt: datetime, is_today: bool) -> dict:
    """Merge one day's inbound + outbound bands into the KPI page day payload."""
    inbound = inbound or []
    outbound = outbound or []
    in_by_key = {b.get("key"): b for b in inbound}
    out_by_key = {b.get("key"): b for b in outbound}
    bands = []
    for h0, h1 in _DAY_TIME_BANDS:
        key = f"{h0:02d}-{h1:02d}"
        b = in_by_key.get(key, {})
        ob = out_by_key.get(key, {})
        in_hours = {h.get("key"): h for h in (b.get("hours") or [])}
        out_hours = {h.get("key"): h for h in (ob.get("hours") or [])}
        hours = []
        for h in range(h0, h1):
            hkey = f"{h:02d}-{h + 1:02d}"
            ih = in_hours.get(hkey, {})
            oh = out_hours.get(hkey, {})
            hours.append({
                "key": hkey,
                "label": ih.get("label") or oh.get("label") or hkey,
                "start": h,
                "end": h + 1,
                "shift": _band_shift(h),
                "active": bool(ih.get("active") or oh.get("active")),
                "current": bool(ih.get("current") or oh.get("current")),
                "inbound_kg": float(ih.get("kg") or 0.0),
                "inbound_count": int(ih.get("count") or 0),
                "inbound_colli": float(ih.get("colli") or 0.0),
                "inbound_parcel": float(ih.get("parcel") or 0.0),
                "outbound_kg": float(oh.get("kg") or 0.0),
                "outbound_count": int(oh.get("count") or 0),
                "outbound_colli": float(oh.get("colli") or 0.0),
                "outbound_parcel": float(oh.get("parcel") or 0.0),
            })
        bands.append({
            "key": b.get("key") or ob.get("key") or key,
            "label": b.get("label") or ob.get("label") or key,
            "start": b.get("start") or ob.get("start") or h0,
            "end": b.get("end") or ob.get("end") or h1,
            "shift": _band_shift(h0),
            "active": bool(b.get("active") or ob.get("active")),
            "current": bool(b.get("current") or ob.get("current")),
            "inbound_kg": float(b.get("kg") or 0.0),
            "inbound_count": int(b.get("count") or 0),
            "inbound_colli": float(b.get("colli") or 0.0),
            "inbound_parcel": float(b.get("parcel") or 0.0),
            "outbound_kg": float(ob.get("kg") or 0.0),
            "outbound_count": int(ob.get("count") or 0),
            "outbound_colli": float(ob.get("colli") or 0.0),
            "outbound_parcel": float(ob.get("parcel") or 0.0),
            "hours": hours,
        })
    return {
        "date": day_dt.strftime("%Y-%m-%d"),
        "weekday": day_dt.strftime("%A"),
        "is_today": bool(is_today),
        "bands": bands,
    }


def _merge_time_bands(inbound: list[dict] | None, outbound: list[dict] | None,
                      inbound_prev: list[dict] | None = None,
                      outbound_prev: list[dict] | None = None,
                      now_dt: datetime | None = None) -> dict:
    """KPI time-bands payload: today plus yesterday, navigable on the page.

    ``days`` is ordered oldest→newest; the top-level ``date``/``bands`` mirror the
    newest (today) day for back-compat with older readers."""
    # Back-compat: older tests/callers passed now_dt as the third positional arg.
    if isinstance(inbound_prev, datetime) and outbound_prev is None and now_dt is None:
        now_dt = inbound_prev
        inbound_prev = None
    elif isinstance(outbound_prev, datetime) and now_dt is None:
        now_dt = outbound_prev
        outbound_prev = None
    now_dt = now_dt or datetime.now()

    if isinstance(inbound, dict) or isinstance(outbound, dict):
        in_days = inbound if isinstance(inbound, dict) else {0: inbound}
        out_days = outbound if isinstance(outbound, dict) else {0: outbound}

        def _offsets(day_map: dict) -> set[int]:
            out = set()
            for key in day_map.keys():
                try:
                    out.add(int(key))
                except Exception:
                    continue
            return out

        offsets = sorted(_offsets(in_days) | _offsets(out_days) | {0}, reverse=True)
        days = [
            _merge_day_bands(
                in_days.get(offset) or in_days.get(str(offset)),
                out_days.get(offset) or out_days.get(str(offset)),
                now_dt - timedelta(days=offset),
                offset == 0,
            )
            for offset in offsets
        ]
        today = next((day for day in days if day.get("is_today")), days[-1])
        return {
            "date": today["date"],
            "weekday": today["weekday"],
            "bands": today["bands"],
            "days": days,
            "today_index": max(0, len(days) - 1),
        }

    today = _merge_day_bands(inbound, outbound, now_dt, True)
    days = []
    if inbound_prev is not None or outbound_prev is not None:
        days.append(_merge_day_bands(inbound_prev, outbound_prev,
                                     now_dt - timedelta(days=1), False))
    days.append(today)
    return {
        "date": today["date"],
        "weekday": today["weekday"],
        "bands": today["bands"],
        "days": days,
        "today_index": len(days) - 1,
    }


_TRACKING_CAPACITY_KG = 500000.0
_TRACKING_CURRENT_STATUS_KEYS = {"felveve", "kiadhato", "vamkez alatt", "mitortenik", "mi tortenik"}
_TRACKING_TRANSFERRED_STATUS_KEYS = _TRACKING_CURRENT_STATUS_KEYS | {"kiadva"}
_TRACKING_TO_TRANSFER_STATUS_KEYS = {"ertesito", "megerkezett", "szemles"}
_TRACKING_ISSUED_STATUS_KEYS = {"kiadva"}


def _numeric_series(raw: pd.DataFrame, column: str, fill: float = 0.0) -> pd.Series:
    if column not in raw.columns:
        return pd.Series(fill, index=raw.index, dtype="float64")
    return pd.to_numeric(raw[column], errors="coerce").fillna(fill)


def _excel_serial_day(dt: datetime, epoch: datetime) -> int:
    return int((dt - epoch).total_seconds() // 86400)


def _safe_ratio(numerator: float, denominator: float) -> float:
    try:
        denominator = float(denominator or 0)
        if denominator == 0:
            return 0.0
        return float(numerator or 0) / denominator
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _sum_masked(weights: pd.Series, mask) -> float:
    try:
        return float(weights[mask].sum())
    except Exception:
        return 0.0


def _compute_tracking_kpis(raw: pd.DataFrame, now_dt: datetime, epoch: datetime) -> dict:
    """Replicate the KPI tracking workbook formulas from the E_COMM raw rows."""
    empty_payload = {
        "current_warehouse_saturation": {"kg": 0.0, "ratio": 0.0, "capacity": _TRACKING_CAPACITY_KG},
        "weekly_avg": {
            "inbound": {"colli": 0.0, "kg": 0.0},
            "outbound": {"colli": 0.0, "kg": 0.0},
        },
        "to_transfer_12h_kg": 0.0,
        "to_release_12h_kg": 0.0,
        "ata_more_than_12h_not_transferred_kg": 0.0,
        "kg_per_parcel": 0.0,
        "kg_per_colli": 0.0,
        "kg_per_awb": 0.0,
        "active_warehouse_items": 0,
        "expected_arrivals": [],
    }
    if raw is None or raw.empty:
        return empty_payload

    weight_raw = pd.to_numeric(raw["weight"], errors="coerce") if "weight" in raw.columns else pd.Series(float("nan"), index=raw.index)
    weights = weight_raw.fillna(0.0)
    boxes = _numeric_series(raw, "boxes")
    parcels = _numeric_series(raw, "parcel_count")
    status_key = raw["status_n"].apply(_status_key) if "status_n" in raw.columns else pd.Series("", index=raw.index)
    ata_serial = pd.to_numeric(raw["ak_raw"], errors="coerce") if "ak_raw" in raw.columns else pd.Series(float("nan"), index=raw.index)
    expected_serial = (
        pd.to_numeric(raw["expected_arrival_raw"], errors="coerce")
        if "expected_arrival_raw" in raw.columns
        else pd.Series(float("nan"), index=raw.index)
    )

    now_serial = (now_dt - epoch).total_seconds() / 86400
    current_mask = status_key.isin(_TRACKING_CURRENT_STATUS_KEYS)
    current_kg = _sum_masked(weights, current_mask)
    if "awb" in raw.columns:
        awb_base_12 = raw["awb"].apply(lambda value: str(value).strip()[:12] if not _empty(value) else "")
        active_warehouse_items = int(awb_base_12[current_mask & (awb_base_12 != "")].nunique())
    else:
        awb_base_12 = pd.Series("", index=raw.index)
        active_warehouse_items = 0

    to_transfer_mask = (
        (status_key == "ertesito")
        | ((status_key == "megerkezett") & ata_serial.notna() & (ata_serial < now_serial))
        | (status_key == "szemles")
    )
    to_release_mask = status_key.isin({"kiadhato", "mitortenik", "mi tortenik"})
    ata_more_than_12h_mask = (
        status_key.isin(_TRACKING_TO_TRANSFER_STATUS_KEYS)
        & ata_serial.notna()
        & (ata_serial < now_serial - 0.5)
    )

    kg_per_parcel = _safe_ratio(float(weights.sum()), float(parcels.sum()))
    issued_mask = status_key.isin(_TRACKING_ISSUED_STATUS_KEYS)
    kg_per_colli = _safe_ratio(float(weights[issued_mask].sum()), float(boxes[issued_mask].sum()))
    awb_weight_mask = (awb_base_12 != "") & weight_raw.notna()
    kg_per_awb = _safe_ratio(float(weights[awb_weight_mask].sum()), int(awb_base_12[awb_weight_mask].nunique()))

    today_day = _excel_serial_day(now_dt, epoch)
    expected_day = expected_serial.apply(lambda value: int(value) if pd.notna(value) else None)
    arrivals = []
    for offset in (3, 2, 1, 0, -1, -2, -3, -4, -5):
        day = today_day + offset
        day_mask = expected_day == day
        remaining_mask = day_mask & (status_key == "elindult")
        arrivals.append({
            "date": (epoch + timedelta(days=day)).date().isoformat(),
            "expected_arrivals_kg": _sum_masked(weights, day_mask),
            "already_arrived_kg": _sum_masked(weights, day_mask & (status_key != "elindult")),
            "already_transferred_kg": _sum_masked(weights, day_mask & status_key.isin(_TRACKING_TRANSFERRED_STATUS_KEYS)),
            "remaining_expected_arrivals_kg": _sum_masked(weights, remaining_mask),
            "remaining_expected_parcel_kg": float(parcels[remaining_mask].sum()) * kg_per_parcel,
            "remaining_expected_colli_kg": float(boxes[remaining_mask].sum()) * kg_per_colli,
        })

    # ── 1-week daily averages ───────────────────────────────────────────────
    # Inbound is bucketed on AL (Áttár), outbound on AS (departure_raw =
    # kiszállítás/átadás). Window = the last 7 days up to now; the colli/kg sums are
    # divided by 7 to get an average-per-day figure.
    week_ago_serial = now_serial - 7.0
    am_serial = (
        pd.to_numeric(raw["am_raw"], errors="coerce")
        if "am_raw" in raw.columns else pd.Series(float("nan"), index=raw.index)
    )
    dep_serial = (
        pd.to_numeric(raw["departure_raw"], errors="coerce")
        if "departure_raw" in raw.columns else pd.Series(float("nan"), index=raw.index)
    )
    in_week_in = am_serial.notna() & (am_serial >= week_ago_serial) & (am_serial <= now_serial)
    in_week_out = dep_serial.notna() & (dep_serial >= week_ago_serial) & (dep_serial <= now_serial)
    weekly_avg = {
        "inbound": {
            "colli": _sum_masked(boxes, in_week_in) / 7.0,
            "kg": _sum_masked(weights, in_week_in) / 7.0,
        },
        "outbound": {
            "colli": _sum_masked(boxes, in_week_out) / 7.0,
            "kg": _sum_masked(weights, in_week_out) / 7.0,
        },
    }

    return {
        "current_warehouse_saturation": {
            "kg": current_kg,
            "ratio": _safe_ratio(current_kg, _TRACKING_CAPACITY_KG),
            "capacity": _TRACKING_CAPACITY_KG,
        },
        "weekly_avg": weekly_avg,
        "to_transfer_12h_kg": _sum_masked(weights, to_transfer_mask),
        "to_release_12h_kg": _sum_masked(weights, to_release_mask),
        "ata_more_than_12h_not_transferred_kg": _sum_masked(weights, ata_more_than_12h_mask),
        "kg_per_parcel": kg_per_parcel,
        "kg_per_colli": kg_per_colli,
        "kg_per_awb": kg_per_awb,
        "active_warehouse_items": active_warehouse_items,
        "expected_arrivals": arrivals,
    }


def _compute_outbound_shift_kpi(pallets: pd.DataFrame) -> dict:
    try:
        pallets = _drop_ecomm_glabs(pallets)
        now_dt = datetime.now()
        s_start, s_end, shift_name = _current_shift_window(now_dt)
        prev_start = s_start - timedelta(hours=12)
        prev_name = "Nappali műszak" if shift_name == "Éjszakai műszak" else "Éjszakai műszak"

        if pallets is None or pallets.empty or "issued_time" not in pallets.columns:
            empty_current = _shift_hour_buckets_dt(pd.Series(dtype="datetime64[ns]"), pd.Series(dtype=float), s_start, now_dt)
            empty_prev = _shift_hour_buckets_dt(pd.Series(dtype="datetime64[ns]"), pd.Series(dtype=float), prev_start, now_dt)
            return {
                "outbound_shift_kg": 0.0,
                "outbound_shift_count": 0,
                "outbound_shift_colli": 0.0,
                "outbound_shift_pallets": 0.0,
                "outbound_shift_parcels": 0.0,
                "outbound_shift_trucks": 0,
                "outbound_shift_loadings": 0,
                "outbound_shift_loading_time_min": 0.0,
                "outbound_shift_loading_time_count": 0,
                "outbound_shift_name": shift_name,
                "outbound_shift_range": f"{s_start.strftime('%H:%M')}–{s_end.strftime('%H:%M')}",
                "outbound_shift_hourly": empty_current,
                "outbound_shift_prev_kg": 0.0,
                "outbound_shift_prev_count": 0,
                "outbound_shift_prev_colli": 0.0,
                "outbound_shift_prev_pallets": 0.0,
                "outbound_shift_prev_parcels": 0.0,
                "outbound_shift_prev_trucks": 0,
                "outbound_shift_prev_loadings": 0,
                "outbound_shift_prev_name": prev_name,
                "outbound_shift_prev_range": f"{prev_start.strftime('%H:%M')}–{s_start.strftime('%H:%M')}",
                "outbound_shift_prev_hourly": empty_prev,
                "outbound_shift_prev_same_kg": 0.0,
                "outbound_shift_prev_same_trucks": 0,
                "outbound_shift_prev_same_loadings": 0,
                "outbound_shift_prev_same_hourly": _shift_hour_buckets_dt(
                    pd.Series(dtype="datetime64[ns]"), pd.Series(dtype=float),
                    s_start - timedelta(hours=24), now_dt),
                "outbound_shift_avg_kg_per_h": 0.0,
                "outbound_shift_avg_loadings_per_h": 0.0,
                "outbound_shift_avg_shift_count": 0,
            }

        issued = pd.to_datetime(pallets["issued_time"], errors="coerce")
        weight_source = pallets["weight"] if "weight" in pallets.columns else pd.Series(0.0, index=pallets.index)
        weights = pd.to_numeric(weight_source, errors="coerce").fillna(0.0)
        box_source = pallets["boxes"] if "boxes" in pallets.columns else pd.Series(0.0, index=pallets.index)
        boxes = pd.to_numeric(box_source, errors="coerce").fillna(0.0)
        pallet_source = pallets["pallets_issued"] if "pallets_issued" in pallets.columns else pd.Series(0.0, index=pallets.index)
        pallets_out = pd.to_numeric(pallet_source, errors="coerce").fillna(0.0)
        parcel_source = pallets["parcel_count"] if "parcel_count" in pallets.columns else pd.Series(0.0, index=pallets.index)
        parcels = pd.to_numeric(parcel_source, errors="coerce").fillna(0.0)
        plate_source = pallets["plate"] if "plate" in pallets.columns else pd.Series("", index=pallets.index)
        plates = plate_source.astype(str).str.strip()
        glabs_source = pallets["glabs_id"] if "glabs_id" in pallets.columns else pd.Series("", index=pallets.index)
        glabs_ids = glabs_source.astype(str).str.strip()
        checkin_source = pallets["driver_checkin"] if "driver_checkin" in pallets.columns else pd.Series(pd.NaT, index=pallets.index)
        checkins = pd.to_datetime(checkin_source, errors="coerce")
        rest_source = pallets["rest_until"] if "rest_until" in pallets.columns else pd.Series(pd.NaT, index=pallets.index)
        rests = pd.to_datetime(rest_source, errors="coerce")
        current_mask = issued.notna() & (issued >= s_start) & (issued < s_end)
        prev_mask = issued.notna() & (issued >= prev_start) & (issued < s_start)
        # Azonos típusú előző műszak (24 órával korábbi ablak) — a TV riport
        # tempó-referenciája és szellemvonala ehhez mér: a közvetlenül megelőző,
        # ellentétes napszakú műszak strukturálisan más terhelésű, torz viszonyítás.
        same_start = s_start - timedelta(hours=24)
        same_end = s_end - timedelta(hours=24)
        prev_same_mask = issued.notna() & (issued >= same_start) & (issued < same_end)

        def _trucks(mask) -> int:
            # Egy "rakodás" = egy rendszám; a műszakban kiadott egyedi rendszámok száma.
            sel = plates[mask]
            sel = sel[(sel != "") & (sel.str.lower() != "none")]
            return int(sel.nunique())

        def _loadings(mask) -> int:
            # Egy rakodas/ora = egy egyedi GLABS ID, ha van ervenyes kiadva datum a muszakban.
            sel = glabs_ids[mask]
            sel = sel[(sel != "") & (sel.str.lower() != "none") & (sel.str.lower() != "nan")]
            return int(sel.nunique())

        def _shift_start_for(ts):
            if pd.isna(ts):
                return None
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            if ts.hour >= 20:
                return ts.replace(hour=20, minute=0, second=0, microsecond=0)
            if ts.hour < 8:
                return (ts - timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
            return ts.replace(hour=8, minute=0, second=0, microsecond=0)

        def _big_average_rates() -> tuple[float, float, int]:
            # Stable TV gauge midpoint: average tempo of completed historical 12h shifts.
            # Current shift rows are excluded, so the reference does not drift during polling.
            hist_mask = issued.notna() & (issued < s_start)
            if not bool(hist_mask.any()):
                return 0.0, 0.0, 0

            hist = pd.DataFrame({
                "issued": issued[hist_mask],
                "weight": weights[hist_mask],
                "glabs": glabs_ids[hist_mask],
            })
            hist["shift_start"] = hist["issued"].map(_shift_start_for)
            hist = hist[hist["shift_start"].notna()]
            if hist.empty:
                return 0.0, 0.0, 0

            kg_rates: list[float] = []
            loading_rates: list[float] = []
            for _, group in hist.groupby("shift_start", sort=True):
                kg_rates.append(float(group["weight"].sum()) / 12.0)
                group_glabs = group["glabs"].astype(str).str.strip()
                group_glabs = group_glabs[
                    (group_glabs != "")
                    & (group_glabs.str.lower() != "none")
                    & (group_glabs.str.lower() != "nan")
                ]
                loading_rates.append(float(group_glabs.nunique()) / 12.0)

            if not kg_rates:
                return 0.0, 0.0, 0
            shift_count = len(kg_rates)
            return (
                round(sum(kg_rates) / shift_count, 2),
                round(sum(loading_rates) / shift_count, 2),
                shift_count,
            )

        def _loading_time_stats(mask) -> tuple[float, int]:
            if not bool(mask.any()):
                return 0.0, 0
            load_keys = glabs_ids.copy()
            missing_key = (load_keys == "") | (load_keys.str.lower().isin(["none", "nan"]))
            load_keys = load_keys.mask(missing_key, plates)
            missing_key = (load_keys == "") | (load_keys.str.lower().isin(["none", "nan"]))
            fallback_keys = pd.Series([f"row-{i}" for i in range(len(load_keys))], index=pallets.index)
            load_keys = load_keys.mask(missing_key, fallback_keys)

            frame = pd.DataFrame({
                "key": load_keys,
                "issued": issued,
                "checkin": checkins,
                "rest": rests,
                "pallets": pallets_out,
            }, index=pallets.index)
            current = frame[mask & frame["issued"].notna()]
            durations: list[float] = []

            for key, current_group in current.groupby("key", sort=False):
                if not key:
                    continue
                group = frame[frame["key"] == key]
                finish = current_group["issued"].max()
                if pd.isna(finish):
                    continue

                item_count = max(1, int(len(current_group)))
                pallet_count = float(current_group["pallets"].sum() or 0.0)
                complexity_min = max(18.0, min(90.0, 12.0 + item_count * 4.0 + pallet_count * 2.2))

                ready_candidates = []
                valid_checkins = group["checkin"].dropna()
                if not valid_checkins.empty:
                    ready_candidates.append(valid_checkins.min())
                valid_rests = group["rest"].dropna()
                if not valid_rests.empty:
                    rest_ready = valid_rests.max()
                    if rest_ready <= finish:
                        ready_candidates.append(rest_ready)

                baseline_start = finish - timedelta(minutes=complexity_min)
                start = baseline_start
                if ready_candidates:
                    driver_ready = max(ready_candidates)
                    if driver_ready <= finish:
                        start = max(driver_ready, baseline_start)

                minutes = (finish - start).total_seconds() / 60.0
                durations.append(max(6.0, min(120.0, minutes)))

            if not durations:
                return 0.0, 0
            durations.sort()
            trimmed = durations[1:-1] if len(durations) >= 5 else durations
            avg_min = sum(trimmed) / len(trimmed)
            return round(avg_min, 1), len(durations)

        loading_time_min, loading_time_count = _loading_time_stats(current_mask)
        avg_kg_per_h, avg_loadings_per_h, avg_shift_count = _big_average_rates()

        return {
            "outbound_shift_kg": float(weights[current_mask].sum()),
            "outbound_shift_count": int(current_mask.sum()),
            "outbound_shift_colli": float(boxes[current_mask].sum()),
            "outbound_shift_pallets": float(pallets_out[current_mask].sum()),
            "outbound_shift_parcels": float(parcels[current_mask].sum()),
            "outbound_shift_trucks": _trucks(current_mask),
            "outbound_shift_loadings": _loadings(current_mask),
            "outbound_shift_loading_time_min": loading_time_min,
            "outbound_shift_loading_time_count": loading_time_count,
            "outbound_shift_name": shift_name,
            "outbound_shift_range": f"{s_start.strftime('%H:%M')}–{s_end.strftime('%H:%M')}",
            "outbound_shift_hourly": _shift_hour_buckets_dt(issued, weights, s_start, now_dt),
            "outbound_shift_prev_kg": float(weights[prev_mask].sum()),
            "outbound_shift_prev_count": int(prev_mask.sum()),
            "outbound_shift_prev_colli": float(boxes[prev_mask].sum()),
            "outbound_shift_prev_pallets": float(pallets_out[prev_mask].sum()),
            "outbound_shift_prev_parcels": float(parcels[prev_mask].sum()),
            "outbound_shift_prev_trucks": _trucks(prev_mask),
            "outbound_shift_prev_loadings": _loadings(prev_mask),
            "outbound_shift_prev_name": prev_name,
            "outbound_shift_prev_range": f"{prev_start.strftime('%H:%M')}–{s_start.strftime('%H:%M')}",
            "outbound_shift_prev_hourly": _shift_hour_buckets_dt(issued, weights, prev_start, now_dt),
            "outbound_shift_prev_same_kg": float(weights[prev_same_mask].sum()),
            "outbound_shift_prev_same_trucks": _trucks(prev_same_mask),
            "outbound_shift_prev_same_loadings": _loadings(prev_same_mask),
            "outbound_shift_prev_same_hourly": _shift_hour_buckets_dt(issued, weights, same_start, now_dt),
            "outbound_shift_avg_kg_per_h": avg_kg_per_h,
            "outbound_shift_avg_loadings_per_h": avg_loadings_per_h,
            "outbound_shift_avg_shift_count": avg_shift_count,
        }
    except Exception as exc:
        log.warning("Outbound KPI compute error: %s", exc)
        return {}


def _compute_shift_rates(kpi: dict) -> dict:
    """Per-hour inbound/outbound throughput for the CURRENT shift, in both pieces and
    kg, using hours elapsed since the shift start. Fed to the warehouse breakdown."""
    h = float(kpi.get("shift_elapsed_h") or 0.0)

    def _rate(value) -> float:
        v = float(value or 0.0)
        return v / h if h > 0 else 0.0

    return {
        "elapsed_h": h,
        "inbound": {
            "count_per_h": _rate(kpi.get("shift_count")),
            "kg_per_h": _rate(kpi.get("shift_kg")),
        },
        "outbound": {
            "count_per_h": _rate(kpi.get("outbound_shift_count")),
            "kg_per_h": _rate(kpi.get("outbound_shift_kg")),
        },
    }


def _compute_trend(raw: pd.DataFrame) -> dict:
    """Daily/weekly kg time-series for the KPI page trend chart.

    Mirrors the Reggeli Riport semantics: the NOA date (AK column / ``noa_raw``)
    drives the bucketing, weight (F) is the metric, and rows are aggregated both
    by LMP (B) and by AWB 3-digit prefix (D). The "Országok" breakdown is derived
    on the client from the LMP labels, so only the ``lmp`` and ``prefix`` series
    are emitted here. Returns ``{day:{lmp,prefix}, week:{lmp,prefix}}`` where each
    dataset is ``{"cols": [...series...], "rows": [{"key": label, "vals": {...}}]}``.
    """
    try:
        noa  = raw["noa_raw"].tolist()
        wts  = pd.to_numeric(raw["weight"], errors="coerce").fillna(0.0).tolist()
        lmps = raw["lmp"].astype(str).tolist()
        awbs = raw["awb"].astype(str).tolist()

        # (mode, dim) -> period key -> series -> kg
        acc = {
            ("day", "lmp"):     defaultdict(lambda: defaultdict(float)),
            ("week", "lmp"):    defaultdict(lambda: defaultdict(float)),
            ("day", "prefix"):  defaultdict(lambda: defaultdict(float)),
            ("week", "prefix"): defaultdict(lambda: defaultdict(float)),
        }
        for d_raw, weight, lmp, awb in zip(noa, wts, lmps, awbs):
            if not weight or weight <= 0:
                continue
            d = _serial_to_dt(d_raw)
            if d is None:
                continue
            day_key = d.strftime("%Y-%m-%d")
            iso = d.isocalendar()
            week_key = f"{iso[0]}-W{iso[1]:02d}"

            lmp_s = lmp.strip()
            if lmp_s and lmp_s.lower() != "nan":
                acc[("day", "lmp")][day_key][lmp_s] += weight
                acc[("week", "lmp")][week_key][lmp_s] += weight

            pfx = awb.strip()[:3]
            if pfx.isdigit():
                label = _AIRLINE_PREFIX.get(pfx, pfx)
                acc[("day", "prefix")][day_key][label] += weight
                acc[("week", "prefix")][week_key][label] += weight

        def _build(mode: str, dim: str, cap: int) -> dict:
            store = acc[(mode, dim)]
            keys = sorted(store.keys())[-cap:]
            totals: dict[str, float] = defaultdict(float)
            for k in keys:
                for series, kg in store[k].items():
                    totals[series] += kg
            # Series ordered by total volume desc -> stable palette + sensible defaults.
            cols = [s for s, _ in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])) if totals[s] > 0]
            rows = []
            for k in keys:
                if mode == "day":
                    label = f"{k[5:7]}.{k[8:10]}"      # MM.DD
                    d0 = d1 = k                          # ISO yyyy-mm-dd
                else:
                    yy, ww = k.split("-W")
                    label = "W" + ww                     # Www
                    d0 = date.fromisocalendar(int(yy), int(ww), 1).isoformat()  # Monday
                    d1 = date.fromisocalendar(int(yy), int(ww), 7).isoformat()  # Sunday
                vals = {s: int(round(store[k].get(s, 0.0)))
                        for s in cols if store[k].get(s, 0.0) > 0}
                rows.append({"key": label, "d0": d0, "d1": d1, "vals": vals})
            return {"cols": cols, "rows": rows}

        return {
            "day":  {"lmp": _build("day", "lmp", _TREND_DAY_CAP),
                     "prefix": _build("day", "prefix", _TREND_DAY_CAP)},
            "week": {"lmp": _build("week", "lmp", _TREND_WEEK_CAP),
                     "prefix": _build("week", "prefix", _TREND_WEEK_CAP)},
        }
    except Exception as exc:
        log.warning("Trend compute error: %s", exc)
        return {}


# TEMU stage-duration KPI (mirrors the "TEMU KPI - BUD wNN" stacked-column report).
# Each shipment's lead time is split into five consecutive milestone gaps; the chart
# stacks the per-period AVERAGE of each gap. TEMU MD is intentionally excluded.
_TEMU_RE = re.compile(r"^TEMU(?:\b|$)", re.IGNORECASE)
_TEMU_MD_RE = re.compile(r"^TEMU\s+MD(?:\b|$)", re.IGNORECASE)
_TEMU_COUNTRY_RE = re.compile(r"^TEMU\s+([A-Z]{2})\b", re.IGNORECASE)

# (key, start_field, end_field) — the gap is end - start, in hours.
_TEMU_STAGES = [
    ("ata_noa",          "ak_raw",  "noa_raw"),
    ("noa_transfer",     "noa_raw", "am_raw"),
    ("transfer_customs", "am_raw",  "aq_raw"),
    ("customs",          "aq_raw",  "ar_raw"),
    ("outbound",         "ar_raw",  "departure_raw"),
]
_TEMU_STAGE_LABELS = {
    "ata_noa":          "ATA-NOA",
    "noa_transfer":     "NOA-Transfer",
    "transfer_customs": "Transfer-Customs Clearance",
    "customs":          "Customs Clearance",
    "outbound":         "Outbound",
}
_TEMU_DAY_CAP = 31   # one extra so the client still has a full window after dropping today
_TEMU_WEEK_CAP = 13
_TEMU_FIELD_ORDER = ["ak_raw", "noa_raw", "am_raw", "aq_raw", "ar_raw", "departure_raw"]
_TEMU_FIELD_INDEX = {field: idx for idx, field in enumerate(_TEMU_FIELD_ORDER)}
_TEMU_STAGE_BY_FIELDS = {(start, end): key for key, start, end in _TEMU_STAGES}
_TEMU_STAGE_MAX_HOURS = {
    "ata_noa": 48.0,
    "noa_transfer": 48.0,
    "transfer_customs": 72.0,
    "customs": 48.0,
    "outbound": 96.0,
}
_TEMU_STAGE_SOFT_HOURS = {
    "ata_noa": 24.0,
    "noa_transfer": 24.0,
    "transfer_customs": 12.0,
    "customs": 12.0,
    "outbound": 48.0,
}
_TEMU_FUTURE_TOLERANCE_DAYS = 2
_TEMU_STATE_LIMIT = 96
# Conservative date-guard tuning. We only auto-correct *human-typo-shaped* slips and
# otherwise flag the milestone as unreliable — we never relocate a date far enough to
# fabricate a plausible-but-invented duration (see _temu_trusted_candidates).
_TEMU_TRUST_SHIFT_DAYS = 2     # only +/-1..2 day typos are auto-corrected
_TEMU_SWAP_NEAR_DAYS = 3       # a day<->month swap is trusted only if it lands within N days of a sibling milestone
_TEMU_DROP_PENALTY = 3.0       # cost of declaring a milestone unreliable; must exceed a small typo fix but stay
                               # below the cost of bending several other milestones to accommodate garbage
_TEMU_SUSPECT_LIMIT = 5000     # high cap; the UI filters this to the visible chart window
_TEMU_FIELD_LABELS = {
    "ak_raw": "ATA", "noa_raw": "NOA", "am_raw": "Transfer",
    "aq_raw": "Customs start", "ar_raw": "Customs end", "departure_raw": "Departure",
}


def _temu_country(lmp: str) -> str:
    m = _TEMU_COUNTRY_RE.match(str(lmp or "").strip())
    return m.group(1).upper() if m else "Egyéb"


def _temu_bucket_date(dates: dict, suspect_fields: set) -> Optional[datetime]:
    """Reference workbook parity: bucket by NOA date, falling back to ATA only
    when NOA is not usable (same intent as Munka1:dátum = IF(NOA invalid, ATA, NOA)).
    """
    if "noa_raw" not in suspect_fields and dates.get("noa_raw") is not None:
        return dates.get("noa_raw")
    if "ak_raw" not in suspect_fields and dates.get("ak_raw") is not None:
        return dates.get("ak_raw")
    return None


def _same_clock_on_date(source: datetime, year: int, month: int, day: int) -> Optional[datetime]:
    try:
        return source.replace(year=year, month=month, day=day)
    except ValueError:
        return None


def _temu_anchor_dt(raw_dates: dict[str, Optional[datetime]], present: list[str]) -> Optional[datetime]:
    """Reference point (middle present milestone) used to keep day<->month swaps next
    to the row's other milestones instead of letting them jump whole months away."""
    vals = sorted(raw_dates[f] for f in present if raw_dates.get(f) is not None)
    return vals[len(vals) // 2] if vals else None


def _temu_trusted_candidates(base_dt: Optional[datetime], now_dt: datetime,
                             anchor_dt: Optional[datetime]) -> list[tuple[datetime, float, str]]:
    """Trusted corrections for ONE milestone date.

    Only human-typo-shaped edits are offered: a small +/- day slip and an exact
    day<->month swap that lands next to the row's other milestones. We deliberately
    never offer a large relocation, so the chain solver can repair obvious typos but
    can never invent a far-off value just to force the chain coherent. A future value
    is dropped from the candidate set (an out-of-range date gets flagged, not kept)."""
    if base_dt is None:
        return []
    cands: dict[datetime, tuple[float, str]] = {}

    def add(dt: Optional[datetime], pen: float, reason: str) -> None:
        if dt is None:
            return
        current = cands.get(dt)
        if current is None or pen < current[0]:
            cands[dt] = (pen, reason)

    add(base_dt, 0.0, "raw")
    for delta in range(-_TEMU_TRUST_SHIFT_DAYS, _TEMU_TRUST_SHIFT_DAYS + 1):
        if delta != 0:
            add(base_dt + timedelta(days=delta), 0.6 + abs(delta), "day_shift")

    if 1 <= base_dt.day <= 12 and 1 <= base_dt.month <= 12 and base_dt.day != base_dt.month:
        swapped = _same_clock_on_date(base_dt, base_dt.year, base_dt.day, base_dt.month)
        if (swapped is not None and anchor_dt is not None
                and abs((swapped - anchor_dt).total_seconds()) <= _TEMU_SWAP_NEAR_DAYS * 86400):
            add(swapped, 0.5, "day_month_swap")
            for delta in (-1, 1):
                add(swapped + timedelta(days=delta), 1.1 + abs(delta), "day_month_swap_shift")

    future_cutoff = now_dt + timedelta(days=_TEMU_FUTURE_TOLERANCE_DAYS)
    out = [(dt, pen, reason) for dt, (pen, reason) in cands.items() if dt <= future_cutoff]
    out.sort(key=lambda item: (item[1], abs((item[0] - base_dt).total_seconds())))
    return out


def _temu_gap_limits(prev_field: str, next_field: str) -> tuple[float, float]:
    prev_idx = _TEMU_FIELD_INDEX.get(prev_field, -1)
    next_idx = _TEMU_FIELD_INDEX.get(next_field, -1)
    if prev_idx < 0 or next_idx <= prev_idx:
        return 0.0, 0.0
    max_h = 0.0
    soft_h = 0.0
    for idx in range(prev_idx, next_idx):
        stage_key = _TEMU_STAGE_BY_FIELDS.get((_TEMU_FIELD_ORDER[idx], _TEMU_FIELD_ORDER[idx + 1]))
        max_h += _TEMU_STAGE_MAX_HOURS.get(stage_key, 96.0)
        soft_h += _TEMU_STAGE_SOFT_HOURS.get(stage_key, 48.0)
    return max_h, soft_h


def _temu_row_needs_date_guard(raw_dates: dict[str, Optional[datetime]], now_dt: datetime) -> bool:
    future_cutoff = now_dt + timedelta(days=_TEMU_FUTURE_TOLERANCE_DAYS)
    if any(dt is not None and dt > future_cutoff for dt in raw_dates.values()):
        return True
    for key, start_field, end_field in _TEMU_STAGES:
        start_dt = raw_dates.get(start_field)
        end_dt = raw_dates.get(end_field)
        if start_dt is None or end_dt is None:
            continue
        gap_h = (end_dt - start_dt).total_seconds() / 3600.0
        if gap_h < 0 or gap_h > _TEMU_STAGE_MAX_HOURS.get(key, 96.0):
            return True
    return False


def _temu_suspect_fields(raw_dates: dict[str, Optional[datetime]], now_dt: datetime) -> set[str]:
    future_cutoff = now_dt + timedelta(days=_TEMU_FUTURE_TOLERANCE_DAYS)
    old_cutoff = now_dt - timedelta(days=120)
    suspect = {
        field for field, dt in raw_dates.items()
        if dt is not None and (dt > future_cutoff or dt < old_cutoff)
    }
    for key, start_field, end_field in _TEMU_STAGES:
        start_dt = raw_dates.get(start_field)
        end_dt = raw_dates.get(end_field)
        if start_dt is None or end_dt is None:
            continue
        gap_h = (end_dt - start_dt).total_seconds() / 3600.0
        if gap_h < 0 or gap_h > _TEMU_STAGE_MAX_HOURS.get(key, 96.0):
            suspect.add(start_field)
            suspect.add(end_field)
    return suspect


def _temu_fmt_dt(dt: Optional[datetime]) -> str:
    return dt.strftime("%m-%d %H:%M") if dt is not None else ""


def _temu_iso_dt(dt: Optional[datetime]) -> str:
    return dt.isoformat(timespec="minutes") if dt is not None else ""


def _temu_suspect_reason(field: str, raw_dates: dict[str, Optional[datetime]], now_dt: datetime) -> str:
    """Plain-language why a milestone was flagged, for the UI list."""
    dt = raw_dates.get(field)
    if dt is None:
        return "missing"
    if dt > now_dt + timedelta(days=_TEMU_FUTURE_TOLERANCE_DAYS):
        return "future date"
    idx = _TEMU_FIELD_INDEX.get(field, -1)
    prev_dt = next((raw_dates[f] for f in reversed(_TEMU_FIELD_ORDER[:idx]) if raw_dates.get(f) is not None), None)
    next_dt = next((raw_dates[f] for f in _TEMU_FIELD_ORDER[idx + 1:] if raw_dates.get(f) is not None), None)
    if prev_dt is not None and dt < prev_dt:
        return "before previous step"
    if next_dt is not None and dt > next_dt:
        return "after next step"
    return "implausible gap"


def _normalize_temu_stage_dates(raw_dates: dict[str, Optional[datetime]],
                                now_dt: datetime) -> tuple[dict[str, Optional[datetime]], dict]:
    """Normalize one TEMU milestone chain before KPI aggregation.

    Conservative by design. Only human-typo-shaped slips are corrected (small day
    shifts, a near day<->month swap); the original time-of-day is always preserved.
    When a milestone cannot be placed coherently with a trusted edit it is *dropped*
    (returned in ``meta["suspect_fields"]``) rather than relocated — so we never
    fabricate an invented duration just to force the chain coherent. Dropped fields
    are surfaced to the operator as flagged records and their stages are excluded
    from the averages.
    """
    meta = {"guarded": False, "path_found": False, "corrected_fields": 0,
            "suspect_fields": set()}
    if not _temu_row_needs_date_guard(raw_dates, now_dt):
        return dict(raw_dates), meta

    present = [field for field in _TEMU_FIELD_ORDER if raw_dates.get(field) is not None]
    meta["guarded"] = True
    if len(present) < 2:
        # A lone milestone that tripped the guard (e.g. a single future date): nothing
        # to chain against, so just flag it.
        meta["suspect_fields"] = _temu_suspect_fields(raw_dates, now_dt) & set(present)
        return dict(raw_dates), meta

    anchor = _temu_anchor_dt(raw_dates, present)
    candidates_by_field = {
        field: _temu_trusted_candidates(raw_dates[field], now_dt, anchor)
        for field in present
    }

    # DP over the chain. Each milestone is either placed at a trusted candidate
    # (must keep monotonic order + within the cumulative gap budget from the last
    # *kept* milestone) or dropped (flagged). State key = (last_kept_field, last_kept_dt).
    # Value = (score, placed {field: dt}, dropped frozenset).
    start_state = (None, None)
    states: dict[tuple, tuple[float, dict, frozenset]] = {start_state: (0.0, {}, frozenset())}
    for field in present:
        nxt: dict[tuple, tuple[float, dict, frozenset]] = {}

        def _offer(key, value):
            cur = nxt.get(key)
            if cur is None or value[0] < cur[0]:
                nxt[key] = value

        for (lk_field, lk_dt), (score, placed, dropped) in states.items():
            # Option 1 — declare this milestone unreliable and carry on.
            _offer((lk_field, lk_dt), (score + _TEMU_DROP_PENALTY, placed, dropped | {field}))
            # Option 2 — place it at a trusted candidate that fits the chain so far.
            for dt, penalty, _reason in candidates_by_field[field]:
                if lk_dt is not None:
                    max_h, soft_h = _temu_gap_limits(lk_field, field)
                    gap_h = (dt - lk_dt).total_seconds() / 3600.0
                    if gap_h < 0 or gap_h > max_h:
                        continue
                    place_pen = penalty + max(0.0, gap_h - soft_h) * 0.015
                else:
                    place_pen = penalty
                new_placed = dict(placed)
                new_placed[field] = dt
                _offer((field, dt), (score + place_pen, new_placed, dropped))
        states = dict(sorted(nxt.items(), key=lambda kv: kv[1][0])[:_TEMU_STATE_LIMIT])

    _score, placed, dropped = min(states.values(), key=lambda item: item[0])
    normalized = dict(raw_dates)          # dropped/absent fields keep their raw value
    normalized.update(placed)
    corrected = 0
    for field in present:
        before, after = raw_dates.get(field), normalized.get(field)
        if field not in dropped and before is not None and after is not None \
                and abs((after - before).total_seconds()) >= 60:
            corrected += 1
    meta["path_found"] = True
    meta["corrected_fields"] = corrected
    meta["suspect_fields"] = set(dropped)
    return normalized, meta


def _compute_temu_stage_kpi(raw: pd.DataFrame, now_dt: datetime, epoch: datetime) -> dict:
    """Per-day / per-week average milestone-gap durations for TEMU (excl. TEMU MD).

    Bucketed on the reference report date: NOA day (noa_raw), with ATA fallback
    only when NOA is unusable. For each period and country we keep, per stage,
    the running sum + count of valid gaps so the client can render the stacked
    average bars and switch country pre-filter without a server roundtrip."""
    empty = {
        "stages": [{"key": k, "label": _TEMU_STAGE_LABELS[k]} for k, _, _ in _TEMU_STAGES],
        "countries": [], "lmps": [], "day": {"ALL": []}, "week": {"ALL": []},
        "quality": {
            "total_rows": 0, "guarded_rows": 0, "corrected_rows": 0,
            "corrected_fields": 0, "suspect_rows": 0, "blocked_gaps": 0,
            "blocked_by_stage": {k: 0 for k, _, _ in _TEMU_STAGES},
        },
        "suspects": {"total": 0, "shown": 0, "field_total": 0, "items": []},
    }
    if raw is None or raw.empty or "lmp" not in raw.columns:
        return empty
    try:
        # Materialise LMP to plain python strings: the pyxlsb/arrow-backed column
        # leaks float NaN into .apply even after astype(str), which crashes the regex.
        lmp = pd.Series(
            ["" if _empty(v) else str(v) for v in raw["lmp"].tolist()],
            index=raw.index,
        )
        is_temu = lmp.apply(lambda v: bool(_TEMU_RE.search(v)) and not bool(_TEMU_MD_RE.search(v)))
        sub = raw[is_temu]
        if sub.empty:
            return empty

        # KEMÉNY szabály (user 2026-06-23): a TEMU KPI CSAK a már kiadott tételeket
        # számolja — azaz amelyeknek az E_COMM AS oszlopában (departure_raw / Tétel
        # indulás) van valós dátum. A még úton / be nem fejezett tételek torzítanák a
        # lead-time átlagokat, ezért teljesen kimaradnak (a Flagged records lista is
        # csak kiadott tételekből épül, hogy konzisztens legyen). A "kiadott" jelet a
        # nyers cella adja, nem a date-guard; a departure szakasz megbízhatóságát a
        # downstream guard külön kezeli.
        if "departure_raw" in sub.columns:
            departed = pd.to_numeric(sub["departure_raw"], errors="coerce")
            sub = sub[departed.notna() & (departed > 0)]
        else:
            sub = sub.iloc[0:0]
        if sub.empty:
            return empty

        serials = {
            field: pd.to_numeric(sub[field], errors="coerce") if field in sub.columns
            else pd.Series(float("nan"), index=sub.index)
            for field in _TEMU_FIELD_ORDER
        }
        awb_series = sub["awb"] if "awb" in sub.columns else pd.Series("", index=sub.index)
        quality = {
            "total_rows": int(len(sub)),
            "guarded_rows": 0,
            "corrected_rows": 0,
            "corrected_fields": 0,
            "suspect_rows": 0,
            "blocked_gaps": 0,
            "blocked_by_stage": {k: 0 for k, _, _ in _TEMU_STAGES},
        }
        suspects_raw: list[dict] = []

        def _slot(store: dict, key: str) -> dict:
            if key not in store:
                store[key] = {
                    "key": key, "count": 0,
                    "_days": set(),
                    **{st: [0.0, 0] for st, _, _ in _TEMU_STAGES},
                }
            return store[key]

        country_volume: dict[str, int] = defaultdict(int)
        lmp_volume: dict[str, int] = defaultdict(int)
        # mode -> scope("ALL" / country code / full LMP string) -> period_key -> slot
        buckets: dict = {"day": {"ALL": {}}, "week": {"ALL": {}}}

        # "Today" markers for the chart (day = today's date, week = current ISO week).
        today_day_key = now_dt.strftime("%Y-%m-%d")
        today_iso = now_dt.isocalendar()
        today_week_key = f"{today_iso[0]}-W{today_iso[1]:02d}"

        future_cutoff = now_dt + timedelta(days=_TEMU_FUTURE_TOLERANCE_DAYS)
        for idx in sub.index:
            raw_dates = {}
            for field in _TEMU_FIELD_ORDER:
                value = serials[field].get(idx)
                raw_dates[field] = None if value is None or pd.isna(value) else _serial_to_dt(value)
            dates, guard_meta = _normalize_temu_stage_dates(raw_dates, now_dt)
            suspect_fields = guard_meta.get("suspect_fields") or set()
            if guard_meta.get("guarded"):
                quality["guarded_rows"] += 1
            corrected_fields = int(guard_meta.get("corrected_fields") or 0)
            if corrected_fields:
                quality["corrected_rows"] += 1
                quality["corrected_fields"] += corrected_fields

            country = _temu_country(lmp.get(idx))
            lmp_str = str(lmp.get(idx) or "").strip()

            # Collect every unreliable record up front — even rows that never make it
            # onto the chart — so nothing TEMU is silently lost; the operator gets a list.
            if suspect_fields:
                quality["suspect_rows"] += 1
                fields_info = [
                    {"key": f, "label": _TEMU_FIELD_LABELS.get(f, f),
                     "raw": _temu_fmt_dt(raw_dates.get(f)),
                     "iso": _temu_iso_dt(raw_dates.get(f)),
                     "reason": _temu_suspect_reason(f, raw_dates, now_dt)}
                    for f in _TEMU_FIELD_ORDER if f in suspect_fields
                ]
                milestones_info = [
                    {"key": f, "label": _TEMU_FIELD_LABELS.get(f, f),
                     "raw": _temu_fmt_dt(raw_dates.get(f)),
                     "iso": _temu_iso_dt(raw_dates.get(f)),
                     "suspect": f in suspect_fields,
                     "reason": _temu_suspect_reason(f, raw_dates, now_dt) if f in suspect_fields else ""}
                    for f in _TEMU_FIELD_ORDER
                ]
                anchor_day = dates.get("ak_raw") if "ak_raw" not in suspect_fields else None
                anchor_day = anchor_day or _temu_anchor_dt(raw_dates, [
                    f for f in _TEMU_FIELD_ORDER if raw_dates.get(f) is not None and f not in suspect_fields])
                anchor_day = anchor_day or _temu_anchor_dt(raw_dates,
                    [f for f in _TEMU_FIELD_ORDER if raw_dates.get(f) is not None])
                suspects_raw.append({
                    "awb": ("" if _empty(awb_series.get(idx)) else str(awb_series.get(idx)).strip()),
                    "lmp": lmp_str, "country": country,
                    "day": anchor_day.strftime("%Y-%m-%d") if anchor_day else "",
                    "_sort": anchor_day.timestamp() if anchor_day else 0.0,
                    "fields": fields_info,
                    "milestones": milestones_info,
                })

            d = _temu_bucket_date(dates, suspect_fields)
            if d is None:
                continue
            if d > future_cutoff:
                continue
            iso = d.isocalendar()
            day_key = d.strftime("%Y-%m-%d")
            week_key = f"{iso[0]}-W{iso[1]:02d}"

            stage_gaps = []
            for st, s_field, e_field in _TEMU_STAGES:
                start_dt = dates.get(s_field)
                end_dt = dates.get(e_field)
                if s_field in suspect_fields or e_field in suspect_fields:
                    # An unreliable endpoint: block the stage instead of inventing a gap.
                    quality["blocked_gaps"] += 1
                    quality["blocked_by_stage"][st] += 1
                    stage_gaps.append((st, None))
                    continue
                if start_dt is None or end_dt is None:
                    stage_gaps.append((st, None))
                    continue
                if start_dt > future_cutoff or end_dt > future_cutoff:
                    quality["blocked_gaps"] += 1
                    quality["blocked_by_stage"][st] += 1
                    stage_gaps.append((st, None))
                    continue
                gap_h = (end_dt - start_dt).total_seconds() / 3600.0
                if 0 <= gap_h <= _TEMU_STAGE_MAX_HOURS.get(st, 96.0):
                    stage_gaps.append((st, gap_h))
                else:
                    quality["blocked_gaps"] += 1
                    quality["blocked_by_stage"][st] += 1
                    stage_gaps.append((st, None))

            if not any(gap is not None for _st, gap in stage_gaps):
                continue

            country_volume[country] += 1
            if lmp_str:
                lmp_volume[lmp_str] += 1

            row_scopes = ("ALL", country, lmp_str) if lmp_str else ("ALL", country)
            for mode, pkey in (("day", day_key), ("week", week_key)):
                for scope in row_scopes:
                    store = buckets[mode].setdefault(scope, {})
                    slot = _slot(store, pkey)
                    slot["count"] += 1
                    slot["_days"].add(day_key)
                    for st, gap in stage_gaps:
                        if gap is not None:
                            slot[st][0] += gap
                            slot[st][1] += 1

        countries = [c for c, _ in sorted(country_volume.items(),
                                          key=lambda kv: (-kv[1], kv[0])) if c != "Egyéb"]
        if "Egyéb" in country_volume:
            countries.append("Egyéb")
        # Top LMP strings by volume (the LMP pre-filter row under the country chips).
        lmps = [l for l, _ in sorted(lmp_volume.items(), key=lambda kv: (-kv[1], kv[0]))][:12]

        def _finalize(mode: str, scope: str, cap: int) -> list[dict]:
            today_key = today_day_key if mode == "day" else today_week_key
            store = buckets[mode].get(scope, {})
            keys = sorted(store.keys())[-cap:]
            out = []
            for k in keys:
                slot = store[k]
                segments = {}
                seg_sum = {}
                seg_cnt = {}
                total = 0.0
                for st, _, _ in _TEMU_STAGES:
                    s_sum, s_cnt = slot[st]
                    avg = (s_sum / s_cnt) if s_cnt else 0.0
                    segments[st] = round(avg, 4)
                    # Raw sum + count per stage so the client can aggregate any
                    # selected range exactly (avg-of-avgs would be wrong).
                    seg_sum[st] = round(s_sum, 4)
                    seg_cnt[st] = int(s_cnt)
                    total += avg
                if mode == "day":
                    label = f"{k[5:7]}.{k[8:10]}"
                    d0 = d1 = k
                else:
                    yy, ww = k.split("-W")
                    label = "W" + ww
                    d0 = date.fromisocalendar(int(yy), int(ww), 1).isoformat()
                    d1 = date.fromisocalendar(int(yy), int(ww), 7).isoformat()
                day_count = len(slot.get("_days") or ())
                expected_days = 1 if mode == "day" else 7
                out.append({
                    "key": label, "period_key": k, "d0": d0, "d1": d1,
                    "count": int(slot["count"]),
                    "day_count": int(day_count),
                    "expected_days": int(expected_days),
                    "complete": bool(day_count >= expected_days),
                    "segments": segments, "seg_sum": seg_sum, "seg_cnt": seg_cnt,
                    "total": round(total, 4),
                    "is_today": k == today_key,
                })
            return out

        # Flagged records: newest first, capped for the UI (the count stays exact).
        suspects_raw.sort(key=lambda r: r["_sort"], reverse=True)
        suspect_field_total = sum(len(r["fields"]) for r in suspects_raw)
        suspect_items = []
        for r in suspects_raw[:_TEMU_SUSPECT_LIMIT]:
            r.pop("_sort", None)
            suspect_items.append(r)

        scopes = ["ALL"] + countries + lmps
        return {
            "stages": [{"key": k, "label": _TEMU_STAGE_LABELS[k]} for k, _, _ in _TEMU_STAGES],
            "countries": countries,
            "lmps": lmps,
            "quality": quality,
            "suspects": {
                "total": len(suspects_raw), "shown": len(suspect_items),
                "field_total": suspect_field_total, "items": suspect_items,
            },
            "day": {sc: _finalize("day", sc, _TEMU_DAY_CAP) for sc in scopes},
            "week": {sc: _finalize("week", sc, _TEMU_WEEK_CAP) for sc in scopes},
        }
    except Exception as exc:
        log.warning("TEMU stage KPI compute error: %s", exc)
        return empty


def _ecomm_at_outbound_shift_kpi(dep_f, w, boxes, parcels, s_start, s_end, now_dt, epoch) -> dict:
    """Outbound shift QUANTITY from the E_COMM 'Tétel indulás' (AS / departure_raw)
    column instead of BUD-Pallets KIADVA.

    Rationale: BUD-Pallets KIADVA is backfilled late during the live shift (the
    warehouse fills it after the fact), so the TV outbound weight read too low.
    E_COMM AS is the authoritative, real-time record of what has left, so the TV
    report drives kg/colli/parcels/count/hourly (and the kg gauge midpoint) off it.
    Loadings, trucks and pallets stay on BUD-Pallets — E_COMM has no such fields.

    Defensive by design: any error here returns zeros rather than propagating, so a
    stray departure serial can never blank the whole (inbound+outbound) KPI dict."""
    empty = {
        "outbound_at_shift_kg": 0.0, "outbound_at_shift_colli": 0.0,
        "outbound_at_shift_parcels": 0.0, "outbound_at_shift_count": 0,
        "outbound_at_shift_hourly": _shift_hour_buckets(
            pd.Series(dtype=float), pd.Series(dtype=float), s_start, now_dt, epoch),
        "outbound_at_shift_prev_same_kg": 0.0,
        "outbound_at_shift_avg_kg_per_h": 0.0, "outbound_at_shift_avg_shift_count": 0,
    }
    try:
        s_start_s = (s_start - epoch).total_seconds() / 86400
        s_end_s   = (s_end   - epoch).total_seconds() / 86400
        cur = dep_f.notna() & (dep_f >= s_start_s) & (dep_f < s_end_s)

        # Same-type previous shift (24h back, same 12h window) — TV prev/ghost reference.
        same_start = s_start - timedelta(hours=24)
        same_end   = s_end   - timedelta(hours=24)
        ss_s = (same_start - epoch).total_seconds() / 86400
        se_s = (same_end   - epoch).total_seconds() / 86400
        prev_same = dep_f.notna() & (dep_f >= ss_s) & (dep_f < se_s)

        # Stable gauge midpoint: mean kg/h across completed historical AU-shifts. Current
        # shift rows are excluded so the reference does not drift during polling. Empty
        # shifts carry no rows and are naturally left out (matching the BUD-Pallets ref).
        # A ~2-year floor keeps every real row (the file holds ~2 months) while dropping
        # garbage far-past serials that could overflow the timedelta conversion.
        avg_kg_per_h = 0.0
        avg_shift_count = 0
        hist_mask = dep_f.notna() & (dep_f >= s_start_s - 800) & (dep_f < s_start_s)
        if bool(hist_mask.any()):
            hist_dt = epoch + pd.to_timedelta(dep_f[hist_mask], unit="D")
            # Bucket each departure into its 12h shift start (08:00 / 20:00). Use
            # normalize() (clears sub-day incl. NANOSECONDS — the Excel serial→datetime
            # conversion leaves a ns residue that .replace(microsecond=0) would NOT clear,
            # which otherwise explodes the group count and corrupts the average).
            day  = hist_dt.dt.normalize()
            hour = hist_dt.dt.hour
            shift_start = (day + pd.Timedelta(hours=8))
            shift_start = shift_start.mask(hour < 8,  day - pd.Timedelta(hours=4))   # prev day 20:00
            shift_start = shift_start.mask(hour >= 20, day + pd.Timedelta(hours=20))
            rates = pd.DataFrame({"ss": shift_start, "w": w[hist_mask]}).groupby("ss")["w"].sum() / 12.0
            if len(rates):
                avg_shift_count = int(len(rates))
                avg_kg_per_h = round(float(rates.mean()), 2)

        return {
            "outbound_at_shift_kg":              float(w[cur].sum()),
            "outbound_at_shift_colli":           float(boxes[cur].sum()),
            "outbound_at_shift_parcels":         float(parcels[cur].sum()),
            "outbound_at_shift_count":           int(cur.sum()),
            "outbound_at_shift_hourly":          _shift_hour_buckets(dep_f, w, s_start, now_dt, epoch),
            "outbound_at_shift_prev_same_kg":    float(w[prev_same].sum()),
            "outbound_at_shift_avg_kg_per_h":    avg_kg_per_h,
            "outbound_at_shift_avg_shift_count": avg_shift_count,
        }
    except Exception as exc:
        log.warning("E_COMM AS outbound shift KPI error: %s", exc)
        return empty


def _compute_kpi(raw: pd.DataFrame) -> dict:
    """Compute header KPI values from the full (pre-active-filter) E_COMM rows."""
    try:
        w      = pd.to_numeric(raw["weight"], errors="coerce").fillna(0.0)
        boxes  = _numeric_series(raw, "boxes")
        parcels = _numeric_series(raw, "parcel_count")
        status = raw["status_n"].astype(str).str.strip()

        now_dt = datetime.now()
        epoch  = datetime(1899, 12, 30)

        # ── Műszak KPI ──────────────────────────────────────────────────────
        s_start, s_end, shift_name = _current_shift_window(now_dt)

        s_start_s = (s_start - epoch).total_seconds() / 86400
        s_end_s   = (s_end   - epoch).total_seconds() / 86400
        am_f      = pd.to_numeric(raw["am_raw"], errors="coerce")
        dep_f     = pd.to_numeric(raw["departure_raw"], errors="coerce") if "departure_raw" in raw.columns \
                    else pd.Series(float("nan"), index=raw.index)
        in_shift  = am_f.between(s_start_s, s_end_s, inclusive="left")
        shift_kg  = float(w[in_shift].sum())
        shift_count = int(in_shift.sum())
        # Hours elapsed inside the running shift (≥3 min floor → no divide-by-zero at
        # the very start of a shift); drives the inbound/outbound per-hour rates.
        shift_elapsed_h = max(0.05, (now_dt - s_start).total_seconds() / 3600.0)

        # Fixed 12-hour hourly breakdown for the current shift (08–20 / 20–08) and
        # the immediately preceding shift, so the chart can step one shift back.
        # Each bucket sums the weight whose felvétel idő (AN) falls in that hour;
        # hours that have not started yet are flagged inactive (rendered faded).
        shift_hourly = _shift_hour_buckets(am_f, w, s_start, now_dt, epoch)
        prev_start   = s_start - timedelta(hours=12)
        prev_hourly  = _shift_hour_buckets(am_f, w, prev_start, now_dt, epoch)
        prev_name    = "Nappali műszak" if shift_name == "Éjszakai műszak" else "Éjszakai műszak"
        prev_range   = f"{prev_start.strftime('%H:%M')}–{s_start.strftime('%H:%M')}"
        prev_start_s = (prev_start - epoch).total_seconds() / 86400
        prev_shift_kg = float(w[am_f.between(prev_start_s, s_start_s, inclusive="left")].sum())
        # Outbound QUANTITY for the TV report comes from E_COMM AS (departure), not
        # BUD-Pallets KIADVA. Emitted as separate outbound_at_shift_* keys so the KPI
        # page / dashboard (shared outbound_shift_* keys) stay unchanged.
        at_out = _ecomm_at_outbound_shift_kpi(dep_f, w, boxes, parcels, s_start, s_end, now_dt, epoch)
        tracking = _compute_tracking_kpis(raw, now_dt, epoch)

        # ── Beérkezhető KPI ─────────────────────────────────────────────────
        # Rows with status Felvéve / Értesítő / Megérkezett
        # whose AJ (ATA) date is not later than now + 4 hours.
        # Extra exclusion for Megérkezett: skip if carrier_k == "AS Cargo"
        # AND AJ (ATA) date is after today 14:00.
        cutoff_serial    = ((now_dt + timedelta(hours=4)) - epoch).total_seconds() / 86400
        today_1400       = now_dt.replace(hour=14, minute=0, second=0, microsecond=0)
        today_1400_serial = (today_1400 - epoch).total_seconds() / 86400

        ak_f     = pd.to_numeric(raw["ak_raw"], errors="coerce")
        carrier  = raw["carrier_k"].astype(str).str.strip() if "carrier_k" in raw.columns \
                   else pd.Series([""] * len(raw), index=raw.index)

        # Base mask: AJ (ATA) present and within cutoff
        mask_ak = ak_f.notna() & (ak_f <= cutoff_serial)

        # ULD vs PLT split: a row is ULD when the V column (uld_raw) is non-empty,
        # PLT otherwise — same canonical rule as the inbound cargo_type.
        if "uld_raw" in raw.columns:
            is_uld = raw["uld_raw"].apply(lambda v: not _empty(v))
        else:
            is_uld = pd.Series(False, index=raw.index)

        bec_kg:  dict[str, float] = {}
        bec_uld: dict[str, float] = {}
        bec_plt: dict[str, float] = {}
        bec_count: dict[str, int] = {}
        bec_prefixes: dict[str, list] = {}
        awb_col = raw["awb"] if "awb" in raw.columns else pd.Series([""] * len(raw), index=raw.index)
        for st in ("Felvéve", "Értesítő", "Megérkezett", "Szemlés"):
            mask_st = status == st
            if st == "Megérkezett":
                # Exclude AS Cargo rows where AL > today 14:00
                exclude = (carrier == "AS Cargo") & ak_f.notna() & (ak_f > today_1400_serial)
                mask = mask_st & mask_ak & ~exclude
            else:
                mask = mask_st & mask_ak
            bec_kg[st]  = float(w[mask].sum())
            bec_uld[st] = float(w[mask & is_uld].sum())
            bec_plt[st] = float(w[mask & ~is_uld].sum())
            bec_count[st] = int(mask.sum())
            # Prefix breakdown (AWB first 3 digits) for this status — for the
            # Ctrl+click "prefix inspector" on the inbound Beérkezhető card.
            pref_map: dict[str, dict] = {}
            for awb_v, wt, uld_v in zip(awb_col[mask].tolist(), w[mask].tolist(), is_uld[mask].tolist()):
                digits = "".join(c for c in str(awb_v or "") if c.isdigit())
                prefix = digits[:3] if len(digits) >= 3 else (digits or "—")
                rec = pref_map.setdefault(prefix, {"prefix": prefix, "count": 0, "kg": 0.0,
                                                   "uld": 0, "plt": 0, "uld_kg": 0.0, "plt_kg": 0.0})
                kg = float(wt) if wt == wt else 0.0
                rec["count"] += 1
                rec["kg"] += kg
                if uld_v:
                    rec["uld"] += 1
                    rec["uld_kg"] += kg
                else:
                    rec["plt"] += 1
                    rec["plt_kg"] += kg
            bec_prefixes[st] = sorted(pref_map.values(), key=lambda r: (-r["kg"], r["prefix"]))

        band_offsets = _time_band_day_offsets(now_dt, epoch, am_f, dep_f)

        return {
            "shift_kg":          shift_kg,
            "shift_count":       shift_count,
            "shift_elapsed_h":   shift_elapsed_h,
            "shift_name":        shift_name,
            "shift_range":       f"{s_start.strftime('%H:%M')}–{s_end.strftime('%H:%M')}",
            "shift_hourly":      shift_hourly,
            "shift_prev_name":   prev_name,
            "shift_prev_range":  prev_range,
            "shift_prev_kg":     prev_shift_kg,
            "shift_prev_hourly": prev_hourly,
            "time_bands_inbound": _time_band_inbound(am_f, w, now_dt, epoch, boxes, parcels),
            "time_bands_outbound": _time_band_outbound_ecomm(dep_f, w, now_dt, epoch, boxes, parcels),
            "time_bands_inbound_prev": _time_band_inbound(am_f, w, now_dt, epoch, boxes, parcels, day_offset=1),
            "time_bands_outbound_prev": _time_band_outbound_ecomm(dep_f, w, now_dt, epoch, boxes, parcels, day_offset=1),
            "time_bands_inbound_days": {
                offset: _time_band_inbound(am_f, w, now_dt, epoch, boxes, parcels, day_offset=offset)
                for offset in band_offsets
            },
            "time_bands_outbound_days": {
                offset: _time_band_outbound_ecomm(dep_f, w, now_dt, epoch, boxes, parcels, day_offset=offset)
                for offset in band_offsets
            },
            **at_out,
            "tracking":          tracking,
            "trend":             _compute_trend(raw),
            "temu_stages":       _compute_temu_stage_kpi(raw, now_dt, epoch),
            "bec_total":         sum(bec_kg.values()),
            "bec_felveve":       bec_kg["Felvéve"],
            "bec_ertesito":      bec_kg["Értesítő"],
            "bec_megerkezett":   bec_kg["Megérkezett"],
            "bec_szemles":       bec_kg["Szemlés"],
            "bec_felveve_count":     bec_count["Felvéve"],
            "bec_ertesito_count":    bec_count["Értesítő"],
            "bec_megerkezett_count": bec_count["Megérkezett"],
            "bec_szemles_count":     bec_count["Szemlés"],
            "bec_felveve_uld":     bec_uld["Felvéve"],
            "bec_felveve_plt":     bec_plt["Felvéve"],
            "bec_ertesito_uld":    bec_uld["Értesítő"],
            "bec_ertesito_plt":    bec_plt["Értesítő"],
            "bec_megerkezett_uld": bec_uld["Megérkezett"],
            "bec_megerkezett_plt": bec_plt["Megérkezett"],
            "bec_szemles_uld":     bec_uld["Szemlés"],
            "bec_szemles_plt":     bec_plt["Szemlés"],
            "bec_prefixes": {
                "felveve":     bec_prefixes["Felvéve"],
                "ertesito":    bec_prefixes["Értesítő"],
                "megerkezett": bec_prefixes["Megérkezett"],
                "szemles":     bec_prefixes["Szemlés"],
            },
        }
    except Exception as exc:
        log.warning("KPI compute error: %s", exc)
        return {}


def _notify_progress(
    callback: Optional[Callable[[int, str, str], None]],
    percent: int,
    stage: str,
    detail: str = "",
) -> None:
    """Best-effort progress reporting; UI feedback must never break a read."""
    if callback is None:
        return
    try:
        callback(max(0, min(100, int(percent))), stage, detail)
    except Exception:
        log.debug("Progress callback failed", exc_info=True)


def _read_ecomm_excel_raw(
    progress_callback: Optional[Callable[[int, str, str], None]] = None,
) -> tuple[pd.DataFrame, dict]:
    """Read the Excel E_COMM source into the shared 21-column raw contract."""
    meta: dict = {"ecomm_source_type": "excel"}
    tmp_path: str | None = None
    try:
        source_age = _source_age_minutes(ECOMM_FILE)
        meta["ecomm_source_age_minutes"] = source_age
        meta["ecomm_source_mtime"] = datetime.fromtimestamp(os.path.getmtime(ECOMM_FILE)).isoformat() if source_age is not None else None
        if source_age is None:
            meta["ecomm_source_missing"] = True
            return pd.DataFrame(), meta
        if source_age > ECOMM_STALE_AFTER_MINUTES:
            meta["ecomm_source_stale"] = True
            log.warning(
                "E_COMM source is stale: %.1f minutes old (limit=%d)",
                source_age,
                ECOMM_STALE_AFTER_MINUTES,
            )
            return pd.DataFrame(), meta

        _notify_progress(progress_callback, 12, "E_COMM előkészítése", "Biztonságos munkamásolat készül")
        tmp_path = _temp_copy(ECOMM_FILE)
        _notify_progress(progress_callback, 16, "E_COMM szerkezetének ellenőrzése", "Oszlopok és fejléc felismerése")
        column_index, header_row = _resolve_ecomm_column_map(tmp_path)
        ordered_columns = sorted(column_index.items(), key=lambda item: item[1])

        # pandas.read_excel re-walks and materialises the whole XLSB sheet before
        # applying usecols. On the live E_COMM workbook that took 2-3 minutes.
        # Read the 21 required cells directly instead; the source contract and
        # row-25000 safety cap stay identical, while fully empty selected rows do
        # not waste DataFrame memory.
        rows: list[list] = []
        max_data_rows = _ecomm_data_nrows(header_row)
        with open_workbook(tmp_path) as wb:
            with wb.get_sheet("E-comm") as sheet:
                for row in sheet.rows():
                    excel_row = (row[0].r + 1) if row else 0
                    if excel_row <= header_row:
                        continue
                    if excel_row > _ECOMM_MAX_EXCEL_ROW:
                        break
                    values = [
                        row[idx].v if idx < len(row) else None
                        for _name, idx in ordered_columns
                    ]
                    if any(not _empty(value) for value in values):
                        rows.append(values)
                    scanned = excel_row - header_row
                    if scanned == 1 or scanned % 500 == 0:
                        fraction = min(1.0, scanned / max(1, max_data_rows))
                        percent = 18 + round(fraction * 42)
                        _notify_progress(
                            progress_callback,
                            percent,
                            "E_COMM adatok beolvasása",
                            f"{min(excel_row, _ECOMM_MAX_EXCEL_ROW):,} / {_ECOMM_MAX_EXCEL_ROW:,} sor".replace(",", " "),
                        )
        raw = pd.DataFrame(rows, columns=[name for name, _idx in ordered_columns])
        _notify_progress(
            progress_callback,
            60,
            "E_COMM adatok beolvasva",
            f"{len(raw):,} hasznos sor".replace(",", " "),
        )
        shifted = {
            name: (_ECOMM_COLUMN_INDEX[name], idx)
            for name, idx in column_index.items()
            if idx != _ECOMM_COLUMN_INDEX[name]
        }
        if shifted:
            log.info("ECOMM header-driven mapping resolved from row %d: %s", header_row, shifted)
        return raw, meta
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _read_ecomm_raw(
    progress_callback: Optional[Callable[[int, str, str], None]] = None,
) -> tuple[pd.DataFrame, dict]:
    """Select Excel, Oracle or shadow mode without changing downstream logic."""
    mode = oracle_ecomm.get_source_mode()
    if mode == "oracle":
        raw, meta = oracle_ecomm.refresh_raw()
        meta["ecomm_source_mode"] = mode
        return raw, meta

    raw, meta = _read_ecomm_excel_raw(progress_callback=progress_callback)
    meta["ecomm_source_mode"] = mode
    if mode == "shadow" and not raw.empty:
        try:
            oracle_raw, oracle_meta = oracle_ecomm.refresh_raw()
            excel_keys = set(zip(raw.get("awb", pd.Series(dtype=str)).astype(str).str.strip(), raw.get("lmp", pd.Series(dtype=str)).astype(str).str.strip().str.casefold()))
            oracle_keys = set(zip(oracle_raw.get("awb", pd.Series(dtype=str)).astype(str).str.strip(), oracle_raw.get("lmp", pd.Series(dtype=str)).astype(str).str.strip().str.casefold()))
            meta.update({
                "oracle_shadow_ok": True,
                "oracle_shadow_rows": len(oracle_raw),
                "oracle_shadow_overlap_keys": len(excel_keys & oracle_keys),
                **{f"shadow_{key}": value for key, value in oracle_meta.items()},
            })
            log.info(
                "Oracle shadow refresh OK: rows=%d overlap_keys=%d",
                len(oracle_raw),
                len(excel_keys & oracle_keys),
            )
        except Exception as exc:
            meta["oracle_shadow_ok"] = False
            meta["oracle_shadow_error"] = str(exc)
            log.warning("Oracle shadow refresh failed; Excel remains active: %s", exc)
    return raw, meta


def _read_ecomm(
    progress_callback: Optional[Callable[[int, str, str], None]] = None,
) -> tuple[pd.DataFrame, set[str], dict, list[dict]]:
    """
    Read E_COMM through the targeted 21-column pyxlsb path.
    Active inbound items: AL (Áttár) filled + N status is Felvéve +
    AP (Vámkez.k.) empty. Also returns all AWBs with AQ (Vámkez.v.) filled
    for the GLABS shippable count,
    header KPI aggregates computed before the active filter, and open ULD records.
    """
    ar_awbs:  set[str] = set()
    kpi_data: dict     = {}
    try:
        raw, source_meta = _read_ecomm_raw(progress_callback=progress_callback)
        kpi_data.update(source_meta)
        if raw.empty and (source_meta.get("ecomm_source_missing") or source_meta.get("ecomm_source_stale")):
            return pd.DataFrame(), ar_awbs, kpi_data, []

        # Keep AWB-less helper rows for KPI Tracking parity.
        raw["awb"] = raw["awb"].apply(lambda value: str(value).strip() if not _empty(value) else "")

        # KPI computed BEFORE the AL (Áttár) filter — Értesítő rows typically
        # have no transfer date.
        source_meta = dict(kpi_data)
        _notify_progress(progress_callback, 64, "Műszakmutatók számítása", "KPI-k és státuszok összesítése")
        kpi_data = _compute_kpi(raw)
        kpi_data.update(source_meta)

        # Drop rows where awb is missing for the active item list and AWB lookups.
        raw = raw[raw["awb"] != ""].copy()

        # AWB → E_COMM "N" column status (státusz) lookup, built from ALL rows
        # BEFORE the AN/active filter so a loading item's status can be resolved by
        # its AWB (E_COMM "D" column) regardless of whether it is an active item.
        awb_status_map: dict[str, str] = {}
        awb_parcel_map: dict[str, float] = {}
        parcel_values = _numeric_series(raw, "parcel_count")
        for _a, _s, _p in zip(raw["awb"].tolist(), raw["status_n"].tolist(), parcel_values.tolist()):
            _key = str(_a).strip()
            if not _key:
                continue
            awb_status_map.setdefault(_key, str(_s).strip() if not _empty(_s) else "")
            parcel_value = float(_p or 0.0)
            awb_parcel_map[_key] = max(float(awb_parcel_map.get(_key, 0.0) or 0.0), parcel_value)
            _base_key = _base_awb(_key)
            if _base_key:
                awb_parcel_map[_base_key] = max(float(awb_parcel_map.get(_base_key, 0.0) or 0.0), parcel_value)
        kpi_data["awb_status_map"] = awb_status_map
        kpi_data["awb_parcel_map"] = awb_parcel_map

        # Build the global AQ (customs-finish) lookup from ALL rows with AQ filled —
        # BEFORE any AL/status/AP filter so cleared items without Áttár are included.
        for awb in raw.loc[raw["ar_raw"].notna(), "awb"]:
            ar_awbs.add(awb)
            parts = awb.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) <= 3:
                ar_awbs.add(parts[0])
        log.info("AQ lookup (kiadható AWB-k, szűrés előtt): %d db", len(ar_awbs))

        # Now apply the AL (Áttár) filter for active-item logic
        raw = raw[raw["am_raw"].notna()]

        # Extract open ULD data (ULD azonosító + Áttár filled, Vissza empty)
        uld_data = _extract_uld_data(raw)
        log.info("Nyitott ULD-k: %d db", len(uld_data))

        status_keys = raw["status_n"].apply(_status_key)
        unique_statuses = raw["status_n"].astype(str).str.strip().unique()
        log.info("status_n unique values (after AL filter): %s", list(unique_statuses))

        # Normal active inbound: Felvéve + AP (Vámkez.k.) empty
        normal_raw = raw[status_keys.isin(_ACTIVE_STATUS_KEYS) & raw["aq_raw"].isna()]
        log.info("Felvéve + AP üres: %d sor", len(normal_raw))

        # Partial active inbound: Részben + AP empty + AH PRE-ALERT in last 24 hours
        epoch = datetime(1899, 12, 30)
        now = datetime.now()
        ai_serials = pd.to_numeric(raw["ai_raw"], errors="coerce")
        now_ts = (now - epoch).total_seconds() / 86400
        cutoff_24h_ts = ((now - timedelta(hours=24)) - epoch).total_seconds() / 86400
        ai_in_window = ai_serials.notna() & (ai_serials >= cutoff_24h_ts) & (ai_serials <= now_ts)
        partial_raw = raw[
            (status_keys == _PARTIAL_STATUS_KEY) & raw["aq_raw"].isna() & ai_in_window
        ]
        log.info("Részben + AH-24h + AP üres: %d sor", len(partial_raw))

        if normal_raw.empty and partial_raw.empty:
            return pd.DataFrame(), ar_awbs, kpi_data, uld_data

        def to_float(s):
            try:
                return float(s)
            except (TypeError, ValueError):
                return None

        cutoff = now - timedelta(hours=24)

        def _build_row(r, is_partial: bool) -> Optional[dict]:
            am_dt = _serial_to_dt(to_float(r["am_raw"]))
            if am_dt is None:
                return None
            if not is_partial and am_dt < cutoff:
                return None
            ar_raw = r["ar_raw"]
            ar_dt = None
            if not _empty(ar_raw):
                ar_f = to_float(ar_raw)
                if ar_f is not None:
                    ar_dt = _serial_to_dt(ar_f)
            if is_partial:
                boxes = int(_to_float(r.get("partial_boxes_raw"), 0.0))
                pw = r.get("partial_weight_raw")
                weight = float(pw) if not _empty(pw) and not isinstance(pw, str) else 0.0
            else:
                weight_raw = r["weight"]
                weight = float(weight_raw) if not _empty(weight_raw) and not isinstance(weight_raw, str) else 0.0
                boxes = int(_to_float(r.get("boxes"), 0.0))
            uld_number = str(r["uld_raw"]).strip() if not _empty(r["uld_raw"]) else ""
            cargo_type = "ULD" if uld_number else "PLT"
            status_value = str(r["status_n"]).strip() if not _empty(r["status_n"]) else ""
            status_ready_value = _is_ready_status(status_value)
            rendszam = str(r["rendszam_raw"]).strip() if "rendszam_raw" in r.index and not _empty(r.get("rendszam_raw")) else ""
            truck_arrival_time = am_dt + timedelta(minutes=30)
            is_en_route = datetime.now() < truck_arrival_time
            return {
                "lmp": str(r["lmp"]).strip() if not _empty(r["lmp"]) else "",
                "awb": str(r["awb"]).strip(),
                "weight": weight,
                "boxes": boxes,
                "cargo_type": cargo_type,
                "uld_number": uld_number,
                "am_time": am_dt,
                "truck_arrival_time": truck_arrival_time,
                "is_en_route": is_en_route,
                "ar_time": ar_dt,
                "status": status_value,
                "ecomm_status_ready": status_ready_value,
                "is_shippable": ar_dt is not None or status_ready_value,
                "is_partial": is_partial,
                "rendszam": rendszam,
            }

        rows = []
        for _, r in normal_raw.iterrows():
            row = _build_row(r, False)
            if row:
                rows.append(row)
        for _, r in partial_raw.iterrows():
            row = _build_row(r, True)
            if row:
                rows.append(row)

        if not rows:
            return pd.DataFrame(), ar_awbs, kpi_data, uld_data

        df = pd.DataFrame(rows)

        # Deduplicate versioned AWBs: if "AWB-1", "AWB-2" etc. exist alongside
        # the base "AWB", keep only the base. A suffix is numeric after the last dash.
        def base_awb(awb: str) -> str:
            parts = awb.rsplit("-", 1)
            # Only treat as version suffix if it's a short number (1-3 digits)
            # e.g. "235-96463382-1" → base "235-96463382"
            # but "157-47004381"   → NOT a suffix (8 digits = part of AWB)
            if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) <= 3:
                return parts[0]
            return awb

        df["base_awb"] = df["awb"].apply(_base_awb)

        # For each base_awb group prefer the un-suffixed row; if none exists keep first
        kept = []
        for _, grp in df.groupby("base_awb", sort=False):
            base_rows = grp[grp["awb"] == grp["base_awb"]]
            kept.append(base_rows.iloc[0] if not base_rows.empty else grp.iloc[0])

        df = pd.DataFrame(kept).drop(columns=["base_awb"])
        return df, ar_awbs, kpi_data, uld_data

    except Exception as exc:
        log.error("E_COMM read error: %s", exc, exc_info=True)
        # Explicit hard-failure signal (distinct from "read OK but no active items")
        # so the scheduler keeps the last good data instead of blanking.
        kpi_data["ecomm_read_error"] = str(exc)
        return pd.DataFrame(), ar_awbs, kpi_data, []


def _is_ecomm_rest_marker(value) -> bool:
    """True if the BUD-Pallets rest/pihenő (M) cell flags the row as E-COM freight,
    in any spelling: 'E-COM', 'ECOMM', 'e-comm', 'e comm', 'e.comm', 'ecommerce'…

    Such GLABS loads are E-commerce and must NOT count toward OUTBOUND anywhere."""
    if value is None:
        return False
    text = str(value).strip().lower()
    if not text:
        return False
    compact = re.sub(r"[\s._\-]+", "", text)  # 'e-com' / 'e comm' / 'e.comm' → 'ecom…'
    return "ecom" in compact


def _drop_ecomm_glabs(pallets: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of the BUD-Pallets frame with every E-COM-flagged GLABS removed.

    If any row of a GLABS id has the E-COM marker in the rest/pihenő (M) column, the
    WHOLE GLABS is dropped (per spec). Explicitly-marked rows without a GLABS id are
    dropped on their own. Used only for OUTBOUND computations — the inbound view keeps
    the unfiltered frame."""
    if pallets is None or pallets.empty or "is_ecomm" not in pallets.columns:
        return pallets
    ecomm_mask = pallets["is_ecomm"].fillna(False).astype(bool)
    if not ecomm_mask.any():
        return pallets
    excluded_glabs = {
        str(g).strip()
        for g in pallets.loc[ecomm_mask, "glabs_id"].dropna()
        if str(g).strip()
    }
    keep = ~ecomm_mask  # always drop the explicitly-marked rows themselves
    if excluded_glabs:
        keep &= ~pallets["glabs_id"].apply(
            lambda g: (not _empty(g)) and str(g).strip() in excluded_glabs
        )
    return pallets[keep].copy()


def _read_pallets() -> pd.DataFrame:
    """
    Read BUD-Pallets Rakodások sheet.
    Columns: A=LMP, B=AWB, E=GLABS_ID, K=driver_checkin, M=rest_until
    Rest is only valid if M explicitly contains rest/pihenő text.
    """
    if oracle_ecomm.get_source_mode() == "oracle":
        try:
            current = oracle_ecomm.current_pallets()
            pallets, meta = current if current is not None else oracle_ecomm.refresh_pallets()
            pallets = pallets.copy()
            if pallets.empty:
                raise oracle_ecomm.OracleSourceError("Az Oracle BUD Pallets adathalmaza ures.")
            rest_flags = pallets.apply(
                lambda row: _parse_rest_text(row.get("rest_raw"), row.get("driver_checkin")),
                axis=1,
            )
            pallets["rest_declared"] = rest_flags.apply(lambda value: bool(value[0]))
            pallets["rest_until"] = rest_flags.apply(lambda value: value[1])
            pallets["is_ecomm"] = pallets["rest_raw"].apply(_is_ecomm_rest_marker)
            pallets = pallets.drop(columns=["rest_raw"], errors="ignore")
            pallets.attrs.update({
                "pallets_source_type": "oracle",
                "oracle_pallet_rows": meta.get("oracle_pallet_rows", len(pallets)),
                "oracle_pallet_locations": meta.get("oracle_pallet_locations", 0),
            })
            return pallets
        except Exception as exc:
            log.error("Oracle BUD Pallets read error: %s", exc, exc_info=True)
            empty = pd.DataFrame()
            empty.attrs["pallets_read_error"] = str(exc)
            empty.attrs["pallets_source_type"] = "oracle"
            return empty

    rows = []
    tmp_path: str | None = None
    try:
        tmp_path = _temp_copy(PALLETS_FILE)
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Data Validation extension is not supported.*",
                category=UserWarning,
                module="openpyxl",
            )
            wb = openpyxl.load_workbook(tmp_path, read_only=True, keep_vba=True, data_only=True)
        ws = wb["Rakodások"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row:
                continue
            awb = row[1] if len(row) > 1 else None
            if _empty(awb):
                continue
            glabs = row[4] if len(row) > 4 else None
            checkin = row[10] if len(row) > 10 else None
            checkin_dt = checkin if _is_dt(checkin) else None
            rest_raw = row[12] if len(row) > 12 else None
            rest_flag, rest_until = _parse_rest_text(rest_raw, checkin_dt)
            ecomm_flag = _is_ecomm_rest_marker(rest_raw)
            kiad_raw = row[7] if len(row) > 7 else None
            kiad_val = str(kiad_raw).strip() if not _empty(kiad_raw) else None
            issued_raw = row[11] if len(row) > 11 else None
            rows.append({
                "bud_lmp":        str(row[0]).strip() if row[0] else "",
                "awb":            str(awb).strip(),
                "plate":          str(row[2]).strip() if len(row) > 2 and not _empty(row[2]) else None,
                "eta_note":       str(row[3]).strip() if len(row) > 3 and not _empty(row[3]) else None,
                "glabs_id":       str(glabs).strip() if not _empty(glabs) else None,
                "boxes":          _to_float(row[5]) if len(row) > 5 else 0.0,
                "weight":         _to_float(row[6]) if len(row) > 6 else 0.0,
                "driver_checkin": checkin_dt,
                "issued_time":    issued_raw if _is_dt(issued_raw) else None,
                "rest_until":     rest_until,
                "rest_declared":  rest_flag,
                "is_ecomm":       ecomm_flag,
                "kiad_status":    kiad_val,
                "pallets_issued": _to_float(row[8]) if len(row) > 8 else 0.0,
                "location":       str(row[9]).strip() if len(row) > 9 and not _empty(row[9]) else None,
                "load_note":      str(row[13]).strip() if len(row) > 13 and not _empty(row[13]) else None,
            })
        wb.close()
    except Exception as exc:
        log.error("BUD-Pallets read error: %s", exc, exc_info=True)
        # Flag the failure on the frame so load_all_flow_data can keep the last
        # good outbound view instead of blanking on a transient lock/sync stall.
        empty = pd.DataFrame()
        empty.attrs["pallets_read_error"] = str(exc)
        return empty
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    result = pd.DataFrame(rows)
    result.attrs["pallets_source_type"] = "excel"
    return result


def load_data(
    pallets: pd.DataFrame | None = None,
    progress_callback: Optional[Callable[[int, str, str], None]] = None,
) -> tuple[pd.DataFrame, dict, dict, list[dict]]:
    ecomm, ar_awbs, kpi_data, uld_data = _read_ecomm(progress_callback=progress_callback)
    if pallets is None:
        pallets = _read_pallets()

    # Loading-item status comes from the E_COMM tracking sheet (N column), matched
    # by AWB (D column) — exact first, then base-AWB (suffix stripped).
    awb_status_map = kpi_data.get("awb_status_map", {}) if isinstance(kpi_data, dict) else {}

    def _ecomm_status_for(awb_val) -> str:
        a = str(awb_val or "").strip()
        if not a:
            return ""
        if a in awb_status_map:
            return awb_status_map[a]
        return awb_status_map.get(_base_awb(a), "")

    if ecomm.empty:
        if kpi_data.get("ecomm_source_missing"):
            return pd.DataFrame(), {"error": "Az E_COMM forrásfájl nem található.", "hard_read_failure": True}, kpi_data, uld_data
        if kpi_data.get("ecomm_source_stale"):
            age = float(kpi_data.get("ecomm_source_age_minutes") or 0)
            mtime = kpi_data.get("ecomm_source_mtime") or "ismeretlen"
            return pd.DataFrame(), {
                "error": (
                    "Az E_COMM fájl túl régi, ezért az app nem mutat aktív inbound tételeket. "
                    f"Utolsó mentés: {mtime}, kor: {age:.0f} perc."
                ),
                "hard_read_failure": True,
            }, kpi_data, uld_data
        if kpi_data.get("ecomm_read_error"):
            return pd.DataFrame(), {
                "error": f"Az E_COMM fájl jelenleg nem olvasható: {kpi_data.get('ecomm_read_error')}",
                "hard_read_failure": True,
            }, kpi_data, uld_data
        # No failure flag → E_COMM read fine, there are simply no active items.
        return pd.DataFrame(), {"error": "Nincs aktív tétel, vagy az E_COMM fájl nem olvasható."}, kpi_data, uld_data

    now = datetime.now()
    # GLABS shippable stats use the same ready-status rule as E_COMM.
    glabs_stats: dict = {}
    glabs_driver_state: dict = {}
    if not pallets.empty:
        for glabs_id, grp in pallets.groupby("glabs_id", dropna=True):
            if not glabs_id:
                continue
            issued_mask = grp.apply(
                lambda r: _is_pallet_issued(r.get("kiad_status"), r.get("issued_time")),
                axis=1,
            )
            active_grp = grp[~issued_mask]
            total = len(active_grp)
            ready_mask = (
                active_grp["kiad_status"].apply(_is_ready_status).astype(bool)
                if "kiad_status" in active_grp.columns
                else pd.Series(False, index=active_grp.index, dtype=bool)
            )
            shippable = int(ready_mask.sum())
            loading_items = []
            for item in active_grp.to_dict("records"):
                awb = _outbound_text(item.get("awb"))
                # Status from E_COMM N column by AWB (not BUD-Pallets kiad_status).
                status = _ecomm_status_for(item.get("awb"))
                loading_items.append({
                    "lmp": _outbound_text(item.get("bud_lmp")),
                    "awb": awb,
                    "status": status,
                    "is_ready": _is_ready_status(status),
                })
            glabs_stats[glabs_id] = {
                "total": total,
                "shippable": shippable,
                "ratio": shippable / total if total > 0 else 0.0,
                "loading_items": loading_items,
            }
            checkins = [v for v in grp["driver_checkin"].tolist() if _is_dt(v)]
            rest_values = [v for v in grp["rest_until"].tolist() if _is_dt(v)]
            rest_declared = bool(grp["rest_declared"].fillna(False).any()) if "rest_declared" in grp.columns else False
            glabs_driver_state[glabs_id] = {
                "driver_checkin": min(checkins) if checkins else None,
                "rest_until": max(rest_values) if rest_values else None,
                "rest_declared": rest_declared,
            }
            log.info("  GLABS %s: %d/%d kiadható (kiad_status alapján)", glabs_id, shippable, total)

    # AWB → pallet lookup
    # If the same AWB appears in multiple rows, prefer the one with driver_checkin.
    pallet_by_awb: dict = {}
    if not pallets.empty:
        for rec in pallets.to_dict("records"):
            awb = rec["awb"]
            existing = pallet_by_awb.get(awb)
            if existing is None or (rec["driver_checkin"] is not None and existing["driver_checkin"] is None):
                pallet_by_awb[awb] = rec
                if existing is not None:
                    log.debug("AWB %s: kept row with driver_checkin over row without", awb)

    merged = []
    for rec in ecomm.to_dict("records"):
        awb = rec["awb"]
        p = pallet_by_awb.get(awb)
        if p is None:
            for p_awb, p_rec in pallet_by_awb.items():
                if p_awb.startswith(awb + "-"):
                    p = p_rec
                    break

        in_bud = p is not None
        log.debug("AWB %-20s → in_bud=%-5s  driver_checkin=%s",
                  awb, in_bud, p["driver_checkin"] if in_bud else "–")
        glabs_id = p["glabs_id"] if in_bud else None
        glabs_driver = glabs_driver_state.get(glabs_id, {}) if glabs_id else {}
        driver_checkin = glabs_driver.get("driver_checkin") if in_bud else None
        if driver_checkin is None and in_bud:
            driver_checkin = p["driver_checkin"]
        rest_until = glabs_driver.get("rest_until") if in_bud else None
        if rest_until is None and in_bud:
            rest_until = p["rest_until"]
        bud_lmp = p["bud_lmp"] if in_bud else None

        rest_declared = bool(glabs_driver.get("rest_declared")) if in_bud else False
        if not rest_declared and in_bud:
            rest_declared = bool(p.get("rest_declared"))
        driver_in_rest = rest_declared
        if driver_in_rest and _is_dt(rest_until):
            driver_in_rest = rest_until > now
        waiting_hours = 0.0
        if _is_dt(driver_checkin):
            waiting_hours = max(0.0, (now - driver_checkin).total_seconds() / 3600)

        bud_kiadható = False
        if in_bud and p:
            kiad = p.get("kiad_status")
            bud_kiadható = _is_ready_status(kiad) and not _is_pallet_issued(kiad, p.get("issued_time"))

        gs = glabs_stats.get(glabs_id, {}) if glabs_id else {}
        loading_items = [dict(item) for item in gs.get("loading_items", [])]
        if rec.get("ecomm_status_ready") and not loading_items:
            loading_items = [{
                "lmp": str(rec.get("lmp") or ""),
                "awb": str(rec.get("awb") or ""),
                "status": str(rec.get("status") or ""),
                "is_ready": True,
            }]
        merged.append({
            **rec,
            "in_bud_pallets":  in_bud,
            "bud_lmp":         bud_lmp,
            "bud_plate":       p["plate"] if in_bud else None,
            "glabs_id":        glabs_id,
            "driver_checkin":  driver_checkin,
            "rest_until":      rest_until,
            "driver_in_rest":  driver_in_rest,
            "waiting_hours":   waiting_hours,
            "glabs_total":     gs.get("total", 0),
            "glabs_shippable": gs.get("shippable", 0),
            "glabs_shippable_base": gs.get("shippable", 0),
            "glabs_ratio":     gs.get("ratio", 0.0),
            "glabs_ratio_base": gs.get("ratio", 0.0),
            "glabs_loading_items": [dict(item) for item in loading_items],
            "glabs_loading_items_base": [dict(item) for item in loading_items],
            "glabs_ready_items": [dict(item) for item in loading_items],
            "glabs_ready_items_base": [dict(item) for item in loading_items],
            "bud_is_shippable": bud_kiadható,
            "is_shippable":    rec.get("is_shippable") or bud_kiadható,
        })

    df = pd.DataFrame(merged)

    # Compute shift window for "Betárolt tételek" view
    if now.hour >= 20:
        s_start = now.replace(hour=20, minute=0, second=0, microsecond=0)
    elif now.hour < 8:
        s_start = (now - timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
    else:
        s_start = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now.hour >= 20:
        s_end = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    elif now.hour < 8:
        s_end = now.replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        s_end = now.replace(hour=20, minute=0, second=0, microsecond=0)

    df["am_in_shift"] = df["am_time"].apply(
        lambda t: _is_dt(t) and s_start <= t < s_end
    )

    return df, {}, kpi_data, uld_data


def _shift_window(now: datetime) -> tuple[datetime, datetime]:
    if now.hour >= 20:
        start = now.replace(hour=20, minute=0, second=0, microsecond=0)
        end = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    elif now.hour < 8:
        start = (now - timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
        end = now.replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(hour=8, minute=0, second=0, microsecond=0)
        end = now.replace(hour=20, minute=0, second=0, microsecond=0)
    return start, end


def _is_outbound_ready(status: str | None) -> bool:
    return _is_ready_status(status)


def _is_outbound_issued(status: str | None, issued_time) -> bool:
    return _is_pallet_issued(status, issued_time)


def _latest_dt(values: list) -> Optional[datetime]:
    dts = [v for v in values if _is_dt(v)]
    return max(dts) if dts else None


def _earliest_dt(values: list) -> Optional[datetime]:
    dts = [v for v in values if _is_dt(v)]
    return min(dts) if dts else None


def _sum_num(values: list) -> float:
    total = 0.0
    for value in values:
        try:
            total += float(value or 0)
        except (TypeError, ValueError):
            pass
    return total


def _outbound_text(value) -> str:
    if _empty(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none"} else text


def _sort_text(value) -> str:
    """Case-insensitive, type-safe text key for sorting mixed Excel values."""
    return _outbound_text(value).casefold()


def load_outbound_data(
    pallets: pd.DataFrame | None = None,
    ecomm_status_map: dict[str, str] | None = None,
) -> tuple[list[dict], dict]:
    """Build outbound truck cards from BUD-Pallets/Rakodások only."""
    if pallets is None:
        pallets = _read_pallets()
    pallets = _drop_ecomm_glabs(pallets)
    if pallets.empty:
        return [], {"error": "A BUD-Pallets fájl nem olvasható, vagy nincs adat."}

    now = datetime.now()
    shift_start, _shift_end = _shift_window(now)
    ecomm_status_map = ecomm_status_map or {}

    def _ecomm_status_for(awb_val) -> str:
        awb = str(awb_val or "").strip()
        if not awb:
            return ""
        if awb in ecomm_status_map:
            return str(ecomm_status_map.get(awb) or "")
        return str(ecomm_status_map.get(_base_awb(awb)) or "")

    rows = []
    for rec in pallets.to_dict("records"):
        # Típusbiztos olvasás: a NaN/"nan"/üres rendszám- és GLABS-érték "" lesz,
        # különben a float NaN truthy volta átcsúszna a szűrőkön (→ "nan" kártya).
        plate = _outbound_text(rec.get("plate"))
        glabs_id = _outbound_text(rec.get("glabs_id"))
        awb = _outbound_text(rec.get("awb"))
        status = rec.get("kiad_status")
        issued_time = rec.get("issued_time")
        checkin = rec.get("driver_checkin")

        if not plate and not glabs_id:
            continue
        if status == "HIBA":
            continue

        issued_recent = _is_dt(issued_time) and issued_time >= shift_start
        active = not _is_outbound_issued(status, issued_time)
        checked_recent = _is_dt(checkin) and checkin >= shift_start

        if not active and not issued_recent and not checked_recent:
            continue

        rec = dict(rec)
        rec["_has_plate"] = bool(plate)
        rec["plate"] = plate or "NINCS RENDSZÁM"
        rec["glabs_id"] = glabs_id or "NINCS GLABS"
        rec["awb"] = awb or ""
        rec["is_issued"] = _is_outbound_issued(status, issued_time)
        rec["is_active"] = not rec["is_issued"]
        rec["is_ready"] = _is_outbound_ready(status) and rec["is_active"]
        rows.append(rec)

    if not rows:
        return [], {}

    cards = []
    by_plate: defaultdict = defaultdict(list)
    for rec in rows:
        # Rendszám nélküli (NaN) sorok NE olvadjanak egy közös "NINCS RENDSZÁM"
        # kártyába: GLABS id szerint csoportosítunk, így minden ki nem osztott
        # GLABS a saját kártyáját kapja (egy rakodás = egy GLABS).
        if rec["_has_plate"]:
            group_key = rec["plate"]
        else:
            group_key = ("\x00noplate", rec["glabs_id"])
        by_plate[group_key].append(rec)

    for _group_key, plate_rows in by_plate.items():
        plate = plate_rows[0]["plate"]
        total = len(plate_rows)
        issued = sum(1 for r in plate_rows if r["is_issued"])
        ready = sum(1 for r in plate_rows if r["is_ready"])
        active = total - issued
        checkin = _earliest_dt([r.get("driver_checkin") for r in plate_rows])
        last_issue = _latest_dt([r.get("issued_time") for r in plate_rows])
        rest_until = _latest_dt([r.get("rest_until") for r in plate_rows])
        rest_declared = any(bool(r.get("rest_declared")) for r in plate_rows)
        in_rest = rest_declared and (not _is_dt(rest_until) or rest_until > now)
        lmp_counter = Counter(_outbound_text(r.get("bud_lmp")) for r in plate_rows if _outbound_text(r.get("bud_lmp")))
        lmp_count = len(lmp_counter)
        locations = sorted({_outbound_text(r.get("location")) for r in plate_rows if _outbound_text(r.get("location"))})
        notes = [_outbound_text(r.get("load_note")) for r in plate_rows if _outbound_text(r.get("load_note"))]
        glabs_items = []

        by_glabs: defaultdict[str, list[dict]] = defaultdict(list)
        for rec in plate_rows:
            by_glabs[rec["glabs_id"]].append(rec)

        for glabs_id, glabs_rows in by_glabs.items():
            g_total = len(glabs_rows)
            g_issued = sum(1 for r in glabs_rows if r["is_issued"])
            g_ready = sum(1 for r in glabs_rows if r["is_ready"])
            lmp_items = []
            by_lmp: defaultdict[str, list[dict]] = defaultdict(list)
            for rec in glabs_rows:
                by_lmp[_outbound_text(rec.get("bud_lmp"))].append(rec)
            for lmp, lmp_rows in by_lmp.items():
                status_counts = Counter(_outbound_text(r.get("kiad_status")) for r in lmp_rows if _outbound_text(r.get("kiad_status")))
                lmp_items.append({
                    "lmp": lmp,
                    "count": len(lmp_rows),
                    "issued": sum(1 for r in lmp_rows if r["is_issued"]),
                    "ready": sum(1 for r in lmp_rows if r["is_ready"]),
                    "boxes": _sum_num([r.get("boxes") for r in lmp_rows]),
                    "weight": _sum_num([r.get("weight") for r in lmp_rows]),
                    "locations": sorted({_outbound_text(r.get("location")) for r in lmp_rows if _outbound_text(r.get("location"))}),
                    "statuses": ", ".join(f"{k}: {v}" for k, v in status_counts.most_common(3)),
                })
            lmp_items.sort(key=lambda item: (-int(item.get("count") or 0), _sort_text(item.get("lmp"))))
            lmp_groups = {
                lmp: idx % 8
                for idx, lmp in enumerate(sorted({_outbound_text(r.get("bud_lmp")) for r in glabs_rows}, key=_sort_text))
            }
            awb_items = []
            for item in sorted(glabs_rows, key=lambda r: (_sort_text(r.get("bud_lmp")), _sort_text(r.get("awb")))):
                lmp_text = _outbound_text(item.get("bud_lmp"))
                awb_items.append({
                    "awb": _outbound_text(item.get("awb")),
                    "lmp": lmp_text,
                    "lmp_group": lmp_groups.get(lmp_text, 0),
                    "boxes": _sum_num([item.get("boxes")]),
                    "weight": _sum_num([item.get("weight")]),
                    "status": _outbound_text(item.get("kiad_status")),
                    "ecomm_status": _ecomm_status_for(item.get("awb")),
                    "is_issued": bool(item.get("is_issued")),
                    "is_ready": bool(item.get("is_ready")),
                    "pallets_issued": _to_float(item.get("pallets_issued")) or 0.0,
                    "location": _outbound_text(item.get("location")),
                    "load_note": _outbound_text(item.get("load_note")),
                    "eta_note": _outbound_text(item.get("eta_note")),
                })
            glabs_items.append({
                "glabs_id": glabs_id,
                "total": g_total,
                "issued": g_issued,
                "ready": g_ready,
                "boxes": _sum_num([r.get("boxes") for r in glabs_rows]),
                "weight": _sum_num([r.get("weight") for r in glabs_rows]),
                "lmp_items": lmp_items,
                "awb_items": awb_items,
            })

        glabs_items.sort(key=lambda item: (
            int(item.get("issued") or 0) == int(item.get("total") or 0),
            -int(item.get("ready") or 0),
            _sort_text(item.get("glabs_id")),
        ))

        if in_rest:
            status_label, color_key, sort_bucket = "PIHENŐN", "driver-rest", 3
        elif active == 0:
            status_label, color_key, sort_bucket = "LEZÁRVA", "standard", 4
        elif checkin:
            status_label, color_key, sort_bucket = "SOFŐR HELYSZÍNEN", "driver-active", 0
        elif ready:
            status_label, color_key, sort_bucket = "KIADÁSRA VÁR", "loading-plan", 1
        else:
            status_label, color_key, sort_bucket = "VÁRAKOZIK", "standard", 2

        cards.append({
            "plate": plate,
            "status_label": status_label,
            "priority_color": color_key,
            "sort_key": (
                sort_bucket,
                -ready,
                -active,
                -(checkin.timestamp() if _is_dt(checkin) else 0),
                _sort_text(plate),
            ),
            "total": total,
            "issued": issued,
            "ready": ready,
            "active": active,
            "glabs_count": len(glabs_items),
            "lmp_count": lmp_count,
            "lmp_summary": ", ".join(k for k, _ in lmp_counter.most_common(3)),
            "locations": ", ".join(locations[:4]),
            "notes": [str(n) for n in notes[:3]],
            "checkin": checkin,
            "last_issue": last_issue,
            "rest_until": rest_until,
            "in_rest": in_rest,
            "boxes": _sum_num([r.get("boxes") for r in plate_rows]),
            "weight": _sum_num([r.get("weight") for r in plate_rows]),
            "glabs_items": glabs_items,
        })

    cards.sort(key=lambda item: item.get("sort_key", (99, 0, 0, 0, "")))
    for idx, card in enumerate(cards, start=1):
        card["rank"] = idx
    return cards, {}


def _awb_base(awb: str) -> str:
    """Strip numeric version suffix (1-3 digits) from a BUD-Pallets AWB.

    '235-96463382-1' → '235-96463382'   (short suffix → stripped)
    '157-47004381'   → '157-47004381'   (8-digit part → kept as-is)
    """
    parts = awb.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) <= 3:
        return parts[0]
    return awb


def load_all_flow_data(
    progress_callback: Optional[Callable[[int, str, str], None]] = None,
) -> tuple[pd.DataFrame, dict, dict, list[dict], dict, set, list[dict]]:
    """Read BUD-Pallets once, then build both inbound and outbound views.

    Returns a 7-tuple; the 6th element is the set of issued AWBs from
    BUD-Pallets; the 7th element is the list of open ULD records from E_COMM.
    """
    if oracle_ecomm.get_source_mode() == "oracle":
        # load_data refreshes the Oracle snapshot first, then _read_pallets reads
        # outbound rows from that exact same atomic snapshot. Rebuilding the
        # small DataFrame below is in-memory only and issues no second query.
        _notify_progress(progress_callback, 4, "Adatforrások előkészítése", "Oracle kapcsolat ellenőrzése")
        df, errors, kpi, uld_data = load_data(progress_callback=progress_callback)
        pallets = _read_pallets()
    else:
        _notify_progress(progress_callback, 4, "Adatforrások előkészítése", "BUD-Pallets beolvasása")
        pallets = _read_pallets()
        _notify_progress(progress_callback, 10, "BUD-Pallets beolvasva", f"{len(pallets):,} sor".replace(",", " "))
        df, errors, kpi, uld_data = load_data(pallets=pallets, progress_callback=progress_callback)
    pallets_read_error = None
    try:
        pallets_read_error = pallets.attrs.get("pallets_read_error")
    except AttributeError:
        pallets_read_error = None
    if pallets_read_error:
        errors = dict(errors or {})
        errors["pallets_read_error"] = pallets_read_error
        errors["hard_read_failure"] = True
    # OUTBOUND must ignore E-COM-flagged GLABS loads (rest/pihenő M column = "E-COM").
    # The inbound view above keeps the unfiltered pallets on purpose.
    _notify_progress(progress_callback, 78, "Aktív tételek összeállítása", "Inbound kapcsolatok és prioritások")
    outbound_pallets = _drop_ecomm_glabs(pallets)
    if isinstance(kpi, dict):
        awb_parcel_map = kpi.get("awb_parcel_map") or {}
        if isinstance(awb_parcel_map, dict) and not outbound_pallets.empty and "awb" in outbound_pallets.columns:
            outbound_pallets = outbound_pallets.copy()

            def _parcel_for_awb(value) -> float:
                awb = str(value or "").strip()
                if not awb:
                    return 0.0
                return float(awb_parcel_map.get(awb, awb_parcel_map.get(_base_awb(awb), 0.0)) or 0.0)

            outbound_pallets["parcel_count"] = outbound_pallets["awb"].apply(_parcel_for_awb)
        ecomm_time_bands_outbound = kpi.get("time_bands_outbound")
        ecomm_time_bands_outbound_prev = kpi.get("time_bands_outbound_prev")
        ecomm_time_bands_outbound_days = kpi.get("time_bands_outbound_days")
        kpi.update(_compute_outbound_shift_kpi(outbound_pallets))
        if ecomm_time_bands_outbound is not None:
            kpi["time_bands_outbound"] = ecomm_time_bands_outbound
        if ecomm_time_bands_outbound_prev is not None:
            kpi["time_bands_outbound_prev"] = ecomm_time_bands_outbound_prev
        if ecomm_time_bands_outbound_days is not None:
            kpi["time_bands_outbound_days"] = ecomm_time_bands_outbound_days
        # Per-hour shift throughput needs both the inbound (E_COMM) and the outbound
        # (BUD-Pallets) shift figures, so it's assembled here and parked on the tracking
        # payload — the single source the warehouse breakdown reads from.
        if isinstance(kpi.get("tracking"), dict):
            kpi["tracking"]["shift_rates"] = _compute_shift_rates(kpi)
        # Napszak (time-of-day) breakdown for the KPI page bottom section: inbound
        # (E_COMM AL / Áttár) + outbound (issued_time) merged per 4-hour band of today.
        kpi["time_bands"] = _merge_time_bands(
            kpi.pop("time_bands_inbound_days", None) or kpi.pop("time_bands_inbound", None),
            kpi.pop("time_bands_outbound_days", None) or kpi.pop("time_bands_outbound", None),
            kpi.pop("time_bands_inbound_prev", None),
            kpi.pop("time_bands_outbound_prev", None),
        )
    _notify_progress(progress_callback, 88, "Kiadási sorrend összeállítása", "Kamionok és rakodási állapotok")
    outbound_cards, outbound_errors = load_outbound_data(
        pallets=outbound_pallets,
        ecomm_status_map=kpi.get("awb_status_map", {}),
    )

    issued_awbs: set[str] = set()
    if not pallets.empty:
        issued_mask = pallets.apply(
            lambda r: _is_pallet_issued(r.get("kiad_status"), r.get("issued_time")),
            axis=1,
        )
        for awb in pallets.loc[issued_mask, "awb"].dropna():
            awb_str = str(awb).strip()
            if awb_str:
                issued_awbs.add(awb_str)
                base = _awb_base(awb_str)
                if base != awb_str:
                    issued_awbs.add(base)

    _notify_progress(progress_callback, 97, "Felület előkészítése", "Az adatok rendezése befejeződik")
    return df, errors, kpi, outbound_cards, outbound_errors, issued_awbs, uld_data
