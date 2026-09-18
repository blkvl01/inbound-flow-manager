"""
Shared activity log for the developer menu.

Daily JSONL files directly under <shared>/activity_YYYY-MM-DD.jsonl;
one JSON object per line:
  {"ts": "<ISO>", "user": "...", "host": "...", "action": "...", "detail": {...}}

Design constraints:
  - Logging must NEVER block or break the UI: short lock timeout, every
    failure is swallowed (the event is dropped, not retried).
  - Multiple machines append to the same file over a shared folder, so the
    append happens under the same lock-file mechanism the other shared
    state uses.
  - Retention: files older than RETENTION_DAYS are deleted on startup.
"""
import json
import logging
import os
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path

from storage_manager import _shared_base_dir

log = logging.getLogger(__name__)

RETENTION_DAYS = 30
_LOCK_TIMEOUT_S = 2.0
_LOCK_POLL_S = 0.05
_LOCK_STALE_S = 30.0
_FILE_PREFIX = "activity_"

_session_started_at = time.time()


def _logs_dir() -> Path:
    # Use the existing operational workspace directly. The app must not create
    # a project-owned activity-log subdirectory in OneDrive.
    return _shared_base_dir()


def _day_file(day: str | None = None) -> Path:
    day = day or datetime.now().strftime("%Y-%m-%d")
    return _logs_dir() / f"{_FILE_PREFIX}{day}.jsonl"


def _lock_path() -> Path:
    return _logs_dir() / "activity.lock"


def _try_acquire_lock() -> bool:
    lock = _lock_path()
    deadline = time.time() + _LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _LOCK_STALE_S:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            except OSError:
                return False
            if time.time() >= deadline:
                return False
            time.sleep(_LOCK_POLL_S)
        except OSError:
            return False


def _release_lock():
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError:
        pass


def log_event(action: str, detail=None):
    """Append one event. Never raises; drops the event on any failure."""
    try:
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "user": os.environ.get("USERNAME", "unknown"),
            "host": socket.gethostname(),
            "action": str(action or "")[:64],
        }
        if detail is not None:
            try:
                json.dumps(detail, ensure_ascii=False)
                entry["detail"] = detail
            except (TypeError, ValueError):
                entry["detail"] = str(detail)[:500]
        line = json.dumps(entry, ensure_ascii=False, default=str)
        if not _try_acquire_lock():
            return
        try:
            with open(_day_file(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        finally:
            _release_lock()
    except Exception as exc:  # logging must never break the app
        log.debug("activity log drop: %s", exc)


def log_session_end(reason: str):
    """Convenience for shutdown paths: include the session duration."""
    minutes = (time.time() - _session_started_at) / 60.0
    log_event("app_stop", {"reason": reason, "session_minutes": round(minutes, 1)})


def list_log_days() -> list[str]:
    """Available log dates, newest first."""
    days = []
    try:
        for p in _logs_dir().glob(f"{_FILE_PREFIX}*.jsonl"):
            day = p.stem[len(_FILE_PREFIX):]
            if len(day) == 10:
                days.append(day)
    except OSError:
        pass
    return sorted(days, reverse=True)


def read_events(day: str, user: str = "", search: str = "", limit: int = 500) -> list[dict]:
    """Read one day's events (newest first), optionally filtered."""
    path = _day_file(day)
    if not path.exists():
        return []
    user = (user or "").strip().lower()
    search = (search or "").strip().lower()
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    continue
                if user and str(e.get("user", "")).lower() != user:
                    continue
                if search and search not in line.lower():
                    continue
                events.append(e)
    except OSError as exc:
        log.warning("activity log read error: %s", exc)
    events.reverse()
    return events[:limit]


def list_users(day: str) -> list[str]:
    """Distinct users seen on one day."""
    seen = []
    for e in read_events(day, limit=100000):
        u = str(e.get("user", "")).strip()
        if u and u not in seen:
            seen.append(u)
    return sorted(seen)


def cleanup_old(days: int = RETENTION_DAYS):
    """Delete log files older than the retention window. Never raises."""
    try:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        for p in _logs_dir().glob(f"{_FILE_PREFIX}*.jsonl"):
            day = p.stem[len(_FILE_PREFIX):]
            if len(day) == 10 and day < cutoff:
                try:
                    p.unlink()
                    log.info("Activity log removed (retention): %s", p.name)
                except OSError:
                    pass
    except Exception as exc:
        log.debug("activity log cleanup skipped: %s", exc)
