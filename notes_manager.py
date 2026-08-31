"""
Shared per-AWB notes for multi-user use (INBOUND cards).

item_notes.shared.json format:
  {
    "<awb>": [
      {
        "id": "<short uuid>",
        "text": "<note text>",
        "by": "<username>",
        "on": "<computer>",
        "at": "<ISO datetime>"
      },
      ...
    ]
  }

Same shared-folder + lock mechanism as storage_manager (Betárolva).
"""
import json
import logging
import os
import socket
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from storage_manager import _shared_base_dir

log = logging.getLogger(__name__)

_LOCK_TIMEOUT_S = 15.0
_LOCK_POLL_S = 0.20
_LOCK_STALE_S = 120.0

MAX_NOTE_LEN = 280

# mtime-keyed read cache so per-render reads stay cheap
_cache: dict = {"mtime": None, "data": {}}


def _path() -> Path:
    return _shared_base_dir() / "item_notes.shared.json"


def _lock_path() -> Path:
    return _shared_base_dir() / "item_notes.shared.lock"


def get_notes_path() -> Path:
    return _path()


def get_notes_mtime() -> float:
    try:
        return _path().stat().st_mtime
    except OSError:
        return 0.0


def _read_unlocked() -> dict:
    p = _path()
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, list)}
            log.warning("item_notes shared file is not a dict, ignoring: %s", p)
        except Exception as exc:
            log.error("item_notes shared read error: %s", exc)
    return {}


def _atomic_write(data: dict):
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.stem + ".", suffix=".tmp", dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            f.flush()
        try:
            os.replace(tmp_name, target)
        except OSError:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
                f.flush()
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _acquire_lock(timeout_s: float = _LOCK_TIMEOUT_S):
    lock = _lock_path()
    deadline = time.time() + timeout_s
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "created_at": datetime.now().isoformat(),
                    "user": os.environ.get("USERNAME", "unknown"),
                    "host": socket.gethostname(),
                    "pid": os.getpid(),
                }, ensure_ascii=False))
            return
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
                if age > _LOCK_STALE_S:
                    log.warning("Removing stale notes lock: %s", lock)
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() >= deadline:
                raise TimeoutError(f"Shared notes lock timeout: {lock}")
            time.sleep(_LOCK_POLL_S)


def _release_lock():
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Shared notes lock release error: %s", exc)


@contextmanager
def _locked_data():
    _acquire_lock()
    try:
        data = _read_unlocked()
        yield data
        _atomic_write(data)
        _cache["mtime"] = None  # invalidate read cache after write
    finally:
        _release_lock()


def get_all_notes() -> dict:
    """All notes keyed by AWB. mtime-cached, safe per UI render."""
    mtime = get_notes_mtime()
    if _cache["mtime"] == mtime:
        return _cache["data"]
    data = _read_unlocked()
    _cache["mtime"] = mtime
    _cache["data"] = data
    return data


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
    with _locked_data() as data:
        data.setdefault(awb, []).append(note)
    log.info("Megjegyzés hozzáadva: %s (%s)", awb, note["id"])
    return note


def delete_note(awb: str, note_id: str) -> bool:
    """Remove one note by id. Returns True when something was deleted."""
    awb = str(awb or "").strip()
    note_id = str(note_id or "").strip()
    if not awb or not note_id:
        return False
    removed = False
    with _locked_data() as data:
        notes = data.get(awb)
        if isinstance(notes, list):
            kept = [n for n in notes if n.get("id") != note_id]
            if len(kept) != len(notes):
                removed = True
                if kept:
                    data[awb] = kept
                else:
                    del data[awb]
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
    removed = 0
    with _locked_data() as data:
        for awb in orphan:
            if awb in data:
                del data[awb]
                removed += 1
    if removed:
        log.info("Megjegyzés cleanup: %d lejárt AWB", removed)
    return removed
