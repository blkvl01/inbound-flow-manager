import re
import unicodedata
from datetime import datetime, timedelta

import pandas as pd

_REST_SOON_HOURS = 4
_TRUCK_ARRIVAL_DELAY_MINUTES = 30
# Felvéve óta ennyi óra fölött a tétel "régóta vár" → minden operatív kategória
# fölé, a normál munka elé sorolódik (sürgősségi öregedés).
_AGING_HOURS = 4


def _is_dt(v) -> bool:
    """True only for a real datetime/Timestamp, rejects None and pd.NaT."""
    return isinstance(v, datetime) and not pd.isna(v)


def _dt_sort_key(v) -> float:
    """Stable numeric timestamp for sorting; invalid dates go last."""
    if _is_dt(v):
        try:
            return float(v.timestamp())
        except (OverflowError, OSError, ValueError):
            return float("inf")
    return float("inf")


def _as_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


_AT_HU = re.compile(r"(?:^| )(?:AT|HU)(?: |$)")
_B2B = re.compile(r"(?:^| )B2B(?: |$)")
_TEMU = re.compile(r"(?:^| )TEMU(?: |$)")
_TEMU_MD = re.compile(r"(?:^| )TEMU\s+MD(?: |$)")
_MEEST_MD = re.compile(r"(?:^| )MEEST\s+MD(?: |$)")
_FOUR_PX = re.compile(r"(?:^| )4PX(?: |$)")
_ECOMM_STATUS_STAGE = {
    "elindult": 1,
    "megerkezett": 2,
    "ertesito": 3,
    "felveve": 4,
}

# Priority group constants (lower number = higher priority visually)
GRP_ATHU_REST_SOON = 1     # AT/HU + pihenő vége <= 4 óra → azonnali előkészítés
GRP_AT_HU = 2              # legacy display constant
GRP_DRIVER_ACTIVE_HIGH = 3
GRP_DRIVER_ACTIVE_LOW = 4
GRP_DRIVER_PASSIVE_HIGH = 5
GRP_DRIVER_PASSIVE_LOW = 6
GRP_EN_ROUTE = 99

_GROUP_META = {
    GRP_ATHU_REST_SOON: ("AT/HU - PIHENŐ HAMAROSAN VÉG", "rest-soon"),
    GRP_AT_HU: ("AT/HU PRIORITAS", "at-hu"),
    GRP_DRIVER_ACTIVE_HIGH: ("SOFOR HELYSZINEN - SOK KIADHATO", "driver-active"),
    GRP_DRIVER_ACTIVE_LOW: ("SOFOR HELYSZINEN - KEVES KIADHATO", "loading-plan"),
    GRP_DRIVER_PASSIVE_HIGH: ("NINCS ITT / PIHENON - SOK KIADHATO", "driver-rest"),
    GRP_DRIVER_PASSIVE_LOW: ("NINCS ITT / PIHENON - KEVES KIADHATO", "standard"),
    GRP_EN_ROUTE: ("UTON", "en-route"),
}


def _is_rest_soon(row) -> bool:
    """True if driver is currently resting and rest ends within 4 hours."""
    if not bool(row.get("driver_in_rest", False)):
        return False
    rest_until = row.get("rest_until")
    if not _is_dt(rest_until):
        return False
    hours_left = (rest_until - datetime.now()).total_seconds() / 3600
    return 0 < hours_left <= _REST_SOON_HOURS


def _hours_since_am(row) -> float:
    """Felvéve (am_time) óta eltelt órák; ismeretlen időnél 0 (nem öregszik)."""
    am = row.get("am_time")
    if not _is_dt(am):
        return 0.0
    return (datetime.now() - am).total_seconds() / 3600


def _is_aged(row) -> bool:
    """True, ha a tétel 4+ órája felvéve és még nincs úton (fizikailag itt van).

    Az úton lévő (még meg nem érkezett) tétel nem öregszik, mert nincs mit kezelni.
    """
    if _is_en_route(row):
        return False
    return _hours_since_am(row) >= _AGING_HOURS


def _is_driver_active(row) -> bool:
    return bool(row.get("in_bud_pallets", False)) and _is_dt(row.get("driver_checkin")) and not bool(row.get("driver_in_rest", False))


def _is_at_hu_priority(row) -> bool:
    """AT/HU display flag; actual boost is applied inside every operational bucket."""
    return bool(row.get("is_at_hu", False))


def _is_b2b_priority(row) -> bool:
    """True when the item's own LMP/customer field contains the B2B lane token."""
    return bool(_B2B.search(_norm_lane(row.get("lmp", ""))))


def _is_temu_like(row) -> bool:
    """Backward-compatible flag for rows that get an LMP tie-break boost."""
    return _lmp_tiebreak(row) <= 2


def _add_lane_part(parts: list[str], value) -> None:
    if value is None:
        return
    if not isinstance(value, (dict, list, tuple)):
        try:
            if pd.isna(value):
                return
        except (TypeError, ValueError):
            pass
    text = str(value).strip()
    if text and text.lower() != "nan":
        parts.append(text)


def _lane_blob(row) -> str:
    parts: list[str] = []
    _add_lane_part(parts, row.get("lmp", ""))
    _add_lane_part(parts, row.get("bud_lmp", ""))
    for field in ("glabs_loading_items", "glabs_ready_items"):
        items = row.get(field)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    _add_lane_part(parts, item.get("lmp", ""))
                else:
                    _add_lane_part(parts, item)
    return " ".join(parts)


def _norm_lane(value) -> str:
    """Normalize LMP text so AT/HU survives separators and copied whitespace."""
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def _is_at_hu_lane(value) -> bool:
    text = _norm_lane(value)
    return bool(_AT_HU.search(text))


def _is_md_lane(value) -> bool:
    text = _norm_lane(value)
    return bool(_MEEST_MD.search(text) or _FOUR_PX.search(text) or _TEMU_MD.search(text))


def _is_temu_general(value) -> bool:
    text = _norm_lane(value)
    return bool(_TEMU.search(text)) and not bool(_TEMU_MD.search(text))


def _lmp_tiebreak(row) -> int:
    """Within the same operational category: AT/HU > TEMU > MEEST MD/4PX/TEMU MD > others."""
    if bool(row.get("is_at_hu", False)):
        return 0
    lmp = row.get("priority_lane_text") or _lane_blob(row)
    if _is_temu_general(lmp):
        return 1
    if _is_md_lane(lmp):
        return 2
    return 3


def _status_key(value) -> str:
    text = str(value or "").strip().casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _status_stage(value) -> int:
    return _ECOMM_STATUS_STAGE.get(_status_key(value), 0)


def _load_status_sort(row) -> tuple:
    statuses: list[str] = []
    items = row.get("glabs_loading_items") or row.get("glabs_ready_items") or []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                status = item.get("status")
                if status:
                    statuses.append(status)
    if not statuses:
        status = row.get("status")
        if status:
            statuses.append(status)

    counts = {stage: 0 for stage in _ECOMM_STATUS_STAGE.values()}
    total_stage = 0
    known = 0
    for status in statuses:
        stage = _status_stage(status)
        if stage:
            counts[stage] += 1
            total_stage += stage
            known += 1

    avg_stage = total_stage / known if known else 0.0
    return (
        -counts[4],
        -counts[3],
        -counts[2],
        counts[1],
        -round(avg_stage, 4),
    )


def _has_many_shippable(row) -> bool:
    """Use both ratio and absolute count to separate high/low readiness."""
    total = _as_int(row.get("glabs_total"), 0)
    shippable = _as_int(row.get("glabs_shippable"), 0)
    ratio = _as_float(row.get("glabs_ratio"), 0.0)

    if total > 0:
        return ratio >= 0.5 or shippable >= 3
    return bool(row.get("is_shippable"))


def _arrival_time(row):
    explicit = row.get("truck_arrival_time")
    if _is_dt(explicit):
        return explicit
    am = row.get("am_time")
    if _is_dt(am):
        return am + timedelta(minutes=_TRUCK_ARRIVAL_DELAY_MINUTES)
    return None


def _is_en_route(row) -> bool:
    arrival = _arrival_time(row)
    return bool(arrival and datetime.now() < arrival)


def _group(row) -> int:
    if _is_en_route(row):
        return GRP_EN_ROUTE
    if bool(row.get("is_at_hu", False)) and _is_rest_soon(row):
        return GRP_ATHU_REST_SOON
    if _is_driver_active(row):
        return GRP_DRIVER_ACTIVE_HIGH if _has_many_shippable(row) else GRP_DRIVER_ACTIVE_LOW
    return GRP_DRIVER_PASSIVE_HIGH if _has_many_shippable(row) else GRP_DRIVER_PASSIVE_LOW


def _score(row) -> tuple:
    """
    Sort key (ascending = highest priority first).
    Levels:
      0. Hard availability gate:
           gate -1 = B2B LMP/ügyfél → minden más tétel elé
           gate 0 = AT/HU rest-soon (sofőr hamarosan indul)
           gate 1 = AGED (4+ órája felvéve, már itt van) → minden normál munka elé
           gate 2 = normál munka
           gate 3 = en-route (még úton, legvégére)
      1. En-route: várható érkezés szerint; Aged: legrégebben felvéve elöl (sürgősség)
      2. Operational category/group + bucket refinement, then shippable count,
         then LMP tie-break, ratio
      3. E_COMM status mix (loading progress) — only a fine tie-break
      4. AM time (older first — longest waiting wins)

    Az operatív helyzet (sofőr itt + mennyi kiadható) az elsődleges rendező a normál
    munkán belül, DE egy 4+ órája felvett, itt lévő tétel ezek elé kerül, mert túl
    régóta vár. Az aged tieren belül a legrégebben felvett az első.
    """
    lmp_tier = _lmp_tiebreak(row)
    ratio = _as_float(row.get("glabs_ratio"), 0.0)
    count = _as_int(row.get("glabs_shippable"), 0)
    am = _dt_sort_key(row.get("am_time"))
    arrival = _dt_sort_key(_arrival_time(row))
    priority_group = _group(row)
    operational_bucket = _operational_bucket(row)
    status_sort = _load_status_sort(row)
    aged = _is_aged(row)
    is_b2b = _is_b2b_priority(row)

    if is_b2b:
        # B2B is an absolute business-priority gate. Keep sensible ordering inside
        # the B2B block: arrived work first, then operational readiness and age.
        is_en_route = priority_group == GRP_EN_ROUTE
        return (
            -1,
            1 if is_en_route else 0,
            arrival if is_en_route else operational_bucket,
            -count,
            -round(ratio, 4),
            am,
            priority_group,
            operational_bucket,
            -count,
            lmp_tier,
            -round(ratio, 4),
            status_sort,
            am,
        )

    if priority_group == GRP_ATHU_REST_SOON:
        hard_gate = 0
    elif aged:
        hard_gate = 1
    elif priority_group == GRP_EN_ROUTE:
        hard_gate = 3
    else:
        hard_gate = 2

    return (
        hard_gate,
        arrival if priority_group == GRP_EN_ROUTE else 0,
        # Aged tier: operatív helyzet + LMP előbb, az am csak tie-break (ne csak az idő döntsön).
        operational_bucket if aged else 0,
        lmp_tier if aged else 0,
        (-count) if aged else 0,
        am if aged else 0,
        # Normál rendezés:
        priority_group,
        operational_bucket,
        -count,
        lmp_tier,
        -round(ratio, 4),
        status_sort,
        am,
    )


def _operational_bucket(row) -> int:
    # AT/HU + pihenő vége <= 4 óra → azonnali előkészítés szükséges
    if bool(row.get("is_at_hu", False)) and _is_rest_soon(row):
        return -1
    active = _is_driver_active(row)
    high = _has_many_shippable(row)
    in_bud = bool(row.get("in_bud_pallets", False))
    if active and high:
        return 0
    if active:
        return 1
    if in_bud and high:
        return 2
    if in_bud:
        return 3
    return 4


def apply_priorities(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()

    # priority_lane_text: per-row blob (contains list fields, must stay as apply)
    df["priority_lane_text"] = df.apply(_lane_blob, axis=1)

    # Vectorized flag computation: str.contains on the normalised lane text.
    # Equivalent to the per-row _is_at_hu_lane / _is_temu_general / _is_md_lane
    # functions but avoids a Python function call per row.
    _lane = df["priority_lane_text"].str.upper().str.replace(r"[^A-Z0-9]+", " ", regex=True)
    _temu_md_pat = r"(?:^| )TEMU MD(?= |$)"
    df["is_at_hu"]   = _lane.str.contains(r"(?:^| )(?:AT|HU)(?= |$)", regex=True, na=False)
    # B2B priority belongs to the item's own E_COMM LMP/customer value. Do not
    # inherit it from another row in the same GLABS loading dropdown.
    _own_lmp = df["lmp"].fillna("").astype(str).str.upper().str.replace(r"[^A-Z0-9]+", " ", regex=True)
    df["is_b2b_priority"] = _own_lmp.str.contains(r"(?:^| )B2B(?= |$)", regex=True, na=False)
    df["is_temu"]    = (
        _lane.str.contains(r"(?:^| )TEMU(?= |$)", regex=True, na=False)
        & ~_lane.str.contains(_temu_md_pat, regex=True, na=False)
    )
    df["is_md_lane"] = (
        _lane.str.contains(r"(?:^| )MEEST MD(?= |$)", regex=True, na=False)
        | _lane.str.contains(r"(?:^| )4PX(?= |$)", regex=True, na=False)
        | _lane.str.contains(_temu_md_pat, regex=True, na=False)
    )

    df["is_at_hu_priority"] = df["is_at_hu"]

    # Vectorized time fields
    _now = datetime.now()
    _am_times = pd.to_datetime(df["am_time"], errors="coerce")
    df["hours_since_am"] = (
        (_now - _am_times).dt.total_seconds().div(3600).fillna(0.0).clip(lower=0.0)
    )
    df["truck_arrival_time"] = df.apply(_arrival_time, axis=1)
    df["is_en_route"] = df.apply(_is_en_route, axis=1)
    df["is_aged"] = (df["hours_since_am"] >= _AGING_HOURS) & ~df["is_en_route"]

    # Remaining fields still require per-row logic
    df["lmp_priority_tier"] = df.apply(_lmp_tiebreak, axis=1)
    df["operational_bucket"] = df.apply(_operational_bucket, axis=1)
    df["is_temu_like"] = df.apply(_is_temu_like, axis=1)
    df["priority_score"] = df.apply(_score, axis=1)
    df["priority_group"] = df.apply(_group, axis=1)
    df["priority_label"] = df["priority_group"].map(lambda g: _GROUP_META[g][0])
    df["priority_color"] = df["priority_group"].map(lambda g: _GROUP_META[g][1])
    df.loc[df["is_b2b_priority"], "priority_label"] = "B2B - AZONNALI PRIORITÁS"
    df.loc[df["is_b2b_priority"], "priority_color"] = "b2b"

    df = df.sort_values("priority_score").reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    return df
