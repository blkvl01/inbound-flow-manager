"""Local source launch and short-lived change watching; never writes workbooks."""
import os
import threading
import time
from urllib.parse import urlsplit


def is_local_request(request):
    try:
        host = urlsplit(request.host_url)
        if request.remote_addr not in {"127.0.0.1", "::1"} or host.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return False
        origin = request.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if (parsed.scheme, parsed.netloc) != (host.scheme, host.netloc):
                return False
    except ValueError:
        return False
    return request.headers.get("Sec-Fetch-Site") not in {"cross-site"}


def signature(path):
    try:
        stat = os.stat(path)
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


class SourceOpener:
    def __init__(self):
        self._lock = threading.Lock()
        self._active = set()

    def open(self, kind, path, refresh, current_path, launch=None):
        baseline = signature(path)
        if baseline is None or not os.path.isfile(path):
            return False, "A fájl nem található. Válaszd ki a Beállításokban."
        if not str(path).lower().endswith((".xlsb", ".xlsm", ".xlsx", ".xls")):
            return False, "Csak Excel fájl nyitható meg."
        try:
            (launch or os.startfile)(path)
        except (OSError, AttributeError):
            return False, "A fájl megnyitása nem sikerült. Ellenőrizd az Excel telepítését."
        watch_key = (kind, path)
        with self._lock:
            if watch_key in self._active:
                return True, "Megnyitva"
            self._active.add(watch_key)
        threading.Thread(target=self._watch, args=(kind, path, baseline, refresh, current_path),
                         daemon=True, name=f"SourceOpen-{kind}").start()
        return True, "Megnyitva"

    def _watch(self, kind, path, baseline, refresh, current_path, timeout=180, interval=2):
        deadline = time.monotonic() + timeout
        previous = baseline
        try:
            while time.monotonic() < deadline:
                time.sleep(interval)
                if current_path() != path:
                    return
                observed = signature(path)
                # Two consecutive equal signatures allow Excel/OneDrive to finish
                # replacing the file before a single requested background read.
                if observed is not None and observed != baseline and observed == previous:
                    refresh()
                    return
                previous = observed
        finally:
            with self._lock:
                self._active.discard((kind, path))


opener = SourceOpener()
