"""
Background data cache — reads Excel files in a daemon thread so the
UI stays responsive at all times.

Refresh triggers:
  1. Scheduled interval (REFRESH_INTERVAL_MS from config)
  2. Manual trigger via trigger_refresh()
  3. Shared runtime-state watcher — updates lightweight UI state only
"""
import logging
import os
import pickle
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import ECOMM_FILE, PALLETS_FILE, REFRESH_INTERVAL_MS, _config_dir
from data_reader import ECOMM_STALE_AFTER_MINUTES, ULD_DATA_PARSER_VERSION, load_all_flow_data, load_uld_data_fast
from priority_engine import apply_priorities
import notes_manager
import overrides_manager
import storage_manager
import uld_stack_manager
import oracle_ecomm

log = logging.getLogger(__name__)

_REFRESH_SECONDS  = REFRESH_INTERVAL_MS / 1000
_WATCH_INTERVAL_S = 3    # file-change check interval
_STACK_STABLE_SECONDS = 6
_CACHE_VERSION = 2
_SUPPORTED_CACHE_VERSIONS = {1, 2}

# A normal full source read (both Excels) takes ~30s. Beyond this budget the
# source is stuck (OneDrive cloud-only hydration, Excel lock, network stall).
# The scheduler abandons the wait, keeps the last good data, flags the stall and
# retries soon — it must NEVER block indefinitely (24/7 TV must keep refreshing).
_READ_TIMEOUT_S = 150.0
# While stalled, poll more often than the 10-min interval so we recover fast the
# moment the source frees up (a late-finishing read is picked up immediately).
_STALE_RETRY_S = 60.0

_lock = threading.Lock()
_state = {
    "df": None,
    "errors": {},
    "kpi": {},
    "last_refresh": None,
    "last_success_refresh": None,   # wall time of the last SUCCESSFUL full source read
    "read_stalled": False,          # True while a source read is stuck / hard-failing
    "read_stall_since": None,       # when the current stall started
    "read_stall_reason": "",        # short human reason for the stall (UI/log)
    "status": "loading",
    "refresh_count": 0,
    "data_version": 0,
    "outbound_cards": [],
    "outbound_errors": {},
    "source_signature": None,
    "issued_awbs": set(),
    "refreshing": False,
    "load_progress": 0,
    "load_stage": "Indítás",
    "load_detail": "Adatforrások előkészítése",
    "load_started_at": None,
    "uld_data": [],
    "uld_times_full": {},
    "uld_returned": [],
    "uld_archive": [],
    "uld_last_refresh": None,
    "uld_refreshing": False,
    "uld_source_signature": None,
}


def get_state() -> dict:
    with _lock:
        return dict(_state)


def trigger_refresh():
    """Ask the background thread to refresh immediately (non-blocking)."""
    _refresh_event.set()


def mark_refresh_started():
    """Surface immediate UI feedback for a manually requested full refresh."""
    with _lock:
        if _state.get("status") != "loading":
            _state["refreshing"] = True
            _state["load_progress"] = 1
            _state["load_stage"] = "Frissítés indítása"
            _state["load_detail"] = "Adatforrások előkészítése"
            _state["load_started_at"] = datetime.now()
            _state["data_version"] += 1


def bump_data_version():
    """Notify Dash clients after lightweight shared-state changes."""
    with _lock:
        _state["data_version"] += 1


def _source_signature() -> dict:
    mode = oracle_ecomm.get_source_mode()
    result = {"ecomm_source_mode": mode}
    for path in (ECOMM_FILE, PALLETS_FILE):
        if mode == "oracle" and path in {ECOMM_FILE, PALLETS_FILE}:
            result[path] = {"source": "oracle"}
            continue
        try:
            st = os.stat(path)
            result[path] = {"mtime": st.st_mtime, "size": st.st_size}
        except OSError:
            result[path] = {"mtime": 0.0, "size": 0}
    return result


def _uld_source_signature(source_signature: dict | None = None) -> dict:
    sig = source_signature or _source_signature()
    return {
        "source": sig.get(ECOMM_FILE),
        "parser": ULD_DATA_PARSER_VERSION,
    }


def _cached_uld_source_signature(payload: dict) -> dict | None:
    if not payload.get("uld_data"):
        return None
    if payload.get("uld_source_signature") is not None:
        return payload.get("uld_source_signature")
    return {
        "source": (payload.get("source_signature") or {}).get(ECOMM_FILE),
        "parser": 0,
    }


def _cache_paths() -> list[Path]:
    """Return the machine-local dashboard cache path.

    The dashboard cache contains a complete in-memory snapshot and is not
    collaborative state. Writing it into the shared OneDrive workspace made
    every successful refresh rewrite a large file and created unnecessary
    sync traffic. Shared operational state continues to use storage_manager;
    this cache intentionally remains local to the current user and machine.
    """
    return [_config_dir() / "dashboard_cache.pkl"]


def _read_cache_file(path: Path) -> dict | None:
    try:
        if oracle_ecomm.get_source_mode() != "oracle":
            try:
                ecomm_age_minutes = (time.time() - os.path.getmtime(ECOMM_FILE)) / 60.0
                if ecomm_age_minutes > ECOMM_STALE_AFTER_MINUTES:
                    log.info(
                        "Dashboard cache ignored: E_COMM source is stale (%.1f min > %d min)",
                        ecomm_age_minutes,
                        ECOMM_STALE_AFTER_MINUTES,
                    )
                    return None
            except OSError:
                return None

        with open(path, "rb") as f:
            payload = pickle.load(f)
        if payload.get("version") not in _SUPPORTED_CACHE_VERSIONS:
            return None
        if not isinstance(payload.get("df"), pd.DataFrame):
            return None
        if "awb" not in payload["df"].columns:
            return None
        payload["_missing_uld_data"] = "uld_data" not in payload
        payload["_source_signature_match"] = payload.get("source_signature") == _source_signature()
        if not payload["_source_signature_match"]:
            log.info("Dashboard cache used as warm start, source files changed: %s", path)
        return payload
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("Dashboard cache read error (%s): %s", path, exc)
        return None


def _load_dashboard_cache() -> bool:
    candidates = []
    for path in _cache_paths():
        payload = _read_cache_file(path)
        if payload:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append((mtime, path, payload))

    if not candidates:
        return False

    _, path, payload = max(candidates, key=lambda item: item[0])
    df = payload.get("df", pd.DataFrame())
    stored = storage_manager.get_stored_awbs()
    if not df.empty:
        df = overrides_manager.apply_overrides_to_df(df)
        df = _apply_stored_state(df, stored)
        df = apply_priorities(df)

    loaded_at = payload.get("loaded_at")
    if isinstance(loaded_at, str):
        try:
            loaded_at = datetime.fromisoformat(loaded_at)
        except ValueError:
            loaded_at = datetime.now()
    if not isinstance(loaded_at, datetime):
        loaded_at = datetime.now()

    with _lock:
        _state["df"] = df if not df.empty else pd.DataFrame()
        _state["errors"] = payload.get("errors", {})
        _state["kpi"] = payload.get("kpi", {})
        _state["outbound_cards"] = payload.get("outbound_cards", [])
        _state["outbound_errors"] = payload.get("outbound_errors", {})
        _state["issued_awbs"] = payload.get("issued_awbs", set()) or set()
        _state["uld_data"] = payload.get("uld_data", []) or []
        _state["uld_last_refresh"] = loaded_at if payload.get("uld_data") else None
        _state["uld_source_signature"] = _cached_uld_source_signature(payload)
        _state["last_refresh"] = loaded_at
        # Cache payloads are written only after a successful source read, so the
        # restored timestamp is also the last known-good refresh. Without this,
        # a later failed read claimed that no fresh data had ever existed even
        # while the UI was visibly serving the cached snapshot.
        _state["last_success_refresh"] = loaded_at
        _state["status"] = "ready"
        _state["data_version"] += 1
        _state["source_signature"] = payload.get("source_signature")
        _state["refreshing"] = (not payload.get("_source_signature_match", False)) or payload.get("_missing_uld_data", False)
        _state["load_progress"] = 100
        _state["load_stage"] = "Gyorsítótár betöltve"
        _state["load_detail"] = "A háttérfrissítés hamarosan elindul"
        _state["load_started_at"] = None

    log.info("Dashboard cache loaded from %s (%d rows)", path, len(df))
    return True


def _write_cache_file(path: Path, payload: dict):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        with open(tmp, "wb") as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        try:
            os.replace(tmp, path)
        except OSError:
            with open(path, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:
        log.debug("Dashboard cache write skipped (%s): %s", path, exc)
        try:
            if "tmp" in locals() and tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _save_dashboard_cache(
    df: pd.DataFrame,
    errors: dict,
    kpi: dict,
    outbound_cards: list[dict],
    outbound_errors: dict,
    issued_awbs: set,
    uld_data: list[dict],
    loaded_at: datetime,
):
    if errors.get("error") and not outbound_cards:
        return
    if df is None or not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(columns=["awb"])
    elif "awb" not in df.columns:
        df = df.copy()
        df["awb"] = pd.Series(dtype="object")
    payload = {
        "version": _CACHE_VERSION,
        "loaded_at": loaded_at.isoformat(),
        "source_signature": _source_signature(),
        "uld_source_signature": _uld_source_signature(),
        "df": df,
        "errors": errors,
        "kpi": kpi,
        "outbound_cards": outbound_cards,
        "outbound_errors": outbound_errors,
        "issued_awbs": issued_awbs,
        "uld_data": uld_data,
    }
    for path in _cache_paths():
        _write_cache_file(path, payload)


def _apply_stored_state(df: pd.DataFrame, stored: set[str]) -> pd.DataFrame:
    df = df.copy()

    def _safe_int(value, default: int = 0) -> int:
        try:
            if pd.isna(value):
                return default
        except (TypeError, ValueError):
            pass
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _series_max_int(series, default: int = 0) -> int:
        numeric = pd.to_numeric(series, errors="coerce").dropna()
        if numeric.empty:
            return default
        return _safe_int(numeric.max(), default)

    def _clone_loading_items(items) -> list[dict]:
        if not isinstance(items, list):
            return []
        return [dict(item) for item in items if isinstance(item, dict)]

    if "glabs_shippable_base" not in df.columns:
        df["glabs_shippable_base"] = df.get("glabs_shippable", 0)
    if "glabs_ratio_base" not in df.columns:
        df["glabs_ratio_base"] = df.get("glabs_ratio", 0.0)
    if "bud_is_shippable" not in df.columns:
        df["bud_is_shippable"] = df.get("is_shippable", False)
    if "glabs_ready_items_base" not in df.columns:
        df["glabs_ready_items_base"] = df.get("glabs_ready_items", [[] for _ in range(len(df))])
    if "glabs_loading_items_base" not in df.columns:
        df["glabs_loading_items_base"] = df.get("glabs_loading_items", df["glabs_ready_items_base"])

    df["is_stored"] = df["awb"].isin(stored)
    df["glabs_shippable"] = df["glabs_shippable_base"]
    df["glabs_ratio"] = df["glabs_ratio_base"]
    df["glabs_loading_items"] = df["glabs_loading_items_base"].apply(_clone_loading_items)
    df["glabs_ready_items"] = df["glabs_loading_items"].apply(_clone_loading_items)

    if "glabs_id" not in df.columns or "glabs_total" not in df.columns:
        return df

    stored_extra = df[
        df["is_stored"]
        & df["glabs_id"].notna()
        & (df["glabs_id"].astype(str).str.strip() != "")
        & ~df["bud_is_shippable"].fillna(False).astype(bool)
    ]
    if stored_extra.empty:
        return df

    extras_by_glabs = stored_extra.groupby("glabs_id")["awb"].nunique().to_dict()
    for glabs_id, extra_count in extras_by_glabs.items():
        mask = df["glabs_id"] == glabs_id
        if not mask.any():
            continue
        total = _series_max_int(df.loc[mask, "glabs_total"], 0)
        base = _series_max_int(df.loc[mask, "glabs_shippable_base"], 0)
        extra_count = _safe_int(extra_count, 0)
        effective = min(total, base + extra_count) if total > 0 else base + extra_count
        ratio = effective / total if total > 0 else 0.0
        df.loc[mask, "glabs_shippable"] = effective
        df.loc[mask, "glabs_ratio"] = ratio

        subset = stored_extra[stored_extra["glabs_id"] == glabs_id]
        lmp_col = subset["lmp"].fillna("").astype(str) if "lmp" in subset.columns else pd.Series("", index=subset.index)
        bud_lmp_col = subset["bud_lmp"].fillna("").astype(str) if "bud_lmp" in subset.columns else pd.Series("", index=subset.index)
        awb_col = subset["awb"].fillna("").astype(str) if "awb" in subset.columns else pd.Series("", index=subset.index)
        lmp_resolved = lmp_col.where(lmp_col.str.strip() != "", bud_lmp_col)
        extras = [
            {"lmp": lmp, "awb": awb, "status": "Betárolva", "is_ready": True, "is_manual": True}
            for lmp, awb in zip(lmp_resolved.tolist(), awb_col.tolist())
        ]
        if extras and "glabs_loading_items" in df.columns:
            existing_awbs = {str(item.get("awb") or "") for items in df.loc[mask, "glabs_loading_items"] for item in _clone_loading_items(items)}

            def _with_extras(items):
                merged = _clone_loading_items(items)
                seen = {str(item.get("awb") or "") for item in merged}
                for extra in extras:
                    awb = str(extra.get("awb") or "")
                    if awb and awb not in seen and awb not in existing_awbs:
                        merged.append(dict(extra))
                        seen.add(awb)
                return merged

            df.loc[mask, "glabs_loading_items"] = df.loc[mask, "glabs_loading_items"].apply(_with_extras)
            df.loc[mask, "glabs_ready_items"] = df.loc[mask, "glabs_loading_items"].apply(_clone_loading_items)

    return df


def update_stored_flags(stored_awbs: set[str] | None = None):
    """Re-apply shared storage state without re-reading Excel (instant).

    Overrides are re-applied first (they restore/replace base fields), so a
    dev-menu override change goes through the same instant path.
    """
    stored = stored_awbs if stored_awbs is not None else storage_manager.get_stored_awbs()
    with _lock:
        df = _state["df"]
        if df is not None and not df.empty:
            df = overrides_manager.apply_overrides_to_df(df)
            df = _apply_stored_state(df, stored)
            _state["df"] = apply_priorities(df)
        _state["data_version"] += 1


_refresh_event = threading.Event()
_uld_refresh_lock = threading.Lock()


def _get_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _save_current_cache_snapshot():
    with _lock:
        df = _state.get("df")
        errors = dict(_state.get("errors") or {})
        kpi = dict(_state.get("kpi") or {})
        outbound_cards = list(_state.get("outbound_cards") or [])
        outbound_errors = dict(_state.get("outbound_errors") or {})
        issued_awbs = set(_state.get("issued_awbs") or set())
        uld_data = list(_state.get("uld_data") or [])
        loaded_at = _state.get("last_refresh") or datetime.now()
    _save_dashboard_cache(df, errors, kpi, outbound_cards, outbound_errors, issued_awbs, uld_data, loaded_at)


def refresh_uld_data(force: bool = False) -> bool:
    """Refresh only the ULD board data from E_COMM.

    Returns True when a fresh ULD read was applied. The function is safe to call
    from a Dash callback; it avoids the full inbound/outbound refresh path.
    """
    source_sig = _uld_source_signature()
    with _lock:
        has_data = bool(_state.get("uld_data"))
        same_source = _state.get("uld_source_signature") == source_sig
        already_refreshing = _state.get("uld_refreshing")
    if already_refreshing:
        return False
    if has_data and same_source and not force:
        return False

    if not _uld_refresh_lock.acquire(blocking=False):
        return False
    try:
        with _lock:
            _state["uld_refreshing"] = True
            _state["data_version"] += 1
        with _lock:
            initial_uld_load = _state.get("status") == "loading" and not _state.get("refresh_count")
        progress_callback = _set_load_progress if initial_uld_load else None
        uld_data, meta = load_uld_data_fast(progress_callback=progress_callback)
        if meta.get("error"):
            log.warning("Fast ULD refresh failed: %s", meta.get("error"))
            return False
        with _lock:
            _state["uld_data"] = uld_data
            # Full am/expiry lookup including aged-out (>60h) ULDs — used by stack
            # rendering so the "Lejárati idő" column survives the active-list cutoff.
            if meta.get("uld_times_full") is not None:
                _state["uld_times_full"] = meta.get("uld_times_full")
            if meta.get("uld_returned") is not None:
                _state["uld_returned"] = meta.get("uld_returned")
            if meta.get("uld_archive") is not None:
                _state["uld_archive"] = meta.get("uld_archive")
            _state["uld_last_refresh"] = datetime.now()
            _state["uld_source_signature"] = source_sig
            if meta.get("ecomm_source_age_minutes") is not None:
                _state.setdefault("kpi", {})["ecomm_source_age_minutes"] = meta.get("ecomm_source_age_minutes")
                _state["kpi"]["ecomm_source_mtime"] = meta.get("ecomm_source_mtime")
                _state["kpi"]["ecomm_source_stale"] = bool(meta.get("ecomm_source_stale"))
            _state["uld_refreshing"] = False
            _state["data_version"] += 1
        _save_current_cache_snapshot()
        log.info("Fast ULD refresh done: %d records", len(uld_data))
        return True
    finally:
        with _lock:
            _state["uld_refreshing"] = False
        _uld_refresh_lock.release()


def _file_watcher():
    """Watches shared stored-state and ULD state JSON files; syncs on change.
    Excel source files are NOT watched here — refreshes only on timer or manual trigger.
    """
    stored_path = str(storage_manager.get_storage_path())
    last_stored_mtime = _get_mtime(stored_path)
    notes_path = str(notes_manager.get_notes_path())
    last_notes_mtime = _get_mtime(notes_path)
    overrides_path = str(overrides_manager.get_overrides_path())
    last_overrides_mtime = _get_mtime(overrides_path)
    def _uld_state_signature():
        return (
            uld_stack_manager.get_stacks_mtime(),
            _get_mtime(str(uld_stack_manager.get_renames_path())),
            _get_mtime(str(uld_stack_manager.get_overrides_path())),
        )

    last_stacks_mtime = _uld_state_signature()
    pending_stacks_mtime = None
    pending_stacks_since = 0.0
    while True:
        time.sleep(_WATCH_INTERVAL_S)
        current = _get_mtime(stored_path)
        if current != last_stored_mtime:
            last_stored_mtime = current
            log.info("Shared stored state changed: %s — syncing flags", os.path.basename(stored_path))
            update_stored_flags()

        current_notes = _get_mtime(notes_path)
        if current_notes != last_notes_mtime:
            last_notes_mtime = current_notes
            log.info("Shared notes changed — bumping data_version")
            bump_data_version()

        current_overrides = _get_mtime(overrides_path)
        if current_overrides != last_overrides_mtime:
            last_overrides_mtime = current_overrides
            log.info("Shared overrides changed — reapplying")
            update_stored_flags()

        now = time.monotonic()
        current_stacks_mtime = _uld_state_signature()
        if current_stacks_mtime != last_stacks_mtime:
            if current_stacks_mtime != pending_stacks_mtime:
                pending_stacks_mtime = current_stacks_mtime
                pending_stacks_since = now
            elif now - pending_stacks_since >= _STACK_STABLE_SECONDS:
                last_stacks_mtime = current_stacks_mtime
                pending_stacks_mtime = None
                pending_stacks_since = 0.0
                log.info("ULD stacks stable - bumping data_version")
                with _lock:
                    _state["data_version"] += 1
        else:
            pending_stacks_mtime = None
            pending_stacks_since = 0.0

        # Do not watch Excel source files here. OneDrive/Excel can touch metadata
        # repeatedly and would start refreshes before the configured interval.
        # Full source reads are intentionally limited to scheduled/manual refreshes.


# Reader plumbing: the (potentially blocking) Excel read runs on a short-lived
# helper thread and hands its result back through this queue, so the scheduler
# thread can wait with a hard timeout and NEVER get stuck on a frozen source.
_read_result_q: "queue.Queue" = queue.Queue()


def _set_load_progress(percent: int, stage: str, detail: str = "") -> None:
    """Publish lightweight, thread-safe source-read progress for the overlay."""
    with _lock:
        _state["load_progress"] = max(0, min(100, int(percent)))
        _state["load_stage"] = str(stage or "Adatok betöltése")
        _state["load_detail"] = str(detail or "")


def _reader_body():
    started_at = datetime.now()
    try:
        result = load_all_flow_data(progress_callback=_set_load_progress)
        _read_result_q.put(("ok", {"result": result, "started_at": started_at}))
    except Exception as exc:
        log.error("Cache: source read raised: %s", exc, exc_info=True)
        _read_result_q.put(("err", exc))


def _has_good_data_locked() -> bool:
    df = _state.get("df")
    return (
        (isinstance(df, pd.DataFrame) and not df.empty)
        or bool(_state.get("outbound_cards"))
        or bool(_state.get("uld_data"))
    )


def _mark_read_stalled(reason: str, first: bool):
    """A read is stuck or hard-failing: keep the last good data and flag the stall.

    The dashboard/TV keeps showing the last good snapshot (never blanks); the UI
    can surface "adat X perce · szinkron elakadt". Only when there is no good data
    yet (cold start) do we fall back to an error status so the loading screen can
    explain why.
    """
    now = datetime.now()
    with _lock:
        have_good = _has_good_data_locked()
        if _state.get("read_stall_since") is None:
            _state["read_stall_since"] = now
        _state["read_stalled"] = True
        _state["read_stall_reason"] = reason
        _state["refreshing"] = False
        _state["load_stage"] = "A beolvasás elakadt"
        _state["load_detail"] = reason
        if have_good:
            _state["status"] = "ready"   # serving last-good data, just stale
        elif first:
            _state["status"] = "error"
            _state["errors"] = {"error": f"Az adatforrás jelenleg nem olvasható ({reason})."}
        _state["data_version"] += 1
    log.warning("Cache: read stalled — keeping last good data (%s)", reason)


def _apply_read_result(result, first: bool, read_started_at: datetime | None = None) -> bool:
    """Apply a successful full read. Returns True on a real data update, or False
    on a hard read failure (last good data is kept)."""
    df, errors, kpi, outbound_cards, outbound_errors, issued_awbs, uld_data = result

    # Hard read failure (missing/stale/locked source) — keep last good data.
    # A merely empty-but-OK read (no active items) has NO hard_read_failure flag
    # and is applied normally, so end-of-shift emptiness still shows correctly.
    if errors.get("hard_read_failure"):
        reason = errors.get("error") or errors.get("pallets_read_error") or "forrás olvasási hiba"
        _mark_read_stalled(str(reason), first)
        return False

    loaded_at = datetime.now()
    with _lock:
        fast_uld_is_newer = bool(
            read_started_at
            and _state.get("uld_last_refresh")
            and _state["uld_last_refresh"] > read_started_at
        )
        effective_uld_data = list(_state.get("uld_data") or []) if fast_uld_is_newer else uld_data
    # Auto-remove stored entries that BUD-Pallets already shows as issued.
    if issued_awbs:
        stored_now = storage_manager.get_stored_awbs()
        for awb in stored_now & issued_awbs:
            storage_manager.mark_unstored(awb)
            log.info("Auto-unstored: %s (Kiadva in BUD-Pallets)", awb)
    if (not errors.get("error")) and (not df.empty or bool(outbound_cards) or bool(effective_uld_data)):
        _save_dashboard_cache(df, errors, kpi, outbound_cards, outbound_errors, issued_awbs, effective_uld_data, loaded_at)
    if not df.empty:
        stored = storage_manager.get_stored_awbs()
        df = overrides_manager.apply_overrides_to_df(df)
        df = _apply_stored_state(df, stored)
        df = apply_priorities(df)
        # Notes cleanup only on a successful background read (a UI render never
        # mutates shared files) — 48h grace inside.
        try:
            notes_manager.cleanup_missing(
                set(df["awb"].astype(str)) if "awb" in df.columns else set(),
                stored,
            )
        except Exception as exc:
            log.debug("Notes cleanup skipped: %s", exc)
    with _lock:
        # A full Excel read is much slower than the ULD-only reader.  If the fast
        # reader completed after this full read started, its snapshot is newer and
        # must not be overwritten by the older in-flight result.
        _state["df"]           = df if not df.empty else pd.DataFrame()
        _state["errors"]       = errors
        _state["kpi"]          = kpi
        _state["outbound_cards"] = outbound_cards
        _state["outbound_errors"] = outbound_errors
        _state["last_refresh"] = loaded_at
        _state["last_success_refresh"] = loaded_at
        _state["read_stalled"] = False
        _state["read_stall_since"] = None
        _state["read_stall_reason"] = ""
        _state["status"]       = "error" if errors.get("error") else "ready"
        _state["refresh_count"] += 1
        _state["data_version"] += 1
        _state["source_signature"] = _source_signature()
        _state["issued_awbs"]  = issued_awbs
        _state["uld_data"]     = effective_uld_data
        if not fast_uld_is_newer:
            _state["uld_last_refresh"] = loaded_at
            _state["uld_source_signature"] = _uld_source_signature(_state["source_signature"])
        _state["refreshing"]   = False
        _state["load_progress"] = 100
        _state["load_stage"] = "Betöltés kész"
        _state["load_detail"] = f"{len(df)} aktív tétel"
        _state["load_started_at"] = None
        row_count = len(_state["df"])
    log.info("Cache: refresh done, %d active items", row_count)
    return True


def _worker():
    first = True
    reader = None
    while True:
        # Dispatch a fresh read only when the previous one is no longer running,
        # so a stuck read never accumulates duplicate reader threads.
        if reader is None or not reader.is_alive():
            with _lock:
                _state["load_progress"] = 1
                _state["load_stage"] = "Betöltés indítása"
                _state["load_detail"] = "Adatforrások ellenőrzése"
                _state["load_started_at"] = datetime.now()
            if first:
                print("  [*] E_COMM adatok betöltése (~30 mp)...", flush=True)
            else:
                with _lock:
                    _state["refreshing"] = True
                    _state["data_version"] += 1   # poll picks up the refreshing flag
            log.info("Cache: starting data refresh")
            reader = threading.Thread(target=_reader_body, daemon=True, name="DataRead")
            reader.start()

        # Bounded wait — the scheduler must never block forever. A late result
        # from a previously-stuck reader is picked up here the instant it lands.
        try:
            status, payload = _read_result_q.get(timeout=_READ_TIMEOUT_S)
            timed_out = False
        except queue.Empty:
            status, payload = None, None
            timed_out = True

        if timed_out:
            source_label = "Oracle" if oracle_ecomm.get_source_mode() == "oracle" else "OneDrive/Excel"
            _mark_read_stalled(
                "a forrás olvasása túllépte a %.0f mp-es korlátot (%s elakadt)" % (_READ_TIMEOUT_S, source_label),
                first,
            )
            wait_s = _STALE_RETRY_S
        elif status == "ok":
            if isinstance(payload, dict) and "result" in payload:
                updated = _apply_read_result(payload["result"], first, payload.get("started_at"))
            else:  # backward-compatible with an already queued pre-upgrade result
                updated = _apply_read_result(payload, first)
            if first:
                with _lock:
                    err = (_state.get("errors") or {}).get("error")
                    n = len(_state["df"]) if isinstance(_state.get("df"), pd.DataFrame) else 0
                if err:
                    print(f"  [X] Hiba: {err}", flush=True)
                else:
                    print(f"  [OK] Betöltve: {n} aktív tétel - dashboard kész", flush=True)
                first = False
            wait_s = _REFRESH_SECONDS if updated else _STALE_RETRY_S
        else:  # status == "err"
            _mark_read_stalled(f"olvasási kivétel: {payload}", first)
            if first:
                print(f"  [X] Betöltési hiba: {payload}", flush=True)
                first = False
            wait_s = _STALE_RETRY_S

        _refresh_event.wait(timeout=wait_s)
        _refresh_event.clear()


def _delayed_worker(delay_seconds: float):
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    _worker()


def _cleanup_stale_tmp_files():
    """Remove leftover *.tmp files from _shared_state/ older than 1 hour.

    These accumulate when atomic writes are interrupted by a crash or race
    condition. Safe to delete on startup since any live writer uses a fresh
    pid-suffixed name and would never be interrupted mid-rename.
    """
    try:
        shared_dir = storage_manager.get_storage_path().parent
        cutoff = time.time() - 3600
        for p in shared_dir.glob("*.tmp"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    log.info("Cleaned up stale tmp: %s", p.name)
            except OSError:
                pass
    except Exception as exc:
        log.debug("Tmp cleanup skipped: %s", exc)


def start():
    _cleanup_stale_tmp_files()
    cache_loaded = _load_dashboard_cache()
    if cache_loaded:
        print("  [i] Dashboard gyorsítótár betöltve, háttérfrissítés indul...", flush=True)
        with _lock:
            _state["refreshing"] = True
            _state["data_version"] += 1
    worker_delay = 4.0 if cache_loaded else 0.0
    threading.Thread(target=refresh_uld_data, kwargs={"force": True}, daemon=True, name="ULDDataFast").start()
    threading.Thread(target=_delayed_worker, args=(worker_delay,), daemon=True, name="DataCache").start()
    threading.Thread(target=_file_watcher, daemon=True, name="FileWatcher").start()
    log.info("Cache: worker started (interval=%.0fs, file-watch=%.0fs)",
             _REFRESH_SECONDS, _WATCH_INTERVAL_S)
