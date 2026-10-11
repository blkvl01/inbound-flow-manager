"""
Shared "Betarolva" state for multi-user use.

Stored as a per-machine ledger (see shared_ledger.py): every machine writes
only ``stored_awbs.<HOST>.json``; readers merge all machines plus the frozen
legacy ``stored_awbs.shared.json``.  Record per AWB:

  {"stored_at": "<ISO>", "stored_by": "<user>", "stored_on": "<computer>",
   "snapshot": {...row dict...}, "ts": "<ISO>", "deleted": false}

The folder is the discovered OneDrive workspace; a launcher may override it
with FLOW_SHARED_STATE_DIR.
"""
import json
import logging
import os
import socket
import sys
from datetime import datetime
from pathlib import Path

from shared_ledger import Ledger

log = logging.getLogger(__name__)


def _shared_base_dir() -> Path:
    # Launcher sets FLOW_SHARED_STATE_DIR to the SharePoint folder so the
    # shared JSON lives there even when the exe runs from a local TEMP copy.
    env_override = os.environ.get("FLOW_SHARED_STATE_DIR", "").strip()
    if env_override:
        shared_dir = Path(env_override)
        if shared_dir.is_dir():
            return shared_dir
        log.warning("FLOW_SHARED_STATE_DIR unavailable (%s), falling back to exe folder", shared_dir)

    # Keep the multi-user state in the canonical discovered OneDrive workspace
    # automatically when the frozen EXE is used. The saved picker value is
    # cleared by config startup once this canonical path is available.
    try:
        from config import _find_shared_state_folder, read_config_snapshot

        shared_state_folder = _find_shared_state_folder()
        if shared_state_folder:
            return Path(shared_state_folder)

        configured = str(read_config_snapshot().get("shared_state_dir") or "").strip()
        if configured:
            shared_dir = Path(configured)
            if shared_dir.is_dir():
                return shared_dir
            log.warning("Configured shared-state directory unavailable (%s), using local fallback", shared_dir)
    except (OSError, ImportError, ValueError, TypeError) as exc:
        log.warning("Shared-state discovery failed: %s", exc)

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent

    # No project-owned fallback directory is created. The executable/source
    # directory already exists and the shared files are written directly there.
    return base


def _legacy_records(data: dict) -> dict:
    return {
        str(awb): {**entry, "deleted": False, "ts": entry.get("stored_at", "")}
        for awb, entry in data.items() if isinstance(entry, dict)
    }


_ledger = Ledger("stored_awbs", lambda: _shared_base_dir(), _legacy_records)

_ENTRY_KEYS = ("stored_at", "stored_by", "stored_on", "snapshot")


def get_storage_path() -> Path:
    """This machine's ledger file (its parent is the shared folder)."""
    return _ledger.own_path()


def get_storage_dir() -> Path:
    return _shared_base_dir()


def state_signature() -> tuple:
    return _ledger.signature()


def get_storage_mtime() -> float:
    return max((entry[1] for entry in state_signature()[1:]), default=0) / 1e9


def load_stored() -> dict:
    return {awb: {k: rec.get(k) for k in _ENTRY_KEYS} for awb, rec in _ledger.live().items()}


def compact() -> int:
    return _ledger.compact()


def _new_entry(row: dict, now: str | None = None) -> dict:
    return {
        "stored_at": now or datetime.now().isoformat(),
        "stored_by": os.environ.get("USERNAME", "unknown"),
        "stored_on": socket.gethostname(),
        "snapshot": _to_snapshot(row or {}),
    }


def _to_snapshot(row: dict) -> dict:
    """Convert a df row dict to a JSON-serialisable snapshot."""
    snap = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            snap[k] = v.isoformat()
        elif v is None or isinstance(v, (str, int, float, bool)):
            snap[k] = v
        else:
            try:
                json.dumps(v)
                snap[k] = v
            except (TypeError, ValueError):
                snap[k] = str(v)
    return snap


def _from_snapshot(snap: dict) -> dict:
    """Restore datetime fields from ISO strings in a snapshot."""
    date_fields = ("am_time", "ar_time", "driver_checkin", "rest_until")
    row = dict(snap)
    for field in date_fields:
        v = row.get(field)
        if isinstance(v, str):
            try:
                row[field] = datetime.fromisoformat(v)
            except ValueError:
                row[field] = None
    return row


def get_stored_awbs() -> set:
    return set(_ledger.view("stored_awbs", lambda recs: frozenset(
        k for k, v in recs.items() if not v.get("deleted"))))


def is_stored(awb: str) -> bool:
    return awb in get_stored_awbs()


def mark_stored(awb: str, row: dict):
    set_stored(awb, row, True)


def mark_many_stored(rows_by_awb: dict[str, dict]) -> tuple[int, set]:
    """Store multiple AWBs in one locked write.

    Returns (newly_stored_count, all_stored_awbs_after_write).
    """
    now = datetime.now().isoformat()
    stored_count = 0
    with _ledger.edit() as txn:
        for awb, row in (rows_by_awb or {}).items():
            awb_text = str(awb or "").strip()
            if not awb_text or txn.is_live(awb_text):
                continue
            txn.put(awb_text, _new_entry(row, now))
            stored_count += 1
        stored_awbs = set(txn.live.keys())
    if stored_count:
        log.info("Betarolva kamionrol: %s tetel", stored_count)
    return stored_count, stored_awbs


def mark_unstored(awb: str):
    set_stored(awb, {}, False)


def mark_many_unstored(awbs) -> int:
    """Remove several AWBs in one locked write. Returns the count."""
    targets = {str(a or "").strip() for a in (awbs or ())} - {""}
    if not targets:
        return 0
    removed = 0
    with _ledger.edit() as txn:
        for awb in targets:
            if txn.delete(awb):
                removed += 1
    if removed:
        log.info("Visszarakva (kiadva): %s tetel", removed)
    return removed


def toggle_stored(awb: str, row: dict) -> tuple[bool, set]:
    """Toggle one AWB. Prefer set_stored: a toggle on a stale card can undo
    another user's change. Returns (is_now_stored, all_stored_awbs)."""
    with _ledger.edit() as txn:
        if txn.is_live(awb):
            txn.delete(awb)
            is_now_stored = False
        else:
            txn.put(awb, _new_entry(row))
            is_now_stored = True
        return is_now_stored, set(txn.live.keys())


def set_stored(awb: str, row: dict, stored: bool) -> tuple[bool, set]:
    """Idempotently put one AWB into the requested stored state.

    A click made on a stale card (another user already applied the same
    change) does not reverse that change.
    Returns (changed, all_stored_awbs_after_write).
    """
    changed = False
    with _ledger.edit() as txn:
        if stored and not txn.is_live(awb):
            txn.put(awb, _new_entry(row))
            changed = True
            log.info("Betarolva: %s", awb)
        elif not stored and txn.is_live(awb):
            txn.delete(awb)
            changed = True
            log.info("Visszarakva: %s", awb)
        return changed, set(txn.live.keys())


def get_snapshot_rows(exclude_awbs: set) -> list[dict]:
    """Return snapshot dicts for stored AWBs NOT in the live dataset."""
    result = []
    for awb, entry in load_stored().items():
        if awb not in exclude_awbs:
            snap = entry.get("snapshot", {})
            if snap:
                row = _from_snapshot(snap)
                row["is_stored"] = True
                row["_snapshot"] = True
                row["stored_at"] = entry.get("stored_at", "")
                row["stored_by"] = entry.get("stored_by", "")
                row["stored_on"] = entry.get("stored_on", "")
                result.append(row)
    return result
