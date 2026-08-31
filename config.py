import json
import os
import shutil
import socket
import sys
from pathlib import Path

_USERNAME     = os.environ.get("USERNAME", "default")
_COMPUTERNAME = os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown"
_BASE         = rf"C:\Users\{_USERNAME}\OneDrive - HGL Group Hungary Kft"


def _find_onedrive_folder() -> str:
    for candidate in ("Ecommerce - Dokumentumok", "Ecommerce - Documents"):
        path = os.path.join(_BASE, candidate)
        if os.path.isdir(path):
            return path
    return os.path.join(_BASE, "Ecommerce - Dokumentumok")


_DEFAULT = {
    "ecomm_file":               rf"{_find_onedrive_folder()}\E_COMM nyomonkövetés_24.xlsb",
    "pallets_file":             rf"{_find_onedrive_folder()}\BUD-Pallets.xlsm",
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
    allowed = {"ecomm_file", "pallets_file", "refresh_interval_minutes", "port"}
    for key, value in (updates or {}).items():
        if key in allowed:
            cfg[key] = value
    cfg_path = get_config_path()
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    return cfg


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
