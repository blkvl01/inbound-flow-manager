"""
Shared per-AWB notes for multi-user use (INBOUND cards).

Stored as a per-machine ledger (see shared_ledger.py), keyed by note id so two
machines adding or deleting notes at the same time never overwrite each other:

  item_notes.<HOST>.json  records: {"<note id>": {"awb": "...", "text": "...",
      "by": "<user>", "on": "<computer>", "at": "<ISO>", "ts": "...", "deleted": false}}

The frozen legacy ``item_notes.shared.json`` ({awb: [note, ...]}) is merged in.
"""
import logging
import os
import socket
import uuid
from datetime import datetime
from pathlib import Path

from shared_ledger import Ledger
from storage_manager import _shared_base_dir

log = logging.getLogger(__name__)

MAX_NOTE_LEN = 280
_NOTE_KEYS = ("id", "text", "by", "on", "at")


def _legacy_records(data: dict) -> dict:
    records = {}
    for awb, notes in data.items():
        if not isinstance(notes, list):
            continue
        for seq, note in enumerate(notes):
            if isinstance(note, dict) and note.get("id"):
                records[str(note["id"])] = {**note, "awb": str(awb), "deleted": False,
                                            "ts": note.get("at", ""), "seq": seq}
    return records


_ledger = Ledger("item_notes", lambda: _shared_base_dir(), _legacy_records)


def get_notes_path() -> Path:
    """This machine's ledger file."""
    return _ledger.own_path()


def state_signature() -> tuple:
    return _ledger.signature()


def get_notes_mtime() -> float:
    return max((entry[1] for entry in state_signature()[1:]), default=0) / 1e9


def compact() -> int:
    return _ledger.compact()


def get_all_notes() -> dict:
    """All live notes keyed by AWB, oldest first. Rebuilt only when any
    machine's notes file changes; treat the result as read-only."""
    return _ledger.view("by_awb", _group_by_awb)


def _group_by_awb(records: dict) -> dict:
    grouped: dict = {}
    for note_id, rec in records.items():
        if rec.get("deleted"):
            continue
        awb = str(rec.get("awb") or "")
        if not awb:
            continue
        # "at" has second resolution: order by creation ts, legacy list order.
        order = (str(rec.get("at") or ""), int(rec.get("seq") or 0), str(rec.get("created_ts") or rec.get("ts") or ""))
        note = {k: rec.get(k) for k in _NOTE_KEYS}
        note["id"] = note.get("id") or note_id
        grouped.setdefault(awb, []).append((order, note))
    return {awb: [n for _, n in sorted(items, key=lambda item: item[0])]
            for awb, items in grouped.items()}


def get_notes(awb: str) -> list[dict]:
    return list(get_all_notes().get(awb, []))


def add_note(awb: str, text: str) -> dict | None:
    """Append a note for an AWB. Returns the note dict, or None when invalid."""
    awb = str(awb or "").strip()
    text = str(text or "").strip()
    if not awb or not text:
        return None
    note = {
        "id": uuid.uuid4().hex[:10],
        "text": text[:MAX_NOTE_LEN],
        "by": os.environ.get("USERNAME", "unknown"),
        "on": socket.gethostname(),
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    with _ledger.edit() as txn:
        txn.put(note["id"], {**note, "awb": awb,
                             "created_ts": datetime.now().isoformat(timespec="microseconds")})
    log.info("Megjegyzés hozzáadva: %s (%s)", awb, note["id"])
    return note


def delete_note(awb: str, note_id: str) -> bool:
    """Remove one note by id. Returns True when something was deleted."""
    awb = str(awb or "").strip()
    note_id = str(note_id or "").strip()
    if not awb or not note_id:
        return False
    removed = False
    with _ledger.edit() as txn:
        rec = txn.live.get(note_id)
        if rec is not None and str(rec.get("awb") or "") == awb:
            removed = txn.delete(note_id)
    if removed:
        log.info("Megjegyzés törölve: %s (%s)", awb, note_id)
    return removed


_CLEANUP_MIN_AGE_H = 48


def _newest_note_age_hours(notes: list[dict]) -> float:
    newest = None
    for n in notes:
        try:
            at = datetime.fromisoformat(str(n.get("at", "")))
        except ValueError:
            continue
        if newest is None or at > newest:
            newest = at
    if newest is None:
        return float("inf")
    return (datetime.now() - newest).total_seconds() / 3600.0


def cleanup_missing(active_awbs: set[str], stored_awbs: set[str]) -> int:
    """Drop notes whose AWB is no longer active nor stored AND whose newest
    note is older than 48h — a transient Excel glitch can hide an AWB for one
    refresh, but past 48h it cannot re-enter the 24h inbound window.

    Called only from the background refresh (mirrors the stored-state
    cleanup policy: a UI render never mutates shared files).
    """
    keep = set(active_awbs or set()) | set(stored_awbs or set())
    if not keep:
        return 0
    current = get_all_notes()
    orphan = [
        awb for awb, notes in current.items()
        if awb not in keep and _newest_note_age_hours(notes) > _CLEANUP_MIN_AGE_H
    ]
    if not orphan:
        return 0
    orphan_set = set(orphan)
    removed_awbs = set()
    with _ledger.edit() as txn:
        for note_id, rec in list(txn.live.items()):
            awb = str(rec.get("awb", ""))
            if awb in orphan_set and txn.delete(note_id):
                removed_awbs.add(awb)
    removed = len(removed_awbs)
    if removed:
        log.info("Megjegyzés cleanup: %d lejárt AWB", removed)
    return removed
