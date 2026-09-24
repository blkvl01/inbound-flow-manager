"""Small, best-effort technical status report for Program HUB.

No business records or exception text leave the application. Reporting never
creates the company OneDrive root and never blocks application startup.
"""

from __future__ import annotations

import atexit
import getpass
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
from datetime import datetime, timezone


_RELATIVE = Path("Ecommerce - Dokumentumok") / "Program HUB"
_INTERVAL = 120
_reporter = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _root() -> Path | None:
    override = os.environ.get("PROGRAM_HUB_SHARED_ROOT")
    if override and Path(override).is_dir():
        return Path(override)
    candidates = [os.environ.get("OneDriveCommercial"), os.environ.get("OneDrive")]
    profile = os.environ.get("USERPROFILE")
    if profile:
        candidates.append(str(Path(profile) / "OneDrive - HGL Group Hungary Kft"))
    for candidate in candidates:
        if candidate:
            root = Path(candidate) / _RELATIVE
            if root.is_dir():
                return root
    return None


def _identity() -> tuple[str, str, str]:
    machine, user = socket.gethostname(), getpass.getuser()
    key = hashlib.sha256(f"{machine}\0{user}".lower().encode()).hexdigest()[:24]
    return machine, user, key


def _error_code(error: BaseException | str) -> str:
    text = str(error).lower()
    if any(word in text for word in ("permission", "access denied", "hozzáfér")):
        return "permission"
    if any(word in text for word in ("timed out", "timeout", "connection", "network", "hálózat")):
        return "network"
    if any(word in text for word in ("disk full", "no space", "tárhely")):
        return "disk"
    return "unknown"


class Reporter:
    def __init__(self, app_id: str, version: str) -> None:
        self.app_id = app_id
        self.version = str(version)
        self.machine, self.user, self.key = _identity()
        self.state = "running"
        self.events: list[dict[str, str]] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._heartbeat, name="ProgramHubStatus", daemon=True)
        target = self._target()
        if target and target.exists():
            try:
                old = json.loads(target.read_text(encoding="utf-8"))
                if old.get("key") == self.key and old.get("appId") == self.app_id:
                    self.events = [item for item in old.get("events", []) if isinstance(item, dict)][-19:]
            except (OSError, ValueError, TypeError):
                pass
        self.event("started")
        self._thread.start()
        atexit.register(self.stop)

    def _target(self) -> Path | None:
        root = _root()
        return root / "fleet" / "app-reports" / self.app_id / f"{self.key}.json" if root else None

    def _write(self) -> None:
        target = self._target()
        if target is None:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = {"schema": 1, "key": self.key, "appId": self.app_id,
                    "machine": self.machine, "user": self.user, "version": self.version,
                    "observedAt": _now(), "state": self.state, "events": self.events[-20:]}
            temporary = target.with_name(f".{self.key}.{os.getpid()}.tmp")
            try:
                temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass

    def _heartbeat(self) -> None:
        while not self._stop.wait(_INTERVAL):
            with self._lock:
                self._write()

    def event(self, kind: str, error: BaseException | str | None = None) -> None:
        if kind not in {"started", "stopped", "error", "update_started", "update_ready", "update_failed"}:
            return
        with self._lock:
            item = {"at": _now(), "type": kind}
            if error is not None:
                item["code"] = _error_code(error)
            self.events.append(item)
            self.events = self.events[-20:]
            self._write()

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self.state = "stopped"
        self.event("stopped")


def start(app_id: str, version: str) -> Reporter:
    global _reporter
    if _reporter is None:
        _reporter = Reporter(app_id, version)
    return _reporter


def event(kind: str, error: BaseException | str | None = None) -> None:
    if _reporter is not None:
        _reporter.event(kind, error)


def stop() -> None:
    if _reporter is not None:
        _reporter.stop()
