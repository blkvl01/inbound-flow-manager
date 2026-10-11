"""
Safe read-for-write of the shared OneDrive JSON state files.

A shared file that exists but cannot be parsed (OneDrive can briefly expose an
incomplete replacement, or the file may really be corrupt) must never be
treated as an empty state inside a write: the write would replace every other
user's entries with only the current change. Instead the read is retried a few
times, then the unreadable file is preserved as a ``.corrupt.<ts>`` copy and
the write is refused with SharedStateUnreadableError.
"""
import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_READ_RETRIES = 4
_READ_RETRY_DELAY_S = 0.25


class SharedStateUnreadableError(RuntimeError):
    """The shared JSON exists but is not a readable JSON object."""


def _backup_unreadable(path: Path) -> None:
    try:
        backup = path.with_name(f"{path.stem}.corrupt.{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
        if not backup.exists():
            shutil.copy2(path, backup)
            log.error("Unreadable shared state preserved: %s", backup)
    except OSError as exc:
        log.warning("Unreadable shared state backup failed (%s): %s", path, exc)


def read_dict_for_write(path: Path, label: str,
                        retries: int = _READ_RETRIES,
                        delay_s: float = _READ_RETRY_DELAY_S) -> dict:
    """Return the shared JSON object at ``path`` for a locked read-modify-write.

    A missing file is the supported empty state. An existing but unreadable
    file raises SharedStateUnreadableError after ``retries`` attempts.
    """
    path = Path(path)
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            last_error = ValueError(f"{label}: JSON root is {type(data).__name__}, not an object")
        except (OSError, ValueError) as exc:
            last_error = exc
        if attempt + 1 < retries:
            time.sleep(delay_s)
    _backup_unreadable(path)
    raise SharedStateUnreadableError(f"{label} közös fájl nem olvasható, a mentés elmaradt: {last_error}")
