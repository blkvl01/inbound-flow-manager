"""
Per-machine shared state ledger (Betárolva, notes, dev overrides).

Why: the shared folder is a OneDrive-synced local folder. A lock file there only
excludes processes on the SAME machine, and rewriting one common JSON from two
machines loses one side's change (last synced writer wins) or produces
"-<PC>" conflict copies.  So every machine writes ONLY its own file:

    <name>.<HOST>.json   {"version": 1, "host": "...", "records": {key: record}}

Every record carries "ts" (ISO) and "deleted" (tombstone).  Readers merge all
machine files plus the frozen legacy ``<name>.shared.json`` and keep, per key,
the record with the newest ts (host name breaks ties).  Two machines never
write the same file, so no write can be lost.

A write always stamps ts strictly newer than the current winner for that key,
so a sequential action is never hidden by a skewed clock on another machine.

Compaction (own file only) drops records another source already supersedes,
and old tombstones once no other source still holds that key, so the files
stay bounded without ever resurrecting a deleted entry.
"""
import json
import logging
import os
import re
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from shared_json import read_dict_for_write

log = logging.getLogger(__name__)

_LOCK_TIMEOUT_S = 15.0
_LOCK_POLL_S = 0.20
_LOCK_STALE_S = 120.0
_FORMAT_VERSION = 1
_TOMBSTONE_RETENTION = timedelta(days=14)


def _host() -> str:
    raw = os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown"
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw)[:64] or "unknown"


def _parse_ts(value) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.min
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _rank(record: dict, source: str) -> tuple:
    return (_parse_ts(record.get("ts")), source)


class Ledger:
    """One logical shared dataset, stored as one file per machine."""

    def __init__(self, name: str, base_dir: Callable[[], Path],
                 legacy_to_records: Callable[[dict], dict]):
        self.name = name
        self._base_dir = base_dir
        self._legacy_to_records = legacy_to_records
        self._mutex = threading.RLock()
        # path -> ((mtime_ns, size), records): last good parse per source file,
        # so a half-synced OneDrive file never blanks another machine's entries.
        self._file_cache: dict[str, tuple[tuple, dict]] = {}
        self._merged_sig = None
        self._merged: dict = {}

    # ── paths ──────────────────────────────────────────────────────────────
    def own_path(self) -> Path:
        return self._base_dir() / f"{self.name}.{_host()}.json"

    def legacy_path(self) -> Path:
        return self._base_dir() / f"{self.name}.shared.json"

    def lock_path(self) -> Path:
        return self._base_dir() / f"{self.name}.{_host()}.lock"

    def source_paths(self) -> list[Path]:
        base = self._base_dir()
        paths = []
        legacy = self.legacy_path()
        if legacy.exists():
            paths.append(legacy)
        pattern = re.compile(rf"^{re.escape(self.name)}\.[A-Za-z0-9_-]+\.json$")
        try:
            for p in sorted(base.glob(f"{self.name}.*.json")):
                if p != legacy and pattern.match(p.name):
                    paths.append(p)
        except OSError as exc:
            log.warning("%s ledger listing failed: %s", self.name, exc)
        return paths

    def signature(self) -> tuple:
        """Changes whenever any machine's file (or the legacy file) changes."""
        sig = [str(self._base_dir())]
        for p in self.source_paths():
            try:
                st = p.stat()
                sig.append((p.name, st.st_mtime_ns, st.st_size))
            except OSError:
                continue
        return tuple(sig)

    # ── reading ────────────────────────────────────────────────────────────
    def _records_of(self, path: Path, data: dict) -> dict:
        if path == self.legacy_path():
            try:
                return self._legacy_to_records(data) or {}
            except Exception as exc:  # malformed legacy content must not break reads
                log.warning("%s legacy conversion failed: %s", self.name, exc)
                return {}
        records = data.get("records")
        return {k: v for k, v in records.items() if isinstance(v, dict)} if isinstance(records, dict) else {}

    def _read_source(self, path: Path) -> dict:
        key = str(path)
        try:
            st = path.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._file_cache.pop(key, None)
            return {}
        cached = self._file_cache.get(key)
        if cached and cached[0] == sig:
            return cached[1]
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("JSON root is not an object")
        except (OSError, ValueError) as exc:
            # Keep the last good parse of this machine's file (partial sync).
            log.warning("%s ledger source unreadable, using last good copy (%s): %s",
                        self.name, path.name, exc)
            return cached[1] if cached else {}
        records = self._records_of(path, data)
        self._file_cache[key] = (sig, records)
        return records

    def _merge(self, sources: list[tuple[str, dict]]) -> dict:
        winners: dict = {}
        ranks: dict = {}
        for source, records in sources:
            for k, rec in records.items():
                r = _rank(rec, source)
                if k not in ranks or r > ranks[k]:
                    ranks[k] = r
                    winners[k] = rec
        return winners

    def records(self) -> dict:
        """Merged winner per key, tombstones included (callers filter)."""
        with self._mutex:
            sig = self.signature()
            if sig == self._merged_sig:
                return self._merged
            sources = [(p.name, self._read_source(p)) for p in self.source_paths()]
            self._merged = self._merge(sources)
            self._merged_sig = sig
            return self._merged

    def live(self) -> dict:
        return {k: v for k, v in self.records().items() if not v.get("deleted")}

    # ── writing (own file only) ────────────────────────────────────────────
    def _acquire_lock(self):
        lock = self.lock_path()
        deadline = time.time() + _LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(json.dumps({"created_at": datetime.now().isoformat(),
                                        "pid": os.getpid()}))
                return
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > _LOCK_STALE_S:
                        log.warning("Removing stale %s lock: %s", self.name, lock)
                        lock.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() >= deadline:
                    raise TimeoutError(f"{self.name} lock timeout: {lock}")
                time.sleep(_LOCK_POLL_S)

    def _release_lock(self):
        try:
            self.lock_path().unlink(missing_ok=True)
        except OSError as exc:
            log.warning("%s lock release error: %s", self.name, exc)

    def _write_own(self, records: dict):
        target = self.own_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _FORMAT_VERSION, "host": _host(), "records": records}
        fd, tmp_name = tempfile.mkstemp(prefix=target.stem + ".", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=1, ensure_ascii=False, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, target)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass

    @contextmanager
    def edit(self):
        """Locked read-modify-write of this machine's file.

        Yields a Txn whose ``live`` view is the merged state of all machines;
        ``put``/``delete`` record changes into this machine's file only.
        """
        with self._mutex:
            self._acquire_lock()
            try:
                own_path = self.own_path()
                own_raw = read_dict_for_write(own_path, self.name)
                own = self._records_of(own_path, own_raw)
                others = [(p.name, self._read_source(p)) for p in self.source_paths() if p != own_path]
                txn = _Txn(self._merge(others + [(own_path.name, own)]), own)
                yield txn
                if txn.dirty:
                    self._write_own(own)
                    self._merged_sig = None
            finally:
                self._release_lock()

    def compact(self, now: datetime | None = None) -> int:
        """Shrink this machine's file without changing the merged result."""
        now = now or datetime.now()
        removed = 0
        with self._mutex:
            self._acquire_lock()
            try:
                own_path = self.own_path()
                if not own_path.exists():
                    return 0
                own = self._records_of(own_path, read_dict_for_write(own_path, self.name))
                other_best: dict = {}
                for src, recs in (
                        (p.name, self._read_source(p)) for p in self.source_paths() if p != own_path):
                    for k, v in recs.items():
                        r = _rank(v, src)
                        if k not in other_best or r > other_best[k]:
                            other_best[k] = r
                for k in list(own):
                    rec = own[k]
                    if k in other_best and other_best[k] > _rank(rec, own_path.name):
                        del own[k]  # superseded elsewhere: dropping it changes nothing
                        removed += 1
                    elif (rec.get("deleted") and k not in other_best
                          and now - _parse_ts(rec.get("ts")) > _TOMBSTONE_RETENTION):
                        del own[k]  # nothing left to hide (retention covers sync lag)
                        removed += 1
                if removed:
                    self._write_own(own)
                    self._merged_sig = None
                    log.info("%s ledger compacted: %d record", self.name, removed)
            finally:
                self._release_lock()
        return removed


class _Txn:
    def __init__(self, merged: dict, own: dict):
        self._merged = merged
        self._own = own
        self.dirty = False

    @property
    def live(self) -> dict:
        return {k: v for k, v in self._merged.items() if not v.get("deleted")}

    def is_live(self, key: str) -> bool:
        rec = self._merged.get(key)
        return rec is not None and not rec.get("deleted")

    def _next_ts(self, key) -> str:
        now = datetime.now()
        current = self._merged.get(key)
        if current is not None:
            floor = _parse_ts(current.get("ts")) + timedelta(microseconds=1)
            if floor > now:
                now = floor
        return now.isoformat(timespec="microseconds")

    def put(self, key: str, record: dict):
        rec = dict(record)
        rec["deleted"] = False
        rec["ts"] = self._next_ts(key)
        self._own[key] = rec
        self._merged[key] = rec
        self.dirty = True

    def delete(self, key: str):
        if not self.is_live(key):
            return False
        tomb = {"deleted": True, "ts": self._next_ts(key)}
        self._own[key] = tomb
        self._merged[key] = tomb
        self.dirty = True
        return True
