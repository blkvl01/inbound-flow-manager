import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

_USERNAME     = os.environ.get("USERNAME", "default")
_COMPUTERNAME = os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown"
_BASE         = rf"C:\Users\{_USERNAME}\OneDrive - HGL Group Hungary Kft"


def _onedrive_roots() -> list[str]:
    """Return likely local OneDrive roots without assuming one user profile."""
    roots: list[str] = []
    values = [
        os.environ.get("FLOW_ONEDRIVE_ROOT"),
        os.environ.get("OneDriveCommercial"),
        os.environ.get("OneDrive"),
    ]
    values.extend(_registered_onedrive_roots())
    values.append(_BASE)
    for value in values:
        if value:
            normalized = os.path.normpath(value)
            if normalized not in roots:
                roots.append(normalized)
    profile = os.environ.get("USERPROFILE") or str(Path.home())
    profile_root = os.path.join(profile, "OneDrive - HGL Group Hungary Kft")
    if os.path.normpath(profile_root) not in roots:
        roots.append(os.path.normpath(profile_root))
    return roots


def _registered_onedrive_roots() -> list[str]:
    """Read locally configured OneDrive account roots without scanning disks."""
    if os.name != "nt":
        return []
    try:
        import winreg

        roots: list[str] = []
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\OneDrive\Accounts",
        ) as accounts_key:
            index = 0
            while True:
                try:
                    account_name = winreg.EnumKey(accounts_key, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(accounts_key, account_name) as account_key:
                        user_folder, _ = winreg.QueryValueEx(account_key, "UserFolder")
                    if user_folder:
                        roots.append(str(user_folder))
                except OSError:
                    continue
        return roots
    except (ImportError, OSError):
        return []


def _find_onedrive_folder() -> str:
    candidates = ("Ecommerce - Dokumentumok", "Ecommerce - Documents")
    for root in _onedrive_roots():
        for candidate in candidates:
            path = os.path.join(root, candidate)
            if os.path.isdir(path):
                return path
    return os.path.join(_onedrive_roots()[0], candidates[0])


def _shared_state_candidates() -> list[Path]:
    """Return the existing-company-workspace candidates for shared state.

    The executable is distributed separately from OneDrive.  Shared state is
    therefore looked up only in the user's existing company OneDrive tree; no
    project folder is created as part of discovery.  The canonical workspace
    is the original Program HUB Flow Manager ``_shared_state`` folder.
    """
    candidates: list[Path] = []
    for root in _onedrive_roots():
        for documents_name in ("Ecommerce - Dokumentumok", "Ecommerce - Documents"):
            candidate = (
                Path(root)
                / documents_name
                / "Program HUB"
                / "Flow Manager"
                / "_shared_state"
            )
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _find_shared_state_folder() -> str:
    """Return the first existing shared-state folder, or an empty string.

    Both the Hungarian and English OneDrive folder names are supported.  An
    empty result is intentional: callers can then ask the user to select an
    existing folder instead of inventing a path or creating directories.
    """
    for candidate in _shared_state_candidates():
        if candidate.is_dir():
            return str(candidate)
    return ""


def _ensure_shared_state_folder() -> str:
    """Create only the canonical final folder under an existing Ecommerce root."""
    existing = _find_shared_state_folder()
    if existing:
        return existing
    for candidate in _shared_state_candidates():
        documents_root = candidate.parents[2]
        if not documents_root.is_dir():
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return str(candidate)
        except OSError:
            continue
    return ""


def _discover_shared_state_folder(wait_seconds: float = 0.0) -> str:
    """Resolve the canonical folder, retrying briefly while OneDrive starts."""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        resolved = _ensure_shared_state_folder()
        if resolved:
            return resolved
        if time.monotonic() >= deadline:
            return ""
        time.sleep(2.0)


def _is_legacy_shared_state_path(path: str) -> bool:
    """Identify older state locations that should migrate to the canonical path."""
    candidate = os.path.normcase(os.path.abspath(str(path)))
    for root in _onedrive_roots():
        for documents_name in ("Ecommerce - Dokumentumok", "Ecommerce - Documents"):
            legacy_paths = (
                Path(root) / documents_name / "Flow Manager" / "_shared_state",
                Path(root) / documents_name / "Flow Manager",
                Path(root) / documents_name / "Program HUB" / "Flow Manager",
            )
            if any(candidate == os.path.normcase(os.path.abspath(str(legacy))) for legacy in legacy_paths):
                return True
    return False


def _migrate_legacy_shared_state(source: Path, target: Path) -> None:
    """Copy durable shared records into the existing new workspace once.

    The old directory is deliberately retained for recovery.  Machine-local
    dashboard caches, lock files and corrupt snapshots are not copied.
    """
    if not source.is_dir() or not target.is_dir():
        return
    for item in source.iterdir():
        if not item.is_file():
            continue
        if not (item.name.endswith(".shared.json") or item.name.startswith("activity_")):
            continue
        destination = target / item.name
        if destination.exists():
            continue
        try:
            shutil.copy2(item, destination)
        except OSError as exc:
            print(f"  [!] Legacy shared-state migration skipped for {item.name}: {exc}", flush=True)


_DEFAULT = {
    "ecomm_file":               rf"{_find_onedrive_folder()}\E_COMM nyomonkövetés_24.xlsb",
    "pallets_file":             rf"{_find_onedrive_folder()}\BUD-Pallets.xlsm",
    "shared_state_dir":          "",
    "refresh_interval_minutes": 10,
    "port":                     8501,
}


def _config_dir() -> Path:
    """Per-user, per-machine config directory.

    Path: %LOCALAPPDATA%\\InboundFlowManager\\<COMPUTERNAME>\\<USERNAME>\\
    If the legacy path (without COMPUTERNAME) has a config.json, it is
    migrated once to the new location so existing users keep their settings.
    """
    local_appdata = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )
    base = Path(local_appdata) / "InboundFlowManager"
    new_dir = base / _COMPUTERNAME / _USERNAME
    new_dir.mkdir(parents=True, exist_ok=True)

    # One-time migration from the old username-only path
    old_cfg = base / _USERNAME / "config.json"
    new_cfg = new_dir / "config.json"
    if old_cfg.exists() and not new_cfg.exists():
        try:
            shutil.copy2(old_cfg, new_cfg)
            print(f"  [i] Config migrálva: {old_cfg} -> {new_cfg}", flush=True)
        except OSError as exc:
            print(f"  [!] Config migráció sikertelen: {exc}", flush=True)

    return new_dir


def _pick_files_gui(cfg: dict, cfg_path: Path) -> dict:
    """Open native Windows file-picker dialogs to locate the two Excel files.
    Called automatically when one or both files are missing.
    Returns the updated config dict (already saved to disk).
    """
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        print("  [!] tkinter nem elerheto - add meg a fajl utvonalakat kezzel a config.json-ban:", flush=True)
        print(f"      {cfg_path}", flush=True)
        return cfg

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    missing = []
    if not os.path.isfile(cfg.get("ecomm_file", "")):
        missing.append("E_COMM nyomonkövetés_24.xlsb")
    if not os.path.isfile(cfg.get("pallets_file", "")):
        missing.append("BUD-Pallets.xlsm")

    messagebox.showinfo(
        "Flow Manager — Fájlok beállítása",
        f"A következő fájl(ok) nem találhatók:\n\n"
        + "\n".join(f"  • {f}" for f in missing)
        + "\n\nKérjük, az OK gomb után válaszd ki a fájlokat.",
        parent=root,
    )

    # Pick E_COMM file
    if not os.path.isfile(cfg.get("ecomm_file", "")):
        initial_dir = str(Path(cfg.get("ecomm_file", _find_onedrive_folder())).parent)
        ecomm = filedialog.askopenfilename(
            title="Válaszd ki az E_COMM nyomonkövetés fájlt (.xlsb)",
            filetypes=[
                ("E_COMM fájl", "*.xlsb"),
                ("Excel fájlok", "*.xlsb *.xlsx *.xlsm"),
                ("Minden fájl", "*.*"),
            ],
            initialdir=initial_dir if os.path.isdir(initial_dir) else "/",
            parent=root,
        )
        if ecomm:
            cfg["ecomm_file"] = ecomm
            print(f"  [OK] E_COMM: {ecomm}", flush=True)
        else:
            print("  [!] E_COMM fajl kivalasztasa megszakitva.", flush=True)

    # Pick BUD-Pallets file
    if not os.path.isfile(cfg.get("pallets_file", "")):
        initial_dir = str(Path(cfg.get("pallets_file", _find_onedrive_folder())).parent)
        pallets = filedialog.askopenfilename(
            title="Válaszd ki a BUD-Pallets fájlt (.xlsm)",
            filetypes=[
                ("BUD-Pallets fájl", "*.xlsm"),
                ("Excel fájlok", "*.xlsb *.xlsx *.xlsm"),
                ("Minden fájl", "*.*"),
            ],
            initialdir=initial_dir if os.path.isdir(initial_dir) else "/",
            parent=root,
        )
        if pallets:
            cfg["pallets_file"] = pallets
            print(f"  [OK] BUD-Pallets: {pallets}", flush=True)
        else:
            print("  [!] BUD-Pallets fajl kivalasztasa megszakitva.", flush=True)

    root.destroy()

    # Save updated paths back to config
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    return cfg


def _ensure_shared_state_config(cfg: dict, cfg_path: Path) -> dict:
    """Resolve the canonical shared state and overwrite older selections."""
    configured = str(cfg.get("shared_state_dir") or "").strip().strip('"')
    automatic = _discover_shared_state_folder(
        wait_seconds=30.0 if getattr(sys, "frozen", False) else 0.0
    )
    if automatic:
        if configured and os.path.normcase(os.path.abspath(configured)) != os.path.normcase(os.path.abspath(automatic)):
            if _is_legacy_shared_state_path(configured):
                _migrate_legacy_shared_state(Path(configured), Path(automatic))
            print(r"  [OK] A korabban kivalasztott shared_state utvonal felulirva a kanonikus Program HUB\Flow Manager\_shared_state mappaval.", flush=True)
        if cfg.get("shared_state_dir"):
            cfg["shared_state_dir"] = ""
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(f"  [OK] Kozos allapotmappa automatikusan: {automatic}", flush=True)
        return cfg

    if configured and os.path.isdir(configured):
        return cfg

    # Source/test runs should remain non-interactive.  The frozen desktop
    # application is the only place where the startup folder picker is shown.
    if not getattr(sys, "frozen", False):
        return cfg

    print("  [!] Kozos allapotmappa nem talalhato - mappavalaszto indul...", flush=True)
    selected = pick_shared_directory(configured or None)
    if selected:
        cfg["shared_state_dir"] = selected
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(f"  [OK] Kozos allapotmappa kivalasztva: {selected}", flush=True)
    else:
        print("  [!] Kozos allapotmappa kivalasztasa megszakitva; helyi tartalek hasznalata.", flush=True)
    return cfg


def _load() -> dict:
    cfg_path = get_config_path()
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Back-fill any keys added in newer versions
        cfg = {**_DEFAULT, **data}
        if int(cfg.get("refresh_interval_minutes", 10) or 10) == 5:
            cfg["refresh_interval_minutes"] = 10
        if cfg != data:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
    else:
        cfg = dict(_DEFAULT)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(f"  [i] Uj felhasznaloi config letrehozva: {cfg_path}", flush=True)

    # In full Oracle mode both E_COMM and BUD Pallets come from the reporting
    # views. Keep the Excel paths for fallback/comparison, but never block an
    # Oracle startup with a file picker.
    oracle_mode = os.environ.get("FLOW_ECOMM_SOURCE", "excel").strip().lower() == "oracle"
    files_missing = (not oracle_mode) and (
        not os.path.isfile(cfg.get("ecomm_file", ""))
        or not os.path.isfile(cfg.get("pallets_file", ""))
    )
    if files_missing:
        print("  [!] Egy vagy tobb forras fajl nem talalhato - fajlvalaszto indul...", flush=True)
        cfg = _pick_files_gui(cfg, cfg_path)

    cfg = _ensure_shared_state_config(cfg, cfg_path)

    return cfg


def get_config_path() -> Path:
    return _config_dir() / "config.json"


def read_config_snapshot() -> dict:
    cfg_path = get_config_path()
    data = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
    cfg = {**_DEFAULT, **data}
    return cfg


def save_config_updates(updates: dict) -> dict:
    cfg = read_config_snapshot()
    allowed = {"ecomm_file", "pallets_file", "shared_state_dir", "refresh_interval_minutes", "port"}
    for key, value in (updates or {}).items():
        if key in allowed:
            cfg[key] = value
    cfg_path = get_config_path()
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return cfg


def pick_shared_directory(current_path: str | None = None) -> str:
    """Open a native folder picker for an existing shared-state directory."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError("A Windows mappavalaszto nem erheto el ezen a gepen.") from exc

    initial_dir = str(current_path or "").strip().strip('"')
    if not os.path.isdir(initial_dir):
        for candidate in _shared_state_candidates():
            if candidate.parent.is_dir():
                initial_dir = str(candidate.parent)
                break
        else:
            initial_dir = _find_onedrive_folder()

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(
            title="Valaszd ki a kozos allapotmappat",
            initialdir=initial_dir if os.path.isdir(initial_dir) else "/",
            mustexist=True,
            parent=root,
        ) or ""
    finally:
        root.destroy()


def pick_source_file(kind: str, current_path: str | None = None) -> str:
    """Open a native Windows file picker for a Flow Manager source file.

    Used by the settings modal. The app is a local desktop-style Dash app, so the
    picker runs on the same machine as the browser window and can return the real
    filesystem path instead of a browser fakepath.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise RuntimeError("A Windows fajlvalaszto nem erheto el ezen a gepen.") from exc

    cfg = read_config_snapshot()
    kind_key = "pallets_file" if kind == "pallets" else "ecomm_file"
    fallback = current_path or cfg.get(kind_key) or _find_onedrive_folder()
    initial_dir = str(Path(fallback).parent if fallback else Path(_find_onedrive_folder()))
    if not os.path.isdir(initial_dir):
        initial_dir = _find_onedrive_folder()

    if kind == "pallets":
        title = "Valaszd ki a BUD-Pallets fajlt"
        filetypes = [
            ("BUD-Pallets fajl", "*.xlsm"),
            ("Excel fajlok", "*.xlsm *.xlsx *.xlsb *.xls"),
            ("Minden fajl", "*.*"),
        ]
    else:
        title = "Valaszd ki az E_COMM nyomonkovetes fajlt"
        filetypes = [
            ("E_COMM fajl", "*.xlsb"),
            ("Excel fajlok", "*.xlsb *.xlsx *.xlsm *.xls"),
            ("Minden fajl", "*.*"),
        ]

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askopenfilename(
            title=title,
            filetypes=filetypes,
            initialdir=initial_dir,
            parent=root,
        ) or ""
    finally:
        root.destroy()


CFG = _load()

ECOMM_FILE          = CFG["ecomm_file"]
PALLETS_FILE        = CFG["pallets_file"]
REFRESH_INTERVAL_MS = int(CFG["refresh_interval_minutes"]) * 60 * 1000
PORT                = int(CFG.get("port", 8501))
