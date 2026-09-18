"""
Shared "Betarolva" state for multi-user use.

stored_awbs.shared.json format:
  {
    "<awb>": {
      "stored_at": "<ISO datetime>",
      "stored_by": "<DOMAIN\\user or username>",
      "stored_on": "<computer>",
      "snapshot": { ...row dict... }
    }
  }

The file is stored in a shared writable folder. The frozen one-file release
automatically uses the discovered OneDrive workspace; a launcher may override
this with FLOW_SHARED_STATE_DIR.
"""
import json
import logging
import os
import socket
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_LOCK_TIMEOUT_S = 15.0
_LOCK_POLL_S = 0.20
_LOCK_STALE_S = 120.0


def _shared_base_dir() -> Path:
    # Launcher sets FLOW_SHARED_STATE_DIR to the SharePoint folder so the
    # shared JSON lives there even when the exe runs from a local TEMP copy.
    env_override = os.environ.get("FLOW_SHARED_STATE_DIR", "").strip()
    if env_override:
        shared_dir = Path(env_override)
        if shared_dir.is_dir():
            return shared_dir
        log.warning("FLOW_SHARED_STATE_DIR unavailable (%s), falling back to exe folder", shared_dir)

    try:
        from config import read_config_snapshot

        configured = str(read_config_snapshot().get("shared_state_dir") or "").strip()
        if configured:
            shared_dir = Path(configured)
            if shared_dir.is_dir():
                return shared_dir
            log.warning("Configured shared-state directory unavailable (%s), using automatic discovery", shared_dir)
    except (OSError, ImportError, ValueError, TypeError) as exc:
        log.warning("Configured shared-state discovery failed: %s", exc)

    # The one-file release is intentionally started from a user-writable local
    # folder, not from the shared OneDrive workspace.  Keep the multi-user
    # state in the same discovered OneDrive workspace automatically when the
    # frozen EXE is used and no explicit override was supplied.
    if getattr(sys, "frozen", False):
        try:
            from config import _find_onedrive_folder

            onedrive_folder = Path(_find_onedrive_folder())
            if onedrive_folder.is_dir():
                # Use the existing operational workspace directly. Do not
                # create a project-owned OneDrive subdirectory.
                return onedrive_folder
        except (OSError, ImportError) as exc:
            log.warning("Automatic OneDrive shared-state discovery failed: %s", exc)

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent

    # No project-owned fallback directory is created. The executable/source
    # directory already exists and the shared files are written directly there.
    return base


def _path() -> Path:
    return _shared_base_dir() / "stored_awbs.shared.json"


def _lock_path() -> Path:
    return _shared_base_dir() / "stored_awbs.shared.lock"


def get_storage_path() -> Path:
    return _path()


def get_storage_mtime() -> float:
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
                return data
            log.warning("stored_awbs shared file is not a dict, ignoring: %s", p)
        except Exception as exc:
            log.error("stored_awbs shared read error: %s", exc)
    return {}


def load_stored() -> dict:
    return _read_unlocked()


def _atomic_write(data: dict):
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=target.stem + ".",
        suffix=".tmp",
        dir=str(target.parent),
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
                f.write(
                    json.dumps(
                        {
                            "created_at": datetime.now().isoformat(),
                            "user": os.environ.get("USERNAME", "unknown"),
                            "host": socket.gethostname(),
                            "pid": os.getpid(),
                        },
                        ensure_ascii=False,
                    )
                )
            return
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
                if age > _LOCK_STALE_S:
                    log.warning("Removing stale shared lock: %s", lock)
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue

            if time.time() >= deadline:
                raise TimeoutError(f"Shared storage lock timeout: {lock}")
            time.sleep(_LOCK_POLL_S)


def _release_lock():
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Shared storage lock release error: %s", exc)


@contextmanager
def _locked_data():
    _acquire_lock()
    try:
        data = _read_unlocked()
        yield data
        _atomic_write(data)
    finally:
        _release_lock()


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
    return set(load_stored().keys())


def is_stored(awb: str) -> bool:
    return awb in load_stored()


def mark_stored(awb: str, row: dict):
    with _locked_data() as data:
        data[awb] = {
            "stored_at": datetime.now().isoformat(),
            "stored_by": os.environ.get("USERNAME", "unknown"),
            "stored_on": socket.gethostname(),
            "snapshot": _to_snapshot(row),
        }
    log.info("Betarolva: %s", awb)


def mark_many_stored(rows_by_awb: dict[str, dict]) -> tuple[int, set]:
    """Store multiple AWBs in one locked shared-state write.

    Returns (newly_stored_count, all_stored_awbs_after_write).
    """
    now = datetime.now().isoformat()
    user = os.environ.get("USERNAME", "unknown")
    host = socket.gethostname()
    stored_count = 0
    with _locked_data() as data:
        for awb, row in (rows_by_awb or {}).items():
            awb_text = str(awb or "").strip()
            if not awb_text or awb_text in data:
                continue
            data[awb_text] = {
                "stored_at": now,
                "stored_by": user,
                "stored_on": host,
                "snapshot": _to_snapshot(row or {}),
            }
            stored_count += 1
        stored_awbs = set(data.keys())
    if stored_count:
        log.info("Betarolva kamionrol: %s tetel", stored_count)
    return stored_count, stored_awbs


def mark_unstored(awb: str):
    removed = False
    with _locked_data() as data:
        if awb in data:
            del data[awb]
            removed = True
    if removed:
        log.info("Visszarakva: %s", awb)


def toggle_stored(awb: str, row: dict) -> tuple[bool, set]:
    """Toggle one AWB in a single locked read/write cycle.

    Returns (is_now_stored, all_stored_awbs_after_write).
    """
    with _locked_data() as data:
        if awb in data:
            del data[awb]
            is_now_stored = False
            log.info("Visszarakva: %s", awb)
        else:
            data[awb] = {
                "stored_at": datetime.now().isoformat(),
                "stored_by": os.environ.get("USERNAME", "unknown"),
                "stored_on": socket.gethostname(),
                "snapshot": _to_snapshot(row),
            }
            is_now_stored = True
            log.info("Betarolva: %s", awb)
        return is_now_stored, set(data.keys())


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
