"""Small, dependency-free GitHub Releases updater for the frozen Flow Manager.

The updater only runs automatically in a PyInstaller executable.  It is kept
independent from Dash so the one-file executable can use it before any
application data is touched.  A staged download is checked twice: once in the
parent process and again by the short-lived helper immediately before the
atomic replacement.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from version import APP_NAME, APP_VERSION, GITHUB_REPOSITORY, PACKAGE_NAME


MANIFEST_NAME = "manifest.json"
HELPER_ARG = "--flow-manager-update-helper"
CLEANUP_HELPER_ARG = "--flow-manager-cleanup-helper"
API_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
_USER_AGENT = "Inbound-Flow-Manager-Updater/1"
_CHUNK_SIZE = 256 * 1024
_DOWNLOAD_READ_TIMEOUT_S = 30.0
_DOWNLOAD_DEADLINE_S = 30 * 60.0
_DOWNLOAD_ATTEMPTS = 3
_STALE_STAGE_AGE_S = 2 * 60 * 60.0
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_HELPER_NAME_RE = re.compile(r"^FlowManager-update-helper-\d+-[0-9a-f]{12}\.exe$")
_SEMVER_RE = re.compile(
    r"^v?(?P<core>\d+(?:\.\d+)*)(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)

_status_lock = threading.Lock()
_status: dict[str, Any] = {
    "phase": "idle",
    "message": "",
    "progress": 0,
    "version": APP_VERSION,
    "available_version": "",
}


class UpdateError(RuntimeError):
    """Raised when a release cannot be trusted or downloaded."""


def _set_status(phase: str, message: str, progress: int = 0, **extra: Any) -> None:
    with _status_lock:
        _status.update(
            phase=str(phase),
            message=str(message),
            progress=max(0, min(100, int(progress))),
            **extra,
        )


def get_status() -> dict[str, Any]:
    """Return a copy suitable for the Dash loading callback."""
    with _status_lock:
        return dict(_status)


def _version_key(value: str) -> tuple[tuple[int, ...], tuple[tuple[int, Any], ...], bool]:
    text = str(value or "").strip()
    match = _SEMVER_RE.fullmatch(text)
    if not match:
        raise UpdateError(f"Érvénytelen verzió: {value!r}")
    core = tuple(int(part) for part in match.group("core").split("."))
    pre = match.group("pre")
    if not pre:
        return core, (), True
    identifiers: list[tuple[int, Any]] = []
    for part in pre.split("."):
        if part.isdigit():
            identifiers.append((0, int(part)))
        else:
            identifiers.append((1, part.casefold()))
    return core, tuple(identifiers), False


def compare_versions(left: str, right: str) -> int:
    """Compare SemVer-like versions, returning -1, 0, or 1.

    A leading ``v`` and trailing build metadata are accepted.  Missing numeric
    components are treated as zero (``1.2 == 1.2.0``); a stable release is
    newer than its prerelease.
    """
    left_core, left_pre, left_stable = _version_key(left)
    right_core, right_pre, right_stable = _version_key(right)
    width = max(len(left_core), len(right_core))
    lc = left_core + (0,) * (width - len(left_core))
    rc = right_core + (0,) * (width - len(right_core))
    if lc != rc:
        return 1 if lc > rc else -1
    if left_stable != right_stable:
        return 1 if left_stable else -1
    if left_pre == right_pre:
        return 0
    if not left_pre or not right_pre:
        return 1 if not left_pre else -1
    for left_item, right_item in zip(left_pre, right_pre):
        if left_item != right_item:
            return 1 if left_item > right_item else -1
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


def _manifest_package(manifest: dict[str, Any]) -> dict[str, Any]:
    package = manifest.get("package")
    if not isinstance(package, dict):
        raise UpdateError("A manifest package mezője hiányzik vagy hibás.")
    name = str(package.get("name") or "").strip()
    if name != PACKAGE_NAME:
        raise UpdateError(f"A manifest csomagneve nem {PACKAGE_NAME!r}.")
    try:
        size = int(package.get("size"))
    except (TypeError, ValueError) as exc:
        raise UpdateError("A manifest mérete nem egész szám.") from exc
    digest = str(package.get("sha256") or "").strip().lower()
    if size < 1 or not _HEX64_RE.fullmatch(digest):
        raise UpdateError("A manifest mérete vagy SHA-256 értéke hibás.")
    return {"name": name, "size": size, "sha256": digest}


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate and normalize the release manifest's trusted fields."""
    if not isinstance(manifest, dict):
        raise UpdateError("A manifest nem JSON objektum.")
    app_name = str(manifest.get("app_name") or "").strip()
    if app_name != APP_NAME:
        raise UpdateError("A manifest alkalmazásneve nem egyezik.")
    version = str(manifest.get("version") or "").strip()
    _version_key(version)
    package = _manifest_package(manifest)
    return {"app_name": app_name, "version": version, "package": package}


def parse_release_payload(payload: Any) -> dict[str, Any]:
    """Extract a verified manifest and executable asset from GitHub JSON."""
    if not isinstance(payload, dict):
        raise UpdateError("A GitHub válasza nem objektum.")
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("A GitHub release válaszából hiányzik a tag.")
    try:
        release_version = tag[1:] if tag.lower().startswith("v") else tag
        _version_key(release_version)
    except UpdateError as exc:
        raise UpdateError("A GitHub release tagje hibás.") from exc
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("A GitHub release asset-listája hiányzik.")
    by_name = {
        str(asset.get("name") or "").strip(): asset
        for asset in assets
        if isinstance(asset, dict)
    }
    manifest_asset = by_name.get(MANIFEST_NAME)
    if not manifest_asset:
        raise UpdateError("A releaseből hiányzik a manifest.json.")
    manifest_url = str(manifest_asset.get("browser_download_url") or "").strip()
    if not manifest_url.startswith("https://"):
        raise UpdateError("A manifest letöltési címe nem biztonságos HTTPS URL.")
    raw_manifest = payload.get("_manifest")
    if raw_manifest is None:
        raise UpdateError("A release payloadhoz nem tartozik beolvasott manifest.")
    manifest = validate_manifest(raw_manifest)
    if compare_versions(manifest["version"], release_version) != 0:
        raise UpdateError("A manifest verziója nem egyezik a release tagjével.")
    package_asset = by_name.get(manifest["package"]["name"])
    if not package_asset:
        raise UpdateError("A releaseből hiányzik a manifestben megadott EXE.")
    download_url = str(package_asset.get("browser_download_url") or "").strip()
    if not download_url.startswith("https://"):
        raise UpdateError("Az EXE letöltési címe nem biztonságos HTTPS URL.")
    github_size = package_asset.get("size")
    if github_size is not None:
        try:
            size_matches = int(github_size) == manifest["package"]["size"]
        except (TypeError, ValueError) as exc:
            raise UpdateError("A GitHub asset mérete hibás.") from exc
        if not size_matches:
            raise UpdateError("A GitHub asset mérete eltér a manifesttől.")
    return {
        "tag": tag,
        "version": manifest["version"],
        "manifest": manifest,
        "download_url": download_url,
        "manifest_url": manifest_url,
    }


def _request_json(url: str, opener: Callable[..., Any] | None = None) -> Any:
    token = os.environ.get("FLOW_MANAGER_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    open_fn = opener or urllib.request.urlopen
    with open_fn(request, timeout=20) as response:
        raw = response.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("A GitHub válasza nem érvényes JSON.") from exc


def _load_release(opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    payload = _request_json(API_URL, opener)
    manifest_url = None
    if isinstance(payload, dict):
        for asset in payload.get("assets") or []:
            if isinstance(asset, dict) and asset.get("name") == MANIFEST_NAME:
                manifest_url = str(asset.get("browser_download_url") or "")
                break
    if not manifest_url or not manifest_url.startswith("https://"):
        raise UpdateError("A release manifest assetje nem érhető el.")
    manifest = _request_json(manifest_url, opener)
    payload = dict(payload) if isinstance(payload, dict) else payload
    if isinstance(payload, dict):
        payload["_manifest"] = manifest
    return parse_release_payload(payload)


def check_for_update(
    current_version: str = APP_VERSION,
    *,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any] | None:
    """Return release metadata only when it is strictly newer."""
    release = _load_release(opener)
    if compare_versions(release["version"], current_version) <= 0:
        return None
    return release


def _hash_file(path: Path, progress: Callable[[int], None] | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            if progress:
                progress(total)
    return total, digest.hexdigest()


def verify_file(path: str | os.PathLike[str], expected_size: int, expected_sha256: str) -> bool:
    target = Path(path)
    if not target.is_file():
        return False
    size, digest = _hash_file(target)
    return size == int(expected_size) and digest == str(expected_sha256).lower()


def _cleanup_stale_stages(target_dir: Path) -> None:
    """Remove abandoned update stages without touching a recent download."""
    cutoff = time.time() - _STALE_STAGE_AGE_S
    for stage in target_dir.glob(".FlowManager-update-*.tmp"):
        try:
            if stage.stat().st_mtime < cutoff:
                stage.unlink()
        except OSError:
            # OneDrive may briefly hold an old stage; it is safe to leave it
            # for the next startup rather than fail the actual update.
            continue


def _download_to_stage(release: dict[str, Any], target_dir: Path, opener: Callable[..., Any] | None = None) -> Path:
    package = release["manifest"]["package"]
    if not target_dir.is_dir():
        raise UpdateError("A futó EXE mappája nem érhető el.")
    _cleanup_stale_stages(target_dir)

    last_error: Exception | None = None
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        fd, raw_path = tempfile.mkstemp(prefix=".FlowManager-update-", suffix=".tmp", dir=target_dir)
        os.close(fd)
        stage = Path(raw_path)
        started = time.monotonic()
        keep_stage = False
        try:
            request = urllib.request.Request(
                release["download_url"],
                headers={
                    "Accept": "application/octet-stream",
                    "User-Agent": _USER_AGENT,
                    **({"Authorization": f"Bearer {os.environ.get('FLOW_MANAGER_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN')}"}
                       if (os.environ.get("FLOW_MANAGER_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")) else {}),
                },
            )
            open_fn = opener or urllib.request.urlopen
            with open_fn(request, timeout=_DOWNLOAD_READ_TIMEOUT_S) as response, stage.open("wb") as stream:
                downloaded = 0
                while True:
                    if time.monotonic() - started >= _DOWNLOAD_DEADLINE_S:
                        raise TimeoutError("A frissítés letöltése túllépte az időkorlátot.")
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    stream.write(chunk)
                    downloaded += len(chunk)
                    percent = min(95, 10 + int(downloaded * 85 / package["size"]))
                    _set_status("downloading", "Új Flow Manager letöltése", percent, available_version=release["version"])
            _set_status(
                "verifying",
                "Letöltött Flow Manager ellenőrzése",
                96,
                available_version=release["version"],
            )

            def report_hash_progress(total: int) -> None:
                if package["size"] > 0:
                    percent = min(99, 96 + int(total * 3 / package["size"]))
                else:
                    percent = 99
                _set_status(
                    "verifying",
                    "Letöltött Flow Manager ellenőrzése",
                    percent,
                    available_version=release["version"],
                )

            size, digest = _hash_file(stage, report_hash_progress)
            if size != package["size"] or digest != package["sha256"].lower():
                raise UpdateError("A letöltött EXE mérete vagy SHA-256 értéke nem egyezik.")
            keep_stage = True
            return stage
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            last_error = exc
            if attempt >= _DOWNLOAD_ATTEMPTS:
                raise UpdateError(
                    f"A frissítés letöltése sikertelen {_DOWNLOAD_ATTEMPTS} próbálkozás után."
                ) from exc
            _set_status(
                "downloading",
                f"Letöltési hiba — újrapróbálás ({attempt + 1}/{_DOWNLOAD_ATTEMPTS})",
                8,
                available_version=release["version"],
            )
            time.sleep(min(2.0 * attempt, 6.0))
        finally:
            if not keep_stage:
                try:
                    stage.unlink(missing_ok=True)
                except OSError:
                    pass

    raise UpdateError("A frissítés letöltése sikertelen.") from last_error


def _helper_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return base / "FlowManager" / "updates"


def _helper_command(helper: Path, parent_pid: int, target: Path, stage: Path, package: dict[str, Any]) -> list[str]:
    return [
        str(helper),
        HELPER_ARG,
        str(int(parent_pid)),
        str(target),
        str(stage),
        str(package["size"]),
        package["sha256"],
    ]


def _spawn_helper(parent_pid: int, target: Path, stage: Path, package: dict[str, Any]) -> None:
    # Windows keeps a running EXE locked. A helper launched from the target
    # executable would keep that very file locked after the parent exits.
    helper_dir = _helper_dir()
    helper_dir.mkdir(parents=True, exist_ok=True)
    helper = helper_dir / f"FlowManager-update-helper-{parent_pid}-{os.urandom(6).hex()}.exe"
    try:
        shutil.copyfile(target, helper)
    except OSError:
        helper.unlink(missing_ok=True)
        raise
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(
            _helper_command(helper, parent_pid, target, stage, package),
            cwd=str(target.parent),
            close_fds=True,
            creationflags=flags,
        )
    except OSError:
        helper.unlink(missing_ok=True)
        raise


def _wait_for_parent(pid: int, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    if os.name == "nt":
        import ctypes
        process = ctypes.windll.kernel32.OpenProcess(0x00100000 | 0x00000400, False, int(pid))
        if process:
            try:
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                return ctypes.windll.kernel32.WaitForSingleObject(process, remaining) == 0
            finally:
                ctypes.windll.kernel32.CloseHandle(process)
        # An already exited process may no longer have an openable PID.
        if ctypes.windll.kernel32.GetLastError() == 87:
            return True
        return False
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.1)
    return False


def _log_helper(message: str) -> None:
    try:
        log_path = _helper_dir().parent / "frissites.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def _restart_executable(target: Path, helper: Path | None = None) -> bool:
    command = [str(target)]
    if helper is not None:
        command.extend((CLEANUP_HELPER_ARG, str(helper)))
    for attempt in range(3):
        try:
            subprocess.Popen(command, cwd=str(target.parent), close_fds=True)
            return True
        except OSError as exc:
            _log_helper(f"Újraindítási kísérlet {attempt + 1}/3 sikertelen: {exc}")
            time.sleep(1)
    return False


def schedule_helper_cleanup_from_arguments(argv: list[str] | None = None) -> None:
    args = list(argv or sys.argv)
    if CLEANUP_HELPER_ARG not in args:
        return
    index = args.index(CLEANUP_HELPER_ARG)
    if index + 1 >= len(args):
        return
    candidate = Path(args[index + 1]).resolve()
    if candidate.parent != _helper_dir().resolve() or not _HELPER_NAME_RE.fullmatch(candidate.name):
        return

    def remove_after_exit() -> None:
        for _ in range(60):
            try:
                candidate.unlink(missing_ok=True)
                return
            except OSError:
                time.sleep(1)

    threading.Thread(target=remove_after_exit, name="UpdateHelperCleanup", daemon=True).start()


def run_helper_cli(argv: list[str] | None = None) -> int:
    """Wait for the parent, re-check the staged hash, replace, and restart."""
    args = list(argv or sys.argv)
    try:
        index = args.index(HELPER_ARG)
        parent_pid, target, stage, size, digest = args[index + 1:index + 6]
        parent_pid = int(parent_pid)
        size = int(size)
    except (ValueError, TypeError):
        return 2
    target_path = Path(target).resolve()
    stage_path = Path(stage).resolve()
    helper_path = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None
    if not _wait_for_parent(parent_pid):
        _log_helper("A korábbi folyamat nem állt le az időkorláton belül; a csere elmaradt.")
        return 5
    if not verify_file(stage_path, size, digest):
        stage_path.unlink(missing_ok=True)
        _log_helper("A letöltött EXE ellenőrzése sikertelen; a korábbi verzió újraindul.")
        _restart_executable(target_path, helper_path)
        return 3
    for attempt in range(60):
        try:
            os.replace(stage_path, target_path)
            break
        except OSError as exc:
            if attempt == 59:
                _log_helper(f"Az EXE cseréje sikertelen: {exc}; a korábbi verzió újraindul.")
                _restart_executable(target_path, helper_path)
                return 4
            time.sleep(1)
    _log_helper("Az EXE cseréje sikeres; az új verzió indul.")
    if not _restart_executable(target_path, helper_path):
        _log_helper("Az új EXE nem indult el automatikusan.")
        return 6
    return 0


def run_startup_update(
    *,
    current_version: str = APP_VERSION,
    target_executable: str | os.PathLike[str] | None = None,
    opener: Callable[..., Any] | None = None,
    exit_process: bool = True,
) -> bool:
    """Check, download, and hand off an update; failures leave the app running."""
    if not getattr(sys, "frozen", False) and target_executable is None:
        _set_status("skipped", "Fejlesztői futtatás: frissítés kihagyva", 100)
        return False
    target = Path(target_executable or sys.executable).resolve()
    _cleanup_stale_stages(target.parent)
    _set_status("checking", "Frissítések ellenőrzése", 3)
    import hub_presence
    stage: Path | None = None
    try:
        release = check_for_update(current_version, opener=opener)
        if release is None:
            _set_status("ready", "Nincs újabb verzió", 100)
            return False
        hub_presence.event("update_started")
        _set_status("downloading", f"Frissítés: {release['version']}", 8, available_version=release["version"])
        stage = _download_to_stage(release, target.parent, opener)
        _set_status("installing", "Frissítés előkészítve, újraindítás…", 100, available_version=release["version"])
        _spawn_helper(os.getpid(), target, stage, release["manifest"]["package"])
        hub_presence.event("update_ready")
        if exit_process:
            hub_presence.stop()
            os._exit(0)
        return True
    except (UpdateError, OSError, urllib.error.URLError, ValueError, TypeError) as exc:
        hub_presence.event("update_failed", exc)
        if stage is not None:
            try:
                stage.unlink(missing_ok=True)
            except OSError:
                pass
        _set_status("offline", f"Frissítés nem érhető el — a jelenlegi verzió indul", 100, error=str(exc))
        return False


def start_async() -> threading.Thread | None:
    """Start the frozen-app startup check without delaying the local dashboard."""
    if not getattr(sys, "frozen", False):
        return None
    thread = threading.Thread(target=run_startup_update, name="ReleaseUpdater", daemon=True)
    thread.start()
    return thread
