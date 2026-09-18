import sys

# The one-file updater helper must be reachable before Dash/Pandas imports.
if __name__ == "__main__" and "--flow-manager-update-helper" in sys.argv:
    import updater as _updater_helper
    raise SystemExit(_updater_helper.run_helper_cli(sys.argv))

import ctypes
import ctypes.wintypes
import hashlib
import html as _html
import json
import logging
import multiprocessing
import os
import re
import signal
import socket
import threading
import time
import unicodedata
import uuid
import webbrowser
from collections import Counter
from datetime import date, datetime, timedelta

_IS_FROZEN = getattr(sys, "frozen", False)

# ---------------------------------------------------------------------------
# PyInstaller metadata fix — must run BEFORE any dash/plotly import.
# Python 3.14 + PyInstaller: importlib.metadata may not find dist-info files
# even when --copy-metadata is used. Patch version() to return "0.0.0" for
# packages whose metadata is missing so plotly/__init__.py doesn't crash.
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    import importlib.metadata as _im
    _orig_version = _im.version
    def _safe_version(pkg):
        try:
            return _orig_version(pkg)
        except _im.PackageNotFoundError:
            return "0.0.0"
    _im.version = _safe_version

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from flask import request

import activity_log
import data_cache
import notes_manager
import overrides_manager
import storage_manager
import uld_stack_manager
import oracle_ecomm
import config as flow_config
import updater
from config import ECOMM_FILE, PORT, REFRESH_INTERVAL_MS
from data_reader import (
    ECOMM_STALE_AFTER_MINUTES,
    _base_awb,
    _has_valid_uld_prefix,
    _normalize_uld_code,
    _normalize_uld_gha,
    _parse_uld_numbers,
)
from priority_engine import (
    GRP_DRIVER_ACTIVE_HIGH,
    GRP_DRIVER_ACTIVE_LOW,
    GRP_DRIVER_PASSIVE_HIGH,
    GRP_DRIVER_PASSIVE_LOW,
    GRP_EN_ROUTE,
    apply_priorities,
)
from version import APP_VERSION

# This release target is the Excel/OneDrive edition.  The existing source tree
# still contains the separate Oracle trial path, but the distributed EXE must
# not silently switch data sources because a machine-level variable happens to
# be present.
if _IS_FROZEN:
    os.environ["FLOW_ECOMM_SOURCE"] = "excel"

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Assets path + vendor check
# ---------------------------------------------------------------------------

if _IS_FROZEN:
    _assets_path = os.path.join(sys._MEIPASS, "assets")
else:
    _assets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

_VENDOR_CSS      = os.path.join(_assets_path, "vendor", "bootstrap-cyborg.min.css")
_VENDOR_FONT     = os.path.join(_assets_path, "vendor", "inter-font.css")
_VENDOR_JSBARCODE = os.path.join(_assets_path, "jsbarcode.min.js")


def _ensure_vendor_assets():
    """Download Bootstrap Cyborg + Inter font if not already present.
    Called before Dash starts so the browser always gets local files.
    Nyomtatáss progress to stdout (visible in a console window if opened).
    """
    import re
    import urllib.request

    vendor_dir = os.path.join(_assets_path, "vendor")
    fonts_dir  = os.path.join(_assets_path, "fonts")
    os.makedirs(vendor_dir, exist_ok=True)
    os.makedirs(fonts_dir,  exist_ok=True)

    def _dl(url, dest, label):
        print(f"  Letöltés: {label}...", flush=True)
        req = urllib.request.Request(url, headers={"Felhasználó-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        with open(dest, "wb") as f:
            f.write(data)
        print(f"  rendben ({len(data)//1024} KB)", flush=True)

    # Bootstrap Cyborg
    if not os.path.exists(_VENDOR_CSS):
        print("[1/3] Bootstrap Cyborg CSS letöltése...", flush=True)
        _dl(
            "https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/cyborg/bootstrap.min.css",
            _VENDOR_CSS,
            "bootstrap-cyborg.min.css",
        )

    # JsBarcode (placed directly in assets/ so Dash auto-loads it before print_preview.js)
    if not os.path.exists(_VENDOR_JSBARCODE):
        print("[2/3] JsBarcode letöltése...", flush=True)
        _dl(
            "https://cdn.jsdelivr.net/npm/jsbarcode@3.11.6/dist/JsBarcode.all.min.js",
            _VENDOR_JSBARCODE,
            "jsbarcode.min.js",
        )

    # Inter font
    if not os.path.exists(_VENDOR_FONT):
        print("[3/3] Inter betűtípus letöltése...", flush=True)
        INTER_URL = (
            "https://fonts.googleapis.com/css2"
            "?family=Inter:wght@300;400;500;600;700;800;900&display=swap"
        )
        req = urllib.request.Request(INTER_URL, headers={
            "Felhasználó-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            inter_css = r.read().decode("utf-8")

        woff2_urls = re.findall(r'url\((https://[^)]+\.woff2[^)]*)\)', inter_css)
        print(f"  {len(woff2_urls)} font fájl letöltése...", flush=True)
        for i, url in enumerate(woff2_urls):
            fname = f"inter_{i:02d}.woff2"
            fpath = os.path.join(fonts_dir, fname)
            if not os.path.exists(fpath):
                _dl(url, fpath, fname)
            inter_css = inter_css.replace(url, f"../fonts/{fname}")

        with open(_VENDOR_FONT, "w", encoding="utf-8") as f:
            f.write(inter_css)
        print("  Inter font kész.", flush=True)


# Run vendor check before Dash initialises (assets_folder must be complete)
try:
    _ensure_vendor_assets()
except Exception as _e:
    print(f"Figyelmeztetés: vendor assets nem tölthetők le ({_e})."
          f" Az alkalmazás CDN nélkül indul.", flush=True)

# ---------------------------------------------------------------------------
# App init
# ---------------------------------------------------------------------------

# Dev: load Bootstrap + Google Fonts from CDN (vendor/ may be absent)
# EXE: everything is local in assets/vendor/ → no external requests needed
if _IS_FROZEN or (os.path.exists(_VENDOR_CSS) and os.path.exists(_VENDOR_FONT)):
    _ext_css = []
else:
    _ext_css = [
        dbc.themes.CYBORG,
        "https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap",
    ]

app = dash.Dash(
    __name__,
    assets_folder=_assets_path,
    external_stylesheets=_ext_css,
    title="Flow Manager",
    update_title=None,
    suppress_callback_exceptions=True,
)
server = app.server


@server.before_request
def _track_post_activity():
    if request.method == "POST":
        _last_post_time[0] = time.time()
    elif request.method == "GET" and request.path == "/":
        activity_log.log_event("page_open")


@server.route("/api/log/ui", methods=["POST"])
def api_log_ui():
    """Lightweight UI-interaction beacon (assets/activity_beacon.js)."""
    from flask import jsonify
    data = request.get_json(force=True, silent=True) or {}
    action = str(data.get("action") or "").strip()[:64]
    if action:
        detail = data.get("detail")
        activity_log.log_event(f"ui_{action}", detail if detail else None)
    return jsonify({"ok": True})


_TV_VERSION_CACHE = {"at": 0.0, "payload": None}
_TV_VERSION_CACHE_TTL = 5.0
_TV_VERSION_BOOT = {"signature": None, "asset_signature": None, "server_signature": None}


def _tv_version_files() -> list[tuple[str, str]]:
    root = os.path.dirname(os.path.abspath(__file__))
    files = [
        ("server", os.path.join(root, "app.py")),
        ("server", os.path.join(root, "data_reader.py")),
        ("asset", os.path.join(_assets_path, "tv_mode.js")),
        ("asset", os.path.join(_assets_path, "style.css")),
    ]
    return files


def _tv_version_signature(kind_filter: str | None = None) -> tuple[str, int, float]:
    root = os.path.dirname(os.path.abspath(__file__))
    digest = hashlib.sha256()
    count = 0
    newest = 0.0
    for kind, path in _tv_version_files():
        if kind_filter and kind != kind_filter:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        try:
            rel = os.path.relpath(path, root)
        except ValueError:
            rel = path
        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        digest.update(kind.encode("utf-8"))
        digest.update(b"\0")
        digest.update(rel.replace("\\", "/").encode("utf-8", "ignore"))
        digest.update(b"\0")
        digest.update(str(st.st_size).encode("ascii"))
        digest.update(b":")
        digest.update(str(mtime_ns).encode("ascii"))
        digest.update(b"\n")
        count += 1
        newest = max(newest, float(st.st_mtime))
    return digest.hexdigest()[:24], count, newest


def _tv_version_payload() -> dict:
    now = time.time()
    cached = _TV_VERSION_CACHE.get("payload")
    if cached and now - float(_TV_VERSION_CACHE.get("at") or 0.0) < _TV_VERSION_CACHE_TTL:
        return dict(cached)

    asset_sig, asset_count, asset_newest = _tv_version_signature("asset")
    server_sig, server_count, server_newest = _tv_version_signature("server")
    full_sig = hashlib.sha256(f"{asset_sig}|{server_sig}".encode("ascii")).hexdigest()[:24]

    if _TV_VERSION_BOOT["signature"] is None:
        _TV_VERSION_BOOT.update({
            "signature": full_sig,
            "asset_signature": asset_sig,
            "server_signature": server_sig,
        })

    payload = {
        "ok": True,
        "signature": full_sig,
        "asset_signature": asset_sig,
        "server_signature": server_sig,
        "asset_count": asset_count,
        "server_count": server_count,
        "newest_mtime": max(asset_newest, server_newest),
        "generated_at": now,
        "asset_changed_since_boot": asset_sig != _TV_VERSION_BOOT["asset_signature"],
        "server_changed_since_boot": server_sig != _TV_VERSION_BOOT["server_signature"],
    }
    _TV_VERSION_CACHE.update({"at": now, "payload": dict(payload)})
    return payload


@server.route("/api/tv/version", methods=["GET"])
def api_tv_version():
    """TV-only update signature for the fullscreen browser client."""
    from flask import jsonify
    response = jsonify(_tv_version_payload())
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


try:
    _tv_version_payload()
except Exception as _e:
    log.warning("TV version baseline could not be initialized: %s", _e)


def _tv_forwarded_marker(*notes) -> bool:
    """Igaz, ha a BUD-Pallets megjegyzés(ek)ben 'tovább engedett / továbbengedett'
    jelölés van (ékezet/szóköz-tűrően)."""
    for note in notes:
        n = (
            unicodedata.normalize("NFKD", str(note or ""))
            .encode("ascii", "ignore").decode().lower().replace(" ", "")
        )
        if "tovabbenged" in n:
            return True
    return False


_TV_RAMP_EXPLICIT_RE = re.compile(
    r"(?<!\d)([0-9]{1,2})\s*(?:[/.-]\s*)?(?:[-.]?\s*(?:es|as|os|0s|s))?\s*(?:vak\s*)?ramp(?:a|an|ara)?\b"
)
_TV_RAMP_REVERSE_RE = re.compile(
    r"\b(?:vak\s*)?ramp(?:a|an|ara)?\s*([0-9]{1,2})\b"
)

_TV_LOADER_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("U. Krisz", re.compile(r"\bu\s*[.,\s]*krisz(?:tian)?\b")),
    ("M. Zoli", re.compile(r"\bm\s*[.,\s]*zoli\b")),
    ("K. Zoli", re.compile(r"\bk+\s*[.,\s]*z(?:p)?oli\b")),
    ("W. Zsolt", re.compile(r"\bw\s*[.,\s]*zsolt\b")),
    ("D. Tibor", re.compile(r"\bd\s*[.,\s]*(?:tibor|tibi)\b")),
    ("G. Peti", re.compile(r"\bg\s*[.,\s]*(?:b\s*[.,\s]*)?peti\b")),
    ("Konka G.", re.compile(r"\bkonka\s*g\b")),
    ("Szabolcs", re.compile(r"\bsz\s*[.,\s]*szabolcs\b|\bszabolcs\b|\bszabi\b")),
    ("Krisz", re.compile(r"\bkrisz(?:tian)?\b")),
    ("Csaba", re.compile(r"\bcsaba\b|\bcsabi\b")),
    ("Alfonz", re.compile(r"\bl\s*[.,\s]*alfonz\b|\balfonz\b|\balfi\b")),
    ("Gazsó", re.compile(r"\bgazso\b|\bgazsi\b")),
    ("Tamás", re.compile(r"\bt\s*[.,\s]*tamas\b|\btamas\b")),
    ("Ricsi", re.compile(r"\bricsi\b")),
    ("B.Z.", re.compile(r"\bb\s*[.,\s]*z\b")),
)


def _tv_note_norm(value) -> str:
    return (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore").decode("ascii").lower()
    )


def _tv_valid_ramp_label(value) -> str:
    label = re.sub(r"\s+", "", str(value or "")).upper()
    try:
        n = int(label)
    except (TypeError, ValueError):
        return ""
    return str(n) if 1 <= n <= 23 else ""


def _tv_extract_loading_note_meta(note) -> dict:
    """TV-only parser for BUD-Pallets column N ramp/name notes."""
    n = _tv_note_norm(note)
    ramp = ""
    for rx in (_TV_RAMP_EXPLICIT_RE, _TV_RAMP_REVERSE_RE):
        hit = rx.search(n)
        if hit:
            ramp = _tv_valid_ramp_label(hit.group(1))
            if ramp:
                break

    loaders: list[str] = []
    for canonical, rx in _TV_LOADER_PATTERNS:
        if rx.search(n) and canonical not in loaders:
            loaders.append(canonical)
    if "U. Krisz" in loaders and "Krisz" in loaders:
        loaders.remove("Krisz")

    return {"ramp": ramp, "loaders": loaders}


def _tv_ranked_note_values(values, limit: int = 2) -> str:
    counts: Counter = Counter()
    first_seen: dict[str, int] = {}
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if text not in first_seen:
            first_seen[text] = len(first_seen)
        counts[text] += 1
    if not counts:
        return ""
    ranked = sorted(counts, key=lambda v: (-counts[v], first_seen[v], v))
    if len(ranked) <= limit:
        return "/".join(ranked)
    return f"{ranked[0]} +{len(ranked) - 1}"


def _tv_loading_note_summary(notes) -> dict:
    ramps: list[str] = []
    loaders: list[str] = []
    for note in notes:
        meta = _tv_extract_loading_note_meta(note)
        if meta.get("ramp"):
            ramps.append(meta["ramp"])
        loaders.extend(meta.get("loaders") or [])
    return {
        "ramp": _tv_ranked_note_values(ramps),
        "loader": _tv_ranked_note_values(loaders),
    }


def _tv_status_key(status) -> str:
    s = _tv_note_norm(status).replace(" ", "")
    if "kiadva" in s:
        return "kiadva"
    if "kiadhat" in s:
        return "kiadhato"
    if "mitortenik" in s or "mitorten" in s:
        return "mitortenik"
    if "vam" in s:
        return "vamkez"
    if "megerkez" in s:
        return "megerkezett"
    if "szeml" in s:
        return "szemles"
    if "ertesit" in s:
        return "ertesito"
    if "felv" in s:
        return "felveve"
    return "other"


def _tv_loading_milestones_near(items: list[dict]) -> bool:
    late_keys = []
    for item in items:
        if item.get("is_ready"):
            continue
        late_keys.append(_tv_status_key(item.get("ecomm_status") or item.get("status")))
    return bool(late_keys) and all(key == "megerkezett" for key in late_keys)


def _tv_fixed_loading_candidate(
    *,
    plate: str,
    glabs_id: str,
    items: list[dict],
    driver_here: bool,
    driver_checkin: str,
    resting: bool,
) -> dict | None:
    total = len(items)
    if total <= 0:
        return None
    ready = sum(1 for item in items if item.get("is_ready"))
    all_ready = ready >= total
    half_ready = ready * 2 >= total

    if all_ready and driver_here:
        priority = 0
        reason = "kész + sofőr"
    elif driver_here and half_ready and _tv_loading_milestones_near(items):
        priority = 1
        reason = "fél felett + közel"
    elif all_ready and not driver_here and not driver_checkin and not resting:
        priority = 2
        reason = "kész, sofőr nélkül"
    else:
        return None

    meta = _tv_loading_note_summary(item.get("load_note") for item in items)
    lmp_counts = Counter(str(item.get("lmp") or "").strip() for item in items if str(item.get("lmp") or "").strip())
    lmps = [
        lmp for lmp, _count in
        sorted(lmp_counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
    ][:2]

    return {
        "plate": plate,
        "glabs_id": glabs_id,
        "ready": ready,
        "total": total,
        "driver_here": bool(driver_here),
        "driver_checkin": driver_checkin,
        "reason": reason,
        "priority": priority,
        "ramp": meta.get("ramp") or "",
        "loader": meta.get("loader") or "",
        "lmps": lmps,
    }


@server.route("/api/tv/ops", methods=["GET"])
def api_tv_ops():
    """Live operational summary for TV mode slide 1 & 2."""
    from flask import jsonify
    import math
    state = data_cache.get_state()
    df    = state.get("df")
    kpi   = state.get("kpi") or {}
    outbound_cards = state.get("outbound_cards") or []

    # ── Inbound summary ─────────────────────────────────────────────────────
    active_items: list[dict] = []
    stored_today = 0
    if df is not None and not df.empty:
        view = df.copy()
        if "is_stored" in view.columns:
            stored_today = int((view["is_stored"] == True).sum())
            view = view[view["is_stored"] != True]
        if "is_en_route" in view.columns:
            view = view[view["is_en_route"] != True]
        if "priority_rank" in view.columns:
            view = view.sort_values("priority_rank", ascending=True)
        for _, row in view.head(10).iterrows():
            waiting_h = float(row.get("waiting_hours") or 0)
            drv_here = bool(row.get("driver_checkin") and not row.get("driver_in_rest"))
            active_items.append({
                "awb":             str(row.get("awb") or ""),
                "lmp":             str(row.get("lmp") or ""),
                "weight":          float(row.get("weight") or 0),
                "waiting_h":       round(waiting_h, 1),
                "is_aged":         bool(row.get("is_aged")),
                "driver_here":     drv_here,
                "driver_rest":     bool(row.get("driver_in_rest")),
                "glabs_shippable": int(row.get("glabs_shippable") or 0),
                "glabs_total":     int(row.get("glabs_total") or 0),
                "cargo_type":      str(row.get("cargo_type") or "PLT"),
                "rank":            int(row.get("priority_rank") or 99),
            })
    total_active = len(active_items)

    # ── KPI ─────────────────────────────────────────────────────────────────
    shift_kg      = float(kpi.get("shift_kg", 0) or 0)
    shift_count   = int(kpi.get("shift_count", 0) or 0)
    shift_name    = str(kpi.get("shift_name", "") or "")
    shift_range   = str(kpi.get("shift_range", "") or "")
    shift_elapsed = float(kpi.get("shift_elapsed_h", 0) or 0)
    bec_total     = float(kpi.get("bec_total", 0) or 0)
    bec_count     = sum(int(kpi.get(f"bec_{k}_count", 0) or 0)
                        for k in ("felveve", "ertesito", "megerkezett", "szemles"))
    tracking = kpi.get("tracking") if isinstance(kpi.get("tracking"), dict) else {}
    warehouse_saturation = tracking.get("current_warehouse_saturation") if isinstance(tracking.get("current_warehouse_saturation"), dict) else {}
    warehouse_active_items = int(tracking.get("active_warehouse_items") or 0)
    warehouse_active_kg = float(warehouse_saturation.get("kg") or 0)

    # ── KPI: previous shift ──────────────────────────────────────────────────
    prev_shift_kg   = float(kpi.get("shift_prev_kg", 0) or 0)
    prev_shift_name = str(kpi.get("shift_prev_name", "") or "")

    # ── Outbound summary ────────────────────────────────────────────────────
    trucks: list[dict] = []
    fixed_loadings: list[dict] = []
    for card in outbound_cards:
        plate  = str(card.get("plate") or "")
        status = str(card.get("status_label") or "")
        checkin = card.get("checkin")
        active_awbs: list[dict] = []
        active_glabs_items: list[tuple[str, list[dict]]] = []
        glabs_ids: list[str] = []
        for glabs in card.get("glabs_items") or []:
            g_has_active = False
            g_active_awbs: list[dict] = []
            for item in glabs.get("awb_items") or []:
                if item.get("is_issued"):
                    continue
                # Hardening: a kiadott tétel SEHOL nem jelenhet meg TV-ben, akkor
                # sem, ha az is_issued flag valamiért hiányzik (státusz "Kiadva").
                _st = str(item.get("ecomm_status") or item.get("status") or "").strip().lower()
                if "kiadva" in _st:
                    continue
                active_awbs.append(item)
                g_active_awbs.append(item)
                g_has_active = True
            if g_has_active:
                gid = str(glabs.get("glabs_id") or "").strip()
                if gid and gid not in glabs_ids:
                    glabs_ids.append(gid)
                active_glabs_items.append((gid, g_active_awbs))
        if not active_awbs:
            continue

        status_counts: dict[str, int] = {}
        lmp_counts: dict[str, int] = {}
        for item in active_awbs:
            item_status = str(item.get("ecomm_status") or item.get("status") or "Státusz nélkül").strip()
            status_counts[item_status] = status_counts.get(item_status, 0) + 1
            lmp = str(item.get("lmp") or "").strip()
            if lmp:
                lmp_counts[lmp] = lmp_counts.get(lmp, 0) + 1

        lmps = [
            lmp for lmp, _count in
            sorted(lmp_counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
        ][:4]
        forwarded = any(
            _tv_forwarded_marker(item.get("load_note"), item.get("eta_note"))
            for item in active_awbs
        )
        active_total = len(active_awbs)
        ready_total = sum(1 for item in active_awbs if item.get("is_ready"))
        pending_total = max(0, active_total - ready_total)
        is_closed = active_total == 0
        note_meta = _tv_loading_note_summary(item.get("load_note") for item in active_awbs)
        driver_here = bool(checkin and not card.get("in_rest"))
        driver_checkin = checkin.strftime("%m.%d %H:%M") if checkin else ""
        resting = bool(card.get("in_rest"))
        rest_until = card.get("rest_until").strftime("%m.%d %H:%M") if card.get("rest_until") else ""

        if plate:
            trucks.append({
                "plate":       plate,
                "status":      status,
                "lmps":        lmps,
                "glabs_ids":   glabs_ids,
                "forwarded":   forwarded,
                "ready":       ready_total,
                "total":       active_total,
                "active":      active_total,
                "issued":      0,
                "pending":     pending_total,
                "driver_here": driver_here,
                "driver_checkin": driver_checkin,
                "resting":     resting,
                "rest_until":  rest_until,
                "ramp":        note_meta.get("ramp") or "",
                "loader":      note_meta.get("loader") or "",
                "is_closed":   is_closed,
                "statuses":    [
                    {"label": label, "count": count}
                    for label, count in sorted(status_counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:4]
                ],
                "units":       [
                    {
                        "status": str(item.get("ecomm_status") or item.get("status") or "Státusz nélkül").strip(),
                        "ready": bool(item.get("is_ready")),
                    }
                    for item in active_awbs
                ],
            })

            for glabs_id, glabs_active_awbs in active_glabs_items:
                fixed = _tv_fixed_loading_candidate(
                    plate=plate,
                    glabs_id=glabs_id,
                    items=glabs_active_awbs,
                    driver_here=driver_here,
                    driver_checkin=driver_checkin,
                    resting=resting,
                )
                if fixed:
                    fixed_loadings.append(fixed)

    active_trucks  = sum(1 for t in trucks if not t["is_closed"])
    waiting_trucks = sum(1 for t in trucks if not t["is_closed"] and not t["resting"])
    fixed_loadings.sort(
        key=lambda item: (
            int(item.get("priority") or 9),
            -int(item.get("ready") or 0),
            str(item.get("plate") or "").casefold(),
            str(item.get("glabs_id") or "").casefold(),
        )
    )
    # Keep the full sorted candidate list for TV mode. The client paginates the
    # "Kovetkezo rakodasok" block, so truncating here would silently hide work.

    # ── Műszak riport (TV riport oldal) ─────────────────────────────────────
    # Outbound-vezérelt műszak-összegzés, minimális inbound jelenléttel. A gauge
    # REFERENCIÁJA külső viszonyítás: az AZONOS TÍPUSÚ előző műszak (tegnap ugyanez
    # a 12 órás ablak) teljes tempója — a közvetlenül megelőző, ellentétes napszakú
    # műszak strukturálisan más terhelésű, torz viszonyítás lenne. Ha nincs tegnapi
    # adat, a saját műszak LEZÁRT óráinak átlaga (a 0 kg-os órákkal EGYÜTT — a
    # nem-nulla-szűrés felfelé torzítana). A saját kumulatív ráta önmaga átlagához
    # mérve semmitmondó lenne (a kettő definíció szerint közel azonos).
    # Gauge midpoint is a stable big average from completed historical outbound shifts.
    # It must not drift with the current shift poll cycle.
    # Outbound QUANTITY (kg / colli / parcels / count / hourly + kg gauge midpoint)
    # is driven off E_COMM AS (departure) via the legacy-named outbound_at_shift_* keys — see
    # data_reader._ecomm_at_outbound_shift_kpi. Falls back to the BUD-Pallets KIADVA
    # figures if the legacy "at" keys are absent (older cache / E_COMM read failure).
    # PALLETS, TRUCKS and LOADINGS (rakodás/óra gauge) intentionally stay on the
    # BUD-Pallets outbound_shift_* figures — E_COMM has no such fields.
    out_kg       = float(kpi.get("outbound_at_shift_kg", kpi.get("outbound_shift_kg", 0)) or 0)
    out_colli    = float(kpi.get("outbound_at_shift_colli", kpi.get("outbound_shift_colli", 0)) or 0)
    out_pallets  = float(kpi.get("outbound_shift_pallets", 0) or 0)
    out_parcels  = float(kpi.get("outbound_at_shift_parcels", kpi.get("outbound_shift_parcels", 0)) or 0)
    out_trucks   = int(kpi.get("outbound_shift_trucks", 0) or 0)
    out_loadings = int(kpi.get("outbound_shift_loadings", 0) or out_trucks or 0)
    out_rows     = int(kpi.get("outbound_at_shift_count", kpi.get("outbound_shift_count", 0)) or 0)
    out_loading_time_min = float(kpi.get("outbound_shift_loading_time_min", 0) or 0)
    out_loading_time_count = int(kpi.get("outbound_shift_loading_time_count", 0) or 0)
    out_prev_kg      = float(kpi.get("outbound_at_shift_prev_same_kg", kpi.get("outbound_shift_prev_same_kg", 0)) or 0)
    out_prev_trucks  = int(kpi.get("outbound_shift_prev_same_trucks", 0) or 0)
    out_prev_loadings = int(kpi.get("outbound_shift_prev_same_loadings", 0) or out_prev_trucks or 0)
    out_avg_kg_per_h = float(kpi.get("outbound_at_shift_avg_kg_per_h", kpi.get("outbound_shift_avg_kg_per_h", 0)) or 0)
    out_avg_loadings_per_h = float(kpi.get("outbound_shift_avg_loadings_per_h", 0) or 0)
    out_avg_shift_count = int(kpi.get("outbound_at_shift_avg_shift_count", kpi.get("outbound_shift_avg_shift_count", 0)) or 0)
    out_shift_name  = str(kpi.get("outbound_shift_name", "") or shift_name)
    out_shift_range = str(kpi.get("outbound_shift_range", "") or shift_range)
    issued_trucks = out_trucks
    shift_out_kg = out_kg

    # Korai műszak: 30 percnél kisebb eltelt időnél is 0.5 órával osztunk, hogy egy
    # korai kiadás ne adjon irreális (kiakadó) kg/h tempót; emellett warmup flag jelzi
    # a kliensnek, hogy a tempó még nem mérvadó (neutrális gauge-állapot).
    cur_h  = max(0.5, shift_elapsed)
    warmup = shift_elapsed < 0.75

    def _rate(val, hours):
        return round(float(val) / hours, 2) if hours > 0 else 0.0

    out_hourly = kpi.get("outbound_at_shift_hourly") or kpi.get("outbound_shift_hourly") or []

    out_kg_per_h = _rate(out_kg, cur_h)
    out_colli_per_h = _rate(out_colli, cur_h)
    out_pallets_per_h = _rate(out_pallets, cur_h)
    out_parcels_per_h = _rate(out_parcels, cur_h)
    out_loadings_per_h = _rate(out_loadings, cur_h)

    out_ref_kg_per_h = round(out_avg_kg_per_h, 2) if out_avg_kg_per_h > 0 else 0.0
    out_ref_loadings_per_h = round(out_avg_loadings_per_h, 2) if out_avg_loadings_per_h > 0 else 0.0
    out_ref_kg_kind = "big_avg" if out_ref_kg_per_h > 0 else "none"
    out_ref_loadings_kind = "big_avg" if out_ref_loadings_per_h > 0 else "none"
    driver_here_trucks = sum(1 for t in trucks if not t["is_closed"] and t["driver_here"])

    report = {
        "shift_name":  out_shift_name,
        "shift_range": out_shift_range,
        "elapsed_h":   round(shift_elapsed, 2),
        "warmup":      warmup,
        "fixed_loadings": fixed_loadings,
        "out": {
            "kg":       out_kg,
            "colli":    out_colli,
            "pallets":  out_pallets,
            "parcels":  out_parcels,
            "trucks":   out_trucks,
            "loadings": out_loadings,
            "rows":     out_rows,
            "loading_time_min": out_loading_time_min,
            "loading_time_count": out_loading_time_count,
            "kg_per_h":      out_kg_per_h,
            "colli_per_h":   out_colli_per_h,
            "pallets_per_h": out_pallets_per_h,
            "parcels_per_h": out_parcels_per_h,
            "loadings_per_h": out_loadings_per_h,
            "ref_kg_per_h":       out_ref_kg_per_h,
            "ref_kg_kind":        out_ref_kg_kind,
            "ref_loadings_per_h": out_ref_loadings_per_h,
            "ref_loadings_kind":  out_ref_loadings_kind,
            "avg_shift_count":    out_avg_shift_count,
            "prev_kg":     out_prev_kg,
            "hourly":      out_hourly,
            "active_trucks":  active_trucks,
            "issued_trucks":  out_trucks,
            "waiting_trucks": waiting_trucks,
            "driver_here_trucks": driver_here_trucks,
        },
        "inb": {
            "kg":       shift_kg,
            "count":    shift_count,
            "kg_per_h": _rate(shift_kg, cur_h),
            "prev_kg":  prev_shift_kg,
            "bec_total_kg": bec_total,
            "bec_count":    bec_count,
        },
    }

    bec_felveve_kg      = float(kpi.get("bec_felveve", 0) or 0)
    bec_ertesito_kg     = float(kpi.get("bec_ertesito", 0) or 0)
    bec_megerkezett_kg  = float(kpi.get("bec_megerkezett", 0) or 0)
    bec_szemles_kg      = float(kpi.get("bec_szemles", 0) or 0)
    bec_felveve_cnt     = int(kpi.get("bec_felveve_count", 0) or 0)
    bec_ertesito_cnt    = int(kpi.get("bec_ertesito_count", 0) or 0)
    bec_megerkezett_cnt = int(kpi.get("bec_megerkezett_count", 0) or 0)
    bec_szemles_cnt     = int(kpi.get("bec_szemles_count", 0) or 0)

    _last_ok = state.get("last_success_refresh")
    return jsonify({
        "status":               state.get("status", "loading"),
        "last_refresh":         state.get("last_refresh").isoformat() if state.get("last_refresh") else None,
        # Freshness / sync-stall signal for the TV (24/7): when the background read
        # is stuck or hard-failing, the payload below is the last GOOD snapshot and
        # the TV shows a visible "adat X perce · szinkron elakadt" warning.
        "freshness": {
            "stalled":      bool(state.get("read_stalled")),
            "reason":       state.get("read_stall_reason") or "",
            "last_success": _last_ok.isoformat() if _last_ok else None,
            "age_seconds":  round((datetime.now() - _last_ok).total_seconds(), 1) if _last_ok else None,
        },
        "active_items":         active_items,
        "total_active":         total_active,
        "stored_today":         stored_today,
        "shift_kg":             shift_kg,
        "shift_count":          shift_count,
        "shift_name":           shift_name,
        "shift_range":          shift_range,
        "shift_elapsed_h":      round(shift_elapsed, 2),
        "prev_shift_kg":        prev_shift_kg,
        "prev_shift_name":      prev_shift_name,
        "bec_total_kg":         bec_total,
        "bec_count":            bec_count,
        "warehouse_active_items": warehouse_active_items,
        "warehouse_active_kg":    warehouse_active_kg,
        "bec_felveve_kg":       bec_felveve_kg,
        "bec_ertesito_kg":      bec_ertesito_kg,
        "bec_megerkezett_kg":   bec_megerkezett_kg,
        "bec_szemles_kg":       bec_szemles_kg,
        "bec_felveve_cnt":      bec_felveve_cnt,
        "bec_ertesito_cnt":     bec_ertesito_cnt,
        "bec_megerkezett_cnt":  bec_megerkezett_cnt,
        "bec_szemles_cnt":      bec_szemles_cnt,
        "trucks":               trucks,
        "active_trucks":        active_trucks,
        "issued_trucks":        issued_trucks,
        "waiting_trucks":       waiting_trucks,
        "shift_out_kg":         shift_out_kg,
        # Óránkénti bontás a header sparkline-okhoz (12 db egy-órás kg-vödör).
        "shift_hourly":         kpi.get("shift_hourly") or [],
        "outbound_shift_hourly": kpi.get("outbound_at_shift_hourly") or kpi.get("outbound_shift_hourly") or [],
        # Műszak riport oldal (TV) — outbound-vezérelt összegzés + ráták.
        "report":               report,
    })


@server.after_request
def _asset_cache_headers(response):
    asset_name = request.path.rsplit("/", 1)[-1]
    if request.path.startswith("/assets/") and asset_name in {
        "uld_manager.js",
        "style.css",
        "kpi_snap.js",
        "kpi_page.js",
        "kpi_subview_switch.js",
        "kpi_temu.js",
        "kpi_trend.js",
        "kpi_bands.js",
        "tv_mode.js",
    }:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ---------------------------------------------------------------------------
# Nyomtatás route — standalone print-ready HTML for a GLABS loading plan
# ---------------------------------------------------------------------------

def _generate_print_html(plate: str, glabs_id: str, ramp: str, glabs: dict) -> str:
    now_str = datetime.now().strftime("%Y.%m.%d %H:%M")
    awb_items = glabs.get("awb_items", [])
    plate_e   = _html.escape(str(plate))
    glabs_id_e = _html.escape(str(glabs_id))
    ramp_e    = _html.escape(str(ramp or "—"))

    total_colli = 0
    total_plt = 0
    rows_html = ""
    for i, item in enumerate(awb_items, 1):
        awb      = _html.escape(str(item.get("awb") or "—"))
        lmp      = _html.escape(str(item.get("lmp") or "—"))
        colli_v  = item.get("boxes") or 0
        plt_v    = item.get("pallets_issued") or 0
        location = _html.escape(str(item.get("location") or ""))
        note     = _html.escape(str(item.get("load_note") or ""))
        total_colli += float(colli_v)
        total_plt   += float(plt_v)
        colli_disp = str(int(colli_v)) if colli_v else "—"
        plt_disp   = str(int(plt_v))   if plt_v   else "—"
        row_cls = "issued" if item.get("is_issued") else "ready" if item.get("is_ready") else ""
        rows_html += (
            f'<tr class="{row_cls}">'
            f'<td class="td-num">{i}</td>'
            f'<td class="td-lmp">{lmp}</td>'
            f'<td class="td-awb">{awb}</td>'
            f'<td class="td-plate">{plate_e}</td>'
            f'<td class="td-num">{colli_disp}</td>'
            f'<td class="td-num">{plt_disp}</td>'
            f'<td class="td-korr"></td>'
            f'<td class="td-loc">{location}</td>'
            f'<td class="td-note">{note}</td>'
            f'</tr>\n'
        )

    total_colli_disp = str(int(total_colli)) if total_colli else "—"
    total_plt_disp   = str(int(total_plt))   if total_plt   else "—"
    row_count = len(awb_items)

    return f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="utf-8">
<title>Loading Plan – {glabs_id_e}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 11px; color: #1a1a1a; background: #fff; }}
  .page {{ padding: 14mm 12mm 10mm; max-width: 297mm; margin: 0 auto; }}
  .doc-header {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 2px solid #1a1a1a; padding-bottom: 6px; margin-bottom: 12px; }}
  .doc-company {{ font-size: 9px; color: #555; letter-spacing: 0.5px; text-transform: uppercase; }}
  .doc-title {{ font-size: 20px; font-weight: 700; letter-spacing: 1px; color: #1a1a1a; }}
  .meta-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 14px; }}
  .meta-box {{ border: 1px solid #ccc; padding: 5px 8px; border-radius: 3px; }}
  .meta-label {{ font-size: 8px; color: #777; text-transform: uppercase; letter-spacing: 0.5px; display: block; }}
  .meta-value {{ font-size: 13px; font-weight: 700; display: block; margin-top: 2px; }}
  .meta-value.ramp {{ color: #c0392b; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 14px; }}
  th {{ background: #2c3e50; color: #fff; padding: 5px 6px; text-align: left; font-size: 9px; letter-spacing: 0.4px; text-transform: uppercase; border: 1px solid #2c3e50; }}
  td {{ padding: 5px 6px; border: 1px solid #d0d0d0; font-size: 10px; vertical-align: middle; }}
  tr:nth-child(even) td {{ background: #f8f8f8; }}
  tr.ready td {{ background: #eafaf1; }}
  tr.issued td {{ background: #f5f5f5; color: #999; }}
  .td-num {{ text-align: center; width: 32px; }}
  .td-lmp {{ font-weight: 600; min-width: 80px; }}
  .td-awb {{ font-family: monospace; font-size: 9.5px; min-width: 100px; }}
  .td-plate {{ font-weight: 600; min-width: 70px; }}
  .td-korr {{ min-width: 48px; border-bottom: 1.5px solid #888 !important; background: #fffde7 !important; }}
  .td-loc {{ min-width: 60px; color: #444; }}
  .td-note {{ color: #555; }}
  tfoot td {{ font-weight: 700; background: #eef2ff !important; border-top: 2px solid #2c3e50; font-size: 11px; }}
  tfoot .td-num {{ font-size: 13px; color: #2c3e50; }}
  .summary-label {{ text-align: right; font-size: 10px; letter-spacing: 0.3px; }}
  .signatures {{ display: flex; gap: 20px; margin-top: 20px; }}
  .sig-block {{ flex: 1; border-top: 1px solid #888; padding-top: 4px; }}
  .sig-label {{ font-size: 9px; color: #777; text-transform: uppercase; letter-spacing: 0.5px; }}
  .doc-footer {{ margin-top: 14px; font-size: 8px; color: #aaa; text-align: right; border-top: 1px solid #eee; padding-top: 4px; }}
  .print-btn-bar {{ text-align: right; margin-bottom: 12px; }}
  .print-btn {{ background: #2c3e50; color: #fff; border: none; padding: 7px 18px; font-size: 12px; border-radius: 4px; cursor: pointer; letter-spacing: 0.5px; }}
  .print-btn:hover {{ background: #34495e; }}
  @media print {{
    .print-btn-bar {{ display: none; }}
    .page {{ padding: 6mm 8mm; }}
    tr {{ page-break-inside: avoid; }}
  }}
</style>
</head>
<body>
<div class="page">
  <div class="print-btn-bar">
    <button class="print-btn" onclick="window.print()"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px" aria-hidden="true"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>Nyomtatás</button>
  </div>
  <div class="doc-header">
    <div>
      <div class="doc-company">HGL Group Hungary &middot; Ecommerce Operations</div>
    </div>
    <div class="doc-title">LOADING PLAN</div>
  </div>
  <div class="meta-grid">
    <div class="meta-box"><span class="meta-label">GLABS ID</span><span class="meta-value">{glabs_id_e}</span></div>
    <div class="meta-box"><span class="meta-label">Rendszám</span><span class="meta-value">{plate_e}</span></div>
    <div class="meta-box"><span class="meta-label">RAMP</span><span class="meta-value ramp">{ramp_e}</span></div>
    <div class="meta-box"><span class="meta-label">Dátum</span><span class="meta-value">{now_str}</span></div>
  </div>
  <table>
    <thead>
      <tr>
        <th class="td-num">#</th>
        <th>LMP</th>
        <th>AWB</th>
        <th>Rendszám</th>
        <th class="td-num">Colli</th>
        <th class="td-num">PLT</th>
        <th>Korr.</th>
        <th>Tárhely</th>
        <th>Megjegyzés</th>
      </tr>
    </thead>
    <tbody>
{rows_html}    </tbody>
    <tfoot>
      <tr>
        <td colspan="4" class="summary-label">ÖSSZESEN &nbsp; ({row_count} tétel)</td>
        <td class="td-num">{total_colli_disp}</td>
        <td class="td-num">{total_plt_disp}</td>
        <td></td><td></td><td></td>
      </tr>
    </tfoot>
  </table>
  <div class="signatures">
    <div class="sig-block"><div class="sig-label">Kiadta</div></div>
    <div class="sig-block"><div class="sig-label">Átvette</div></div>
    <div class="sig-block"><div class="sig-label">Ellenőrizte</div></div>
  </div>
  <div class="doc-footer">Nyomtatva: {now_str}</div>
</div>
</body>
</html>"""


@server.route("/print/glabs")
def print_glabs_route():
    plate    = request.args.get("plate", "").strip()
    glabs_id = request.args.get("glabs_id", "").strip()
    ramp     = request.args.get("ramp", "").strip()

    state = data_cache.get_state()
    cards = state.get("outbound_cards") or []

    card = next(
        (c for c in cards if (_clean_text(c.get("plate")) or "") == plate),
        None,
    )
    if card is None:
        return f"<p>Rakodás nem található: {plate}</p>", 404

    glabs = next(
        (g for g in card.get("glabs_items", []) if (_clean_text(g.get("glabs_id")) or "") == glabs_id),
        None,
    )
    if glabs is None:
        return f"<p>GLABS nem található: {glabs_id}</p>", 404

    return _generate_print_html(plate, glabs_id, ramp, glabs)


# ---------------------------------------------------------------------------
# ULD Stack API — lightweight REST endpoint for drag-drop and JS interactions
# ---------------------------------------------------------------------------

def _normalize_uld_numbers(value) -> list[str]:
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        clean = _normalize_uld_code(item)
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    return result


def _uld_gha_lookup() -> dict[str, str]:
    state = data_cache.get_state()
    stacks = uld_stack_manager.get_stacks()
    lookup: dict[str, str] = {}
    records = _apply_uld_overrides(_apply_uld_renames(state.get("uld_data", []) or []))
    for uld in _apply_stack_gha_fallback(records, stacks):
        number = _normalize_uld_code(uld.get("uld_number"))
        if number:
            lookup[number] = _normalize_uld_gha(uld.get("gha"))
    for stack in stacks:
        for uld, info in (stack.get("manual_ulds") or {}).items():
            number = _normalize_uld_code(uld)
            gha = (info.get("gha") or "").strip() if isinstance(info, dict) else ""
            if number and gha:
                lookup[number] = gha
    return lookup


def _manual_uld_records(stacks: list[dict]) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    now = datetime.now()
    for stack in stacks:
        manual = stack.get("manual_ulds") if isinstance(stack.get("manual_ulds"), dict) else {}
        for raw_uld, raw_info in manual.items():
            if not isinstance(raw_info, dict):
                continue
            uld = _normalize_uld_code(raw_uld)
            if not uld or uld in seen:
                continue
            seen.add(uld)
            awb = str(raw_info.get("awb") or "").strip()
            awbs = raw_info.get("awbs") if isinstance(raw_info.get("awbs"), list) else ([awb] if awb else [])
            am_raw = str(raw_info.get("am_time") or "").strip()
            am_dt = None
            if am_raw:
                try:
                    am_dt = datetime.fromisoformat(am_raw)
                    if am_dt.tzinfo is not None:
                        am_dt = am_dt.astimezone().replace(tzinfo=None)
                except ValueError:
                    am_dt = None
            # No áttár idő → no deadline at all: empty expiry, no countdown.
            # The item is still a valid kézi ULD, just without an expiry clock.
            if am_dt:
                expiry_dt = am_dt + timedelta(hours=_ULD_DEFAULT_EXPIRY_H)
                remaining_h = (expiry_dt - now).total_seconds() / 3600.0
                expiry_iso = expiry_dt.isoformat()
            else:
                remaining_h = 0.0
                expiry_iso = ""
            records.append({
                "uld_number": uld,
                "uld_type": uld[:3].upper(),
                "awbs": [str(item).strip() for item in awbs if str(item).strip()],
                "lmps": [],
                "gha": str(raw_info.get("gha") or "").strip(),
                "am_time": am_dt.isoformat() if am_dt else "",
                "expiry_time": expiry_iso,
                "expiry_hours": _ULD_DEFAULT_EXPIRY_H,
                "remaining_hours": round(remaining_h, 2),
                "elapsed_hours": round((now - am_dt).total_seconds() / 3600.0, 2) if am_dt else 0.0,
                "is_expired": False,
                "has_expiry": bool(am_dt),
                "status": "kézi",
                "is_manual": True,
            })
    return records


def _with_manual_ulds(all_uld: list[dict], stacks: list[dict]) -> list[dict]:
    by_uld = {_normalize_uld_code(u.get("uld_number")): u for u in all_uld if u.get("uld_number")}
    merged = list(all_uld)
    for record in _manual_uld_records(stacks):
        if record["uld_number"] not in by_uld:
            merged.append(record)
    return merged


def _apply_uld_renames(records: list[dict]) -> list[dict]:
    """Re-apply persisted ULD-number corrections to the display records. Excel
    rebuilds the list from source each refresh, so the override (normalized
    original → new) is layered on here. Records are shallow-copied before mutation
    so the cached state is never altered."""
    renames = uld_stack_manager.get_uld_renames()
    if not renames:
        return records
    out = []
    for r in records:
        key = _normalize_uld_code(r.get("uld_number"))
        new = renames.get(key)
        if new and new != r.get("uld_number"):
            r2 = dict(r)
            r2["uld_number"] = new
            out.append(r2)
        else:
            out.append(r)
    return out


def _apply_uld_overrides(records: list[dict]) -> list[dict]:
    overrides = uld_stack_manager.get_uld_overrides()
    if not overrides:
        return records
    out = []
    for r in records:
        key = _normalize_uld_code(r.get("uld_number"))
        _override_key, override = _find_uld_override(key, overrides)
        if not override:
            out.append(r)
            continue
        out.append(_overlay_uld_override(r, override))
    return out


def _apply_stack_gha_fallback(records: list[dict], stacks: list[dict]) -> list[dict]:
    """Fill a temporarily blank source GHA from persisted active stack identity.

    ``stack_gha`` is written when the first ULD is authoritatively added to a
    stack, so it is a safer fallback than guessing from ULD prefixes or LMP text.
    Conflicting source evidence remains visible and is never overwritten.
    """
    gha_by_uld: dict[str, set[str]] = {}
    for stack in stacks or []:
        if stack.get("dispatched"):
            continue
        stack_gha = _normalize_uld_gha(stack.get("stack_gha"))
        if not stack_gha:
            continue
        for raw_uld in stack.get("ulds", []) or []:
            code = _normalize_uld_code(raw_uld)
            if code:
                gha_by_uld.setdefault(code, set()).add(stack_gha)

    out: list[dict] = []
    for record in records or []:
        code = _normalize_uld_code(record.get("uld_number"))
        candidates = gha_by_uld.get(code, set())
        if (not _normalize_uld_gha(record.get("gha"))
                and not record.get("gha_conflict")
                and len(candidates) == 1):
            updated = dict(record)
            updated["gha"] = next(iter(candidates))
            updated["gha_source"] = "stack"
            out.append(updated)
        else:
            out.append(record)
    return out


def _find_uld_override(uld_number: str, overrides: dict[str, dict] | None = None) -> tuple[str | None, dict | None]:
    key = _normalize_uld_code(uld_number)
    if not key:
        return None, None
    overrides = overrides if overrides is not None else uld_stack_manager.get_uld_overrides()
    if not overrides:
        return None, None
    if key in overrides:
        return key, overrides[key]
    for source_key, info in overrides.items():
        if _normalize_uld_code(info.get("uld_number")) == key:
            return source_key, info
    return None, None


def _overlay_uld_override(record: dict, override: dict | None) -> dict:
    r2 = dict(record or {})
    if not override:
        return r2
    if override.get("uld_number"):
        r2["uld_number"] = override["uld_number"]
        r2["uld_type"] = override["uld_number"][:3].upper()
    if "awbs" in override:
        r2["awbs"] = list(override.get("awbs") or [])
    if override.get("gha"):
        r2["gha"] = override["gha"]
    edited_at = override.get("edited_at") or override.get("szerkesztve_at")
    edited_by = override.get("edited_by") or override.get("szerkesztve_by")
    if edited_at:
        r2["edited_at"] = edited_at
        r2["szerkesztve_at"] = edited_at
    if edited_by:
        r2["edited_by"] = edited_by
        r2["szerkesztve_by"] = edited_by
    if override.get("change_summary"):
        r2["change_summary"] = override["change_summary"]
    r2["is_edited"] = True
    r2["is_szerkesztve"] = True
    return r2


def _stack_uld_info(uld_num: str, uld_lookup: dict, uld_times_full: dict, overrides: dict[str, dict] | None = None) -> dict:
    info = uld_lookup.get(uld_num) or uld_times_full.get(uld_num) or {}
    override_key, override = _find_uld_override(uld_num, overrides)
    if not info and override_key:
        info = uld_lookup.get(override_key) or uld_times_full.get(override_key) or {}
    return _overlay_uld_override(info, override) if override else dict(info or {})


def _parse_uld_cycle_time(value) -> datetime | None:
    """Parse persisted ECOMM/stack timestamps for ULD lifecycle comparisons."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _uld_cycle_times_for_state(state: dict) -> dict[str, dict]:
    """Current open-cycle metadata keyed by canonical ULD number."""
    result: dict[str, dict] = {}
    for raw_uld, info in (state.get("uld_times_full") or {}).items():
        code = _normalize_uld_code(raw_uld)
        if code and isinstance(info, dict):
            result[code] = info
    # Older dashboard-cache payloads can contain ``uld_data`` without the fast
    # reader's full lookup. Keep cycle detection working during that transition.
    for record in state.get("uld_data", []) or []:
        if not isinstance(record, dict):
            continue
        code = _normalize_uld_code(record.get("uld_number"))
        if code and (code not in result or not result[code].get("am_time")):
            result[code] = record
    return result


def _stack_predates_active_uld_cycle(stack: dict, uld_number: str, cycle_times: dict[str, dict]) -> bool:
    """True when a dispatched stack belongs to an older use of the same ULD.

    ULD numbers are reusable. Once E_COMM closes the old AWB row (Vissza filled)
    and opens a later row for the same physical ULD, an earlier dispatched stack
    must no longer hide or lock that new active cycle.
    """
    code = _normalize_uld_code(uld_number)
    info = cycle_times.get(code, {}) if code else {}
    active_am = _parse_uld_cycle_time(info.get("am_time") if isinstance(info, dict) else None)
    dispatched_at = _parse_uld_cycle_time(stack.get("dispatched_at"))
    return bool(active_am and dispatched_at and dispatched_at < active_am)


def _reactivated_uld_codes(uld_numbers: list[str], state: dict, stacks: list[dict]) -> set[str]:
    """ULDs whose every dispatched occurrence predates their current open cycle."""
    requested = set(_normalize_uld_numbers(uld_numbers))
    if not requested:
        return set()
    cycle_times = _uld_cycle_times_for_state(state)
    reactivated: set[str] = set()
    for code in requested:
        occurrences = [
            stack for stack in (stacks or [])
            if stack.get("dispatched")
            and code in set(_normalize_uld_numbers(stack.get("ulds", [])))
        ]
        if occurrences and all(_stack_predates_active_uld_cycle(stack, code, cycle_times) for stack in occurrences):
            reactivated.add(code)
    return reactivated


def _active_uld_records(state: dict, stacks: list[dict]) -> list[dict]:
    cycle_times = _uld_cycle_times_for_state(state)
    dispatched_ulds = set()
    for stack in stacks or []:
        if not stack.get("dispatched"):
            continue
        for uld in stack.get("ulds", []) or []:
            code = _normalize_uld_code(uld)
            if code and not _stack_predates_active_uld_cycle(stack, code, cycle_times):
                dispatched_ulds.add(code)
    active_stacks = [stack for stack in (stacks or []) if not stack.get("dispatched")]
    records = _apply_uld_overrides(_apply_uld_renames(_with_manual_ulds(state.get("uld_data", []) or [], active_stacks)))
    records = _apply_stack_gha_fallback(records, active_stacks)
    if not dispatched_ulds:
        return records
    return [
        record for record in records
        if _normalize_uld_code(record.get("uld_number")) not in dispatched_ulds
    ]


def _active_stacks(stacks: list[dict]) -> list[dict]:
    return [stack for stack in (stacks or []) if not stack.get("dispatched")]


def _dispatched_stacks(stacks: list[dict]) -> list[dict]:
    return [stack for stack in (stacks or []) if stack.get("dispatched")]


def _returned_uld_set(state: dict) -> set:
    """ULD-k, amelyeknek az E_COMM Vissza mezőjében már van dátum."""
    return {_normalize_uld_code(u) for u in (state.get("uld_returned") or []) if u}


def _strip_returned_ulds(stacks: list[dict], returned: set, uld_times_full: dict | None = None) -> list[dict]:
    """Kiküldött nézet: a visszaadott ULD-k lekerülnek a stack sorról;
    ha egy stack minden ULD-je visszaadott, a stack sora sem jelenik meg.

    A korábbi kiküldést akkor is leválasztjuk, ha ugyanaz az ULD azóta egy újabb,
    nyitott E_COMM ciklusban ismét megjelent.
    """
    returned = {_normalize_uld_code(u) for u in (returned or set()) if u}
    cycle_times = {
        _normalize_uld_code(raw_uld): info
        for raw_uld, info in (uld_times_full or {}).items()
        if _normalize_uld_code(raw_uld) and isinstance(info, dict)
    }
    out = []
    for stack in stacks:
        ulds = list(stack.get("ulds", []) or [])
        visible = [
            u for u in ulds
            if _normalize_uld_code(u) not in returned
            and not _stack_predates_active_uld_cycle(stack, u, cycle_times)
        ]
        if not visible:
            continue
        if len(visible) != len(ulds):
            stack = dict(stack, ulds=visible)
        out.append(stack)
    return out


def _visible_stacks_for_uld_view(stacks: list[dict], all_uld: list[dict], search_text: str = "") -> list[dict]:
    if not stacks:
        return []

    active_uld_numbers = set()
    for record in all_uld or []:
        uld_number = _normalize_uld_code(record.get("uld_number"))
        if uld_number:
            active_uld_numbers.add(uld_number)

    # If the ULD source is temporarily empty/loading, keep persisted stacks visible.
    # Hiding only happens once we have an active ULD set to compare against.
    if not active_uld_numbers:
        return list(stacks)

    search_text = search_text or ""
    visible = []
    for stack in stacks:
        raw_ulds = stack.get("ulds", []) or []
        ulds = [u for u in (_normalize_uld_code(uld) for uld in raw_ulds) if u]
        # A stack stays visible if it holds an active ULD. When searching, also keep
        # any stack that *contains* the searched ULD even if that ULD is no longer
        # active (e.g. already returned, or in a prepared stack) — the whole point of
        # a search is to surface where that ULD physically is.
        if not ulds or any(uld in active_uld_numbers for uld in ulds):
            visible.append(stack)
        elif search_text and any(_uld_matches_query(uld, {}, search_text) for uld in raw_ulds):
            visible.append(stack)
    return visible


def _parse_manual_am_time(value: str):
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _manual_duplicate_payload(uld_number: str, awb: str, stacks: list[dict], all_uld: list[dict]) -> dict | None:
    clean_uld = _normalize_uld_code(uld_number)
    clean_awb = str(awb or "").strip()
    stack_by_uld: dict[str, dict] = {}
    for stack in stacks:
        for uld in stack.get("ulds", []) or []:
            normalized = _normalize_uld_code(uld)
            if normalized:
                stack_by_uld[normalized] = stack

    for record in all_uld:
        record_uld = _normalize_uld_code(record.get("uld_number"))
        record_awbs = [str(item).strip() for item in (record.get("awbs") or []) if str(item).strip()]
        match_type = ""
        if clean_uld and record_uld == clean_uld:
            match_type = "uld"
        elif clean_awb and clean_awb in record_awbs:
            match_type = "awb"
        if not match_type:
            continue
        stack = stack_by_uld.get(record_uld)
        return {
            "match_type": match_type,
            "uld_number": record.get("uld_number") or record_uld,
            "awbs": record_awbs,
            "gha": record.get("gha") or "",
            "am_time": _iso_or_str(record.get("am_time")),
            "expiry_time": _iso_or_str(record.get("expiry_time")),
            "status": record.get("status") or "",
            "stack_id": stack.get("id", "") if stack else "",
            "stack_name": stack.get("name", "") if stack else "",
            "is_manual": bool(record.get("is_manual")),
        }
    return None


def _validate_stack_gha_move(stack_id: str, uld_numbers: list[str], stacks: list[dict] | None = None) -> tuple[bool, str]:
    """Keep every stack single-GHA, including multi-select drops into empty stacks."""
    clean_ulds = _normalize_uld_numbers(uld_numbers)
    if not clean_ulds:
        return False, "Nincs érvényes ULD"

    stacks = stacks if stacks is not None else uld_stack_manager.get_stacks()
    target = next((s for s in stacks if s.get("id") == stack_id), None)
    if target is None:
        return False, "Stack nem található"

    tk_ok, tk_err = _validate_stack_tk_move(target, clean_ulds)
    if not tk_ok:
        return False, tk_err

    gha_by_uld = _uld_gha_lookup()
    incoming = set(clean_ulds)
    target_ulds = set(_normalize_uld_numbers(target.get("ulds", [])))
    incoming_ghas = {gha_by_uld.get(uld, "") for uld in clean_ulds}
    missing_gha = "" in incoming_ghas
    incoming_ghas.discard("")
    if len(incoming_ghas) != 1:
        if incoming and incoming.issubset(target_ulds):
            return True, ""
        return False, "Több GHA nem tehető egy stackbe"
    if missing_gha and not incoming.issubset(target_ulds):
        return False, "Ismeretlen GHA nem tehető stackbe"

    existing_ghas: set[str] = set()
    for uld in target.get("ulds", []):
        normalized = _normalize_uld_numbers(uld)
        if not normalized or normalized[0] in incoming:
            continue
        existing_ghas.add(gha_by_uld.get(normalized[0], ""))
    existing_ghas.discard("")
    if len(existing_ghas) > 1:
        return False, "A stack már vegyes GHA-t tartalmaz"
    if existing_ghas and next(iter(existing_ghas)) != next(iter(incoming_ghas)):
        return False, "Más GHA nem tehető ebbe a stackbe"

    return True, ""


def _is_tk_uld(uld_number: str) -> bool:
    """A TK készlet a "TK"-ra végződő ULD-ket jelenti — csak a suffix számít."""
    return _normalize_uld_code(uld_number).endswith("TK")


def _validate_stack_tk_move(target: dict, uld_numbers: list[str]) -> tuple[bool, str]:
    """A TK-ra végződő ULD-ket külön kell kezelni: TK ULD csak olyan stackbe kerülhet,
    amiben kizárólag TK ULD van, és fordítva — nem TK ULD nem mehet TK stackbe.
    A suffixen kívül semmi más nem számít."""
    clean_ulds = _normalize_uld_numbers(uld_numbers)
    if not clean_ulds or not isinstance(target, dict):
        return True, ""
    incoming = set(clean_ulds)
    incoming_tk = {_is_tk_uld(u) for u in clean_ulds}
    if len(incoming_tk) > 1:
        return False, "TK-s és nem TK-s ULD nem tehető egy stackbe"
    incoming_is_tk = next(iter(incoming_tk))
    for uld in target.get("ulds", []):
        normalized = _normalize_uld_code(uld)
        if not normalized or normalized in incoming:
            continue
        if _is_tk_uld(normalized) != incoming_is_tk:
            return False, (
                "TK-ra végződő ULD csak külön, TK-s stackbe tehető"
                if incoming_is_tk else
                "TK-s stackbe csak TK-ra végződő ULD tehető"
            )
    return True, ""


def _find_exact_stack_for_ulds(clean_ulds: list[str], stacks: list[dict]) -> dict | None:
    """Return a stack that already holds exactly this ULD set."""
    wanted = set(_normalize_uld_numbers(clean_ulds))
    if not wanted:
        return None
    for stack in stacks or []:
        # A dispatched stack is history, not an idempotent match for a new active
        # stack. This matters when a returned ULD number is used again.
        if stack.get("dispatched"):
            continue
        existing = set(_normalize_uld_numbers(stack.get("ulds", [])))
        if existing == wanted and len(existing) == len(wanted):
            return stack
    return None


def _single_gha_for_ulds(clean_ulds: list[str]) -> str:
    lookup = _uld_gha_lookup()
    ghas = {lookup.get(uld, "") for uld in _normalize_uld_numbers(clean_ulds)}
    ghas.discard("")
    return next(iter(ghas)) if len(ghas) == 1 else ""


_MANUAL_GHA_OPTIONS = {"Menzies", "AS Cargo", "Celebi"}


def _canonical_manual_gha(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    folded = text.casefold()
    for gha in _MANUAL_GHA_OPTIONS:
        if folded == gha.casefold():
            return gha
    return ""


def _manual_stack_ghas(stack: dict, lookup: dict[str, str]) -> set[str]:
    """Resolve the GHA already represented by a stack, including manual items."""
    existing = {
        lookup.get(_normalize_uld_code(uld), "")
        for uld in stack.get("ulds", [])
        if _normalize_uld_code(uld)
    }
    for raw_info in (stack.get("manual_ulds") or {}).values():
        if isinstance(raw_info, dict):
            existing.add(str(raw_info.get("gha") or "").strip())
    existing.discard("")
    return existing


def _resolve_manual_uld_payload(stack_id: str, uld_number: str, awb: str, requested_gha: str) -> tuple[bool, str, str, dict | None]:
    if not stack_id:
        return False, "Stack nem található", "", None
    if not _normalize_uld_code(uld_number):
        return False, "Egy kötelező elem nincs kitöltve", "", None
    # AWB is optional for manual ULDs; if given it must be numeric.
    awb_clean = str(awb or "").strip()
    if awb_clean and not awb_clean.isdigit():
        return False, "Az AWB csak számjegyeket tartalmazhat", "", None
    incoming_gha = str(requested_gha or "").strip()
    if stack_id == "__new__":
        if incoming_gha not in _MANUAL_GHA_OPTIONS:
            return False, "Egy kötelező elem nincs kitöltve", "", None
        return True, "", incoming_gha, None
    stacks = uld_stack_manager.get_stacks()
    target = next((s for s in stacks if s.get("id") == stack_id), None)
    if target is None:
        return False, "Stack nem található", "", None
    tk_ok, tk_err = _validate_stack_tk_move(target, [uld_number])
    if not tk_ok:
        return False, tk_err, "", target
    lookup = _uld_gha_lookup()
    stack_ghas = _manual_stack_ghas(target, lookup)
    if len(stack_ghas) > 1:
        return False, "A stack már vegyes GHA-t tartalmaz", "", target
    if len(stack_ghas) == 1:
        return True, "", next(iter(stack_ghas)), target
    if incoming_gha in _MANUAL_GHA_OPTIONS:
        return True, "", incoming_gha, target
    return False, "A kiválasztott stack GHA-ja nem azonosítható", "", target


def _validate_manual_uld_payload(stack_id: str, uld_number: str, awb: str, gha: str) -> tuple[bool, str]:
    valid, error, _resolved_gha, _target = _resolve_manual_uld_payload(stack_id, uld_number, awb, gha)
    return valid, error


def _split_awb_values(value) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    parts = raw.replace(";", ",").replace("\n", ",").split(",")
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = "".join(ch for ch in str(part).strip() if ch.isdigit())
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result


def _find_uld_record(records: list[dict], uld_number: str) -> dict | None:
    wanted = _normalize_uld_code(uld_number)
    if not wanted:
        return None
    return next((r for r in records or [] if _normalize_uld_code(r.get("uld_number")) == wanted), None)


def _uld_original_number(display_number: str) -> str:
    """Trace a (possibly renamed) display number back to its source ULD number."""
    key = _normalize_uld_code(display_number)
    if not key:
        return ""
    for orig, cur in (uld_stack_manager.get_uld_renames() or {}).items():
        if _normalize_uld_code(cur) == key:
            return _normalize_uld_code(orig)
    return key


def _uld_edit_is_revert(old_display: str, new_uld: str, awbs: list[str], gha: str) -> bool:
    """True if an edit lands back exactly on the source (E_COMM) values — number,
    AWB set and GHA all match the original — so the override / 'szerk.' marker
    should be cleared instead of recorded."""
    original = _uld_original_number(old_display)
    if not original or _normalize_uld_code(new_uld) != original:
        return False
    source = _find_uld_record(data_cache.get_state().get("uld_data") or [], original)
    if source is None:
        return False
    src_awbs = sorted(
        "".join(ch for ch in str(a) if ch.isdigit())
        for a in (source.get("awbs") or []) if str(a).strip()
    )
    new_awbs = sorted(str(a).strip() for a in (awbs or []) if str(a).strip())
    if new_awbs != src_awbs:
        return False
    return str(gha or "").strip() == str(source.get("gha") or "").strip()


def _validate_uld_edit_payload(old_uld: str, new_uld: str, awbs: list[str], gha: str) -> tuple[bool, str, dict]:
    old_clean = _normalize_uld_code(old_uld)
    new_clean = _normalize_uld_code(new_uld)
    gha = str(gha or "").strip()
    if not old_clean or not new_clean:
        return False, "ULD mező kötelező és érvényes ULD szám kell", {}
    if not _has_valid_uld_prefix(new_clean):
        return False, "Az ULD szám 3 betűs típuskóddal kezdődjön (pl. PMC, AKE)", {}
    if not awbs:
        return False, "AWB mező kötelező, legalább egy számot adj meg", {}
    if gha not in _MANUAL_GHA_OPTIONS:
        return False, "GHA csak ezek közül választható: AS Cargo, Menzies, Celebi", {}

    stacks = uld_stack_manager.get_stacks()
    state = data_cache.get_state()
    records = _active_uld_records(state, stacks)
    current = _find_uld_record(records, old_clean)
    if not current:
        return False, "Az ULD már nem aktív vagy időközben eltűnt a listából", {}

    for record in records:
        rec_uld = _normalize_uld_code(record.get("uld_number"))
        if rec_uld == old_clean:
            continue
        if rec_uld == new_clean:
            return False, f"Az ULD szám már létezik: {new_clean}", {}
        rec_awbs = {str(item).strip() for item in (record.get("awbs") or []) if str(item).strip()}
        clash = rec_awbs & set(awbs)
        if clash:
            return False, f"AWB már másik aktív ULD-n szerepel: {sorted(clash)[0]}", {}

    stack = next((s for s in stacks if old_clean in _normalize_uld_numbers(s.get("ulds", []))), None)
    if stack:
        lookup = {
            _normalize_uld_code(r.get("uld_number")): str(r.get("gha") or "").strip()
            for r in records
            if _normalize_uld_code(r.get("uld_number"))
        }
        other_ghas = {
            lookup.get(_normalize_uld_code(uld), "")
            for uld in stack.get("ulds", []) or []
            if _normalize_uld_code(uld) and _normalize_uld_code(uld) != old_clean
        }
        other_ghas.discard("")
        if other_ghas and other_ghas != {gha}:
            return False, "A szerkesztés vegyes GHA-t hozna létre a stackben", {}

        # TK rule: an edit must not turn a stack into a mixed TK / non-TK set either.
        other_tk = {
            _is_tk_uld(uld)
            for uld in stack.get("ulds", []) or []
            if _normalize_uld_code(uld) and _normalize_uld_code(uld) != old_clean
        }
        if other_tk and other_tk != {_is_tk_uld(new_clean)}:
            return False, "A szerkesztés vegyes TK-tartalmat hozna létre a stackben", {}

    return True, "", {
        "old_uld": old_clean,
        "new_uld": new_clean,
        "old_awbs": list(current.get("awbs") or []),
        "old_gha": str(current.get("gha") or "").strip(),
        "awbs": awbs,
        "gha": gha,
    }


def _parse_top_position(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _uld_stack_api_state() -> dict:
    stacks = uld_stack_manager.get_stacks()
    active_stacks = _active_stacks(stacks)
    state = data_cache.get_state()
    all_uld = _active_uld_records(state, stacks)
    visible_stacks = _visible_stacks_for_uld_view(active_stacks, all_uld)
    uld_times_full = state.get("uld_times_full") or {}
    uld_lookup = {u.get("uld_number"): u for u in all_uld if u.get("uld_number")}
    uld_overrides = uld_stack_manager.get_uld_overrides()
    display_names = _stack_display_name_map(visible_stacks, uld_lookup, stacks)
    gha_color_map = _compute_gha_color_map(all_uld)

    response_stacks = []
    for stack in visible_stacks:
        stack_id = stack.get("id", "")
        ulds_bottom_to_top = list(stack.get("ulds", []))
        top_to_bottom = list(reversed(ulds_bottom_to_top))
        single_gha = _stack_single_gha(stack, uld_lookup)
        gha_color = (gha_color_map or {}).get(single_gha, "") if single_gha else ""
        items = []
        for idx, uld_num in enumerate(top_to_bottom):
            info = _stack_uld_info(uld_num, uld_lookup, uld_times_full, uld_overrides)
            is_active = uld_num in uld_lookup
            prefix = (uld_num or "")[:3].upper()
            items.append({
                "pos": idx + 1,
                "position_label": "teteje" if idx == 0 else ("alja" if idx == len(top_to_bottom) - 1 and len(top_to_bottom) > 1 else ""),
                "uld": uld_num,
                "prefix": prefix,
                "prefix_color": _uld_prefix_color(prefix),
                "gha": info.get("gha", ""),
                "awbs": info.get("awbs", []) or [],
                "status": info.get("status", "inactive"),
                "active": is_active,
                "am": _iso_or_str(info.get("am_time")),
                "expiry": _stack_item_expiry(info),
                "is_edited": bool(info.get("is_edited") or info.get("is_szerkesztve")),
                "edited_at": info.get("edited_at") or info.get("szerkesztve_at", ""),
                "edited_by": info.get("edited_by") or info.get("szerkesztve_by", ""),
                "is_szerkesztve": bool(info.get("is_edited") or info.get("is_szerkesztve")),
                "szerkesztve_at": info.get("szerkesztve_at") or info.get("edited_at", ""),
                "szerkesztve_by": info.get("szerkesztve_by") or info.get("edited_by", ""),
                "change_summary": info.get("change_summary", ""),
            })
        response_stacks.append({
            "id": stack_id,
            "name": stack.get("name", "") or "Stack",
            "display_name": display_names.get(stack_id) or stack.get("name", "") or "Stack",
            "revision": stack.get("revision", 0),
            "created_at": stack.get("created_at", ""),
            "created_by": stack.get("created_by", ""),
            "updated_at": stack.get("updated_at", ""),
            "updated_by": stack.get("updated_by", ""),
            "prepared": bool(stack.get("prepared")),
            "single_gha": single_gha,
            "gha_color": gha_color,
            "ulds_bottom_to_top": ulds_bottom_to_top,
            "items_top_to_bottom": items,
        })

    return {
        "mtime": uld_stack_manager.get_stacks_mtime(),
        "stacks": response_stacks,
    }


def _uld_stack_success(extra: dict | None = None):
    from flask import jsonify
    state = _uld_stack_api_state()
    payload = {"ok": True, "stack_state": state, "stacks": state["stacks"], "stacks_mtime": state["mtime"]}
    if extra:
        payload.update(extra)
    return jsonify(payload)


def _settings_payload() -> dict:
    cfg = flow_config.read_config_snapshot()
    configured_shared_state = str(cfg.get("shared_state_dir") or "").strip()
    try:
        active_shared_state = str(storage_manager._shared_base_dir())
    except OSError:
        active_shared_state = ""
    return {
        "ecomm_file": cfg.get("ecomm_file", ""),
        "pallets_file": cfg.get("pallets_file", ""),
        "shared_state_dir": configured_shared_state,
        "active_shared_state_dir": active_shared_state,
        "shared_state_dir_exists": bool(configured_shared_state and os.path.isdir(configured_shared_state)),
        "refresh_interval_minutes": int(cfg.get("refresh_interval_minutes", 10) or 10),
        "ecomm_source_mode": oracle_ecomm.get_source_mode(),
        "active_ecomm_file": ECOMM_FILE,
        "active_pallets_file": flow_config.PALLETS_FILE,
    }


def _validate_excel_path(path: str, label: str, extensions: tuple[str, ...]) -> tuple[bool, str]:
    clean = str(path or "").strip().strip('"')
    if not clean:
        return False, f"{label} útvonal kötelező"
    if not os.path.isfile(clean):
        return False, f"{label} fájl nem található"
    if not clean.lower().endswith(extensions):
        return False, f"{label} csak Excel fájl lehet"
    return True, clean


@server.route("/api/settings/pick", methods=["POST"])
def api_settings_pick():
    from flask import jsonify
    data = request.get_json(force=True, silent=True) or {}
    kind = str(data.get("kind") or "").strip().lower()
    if kind not in {"ecomm", "pallets", "shared_state"}:
        return jsonify({"ok": False, "error": "Ismeretlen fajl tipus"}), 400
    try:
        if kind == "shared_state":
            selected = flow_config.pick_shared_directory(data.get("current_path"))
        else:
            selected = flow_config.pick_source_file(kind, data.get("current_path"))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    if not selected:
        return jsonify({"ok": True, "cancelled": True})
    return jsonify({"ok": True, "path": selected})


@server.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    from flask import jsonify
    if request.method == "GET":
        return jsonify({"ok": True, "settings": _settings_payload()})

    data = request.get_json(force=True, silent=True) or {}
    current_cfg = flow_config.read_config_snapshot()
    if oracle_ecomm.get_source_mode() == "oracle":
        # Excel paths remain editable fallback settings, but they are not runtime
        # dependencies in full Oracle mode and therefore must not block saving.
        ecomm_or_error = str(data.get("ecomm_file") or current_cfg.get("ecomm_file") or "").strip()
        pallets_or_error = str(data.get("pallets_file") or current_cfg.get("pallets_file") or "").strip()
    else:
        ok, ecomm_or_error = _validate_excel_path(
            data.get("ecomm_file"),
            "E_COMM",
            (".xlsb", ".xlsm", ".xlsx", ".xls"),
        )
        if not ok:
            return jsonify({"ok": False, "error": ecomm_or_error}), 400
        ok, pallets_or_error = _validate_excel_path(
            data.get("pallets_file"),
            "BUD-Pallets",
            (".xlsm", ".xlsx", ".xlsb", ".xls"),
        )
        if not ok:
            return jsonify({"ok": False, "error": pallets_or_error}), 400
    try:
        refresh_minutes = int(data.get("refresh_interval_minutes") or 10)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "A frissítési idő csak szám lehet"}), 400
    refresh_minutes = max(1, min(refresh_minutes, 60))
    shared_state_dir = str(data.get("shared_state_dir") or "").strip().strip('"')
    if shared_state_dir and not os.path.isdir(shared_state_dir):
        return jsonify({"ok": False, "error": "A közös állapotmappa nem található"}), 400
    flow_config.save_config_updates({
        "ecomm_file": ecomm_or_error,
        "pallets_file": pallets_or_error,
        "shared_state_dir": shared_state_dir,
        "refresh_interval_minutes": refresh_minutes,
    })
    activity_log.log_event("settings_save", {"refresh_minutes": refresh_minutes})
    return jsonify({
        "ok": True,
        "settings": _settings_payload(),
        "restart_kötelező": True,
        "message": "Beállítások mentve. Indítsd újra a Flow Managert, hogy az új útvonalak életbe lépjenek.",
    })


@server.route("/api/uld/stack", methods=["POST"])
def api_uld_stack():
    from flask import jsonify
    try:
        data   = request.get_json(force=True, silent=True) or {}
        action = data.get("action", "")
        expected_revision = data.get("stack_revision")
        response_payload = {}
        activity_log.log_event(f"uld_{action or 'ismeretlen'}", {
            k: data.get(k)
            for k in ("stack_id", "uld_number", "uld_numbers", "name", "old", "new", "awb")
            if data.get(k)
        })
        if action in {"add", "add_from_list"}:
            pos = _parse_top_position(data.get("position"))
            uld_numbers = data.get("uld_numbers")
            clean_ulds = _normalize_uld_numbers(uld_numbers if isinstance(uld_numbers, list) else data.get("uld_number"))
            valid, error = _validate_stack_gha_move(data.get("stack_id", ""), clean_ulds)
            if not valid:
                return jsonify({"ok": False, "error": error}), 400
            current_stacks = uld_stack_manager.get_stacks()
            reactivated_ulds = _reactivated_uld_codes(clean_ulds, data_cache.get_state(), current_stacks)
            add_kwargs = {}
            if reactivated_ulds:
                add_kwargs["allow_dispatched_ulds"] = reactivated_ulds
            resolved_gha = _single_gha_for_ulds(clean_ulds)
            ok = uld_stack_manager.add_from_list_to_stack(
                data.get("stack_id", ""),
                clean_ulds,
                position=pos,
                expected_revision=expected_revision,
                stack_gha=resolved_gha,
                **add_kwargs,
            )
            if not ok:
                return jsonify({"ok": False, "error": "ULD vagy stack nem található"}), 400
        elif action == "move_between_stacks":
            pos = _parse_top_position(data.get("position"))
            clean_ulds = _normalize_uld_numbers(data.get("uld_numbers", []))
            target_stack_id = data.get("stack_id", "")
            valid, error = _validate_stack_gha_move(target_stack_id, clean_ulds)
            if not valid:
                return jsonify({"ok": False, "error": error}), 400
            ok = uld_stack_manager.move_between_stacks(
                data.get("source_stack_id", ""),
                target_stack_id,
                clean_ulds,
                position=pos,
                source_expected_revision=data.get("source_stack_revision"),
                target_expected_revision=data.get("target_stack_revision", expected_revision),
                stack_gha=_single_gha_for_ulds(clean_ulds),
            )
            if not ok:
                return jsonify({"ok": False, "error": "ULD vagy stack nem található"}), 400
        elif action in {"remove", "remove_from_stack"}:
            uld_numbers = data.get("uld_numbers")
            if action == "remove_from_stack":
                items_by_stack = data.get("items_by_stack")
                if isinstance(items_by_stack, dict) and items_by_stack:
                    revisions = data.get("stack_revisions") if isinstance(data.get("stack_revisions"), dict) else {}
                    for source_stack_id, source_ulds in items_by_stack.items():
                        if not uld_stack_manager.remove_from_stack(source_stack_id, source_ulds, expected_revision=revisions.get(source_stack_id)):
                            return jsonify({"ok": False, "error": "Stack nem található"}), 400
                else:
                    clean_ulds = _normalize_uld_numbers(uld_numbers if isinstance(uld_numbers, list) else data.get("uld_number"))
                    if not uld_stack_manager.remove_from_stack(data.get("stack_id", ""), clean_ulds, expected_revision=expected_revision):
                        return jsonify({"ok": False, "error": "Stack nem található"}), 400
            else:
                if isinstance(uld_numbers, list):
                    uld_stack_manager.remove_ulds_from_stack(uld_numbers)
                else:
                    uld_stack_manager.remove_uld_from_stack(data.get("uld_number", ""))
                # idempotent: succeed even if ULD was already absent
        elif action == "create":
            response_payload["stack"] = uld_stack_manager.create_stack(
                data.get("name", ""),
                client_op_id=data.get("client_op_id"),
            )
        elif action in {"create_with_ulds", "create_and_add"}:
            pos = _parse_top_position(data.get("position"))
            clean_ulds = _normalize_uld_numbers(data.get("uld_numbers", []))
            if not clean_ulds:
                return jsonify({"ok": False, "error": "Nincs kijelölt ULD"}), 400
            current_stacks = uld_stack_manager.get_stacks()
            existing_stack = _find_exact_stack_for_ulds(clean_ulds, current_stacks)
            if existing_stack is not None:
                resolved_gha = _single_gha_for_ulds(clean_ulds)
                if resolved_gha and not str(existing_stack.get("stack_gha") or "").strip():
                    existing_stack = uld_stack_manager.ensure_stack_gha(existing_stack.get("id", ""), resolved_gha) or existing_stack
                response_payload["stack"] = existing_stack
                response_payload["already_stacked"] = True
                return _uld_stack_success(response_payload)
            valid, error = _validate_stack_gha_move("__new_stack__", clean_ulds, [{"id": "__new_stack__", "ulds": []}])
            if not valid:
                return jsonify({"ok": False, "error": error}), 400
            reactivated_ulds = _reactivated_uld_codes(clean_ulds, data_cache.get_state(), current_stacks)
            create_kwargs = {}
            if reactivated_ulds:
                create_kwargs["allow_dispatched_ulds"] = reactivated_ulds
            new_stack = uld_stack_manager.create_stack_with_ulds(
                data.get("name", ""),
                clean_ulds,
                position=pos,
                client_op_id=data.get("client_op_id"),
                stack_gha=_single_gha_for_ulds(clean_ulds),
                **create_kwargs,
            )
            if not new_stack:
                return jsonify({"ok": False, "error": "Az új stack nem hozható létre"}), 400
            response_payload["stack"] = new_stack
        elif action == "add_manual":
            uld_number = _normalize_uld_code(data.get("uld_number"))
            awb = str(data.get("awb") or "").strip()
            gha = str(data.get("gha") or "").strip()
            stack_id = str(data.get("stack_id") or "").strip()
            valid, error, resolved_gha, _target = _resolve_manual_uld_payload(stack_id, uld_number, awb, gha)
            if not valid:
                return jsonify({"ok": False, "error": error}), 400
            stacks = uld_stack_manager.get_stacks()
            all_uld = _with_manual_ulds(data_cache.get_state().get("uld_data", []) or [], stacks)
            duplicate = _manual_duplicate_payload(uld_number, awb, stacks, all_uld)
            if duplicate:
                return jsonify({
                    "ok": False,
                    "duplicate": duplicate,
                    "error": "Ez az ULD vagy AWB már szerepel a listában",
                }), 409
            am_time = str(data.get("am_time") or "").strip()
            am_dt = _parse_manual_am_time(am_time)
            if am_time and am_dt is None:
                return jsonify({"ok": False, "error": "Érvénytelen áttár idő"}), 400
            if am_dt and am_dt > datetime.now():
                return jsonify({"ok": False, "error": "Az áttár idő nem lehet későbbi a jelenlegi időnél"}), 400
            created_new_stack_id = ""
            if stack_id == "__new__":
                response_payload["stack"] = uld_stack_manager.create_stack(stack_gha=resolved_gha)
                stack_id = response_payload["stack"].get("id", "")
                created_new_stack_id = stack_id
                # A brand-new stack starts at revision 1; the client's stale
                # expected_revision (0/None) would otherwise be ignored anyway, but
                # never let it trip a false conflict against the fresh stack.
                expected_revision = None
            try:
                ok = uld_stack_manager.add_manual_uld_to_stack(
                    stack_id,
                    uld_number=uld_number,
                    awb=awb,
                    gha=resolved_gha,
                    am_time=am_dt.isoformat() if am_dt else am_time,
                    position=_parse_top_position(data.get("position")),
                    expected_revision=expected_revision,
                )
            except Exception:
                # Don't leave an orphaned empty stack behind if the add raises
                # (mirrors the create_with_ulds cleanup contract).
                if created_new_stack_id:
                    uld_stack_manager.delete_stack(created_new_stack_id)
                raise
            if not ok:
                if created_new_stack_id:
                    uld_stack_manager.delete_stack(created_new_stack_id)
                return jsonify({"ok": False, "error": "Egyedi ULD nem adható hozzá"}), 400
        elif action == "delete":
            stack_id = data.get("stack_id", "")
            if not stack_id:
                return jsonify({"ok": False, "error": "Stack nem található"}), 400
            if not uld_stack_manager.delete_stack(stack_id, expected_revision=expected_revision):
                response_payload["already_deleted"] = True
        elif action == "rename":
            if not uld_stack_manager.rename_stack(data.get("stack_id", ""), data.get("name", ""), expected_revision=expected_revision):
                return jsonify({"ok": False, "error": "Stack nem található"}), 400
        elif action == "rename_uld":
            old_uld = _normalize_uld_code(data.get("uld_number"))
            new_uld = _normalize_uld_code(data.get("new_uld_number"))
            if not old_uld or not new_uld:
                return jsonify({"ok": False, "error": "Hiányzó vagy érvénytelen ULD szám"}), 400
            if old_uld == new_uld:
                return jsonify({"ok": False, "error": "Az új szám megegyezik a régivel"}), 400
            if not _has_valid_uld_prefix(new_uld):
                return jsonify({"ok": False, "error": "Az ULD szám 3 betűs típuskóddal kezdődjön (pl. PMC, AKE)"}), 400
            if not uld_stack_manager.rename_uld_everywhere(old_uld, new_uld):
                return jsonify({"ok": False, "error": "Átnevezés sikertelen — lehet, hogy az új szám már létezik"}), 400
            response_payload["renamed"] = {"old": old_uld, "new": new_uld}
        elif action == "edit_uld":
            old_uld = _normalize_uld_code(data.get("uld_number"))
            new_uld = _normalize_uld_code(data.get("new_uld_number"))
            awbs = _split_awb_values(data.get("awb") if data.get("awb") is not None else data.get("awbs"))
            gha = str(data.get("gha") or "").strip()
            valid, error, clean = _validate_uld_edit_payload(old_uld, new_uld, awbs, gha)
            if not valid:
                return jsonify({"ok": False, "error": error}), 400
            # Editing back to the original E_COMM values clears the override so the
            # ULD becomes pristine again (no lingering "szerk." marker).
            is_revert = _uld_edit_is_revert(clean["old_uld"], clean["new_uld"], clean["awbs"], clean["gha"])
            if not uld_stack_manager.update_uld_details(
                clean["old_uld"],
                clean["new_uld"],
                awbs=clean["awbs"],
                gha=clean["gha"],
                revert=is_revert,
                previous_awbs=clean.get("old_awbs"),
                previous_gha=clean.get("old_gha", ""),
            ):
                return jsonify({"ok": False, "error": "ULD módosítás sikertelen"}), 400
            if is_revert:
                response_payload["edited_uld"] = {**clean, "edited_at": "", "edited_by": "", "szerkesztve_at": "", "szerkesztve_by": "", "reverted": True}
                response_payload["szerkesztve_uld"] = response_payload["edited_uld"]
            else:
                _override_key, override = _find_uld_override(clean["new_uld"])
                response_payload["edited_uld"] = {
                    **clean,
                    "edited_at": (override or {}).get("edited_at", ""),
                    "edited_by": (override or {}).get("edited_by", ""),
                    "szerkesztve_at": (override or {}).get("edited_at", "") or (override or {}).get("szerkesztve_at", ""),
                    "szerkesztve_by": (override or {}).get("edited_by", "") or (override or {}).get("szerkesztve_by", ""),
                    "change_summary": (override or {}).get("change_summary", ""),
                }
                response_payload["szerkesztve_uld"] = response_payload["edited_uld"]
        elif action == "prepared":
            prepared = bool(data.get("prepared"))
            # "prepared" is a concurrency-safe metadata flag — intentionally ignore
            # expected_revision so several users can toggle simultaneously without
            # tripping over ULD-content changes happening at the same time.
            if not uld_stack_manager.set_stack_prepared(data.get("stack_id", ""), prepared, expected_revision=None):
                return jsonify({"ok": False, "error": "Stack nem található"}), 400
        elif action == "dispatched":
            stack_id = data.get("stack_id", "")
            if not stack_id:
                return jsonify({"ok": False, "error": "Stack nem található"}), 400
            dispatch_plate = str(data.get("dispatch_plate") or "").strip()
            if not uld_stack_manager.set_stack_dispatched(stack_id, True, expected_revision=expected_revision, dispatch_plate=dispatch_plate):
                return jsonify({"ok": False, "error": "Stack nem található"}), 400
            response_payload["dispatched_stack_id"] = stack_id
        elif action == "undispatch":
            # Revert a dispatch: the stack returns to the active view, fully editable.
            # Concurrency-friendly (no revision check) — it only flips status flags.
            stack_id = data.get("stack_id", "")
            if not stack_id:
                return jsonify({"ok": False, "error": "Stack nem található"}), 400
            if not uld_stack_manager.set_stack_dispatched(stack_id, False, expected_revision=None):
                return jsonify({"ok": False, "error": "Stack nem található"}), 400
            response_payload["undispatched_stack_id"] = stack_id
        elif action in {"reorder", "reorder_stack"}:
            clean_ulds = _normalize_uld_numbers(data.get("ulds", []))
            valid, error = _validate_stack_gha_move(data.get("stack_id", ""), clean_ulds)
            if not valid:
                return jsonify({"ok": False, "error": error}), 400
            if not uld_stack_manager.reorder_stack(data.get("stack_id", ""), clean_ulds, expected_revision=expected_revision):
                return jsonify({"ok": False, "error": "Stack nem található"}), 400
        else:
            return jsonify({"ok": False, "error": "Ismeretlen művelet"}), 400
        return _uld_stack_success(response_payload)
    except uld_stack_manager.StackLocked as exc:
        return jsonify({"ok": False, "error": str(exc), "locked": True}), 423
    except uld_stack_manager.StackValidationError as exc:
        return jsonify({"ok": False, "error": str(exc), "validation": True}), 400
    except uld_stack_manager.StackConflict as exc:
        # Return current revisions with the conflict so a safe/idempotent client
        # operation can refresh its precondition and retry without another full
        # drag gesture.  The authoritative Dash render still owns the final DOM.
        current = _uld_stack_api_state()
        return jsonify({
            "ok": False,
            "error": str(exc),
            "conflict": True,
            "stack_state": current,
            "stacks": current.get("stacks", []),
            "stacks_mtime": current.get("mtime"),
        }), 409
    except TimeoutError as exc:
        log.warning("ULD stack API lock timeout: %s", exc)
        return jsonify({"ok": False, "error": "Stack mentes foglalt, probald ujra"}), 423
    except Exception as exc:
        log.warning("ULD stack API error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------

_COLOR = {
    "b2b":           "#ef4444",
    "at-hu":         "#ff6b35",
    "driver-active": "#00d4aa",
    "driver-rest":   "#7c72dc",
    "loading-plan":  "#4a9eff",
    "standard":      "#8b949e",
    "en-route":      "#38bdf8",
}

_TRUCK_ARRIVAL_DELAY_MINUTES = 30


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _fmt_kg(v) -> str:
    if v is None or v == 0:
        return "–"
    return f"{v:,.0f} kg".replace(",", " ")  # narrow no-break space as thousands separator


def _fmt_kg_num(v) -> str:
    """Compact kg value without the unit — used on the small ULD/PLT flip face
    where width is tight; the unit is implied by the kg face it flips from."""
    if v is None or v == 0:
        return "–"
    return f"{v:,.0f}".replace(",", " ")


def _fmt_tonnes(v) -> str:
    """Ultra-compact tonne value for the condensed-header mini cells
    ('12t', '3,4t', '0,2t') — width matters more than precision there;
    the exact kg lives in the cell tooltip and the floating bec panel."""
    try:
        t = float(v or 0) / 1000.0
    except (TypeError, ValueError):
        t = 0.0
    if t <= 0:
        return "0t"
    if t >= 9.95:
        return f"{t:,.0f}t".replace(",", " ")
    return f"{t:.1f}t".replace(".", ",")


def _fmt_kg_copy(v) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        n = 0.0
    return f"{n:,.0f} kg".replace(",", " ")


def _kpi_shift_copy_text(kpi: dict, mode: str) -> str:
    outbound = mode == "outbound"
    prefix = "Outbound mennyiség" if outbound else "Inbound mennyiség"
    current_name = kpi.get("outbound_shift_name" if outbound else "shift_name", "Műszak")
    current_range = kpi.get("outbound_shift_range" if outbound else "shift_range", "")
    current_kg = kpi.get("outbound_shift_kg" if outbound else "shift_kg")
    prev_name = kpi.get("outbound_shift_prev_name" if outbound else "shift_prev_name", "Előző műszak")
    prev_range = kpi.get("outbound_shift_prev_range" if outbound else "shift_prev_range", "")
    prev_kg = kpi.get("outbound_shift_prev_kg")
    if not outbound:
        prev_kg = sum(float((h or {}).get("kg") or 0) for h in (kpi.get("shift_prev_hourly") or []))
    lines = [
        prefix,
        f"{current_name} ({current_range}): {_fmt_kg_copy(current_kg)}",
    ]
    lines.append(f"{prev_name} ({prev_range}): {_fmt_kg_copy(prev_kg)}")
    return "\n".join(lines)


def _kpi_bec_copy_text(kpi: dict) -> str:
    """Structured payload for the Várható beérkező copy. The client (kpi_chart.js)
    parses this JSON and builds a colour-coded HTML table for the clipboard, with a
    tab-separated plain-text fallback. Numbers are sent raw; the client formats."""
    rows_def = [
        ("Felvéve",     "bec_felveve",     "bec_felveve_uld",     "bec_felveve_plt"),
        ("Értesítő",    "bec_ertesito",    "bec_ertesito_uld",    "bec_ertesito_plt"),
        ("Megérkezett", "bec_megerkezett", "bec_megerkezett_uld", "bec_megerkezett_plt"),
        ("Szemlés",     "bec_szemles",     "bec_szemles_uld",     "bec_szemles_plt"),
    ]

    def _num(v) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    rows = []
    uld_total = 0.0
    plt_total = 0.0
    for label, kg_key, uld_key, plt_key in rows_def:
        uld = _num(kpi.get(uld_key))
        plt = _num(kpi.get(plt_key))
        uld_total += uld
        plt_total += plt
        rows.append({"label": label, "kg": _num(kpi.get(kg_key)), "uld": uld, "plt": plt})

    return json.dumps(
        {
            "title": "Várható beérkező",
            "total": _num(kpi.get("bec_total")),
            "uld_total": uld_total,
            "plt_total": plt_total,
            "rows": rows,
        },
        ensure_ascii=False,
    )


def _ecomm_source_badge():
    mode = oracle_ecomm.get_source_mode()
    if mode == "oracle":
        state = data_cache.get_state()
        kpi = state.get("kpi") or {}
        raw_ts = kpi.get("oracle_last_refresh")
        try:
            refreshed = datetime.fromisoformat(raw_ts) if raw_ts else None
        except (TypeError, ValueError):
            refreshed = None
        if refreshed is None:
            return html.Span("ECOMM Oracle: kapcsolódásra vár", className="source-age source-stale")
        age_minutes = max(0.0, (datetime.now() - refreshed).total_seconds() / 60.0)
        age_text = "most" if age_minutes < 1 else (f"{int(age_minutes)} perce" if age_minutes < 60 else f"{age_minutes / 60:.1f} órája")
        stalled = bool(state.get("read_stalled"))
        return html.Span(
            f"ECOMM Oracle: {refreshed.strftime('%H:%M:%S')} ({age_text})",
            className=f"source-age {'source-stale' if stalled else 'source-fresh'}",
            title=(
                f"Oracle watermark: {kpi.get('oracle_watermark', '—')} · "
                f"head: {kpi.get('oracle_head_count', '—')} · detail: {kpi.get('oracle_detail_count', '—')}"
            ),
        )

    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(ECOMM_FILE))
    except OSError:
        return html.Span("ECOMM: nem található", className="source-age source-stale")

    age_seconds = max(0.0, (datetime.now() - mtime).total_seconds())
    age_minutes = age_seconds / 60.0
    if age_minutes < 1:
        age_text = "most"
    elif age_minutes < 60:
        age_text = f"{int(age_minutes)} perce"
    else:
        age_text = f"{age_minutes / 60:.1f} oraja"

    stale = age_minutes > ECOMM_STALE_AFTER_MINUTES
    shadow_suffix = " + Oracle shadow" if mode == "shadow" else ""
    return html.Span(
        f"ECOMM Excel{shadow_suffix}: {mtime.strftime('%H:%M:%S')} ({age_text})",
        className=f"source-age {'source-stale' if stale else 'source-fresh'}",
        title=f"E_COMM utolsó módosítás: {mtime.strftime('%Y-%m-%d %H:%M:%S')}",
    )


def _pallets_source_badge():
    """Same freshness indicator as ECOMM, but for the BUD-Pallets workbook — so a
    stalled BUD-Pallets save is just as visible as a stalled E_COMM one."""
    if oracle_ecomm.get_source_mode() == "oracle":
        state = data_cache.get_state()
        kpi = state.get("kpi") or {}
        raw_ts = kpi.get("oracle_last_refresh")
        try:
            refreshed = datetime.fromisoformat(raw_ts) if raw_ts else None
        except (TypeError, ValueError):
            refreshed = None
        if refreshed is None:
            return html.Span("PALLETS Oracle: kapcsolódásra vár", className="source-age source-stale")
        age_minutes = max(0.0, (datetime.now() - refreshed).total_seconds() / 60.0)
        age_text = "most" if age_minutes < 1 else (
            f"{int(age_minutes)} perce" if age_minutes < 60 else f"{age_minutes / 60:.1f} órája"
        )
        return html.Span(
            f"PALLETS Oracle: {refreshed.strftime('%H:%M:%S')} ({age_text})",
            className=f"source-age {'source-stale' if state.get('read_stalled') else 'source-fresh'}",
            title=(
                f"Oracle BUD Pallets sor: {kpi.get('oracle_pallet_rows', '—')} · "
                f"lokációval: {kpi.get('oracle_pallet_locations', '—')}"
            ),
        )
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(flow_config.PALLETS_FILE))
    except OSError:
        return html.Span("PALLETS: nem található", className="source-age source-stale")

    age_seconds = max(0.0, (datetime.now() - mtime).total_seconds())
    age_minutes = age_seconds / 60.0
    if age_minutes < 1:
        age_text = "most"
    elif age_minutes < 60:
        age_text = f"{int(age_minutes)} perce"
    else:
        age_text = f"{age_minutes / 60:.1f} oraja"

    stale = age_minutes > ECOMM_STALE_AFTER_MINUTES
    return html.Span(
        f"PALLETS: {mtime.strftime('%H:%M:%S')} ({age_text})",
        className=f"source-age {'source-stale' if stale else 'source-fresh'}",
        title=f"BUD-Pallets utolsó módosítás: {mtime.strftime('%Y-%m-%d %H:%M:%S')}",
    )


def _sync_stall_badge():
    """App-level 'the refresh itself is stuck' warning.

    Distinct from the source-age badges (which show the Excel file mtime): this
    fires when the background read timed out or hard-failed, so the shown data is
    the last good snapshot. Makes the 'sees only own/old version' situation
    visible instead of silent.
    """
    state = data_cache.get_state()
    if not state.get("read_stalled"):
        return None
    last_ok = state.get("last_success_refresh")
    if last_ok:
        mins = int(max(0.0, (datetime.now() - last_ok).total_seconds()) // 60)
        age_txt = "most" if mins < 1 else f"{mins} perce"
        text = f"Szinkron elakadt — utolsó friss adat: {last_ok.strftime('%H:%M')} ({age_txt})"
    else:
        text = "Szinkron elakadt — még nincs friss adat"
    reason = state.get("read_stall_reason") or ""
    return html.Span(
        text,
        className="sync-stall-badge",
        title=f"A háttérfrissítés elakadt. Ok: {reason}. Az app az utolsó jó adatot mutatja." if reason else "A háttérfrissítés elakadt. Az app az utolsó jó adatot mutatja.",
    )


def _glass_fx(level: str = "l1"):
    """Decorative Apple-style liquid-glass overlay: specular sheen, chromatic
    edge ring and (L1 only) edge refraction/distortion. Purely visual, sits
    behind the content (pointer-events:none), never interactive."""
    return html.Div(className=f"lg-fx lg-{level}", **{"aria-hidden": "true"})


def _sub_card(val_id: str, label: str, color_cls: str):
    # val_id e.g. "kpi-bec-felveve"; the main value keeps that id, the ULD/PLT
    # split values get <id>-uld / <id>-plt. Click toggles a vertical reel animation.
    return html.Div(
        className=f"kpi-card kpi-sub-card {color_cls}",
        id=f"{val_id}-card", n_clicks=0,
        title="Kattints az ULD / PLT kg bontásért",
        children=[
            _glass_fx("l2"),
            html.Div(className="kpi-sub-title-window", children=[
                html.Div(label, className="kpi-label kpi-sub-title"),
            ]),
            html.Div(className="kpi-sub-value-window", children=[
                html.Div(className="kpi-sub-value-reel", children=[
                    html.Div(id=val_id, className="kpi-value kpi-sub-main-value", children="–"),
                    html.Div(className="kpi-sub-breakdown", children=[
                        html.Div(className="kpi-split-row", children=[
                            html.Span("ULD", className="kpi-split-tag kpi-split-tag-uld"),
                            html.Span(id=f"{val_id}-uld", className="kpi-split-val", children="–"),
                        ]),
                        html.Div(className="kpi-split-row", children=[
                            html.Span("PLT", className="kpi-split-tag kpi-split-tag-plt"),
                            html.Span(id=f"{val_id}-plt", className="kpi-split-val", children="–"),
                        ]),
                    ]),
                ]),
            ]),
        ],
    )


def _mini_chart_segment(key: str, label: str, short: str) -> html.Div:
    return html.Div(
        id=f"kpi-chart-{key}-segment",
        className=f"kpi-chart-segment kpi-chart-{key}",
        title=f"{label}: –",
        **{"data-tooltip": f"{label}: –"},
        style={"width": "33.333%"},
        children=html.Span(short, className="kpi-chart-segment-label"),
    )


def _kpi_mini_chart_layout() -> html.Div:
    return html.Div(className="kpi-composition-card", children=[
        _glass_fx("l1"),
        html.Div(className="kpi-composition-track", children=[
            _mini_chart_segment("felveve",     "Felvéve",    "FEL"),
            _mini_chart_segment("ertesito",    "Értesítő",   "ÉRT"),
            _mini_chart_segment("megerkezett", "Megérkezett","MEG"),
            _mini_chart_segment("szemles",     "Szemlés",    "SZEM"),
        ]),
        # Hover plate at the END of the bar — shows the hovered status's % + kg.
        # Filled client-side from each segment's data-tooltip (assets/kpi_comp_plate.js).
        html.Div(id="kpi-comp-plate", className="kpi-comp-plate", **{"aria-hidden": "true"}),
    ])


def _kpi_static_layout():
    """Static KPI card structure — values updated via targeted callbacks,
    never fully re-rendered, so the bec-pinned CSS class survives data refreshes."""
    return [
        # Wrapper anchors the floating hourly-chart panel; the panel must live
        # OUTSIDE .kpi-card (overflow:hidden) so it isn't clipped — mirrors kpi-bec.
        html.Div(id="kpi-muszak-wrapper", className="kpi-muszak-wrapper", children=[
            html.Div(id="kpi-muszak-card", className="kpi-card kpi-muszak", n_clicks=0, children=[
                _glass_fx("l1"),
                html.Div(className="kpi-card-tools kpi-shift-tools", children=[
                    html.Div(className="kpi-mode-toggle", children=[
                        html.Button("IN", id="kpi-mode-inbound", className="kpi-mode-btn is-active",
                                    n_clicks=0, title="Inbound műszak"),
                        html.Button("OUT", id="kpi-mode-outbound", className="kpi-mode-btn",
                                    n_clicks=0, title="Outbound műszak"),
                    ]),
                    html.Button("⧉", id="kpi-muszak-copy", className="kpi-copy-btn",
                                n_clicks=0, title="Műszak KPI másolása",
                                **{"aria-label": "Műszak KPI másolása",
                                   "data-copy-text": ""}),
                ]),
                # Same anatomy as the bec card (.kpi-bec-summary) so both KPI cards
                # share an identical content box and therefore the same height.
                html.Div(className="kpi-bec-summary", children=[
                    html.Div(id="kpi-muszak-label", className="kpi-label", children="Műszak"),
                    html.Div(id="kpi-muszak-value", className="kpi-value kpi-countup", children="–"),
                    html.Span("▾", className="kpi-expand-arrow"),
                ]),
                # Kept (hidden) only so the existing kpi-muszak-sub callback Output
                # still has a target; the range is shown in the panel header instead.
                html.Div(id="kpi-muszak-sub", className="kpi-sub", children="",
                         style={"display": "none"}),
            ]),
            # Shift step arrows — children of the WRAPPER (not the card) so they
            # overlay the card edges without ever affecting the card's height.
            # Synced with the panel arrows via the shared .kpi-shift-nav class.
            html.Button("‹", className="kpi-shift-nav kpi-shift-nav-card kpi-shift-prev",
                        **{"data-shift-dir": "prev", "aria-label": "Előző műszak",
                           "title": "Előző műszak"}),
            html.Button("›", className="kpi-shift-nav kpi-shift-nav-card kpi-shift-next is-hidden",
                        **{"data-shift-dir": "next", "aria-label": "Aktuális műszak",
                           "title": "Aktuális műszak"}),
            # Floating panel — sibling of the card, hosts the óránkénti kg chart.
            html.Div(className="kpi-muszak-panel", children=[
                html.Div(className="kpi-muszak-panel-arrow"),
                html.Div(className="kpi-muszak-panel-inner", children=[
                    _glass_fx("l1"),
                    html.Div(className="kpi-hourly-head", children=[
                        html.Button("‹", className="kpi-shift-nav kpi-shift-nav-panel kpi-shift-prev",
                                    **{"data-shift-dir": "prev", "title": "Előző műszak",
                                       "aria-label": "Előző műszak"}),
                        html.Span(id="kpi-hourly-title", className="kpi-hourly-title", children="Óránkénti kg"),
                        html.Button("›", className="kpi-shift-nav kpi-shift-nav-panel kpi-shift-next is-hidden",
                                    **{"data-shift-dir": "next", "title": "Aktuális műszak",
                                       "aria-label": "Aktuális műszak"}),
                    ]),
                    html.Div(id="kpi-muszak-chart", className="kpi-hourly-chart"),
                ]),
            ]),
        ]),
        # Wrapper provides the relative positioning anchor for the floating panel.
        # The panel must be OUTSIDE .kpi-card (which has overflow:hidden) so it isn't clipped.
        html.Div(id="kpi-bec-wrapper", className="kpi-bec-wrapper", children=[
            html.Div(id="kpi-bec-card", className="kpi-card kpi-bec", n_clicks=0, children=[
                _glass_fx("l1"),
                html.Div(className="kpi-card-tools", children=[
                    html.Button("⧉", id="kpi-bec-copy", className="kpi-copy-btn",
                                n_clicks=0, title="Várható beérkezők másolása",
                                **{"aria-label": "Várható beérkezők másolása",
                                   "data-copy-text": ""}),
                ]),
                html.Div(className="kpi-bec-summary", children=[
                    html.Div("Várható beérkező", className="kpi-label"),
                    html.Div(id="kpi-bec-total", className="kpi-value kpi-countup", children="–"),
                    html.Span("▾", className="kpi-expand-arrow"),
                ]),
                # Condensed-header (hdr-mini) replacement for the total value:
                # the four status squares with compact tonne values. Hidden in
                # the full header — CSS shows it only while scrolled (hdr-mini).
                html.Div(className="kpi-bec-mini-grid", children=[
                    html.Div(id="kpi-bec-mini-felveve",
                             className="kpi-bec-mini-cell mini-felveve",
                             title="Felvéve: –", children="–"),
                    html.Div(id="kpi-bec-mini-ertesito",
                             className="kpi-bec-mini-cell mini-ertesito",
                             title="Értesítő: –", children="–"),
                    html.Div(id="kpi-bec-mini-megerkezett",
                             className="kpi-bec-mini-cell mini-megerkezett",
                             title="Megérkezett: –", children="–"),
                    html.Div(id="kpi-bec-mini-szemles",
                             className="kpi-bec-mini-cell mini-szemles",
                             title="Szemlés: –", children="–"),
                ]),
            ]),
            # Floating panel — sibling of the card, not clipped by overflow:hidden
            html.Div(className="kpi-bec-panel", children=[
                html.Div(className="kpi-bec-panel-arrow"),
                html.Div(className="kpi-bec-panel-inner", children=[
                    _glass_fx("l1"),
                    _sub_card("kpi-bec-felveve",     "Felvéve",     "kpi-sub-felveve"),
                    _sub_card("kpi-bec-ertesito",    "Értesítő",    "kpi-sub-ertesito"),
                    _sub_card("kpi-bec-megerkezett", "Megérkezett", "kpi-sub-megerkezett"),
                    _sub_card("kpi-bec-szemles",     "Szemlés",     "kpi-sub-szemles"),
                ]),
            ]),
        ]),
        _kpi_mini_chart_layout(),
    ]


def _kpi_page_chart_shell(key: str, label: str, accent_label: str) -> html.Div:
    return html.Section(
        className=f"kpi-page-chart-card kpi-page-chart-{key}",
        **{"data-kpi-flow": key},
        children=[
            html.Div(className="kpi-page-card-glow"),
            html.Div(className="kpi-page-chart-head", children=[
                html.Div(children=[
                    *( [html.Div(accent_label, className="kpi-page-kicker")] if accent_label else [] ),
                    html.H2(label, className="kpi-page-chart-title"),
                ]),
                html.Div(className="kpi-page-chart-legend"),
            ]),
            html.Div(className="kpi-page-chart-metrics", children=[
                html.Div(
                    className="kpi-page-metric current is-active-view",
                    role="button", tabIndex=0,
                    title="Show the current shift on the chart",
                    **{"data-flow": key, "data-view": "current"},
                    children=[
                        html.Span("Current", className="kpi-page-metric-label"),
                        html.Strong(id=f"kpi-page-{key}-current-kg", children="–"),
                        html.Span(id=f"kpi-page-{key}-current-range", className="kpi-page-metric-range", children=""),
                    ],
                ),
                html.Div(
                    className="kpi-page-metric prev",
                    role="button", tabIndex=0,
                    title="Show the previous shift on the chart",
                    **{"data-flow": key, "data-view": "prev"},
                    children=[
                        html.Span("Previous", className="kpi-page-metric-label"),
                        html.Strong(id=f"kpi-page-{key}-prev-kg", children="–"),
                        html.Span(id=f"kpi-page-{key}-prev-range", className="kpi-page-metric-range", children=""),
                    ],
                ),
            ]),
            html.Div(
                id=f"kpi-page-{key}-chart",
                className="kpi-page-chart-canvas",
                **{"data-kpi-chart": key},
            ),
        ],
    )


def _kpi_trend_shell() -> html.Section:
    """Full-width volume-trend chart above the inbound/outbound shift charts.

    The interactive guts (breakdown + view segmented controls, series chips,
    SVG multi-line render, hover) live in assets/kpi_trend.js — the containers
    below are filled client-side from the ``trend`` slice of the KPI payload."""
    return html.Section(id="kpi-trend-section", className="kpi-snap-section kpi-trend-section", children=[
        html.Div(className="kpi-trend-card-glow"),
        html.Div(className="kpi-trend-head", children=[
            html.Div(className="kpi-trend-head-titles", children=[
                html.Div("Volume trend", className="kpi-page-kicker"),
                html.H2("Inbound trend", id="kpi-trend-title", className="kpi-trend-title"),
            ]),
            html.Div(className="kpi-trend-toolbar", children=[
                # Breakdown (3 filters) — LMP / Országok / Prefix
                html.Div(className="kpi-trend-ctl", id="kpi-trend-breakdown", role="tablist"),
                # View toggle — Nap / Hét
                html.Div(className="kpi-trend-ctl kpi-trend-ctl-view", id="kpi-trend-view",
                         role="tablist"),
            ]),
        ]),
        html.Div(id="kpi-trend-range", className="kpi-trend-range"),
        html.Div(id="kpi-trend-filter", className="kpi-trend-filter"),
        html.Div(className="kpi-trend-chart-shell", children=[
            html.Div(id="kpi-trend-chart", className="kpi-trend-chart"),
            html.Div(id="kpi-trend-table", className="kpi-trend-table-panel"),
        ]),
    ])


def _kpi_temu_shell() -> html.Section:
    """TEMU milestone stage-duration chart (stacked columns, AVG per stage).

    Mirrors the "TEMU KPI - BUD wNN" report: each shipment's lead time is split
    into five consecutive milestone gaps (ATA→NOA … Outbound) and the per-period
    average of each gap is stacked. Rendered client-side from the ``temu_stages``
    slice of the KPI payload by assets/kpi_temu.js. Day/Week toggle + country
    pre-filter chips + per-bar selection live in the JS; the shell is the frame."""
    return html.Section(id="kpi-temu-section", className="kpi-snap-section kpi-temu-section", children=[
        html.Div(className="kpi-temu-card-glow"),
        html.Div(className="kpi-temu-head", children=[
            html.Div(className="kpi-temu-head-titles", children=[
                html.Div("Lead-time stages", className="kpi-page-kicker"),
                html.H2("TEMU KPI", id="kpi-temu-title", className="kpi-temu-title"),
                html.Div(id="kpi-temu-subtitle", className="kpi-temu-subtitle",
                         children="Average stage durations by ATA day"),
            ]),
            html.Div(className="kpi-temu-toolbar", children=[
                # From/To date range pickers (rendered client-side).
                html.Div(className="kpi-temu-ctl kpi-temu-ctl-dates", id="kpi-temu-dates"),
                # Compare toggle — pin two ranges (A vs B) on the chart
                html.Div(className="kpi-temu-ctl kpi-temu-ctl-compare", id="kpi-temu-compare",
                         role="tablist"),
                # View toggle — Day / Week
                html.Div(className="kpi-temu-ctl kpi-temu-ctl-view", id="kpi-temu-view",
                         role="tablist"),
            ]),
        ]),
        # Country pre-filter chips (All + per country).
        html.Div(id="kpi-temu-filter", className="kpi-temu-filter"),
        # Inspected-period range row: presets + active range label (brush lives on the chart).
        html.Div(id="kpi-temu-range", className="kpi-temu-range"),
        # Summary stat chips (avg total lead time + Δ, per-stage chips, fastest/slowest).
        html.Div(id="kpi-temu-summary", className="kpi-temu-summary"),
        html.Div(className="kpi-temu-chart-shell", children=[
            html.Div(id="kpi-temu-chart", className="kpi-temu-chart"),
            html.Div(id="kpi-temu-detail", className="kpi-temu-detail"),
            html.Div(id="kpi-temu-legend", className="kpi-temu-legend"),
        ]),
        # Flagged records (drawer): TEMU shipments with unreliable milestone dates
        # that were excluded from the averages — surfaced so they can be fixed at source.
        html.Div(id="kpi-temu-suspects", className="kpi-temu-suspects-panel"),
        # Periods × stages data table (drawer) with XLSX export.
        html.Div(id="kpi-temu-table", className="kpi-temu-table-panel"),
    ])


def _kpi_flow_section() -> html.Section:
    """Paired inbound/outbound shift-volume line charts (2-műszakos volume)."""
    return html.Section(className="kpi-snap-section kpi-flow-section", children=[
        html.Div(className="kpi-flow-mode-row", children=[
            html.Div(className="kpi-flow-mode-control", id="kpi-flow-mode-control",
                     role="tablist", **{"aria-label": "KPI flow chart mode"}, children=[
                html.Button("Shift based", className="kpi-flow-mode-btn is-on",
                            id="kpi-flow-mode-shift", role="tab",
                            **{"data-flow-mode": "shift", "aria-selected": "true"}, n_clicks=0),
                html.Button("Day based", className="kpi-flow-mode-btn",
                            id="kpi-flow-mode-day", role="tab",
                            **{"data-flow-mode": "day", "aria-selected": "false"}, n_clicks=0),
            ]),
        ]),
        html.Div(className="kpi-page-grid", children=[
            _kpi_page_chart_shell("inbound", "Inbound", ""),
            _kpi_page_chart_shell("outbound", "Outbound", ""),
        ]),
        html.Div(id="kpi-flow-day-panel", className="kpi-flow-day-panel", children=[
            html.Div(className="kpi-flow-day-head", children=[
                html.Div(className="kpi-flow-day-titleblock", children=[
                    html.H2("Inbound vs outbound", className="kpi-flow-day-title"),
                    html.Div("Daily volume comparison", className="kpi-flow-day-subtitle"),
                ]),
                html.Div(className="kpi-flow-day-controls", children=[
                    html.Div(className="kpi-flow-period-control", id="kpi-flow-period-control",
                             role="tablist", **{"aria-label": "KPI flow day range"}, children=[
                        html.Button("7 day", className="kpi-flow-period-btn is-on",
                                    role="tab", **{"data-flow-days": "7", "aria-selected": "true"}, n_clicks=0),
                        html.Button("14 day", className="kpi-flow-period-btn",
                                    role="tab", **{"data-flow-days": "14", "aria-selected": "false"}, n_clicks=0),
                    ]),
                    html.Div(className="kpi-flow-unit-control", id="kpi-flow-unit-control",
                             role="tablist", **{"aria-label": "KPI flow unit"}, children=[
                        html.Button("kg", className="kpi-flow-unit-btn is-on",
                                    role="tab", **{"data-flow-unit": "kg", "aria-selected": "true"}, n_clicks=0),
                        html.Button("colli", className="kpi-flow-unit-btn",
                                    role="tab", **{"data-flow-unit": "colli", "aria-selected": "false"}, n_clicks=0),
                        html.Button("parcel", className="kpi-flow-unit-btn",
                                    role="tab", **{"data-flow-unit": "parcel", "aria-selected": "false"}, n_clicks=0),
                    ]),
                ]),
            ]),
            html.Div(className="kpi-flow-day-summary", children=[
                html.Div(className="kpi-flow-day-stat inbound", children=[
                    html.Span("Inbound", className="kpi-flow-day-stat-label"),
                    html.Strong(id="kpi-flow-day-inbound-total-value", children="–"),
                    html.Small(id="kpi-flow-day-inbound-total-meta", children="Period total"),
                ]),
                html.Div(className="kpi-flow-day-stat inbound average", children=[
                    html.Span("Inbound avg/day", className="kpi-flow-day-stat-label"),
                    html.Strong(id="kpi-flow-day-inbound-avg-value", children="–"),
                    html.Small(id="kpi-flow-day-inbound-avg-meta", children="Daily average"),
                ]),
                html.Div(className="kpi-flow-day-stat outbound", children=[
                    html.Span("Outbound", className="kpi-flow-day-stat-label"),
                    html.Strong(id="kpi-flow-day-outbound-total-value", children="–"),
                    html.Small(id="kpi-flow-day-outbound-total-meta", children="Period total"),
                ]),
                html.Div(className="kpi-flow-day-stat outbound average", children=[
                    html.Span("Outbound avg/day", className="kpi-flow-day-stat-label"),
                    html.Strong(id="kpi-flow-day-outbound-avg-value", children="–"),
                    html.Small(id="kpi-flow-day-outbound-avg-meta", children="Daily average"),
                ]),
            ]),
            html.Div(id="kpi-flow-day-chart", className="kpi-flow-day-chart"),
        ]),
    ])


def _kpi_tracking_section() -> html.Section:
    """Warehouse tracking (KPI Tracking workbook parity) — the lead panel of the
    default subpage."""
    return html.Section(className="kpi-snap-section kpi-tracking-section", children=[
        html.Div(className="kpi-tracking-head", children=[
            html.Div(children=[
                html.H2("Warehouse tracking", className="kpi-tracking-title"),
                html.Div("Click a group for the breakdown", className="kpi-tracking-subtitle"),
            ]),
            html.Div("500 000 kg capacity", className="kpi-tracking-capacity"),
        ]),
        html.Div(id="kpi-tracking-summary", className="kpi-tracking-summary", children=[
            html.Section(className=f"kpi-tracking-group {group_cls}", children=[
                html.Div(className="kpi-tracking-group-head", children=[
                    html.Span(title),
                ]),
                html.Div(className="kpi-tracking-group-grid", children=[
                    html.Div(className="kpi-tracking-card skeleton", children=[
                        html.Span(label),
                        html.Strong("–"),
                    ])
                    for label in labels
                ]),
            ])
            for group_cls, title, labels in (
                ("warehouse", "Warehouse", (
                    "Current warehouse saturation",
                    "Active items",
                )),
                ("operations", "12h operations", (
                    "To transfer (NOA + ATA)",
                    "To release (releasable)",
                    "ATA > 12h, not transferred",
                )),
                ("arrivals", "Expected arrivals", (
                    "Expected arrivals",
                    "Already arrived",
                    "Already transferred",
                    "Remaining expected",
                )),
                ("forecast", "Unit ratios", (
                    "Status composition",
                    "Kg / parcel",
                    "Kg / colli",
                    "Kg / AWB",
                )),
            )
        ]),
        html.Div(id="kpi-tracking-detail", className="kpi-tracking-detail"),
    ])


def _kpi_page_layout() -> html.Div:
    """KPI page split into two snap-scrolled subpages toggled by the top-bar switch.

    • ``kpi-subpage-tracking`` (default): Warehouse tracking → shift volume → time
      bands — the cool blue/cyan operational read.
    • ``kpi-subpage-report``: TEMU KPI → volume trend — the warm "Riport" surface.

    The hidden subpage is ``display:none``, so kpi_snap.js (which only collects
    *visible* ``.kpi-snap-section`` blocks) automatically scopes snap + dots to the
    active subpage. Backgrounds live in the body-level ``.kpi-bg`` layer."""
    return html.Div(id="kpi-page", className="kpi-page", children=[
        html.Div(className="kpi-subpage kpi-subpage-tracking",
                 **{"data-kpi-sub": "tracking"}, children=[
            _kpi_tracking_section(),
            _kpi_flow_section(),
            _kpi_bands_section(),
        ]),
        html.Div(className="kpi-subpage kpi-subpage-report",
                 **{"data-kpi-sub": "report"}, children=[
            _kpi_temu_shell(),
            _kpi_trend_shell(),
        ]),
    ])


def _kpi_bands_section() -> html.Section:
    """Time-of-day breakdown at the very bottom of the KPI page.

    Inbound vs outbound split into four 4-hour bands of the day
    (08-12 / 12-16 / 16-20 / 20-24). Fully rendered client-side from
    ``payload.time_bands`` by assets/kpi_bands.js; the shell here is just the
    static frame + metric controls."""
    return html.Section(className="kpi-snap-section kpi-bands-section kpi-reveal-pending", children=[
        html.Div(className="kpi-bands-head", children=[
            html.Div(className="kpi-bands-head-text", children=[
                html.Div("Daypart distribution", className="kpi-bands-eyebrow"),
                html.H2("Operational time bands", className="kpi-bands-title"),
                html.Div(
                    id="kpi-bands-subtitle",
                    className="kpi-bands-subtitle",
                    children="Inbound and outbound by 4-hour window",
                ),
            ]),
            html.Div(className="kpi-bands-controls", children=[
                html.Div(className="kpi-bands-day-nav", children=[
                    html.Button("‹", className="kpi-bands-day-btn",
                                **{"data-band-day": "prev"}, n_clicks=0),
                    html.Span(id="kpi-bands-day-label", className="kpi-bands-day-label"),
                    html.Select(id="kpi-bands-day-select", className="kpi-bands-day-select",
                                **{"aria-label": "Select operational day"}),
                    html.Button("›", className="kpi-bands-day-btn",
                                **{"data-band-day": "next"}, n_clicks=0),
                ]),
                html.Div(className="kpi-bands-toggle", **{"data-toggle": "metric"}, children=[
                    html.Button("KG", className="kpi-bands-tg-btn is-active",
                                **{"data-metric": "kg"}, n_clicks=0),
                    html.Button("COLLI", className="kpi-bands-tg-btn",
                                **{"data-metric": "colli"}, n_clicks=0),
                    html.Button("PARCEL", className="kpi-bands-tg-btn",
                                **{"data-metric": "parcel"}, n_clicks=0),
                ]),
            ]),
        ]),
        html.Div(id="kpi-bands-summary", className="kpi-bands-summary"),
        html.Div(id="kpi-bands-chart", className="kpi-bands-chart"),
        html.Div(id="kpi-bands-grid", className="kpi-bands-grid"),
    ])


def _sum_shift_hours(hours) -> float:
    total = 0.0
    for item in hours or []:
        try:
            total += float((item or {}).get("kg") or 0)
        except (TypeError, ValueError):
            pass
    return total


def _numeric_payload_value(value, fallback=0.0):
    try:
        result = float(value)
        if result != result:
            return fallback
        return result
    except (TypeError, ValueError):
        return fallback


def _kpi_page_flow_payload(kpi: dict, prefix: str = "") -> dict:
    current_hours = list(kpi.get(f"{prefix}shift_hourly") or [])
    prev_hours = list(kpi.get(f"{prefix}shift_prev_hourly") or [])
    current_kg = _numeric_payload_value(kpi.get(f"{prefix}shift_kg"), None)
    prev_kg = _numeric_payload_value(kpi.get(f"{prefix}shift_prev_kg"), None)
    if not current_kg:
        current_kg = _sum_shift_hours(current_hours)
    if not prev_kg:
        prev_kg = _sum_shift_hours(prev_hours)
    return {
        "current": {
            "name": kpi.get(f"{prefix}shift_name") or "Jelenlegi műszak",
            "range": kpi.get(f"{prefix}shift_range") or "",
            "kg": current_kg,
            "hours": current_hours,
        },
        "prev": {
            "name": kpi.get(f"{prefix}shift_prev_name") or "Előző műszak",
            "range": kpi.get(f"{prefix}shift_prev_range") or "",
            "kg": prev_kg,
            "hours": prev_hours,
        },
    }


def _kpi_page_payload(kpi: dict, last_refresh=None) -> dict:
    def _num(v) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    tracking = dict(kpi.get("tracking") or {})
    # Surface the inbound "Beérkező" status mix (same numbers as the header
    # composition bar) so the Unit ratios block can render a mini status diagram.
    tracking["status_composition"] = {
        "felveve": _num(kpi.get("bec_felveve")),
        "ertesito": _num(kpi.get("bec_ertesito")),
        "megerkezett": _num(kpi.get("bec_megerkezett")),
        "szemles": _num(kpi.get("bec_szemles")),
    }
    return {
        "updated": _fmt_dt(last_refresh) if last_refresh else "",
        "inbound": _kpi_page_flow_payload(kpi, ""),
        "outbound": _kpi_page_flow_payload(kpi, "outbound_"),
        "tracking": tracking,
        "trend": kpi.get("trend") or {},
        "temu_stages": kpi.get("temu_stages") or {},
        "time_bands": kpi.get("time_bands") or {},
    }


def _clean_export_color(value: str, fallback: str = "94A3B8") -> str:
    raw = str(value or "").strip().lstrip("#")
    if len(raw) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in raw):
        return raw.upper()
    return fallback


def _blend_hex_with_white(hex_color: str, amount: float = 0.86) -> str:
    raw = _clean_export_color(hex_color)
    r = int(raw[0:2], 16)
    g = int(raw[2:4], 16)
    b = int(raw[4:6], 16)
    r = int(round(r + (255 - r) * amount))
    g = int(round(g + (255 - g) * amount))
    b = int(round(b + (255 - b) * amount))
    return f"{r:02X}{g:02X}{b:02X}"


def _font_color_for_bg(hex_color: str) -> str:
    raw = _clean_export_color(hex_color)
    r = int(raw[0:2], 16)
    g = int(raw[2:4], 16)
    b = int(raw[4:6], 16)
    luminance = (0.299 * r) + (0.587 * g) + (0.114 * b)
    return "111827" if luminance > 170 else "FFFFFF"


def _safe_export_number(value) -> int:
    try:
        n = float(value)
        if n != n:
            return 0
        return int(round(n))
    except (TypeError, ValueError):
        return 0


def _kpi_trend_export_filename(payload: dict) -> str:
    import unicodedata

    bits = [
        "kpi_trend",
        str(payload.get("breakdownLabel") or payload.get("breakdown") or "adatok"),
        str(payload.get("rangeLabel") or "teljes"),
        str(payload.get("modeLabel") or payload.get("mode") or "nezet"),
        datetime.now().strftime("%Y%m%d_%H%M%S"),
    ]
    raw = "_".join(bits)
    safe = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in safe)
    while "__" in safe:
        safe = safe.replace("__", "_")
    return (safe.strip("._") or "kpi_trend")[:120] + ".xlsx"


def _format_export_timestamp(raw) -> str:
    """ISO timestamp (e.g. JS `new Date().toISOString()` → '2026-06-17T06:48:07.058Z')
    rendered as a clean, human-readable local time without the 'T'/'Z' machine markers."""
    from datetime import timezone

    s = str(raw or "").strip()
    if not s:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        iso = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone()  # UTC → the machine's local time (HU operation)
            dt = dt.replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        # Fallback: strip the ISO machine markers kézily.
        cleaned = s.replace("T", " ").split(".")[0].replace("Z", "").strip()
        return cleaned or datetime.now().strftime("%Y-%m-%d %H:%M")


def _build_kpi_trend_export(payload: dict) -> tuple[bytes, str]:
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.chart import LineChart, Reference
    from openpyxl.chart.axis import ChartLines
    from openpyxl.chart.marker import Marker
    from openpyxl.chart.shapes import GraphicalProperties
    from openpyxl.drawing.line import LineProperties
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    if not isinstance(payload, dict):
        raise ValueError("Missing export data.")
    raw_cols = payload.get("columns") or []
    raw_rows = payload.get("rows") or []
    if not raw_cols or not raw_rows:
        raise ValueError("No exportable trend data.")

    columns = []
    seen_ids = set()
    for item in raw_cols[:80]:
        if not isinstance(item, dict):
            continue
        col_id = str(item.get("id") or item.get("label") or "").strip()
        if not col_id or col_id in seen_ids:
            continue
        seen_ids.add(col_id)
        columns.append({
            "id": col_id,
            "label": str(item.get("label") or col_id).strip()[:80],
            "color": _clean_export_color(item.get("color")),
        })
    if not columns:
        raise ValueError("No exportable series.")

    rows = []
    for item in raw_rows[:1200]:
        if not isinstance(item, dict):
            continue
        vals = item.get("vals") if isinstance(item.get("vals"), dict) else {}
        values = [_safe_export_number(vals.get(col["id"])) for col in columns]
        rows.append({
            "key": str(item.get("key") or "").strip()[:40],
            "d0": str(item.get("d0") or "").strip()[:20],
            "d1": str(item.get("d1") or "").strip()[:20],
            "values": values,
            "total": sum(values),
        })
    if not rows:
        raise ValueError("No exportable time series.")

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    summary = wb.create_sheet("Summary")

    mode_label = str(payload.get("modeLabel") or payload.get("mode") or "")
    breakdown_label = str(payload.get("breakdownLabel") or payload.get("breakdown") or "")
    range_label = str(payload.get("rangeLabel") or "")
    generated = _format_export_timestamp(payload.get("generatedAt"))
    period_head = "Day" if str(payload.get("mode") or "").lower() == "day" else "Week"

    # Keep the XLSX table header aligned with the visible KPI table: period, one
    # readable date range column, series columns, total. Labels must be unique so
    # Excel never has to repair the workbook when two visible series names collide.
    reserved_labels = {period_head, "Date", "Total"}
    seen_labels: set[str] = set()
    for col in columns:
        base = (col["label"] or col["id"] or "Series").strip() or "Series"
        label = base
        suffix = 2
        while label in reserved_labels or label in seen_labels:
            label = f"{base} ({suffix})"
            suffix += 1
        col["label"] = label
        seen_labels.add(label)

    header_row = 3
    data_start = header_row + 1
    total_cols = 3 + len(columns)
    last_col = get_column_letter(total_cols)

    dark_fill = PatternFill("solid", fgColor="111827")
    accent_fill = PatternFill("solid", fgColor="1F4E79")
    total_fill = PatternFill("solid", fgColor="E8EEF8")
    border = Border(
        left=Side(style="thin", color="D7DEE9"),
        right=Side(style="thin", color="D7DEE9"),
        top=Side(style="thin", color="D7DEE9"),
        bottom=Side(style="thin", color="D7DEE9"),
    )

    # Header mirrors the UI title ("Full range · Weekly · LMP") and keeps the generated
    # timestamp as clean local text, never the JS ISO T/Z form.
    view_title = " · ".join(part for part in (range_label, mode_label, breakdown_label) if part) or "KPI trend"
    meta = f"{view_title} | Export: {generated}"
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    ws["A1"] = view_title
    ws["A1"].font = Font(size=12, bold=True, color="FFFFFF")
    ws["A1"].fill = dark_fill
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 26
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=total_cols)
    ws["A2"] = f"Export: {generated}"
    ws["A2"].font = Font(size=10, bold=True, color="475569")
    ws["A2"].alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 18

    headers = [period_head, "Date"] + [col["label"] for col in columns] + ["Total"]
    for idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=idx, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = dark_fill if idx <= 2 or idx == total_cols else PatternFill("solid", fgColor=columns[idx - 3]["color"])
        if 3 <= idx < total_cols:
            cell.font = Font(bold=True, color=_font_color_for_bg(columns[idx - 3]["color"]))
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border

    def _date_label(row: dict) -> str:
        d0 = str(row.get("d0") or "").strip()
        d1 = str(row.get("d1") or "").strip()
        if d0 and d1 and d0 != d1:
            return f"{d0} - {d1}"
        return d0 or d1

    for r_idx, row in enumerate(rows, start=data_start):
        ws.cell(r_idx, 1, row["key"])
        ws.cell(r_idx, 2, _date_label(row))
        for c_idx, value in enumerate(row["values"], start=3):
            cell = ws.cell(r_idx, c_idx, value)
            cell.number_format = '#,##0" kg"'
            cell.fill = PatternFill("solid", fgColor=_blend_hex_with_white(columns[c_idx - 3]["color"]))
        total_cell = ws.cell(r_idx, total_cols, row["total"])
        total_cell.number_format = '#,##0" kg"'
        total_cell.fill = total_fill
        total_cell.font = Font(bold=True, color="111827")
        for c_idx in range(1, total_cols + 1):
            cell = ws.cell(r_idx, c_idx)
            cell.border = border
            cell.alignment = Alignment(horizontal="right" if c_idx >= 4 else "left")

    total_row = data_start + len(rows)
    ws.cell(total_row, 1, "Total")
    ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=2)
    for c_idx in range(3, total_cols + 1):
        col_letter = get_column_letter(c_idx)
        ws.cell(total_row, c_idx, f"=SUM({col_letter}{data_start}:{col_letter}{total_row - 1})")
        ws.cell(total_row, c_idx).number_format = '#,##0" kg"'
    for c_idx in range(1, total_cols + 1):
        cell = ws.cell(total_row, c_idx)
        cell.fill = accent_fill
        cell.font = Font(bold=True, color="FFFFFF")
        cell.border = border
        cell.alignment = Alignment(horizontal="right" if c_idx >= 4 else "left")

    ws.freeze_panes = f"C{data_start}"
    # NOTE: we deliberately do NOT use an Excel Table object here. openpyxl-generated
    # <table> parts are what Excel flagged as corrupt ("Eltávolított funkció: Táblázat
    # innen: table1.xml") and then opened read-only. A plain worksheet AutoFilter gives
    # the same drop-down filtering without any of the table-part fragility, and the row
    # striping is applied kézily (see the data-row loop above).
    ws.auto_filter.ref = f"A{header_row}:{last_col}{total_row - 1}"

    widths = [14, 24] + [max(14, min(28, len(col["label"]) + 3)) for col in columns] + [16]
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    if len(rows) >= 2 and len(columns) <= 45:
        # A polished, easy-to-read line chart placed BELOW the table (so the export
        # reads top-to-bottom: band → data table → totals → chart). The series cap is
        # high enough to cover the full LMP breakdown (~33 carriers).
        many_series = len(columns) > 10
        chart = LineChart()
        chart.title = f"{view_title} - kg trend"
        chart.style = 2
        # Taller plot when there are many series so the bottom legend doesn't crush it.
        chart.height = 17 if len(columns) > 16 else 11.5
        chart.width = 27.5
        chart.y_axis.title = "Volume (kg)"
        chart.x_axis.title = period_head
        # Force both axes to render (openpyxl hides them by default on some charts).
        chart.x_axis.delete = False
        chart.y_axis.delete = False
        chart.y_axis.majorGridlines = ChartLines()
        chart.y_axis.majorGridlines.spPr = GraphicalProperties(
            ln=LineProperties(solidFill="E2E8F0", w=6350)
        )
        chart.y_axis.number_format = '#,##0'
        chart.y_axis.scaling.min = 0  # honest, zero-based comparison
        chart.x_axis.tickLblPos = "low"
        chart.x_axis.majorTickMark = "out"
        chart.y_axis.majorTickMark = "out"
        # Bottom legend reads cleaner than a side legend when there are many series.
        chart.legend.position = "b"
        chart.legend.overlay = False

        data_ref = Reference(ws, min_col=3, max_col=2 + len(columns), min_row=header_row, max_row=total_row - 1)
        cats_ref = Reference(ws, min_col=1, min_row=data_start, max_row=total_row - 1)
        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats_ref)
        for idx, series in enumerate(chart.series):
            color = columns[idx]["color"]
            series.smooth = False
            if series.graphicalProperties is None:
                series.graphicalProperties = GraphicalProperties()
            series.graphicalProperties.line = LineProperties(solidFill=color, w=22000 if many_series else 28000)
            if not many_series:
                # Markers help readability when the series count is small; with a
                # crowded chart they only add clutter, so we drop them.
                series.marker = Marker(symbol="circle", size=6)
                series.marker.graphicalProperties = GraphicalProperties(
                    solidFill=color,
                    ln=LineProperties(solidFill=color),
                )
        ws.add_chart(chart, f"A{total_row + 2}")

    summary["A1"] = "KPI trend summary"
    summary["A1"].font = Font(size=16, bold=True, color="FFFFFF")
    summary["A1"].fill = dark_fill
    summary.merge_cells(start_row=1, start_column=1, end_row=1, end_column=5)
    summary["A2"] = meta
    summary["A2"].font = Font(size=10, color="475569", bold=True)
    summary.merge_cells(start_row=2, start_column=1, end_row=2, end_column=5)
    summary_headers = ["#", "Series", "Kg", "Share", "Color"]
    for idx, header in enumerate(summary_headers, start=1):
        cell = summary.cell(4, idx, header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = dark_fill
        cell.border = border
        cell.alignment = Alignment(horizontal="center")
    grand_total = sum(row["total"] for row in rows)
    ranked = sorted(
        (
            {
                "label": col["label"],
                "color": col["color"],
                "kg": sum(row["values"][idx] for row in rows),
            }
            for idx, col in enumerate(columns)
        ),
        key=lambda item: (-item["kg"], item["label"]),
    )
    for r_idx, item in enumerate(ranked, start=5):
        summary.cell(r_idx, 1, r_idx - 4)
        summary.cell(r_idx, 2, item["label"])
        summary.cell(r_idx, 3, item["kg"])
        summary.cell(r_idx, 4, item["kg"] / grand_total if grand_total else 0)
        summary.cell(r_idx, 5, "#" + item["color"])
        summary.cell(r_idx, 3).number_format = '#,##0" kg"'
        summary.cell(r_idx, 4).number_format = "0.0%"
        summary.cell(r_idx, 5).fill = PatternFill("solid", fgColor=item["color"])
        summary.cell(r_idx, 5).font = Font(color=_font_color_for_bg(item["color"]), bold=True)
        for c_idx in range(1, 6):
            cell = summary.cell(r_idx, c_idx)
            cell.border = border
            cell.alignment = Alignment(horizontal="right" if c_idx in (1, 3, 4) else "left")
    summary.freeze_panes = "A5"
    for idx, width in enumerate([8, 32, 16, 14, 14], start=1):
        summary.column_dimensions[get_column_letter(idx)].width = width

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue(), _kpi_trend_export_filename(payload)


@server.route("/kpi-trend-export", methods=["POST"])
def kpi_trend_export_route():
    from io import BytesIO
    from flask import jsonify, send_file

    payload = request.get_json(force=True, silent=True) or {}
    try:
        content, filename = _build_kpi_trend_export(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        log.error("KPI trend XLSX export error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": f"XLSX export hiba: {exc}"}), 500
    return send_file(
        BytesIO(content),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


def _build_kpi_day_flow_export(payload: dict) -> tuple[bytes, str]:
    from datetime import datetime
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.chart import LineChart, Reference
    from openpyxl.chart.marker import Marker
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    if not isinstance(payload, dict):
        raise ValueError("Missing export data.")

    raw_rows = payload.get("rows") or []
    metric: str = payload.get("metric") or "kg"
    day_range: int = int(payload.get("range") or 7)

    if metric not in ("kg", "parcel", "colli"):
        metric = "kg"

    unit_label = {"kg": "kg", "parcel": "parcel", "colli": "colli"}[metric]
    in_field = f"inbound_{metric if metric != 'parcel' else 'parcel'}"
    out_field = f"outbound_{metric if metric != 'parcel' else 'parcel'}"

    # Normalise rows — accept both 'inbound'/'outbound' and per-metric fields
    rows = []
    for r in raw_rows:
        if not isinstance(r, dict):
            continue
        inval = r.get(in_field) or r.get("inbound") or 0
        outval = r.get(out_field) or r.get("outbound") or 0
        rows.append({
            "date": str(r.get("date") or ""),
            "label": str(r.get("label") or r.get("date") or ""),
            "inbound": int(round(float(inval))),
            "outbound": int(round(float(outval))),
        })

    if not rows:
        raise ValueError("Nincsenek exportálható adatok.")

    # ── Colour palette ─────────────────────────────────────────────────────
    C_IN = "FF8A3D"    # inbound orange
    C_OUT = "A78BFA"   # outbound purple
    C_BG = "111827"    # dark card bg
    C_HEADER = "1E293B"
    C_LABEL = "94A3B8"
    C_WHITE = "E2E8F0"
    C_DIFF_POS = "34D399"
    C_DIFF_NEG = "F87171"
    C_TOTAL_BG = "1E2D40"

    def thin_border(sides="lrtb"):
        s = Side(style="thin", color="2D3748")
        ns = Side(style=None)
        return Border(
            left=s if "l" in sides else ns,
            right=s if "r" in sides else ns,
            top=s if "t" in sides else ns,
            bottom=s if "b" in sides else ns,
        )

    wb = Workbook()

    # ══════════════════════════════════════════════════════════════════
    # Sheet 1 — Adatok (Data table + chart)
    # ══════════════════════════════════════════════════════════════════
    ws = wb.active
    ws.title = "Adatok"
    ws.sheet_view.showGridLines = False
    ws.tab_color = C_IN

    # Column widths
    ws.column_dimensions["A"].width = 12   # Date
    ws.column_dimensions["B"].width = 18   # Label
    ws.column_dimensions["C"].width = 16   # Inbound
    ws.column_dimensions["D"].width = 16   # Outbound
    ws.column_dimensions["E"].width = 16   # Diff

    header_font = Font(name="Calibri", bold=True, size=11, color=C_WHITE)
    label_font  = Font(name="Calibri", size=10, color=C_LABEL)
    data_font   = Font(name="Calibri", size=10, color=C_WHITE)
    total_font  = Font(name="Calibri", bold=True, size=10, color=C_WHITE)

    def fill(hex_color):
        return PatternFill("solid", fgColor=hex_color)

    # Row 1: title
    ws.merge_cells("A1:E1")
    title_cell = ws["A1"]
    title_cell.value = f"Inbound vs Outbound — {day_range} napos nézet ({unit_label})"
    title_cell.font = Font(name="Calibri", bold=True, size=13, color=C_WHITE)
    title_cell.fill = fill(C_BG)
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28

    # Row 2: column headers
    headers = ["Dátum", "Nap", f"Inbound ({unit_label})", f"Outbound ({unit_label})", "Különbség"]
    header_fills = [C_HEADER, C_HEADER, C_IN, C_OUT, C_HEADER]
    for col_idx, (h, f_color) in enumerate(zip(headers, header_fills), start=1):
        cell = ws.cell(row=2, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = fill(f_color)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border()
    ws.row_dimensions[2].height = 22

    # Data rows
    data_start = 3
    for row_idx, row in enumerate(rows, start=data_start):
        diff = row["inbound"] - row["outbound"]
        diff_color = C_DIFF_POS if diff >= 0 else C_DIFF_NEG
        row_fill = fill("161E2C") if row_idx % 2 == 0 else fill(C_BG)

        cells_data = [
            (row["date"], "left"),
            (row["label"], "left"),
            (row["inbound"], "right"),
            (row["outbound"], "right"),
            (diff, "right"),
        ]
        for col_idx, (val, align) in enumerate(cells_data, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = Font(name="Calibri", size=10,
                             color=(diff_color if col_idx == 5 else C_WHITE),
                             bold=(col_idx == 5 and diff != 0))
            cell.fill = row_fill
            cell.alignment = Alignment(horizontal=align, vertical="center")
            cell.border = thin_border()
            if col_idx >= 3:
                cell.number_format = '#,##0'
        ws.row_dimensions[row_idx].height = 18

    # Totals row
    total_row = data_start + len(rows)
    total_in = sum(r["inbound"] for r in rows)
    total_out = sum(r["outbound"] for r in rows)
    total_diff = total_in - total_out
    avg_in = total_in / len(rows) if rows else 0
    avg_out = total_out / len(rows) if rows else 0

    totals = [("Összesen", "", total_in, total_out, total_diff),
              ("Átlag/nap", "", avg_in, avg_out, avg_in - avg_out)]
    for t_idx, (label_a, label_b, vin, vout, vdiff) in enumerate(totals):
        r = total_row + t_idx
        row_cells = [(label_a, "left"), (label_b, "left"), (vin, "right"), (vout, "right"), (vdiff, "right")]
        for col_idx, (val, align) in enumerate(row_cells, start=1):
            cell = ws.cell(row=r, column=col_idx, value=round(val, 1) if isinstance(val, float) else val)
            cell.font = Font(name="Calibri", bold=True, size=10, color=C_WHITE)
            cell.fill = fill(C_TOTAL_BG)
            cell.alignment = Alignment(horizontal=align, vertical="center")
            cell.border = thin_border()
            if col_idx >= 3:
                cell.number_format = '#,##0'
        ws.row_dimensions[r].height = 20

    ws.freeze_panes = f"A{data_start}"

    # AutoFilter on header row
    ws.auto_filter.ref = f"A2:E{total_row - 1}"

    # ── Embedded LineChart ─────────────────────────────────────────────
    chart = LineChart()
    chart.title = f"Inbound vs Outbound ({unit_label})"
    chart.style = 2
    chart.y_axis.title = unit_label
    chart.x_axis.title = "Nap"
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    chart.height = 14
    chart.width = 28

    last_data_row = total_row - 1
    in_ref = Reference(ws, min_col=3, min_row=2, max_row=last_data_row)
    out_ref = Reference(ws, min_col=4, min_row=2, max_row=last_data_row)
    cats = Reference(ws, min_col=2, min_row=data_start, max_row=last_data_row)

    chart.add_data(in_ref, titles_from_data=True)
    chart.add_data(out_ref, titles_from_data=True)
    chart.set_categories(cats)

    # Style the two series
    for i, (ser_color, ser_label) in enumerate([(C_IN, "Inbound"), (C_OUT, "Outbound")]):
        s = chart.series[i]
        s.graphicalProperties.line.solidFill = ser_color
        s.graphicalProperties.line.width = 20000
        m = Marker()
        m.symbol = "circle"
        m.size = 5
        m.graphicalProperties.solidFill = ser_color
        m.graphicalProperties.line.solidFill = ser_color
        s.marker = m
        s.smooth = True

    ws.add_chart(chart, f"G2")

    # ══════════════════════════════════════════════════════════════════
    # Sheet 2 — Összegzés (Summary)
    # ══════════════════════════════════════════════════════════════════
    ws2 = wb.create_sheet("Összegzés")
    ws2.sheet_view.showGridLines = False
    ws2.tab_color = C_OUT
    ws2.column_dimensions["A"].width = 28
    ws2.column_dimensions["B"].width = 20

    def s2_row(r, label, value, is_header=False):
        label_cell = ws2.cell(row=r, column=1, value=label)
        val_cell = ws2.cell(row=r, column=2, value=value)
        label_cell.font = Font(name="Calibri", bold=is_header, size=10,
                                color=C_WHITE if is_header else C_LABEL)
        val_cell.font = Font(name="Calibri", bold=is_header, size=10,
                              color=C_WHITE if is_header else C_WHITE)
        label_cell.fill = fill(C_HEADER if is_header else C_BG)
        val_cell.fill = fill(C_HEADER if is_header else C_BG)
        label_cell.alignment = Alignment(horizontal="left", vertical="center")
        val_cell.alignment = Alignment(horizontal="right", vertical="center")
        ws2.row_dimensions[r].height = 20

    ws2.merge_cells("A1:B1")
    ws2["A1"].value = "KPI Összegzés — Inbound vs Outbound"
    ws2["A1"].font = Font(name="Calibri", bold=True, size=13, color=C_WHITE)
    ws2["A1"].fill = fill(C_BG)
    ws2["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws2.row_dimensions[1].height = 28

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    s2_row(2, "Exportálva", now_str)
    s2_row(3, "Mértékegység", unit_label)
    s2_row(4, "Időszak (nap)", day_range)
    s2_row(5, "Napok száma", len(rows))
    s2_row(6, "", "")
    s2_row(7, "INBOUND", "", is_header=True)
    s2_row(8, f"Összesen ({unit_label})", total_in)
    s2_row(9, f"Átlag/nap ({unit_label})", round(avg_in, 1))
    s2_row(10, "", "")
    s2_row(11, "OUTBOUND", "", is_header=True)
    s2_row(12, f"Összesen ({unit_label})", total_out)
    s2_row(13, f"Átlag/nap ({unit_label})", round(avg_out, 1))
    s2_row(14, "", "")
    s2_row(15, "KÜLÖNBSÉG", "", is_header=True)
    s2_row(16, f"Összesen ({unit_label})", total_diff)
    s2_row(17, f"Átlag/nap ({unit_label})", round(avg_in - avg_out, 1))

    for r in range(2, 18):
        for c in range(1, 3):
            cell = ws2.cell(row=r, column=c)
            if not cell.fill or cell.fill.patternType not in ("solid",):
                cell.fill = fill(C_BG)

    # ── Bar chart: per-day Inbound vs Outbound (references Sheet 1 data) ────
    from openpyxl.chart import BarChart, Reference

    ws2.column_dimensions["D"].width = 14

    bar = BarChart()
    bar.type = "col"
    bar.grouping = "clustered"
    bar.title = f"Napi bontás — Inbound vs Outbound ({unit_label})"
    bar.style = 2
    bar.y_axis.title = unit_label
    bar.x_axis.title = "Nap"
    bar.x_axis.delete = False
    bar.y_axis.delete = False
    bar.height = 14
    bar.width = 28

    # Reference Sheet 1 data directly (cross-sheet)
    in_ref_b = Reference(ws, min_col=3, min_row=2, max_row=last_data_row)
    out_ref_b = Reference(ws, min_col=4, min_row=2, max_row=last_data_row)
    cats_b = Reference(ws, min_col=2, min_row=data_start, max_row=last_data_row)

    bar.add_data(in_ref_b, titles_from_data=True)
    bar.add_data(out_ref_b, titles_from_data=True)
    bar.set_categories(cats_b)

    for idx, color_hex in enumerate([C_IN, C_OUT]):
        s = bar.series[idx]
        s.graphicalProperties.solidFill = color_hex
        s.graphicalProperties.line.solidFill = color_hex

    ws2.add_chart(bar, "D2")

    bio = BytesIO()
    wb.save(bio)

    metric_tag = {"kg": "kg", "parcel": "parcel", "colli": "colli"}[metric]
    from datetime import date
    fname = f"kpi_flow_day_{metric_tag}_{day_range}d_{date.today().isoformat()}.xlsx"
    return bio.getvalue(), fname


@server.route("/kpi-flow-day-export", methods=["POST"])
def kpi_flow_day_export_route():
    from io import BytesIO
    from flask import jsonify, send_file

    payload = request.get_json(force=True, silent=True) or {}
    try:
        content, filename = _build_kpi_day_flow_export(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        log.error("KPI day flow XLSX export error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": f"XLSX export hiba: {exc}"}), 500
    return send_file(
        BytesIO(content),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


_TEMU_EXPORT_FIELD_ORDER = ("ak_raw", "noa_raw", "am_raw", "aq_raw", "ar_raw", "departure_raw")
_TEMU_EXPORT_FIELD_LABELS = {
    "ak_raw": "ATA",
    "noa_raw": "NOA",
    "am_raw": "Transfer",
    "aq_raw": "Customs start",
    "ar_raw": "Customs end",
    "departure_raw": "Departure",
}


def _coerce_export_datetime(raw):
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None) if raw.tzinfo is None else raw.astimezone().replace(tzinfo=None)
    if isinstance(raw, date):
        return datetime.combine(raw, datetime.min.time())
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except (TypeError, ValueError):
        return None


def _write_export_date(cell, raw, *, with_time: bool = False) -> None:
    dt = _coerce_export_datetime(raw)
    if dt is None:
        cell.value = str(raw or "").strip()
        return
    cell.value = dt if with_time else dt.date()
    cell.number_format = "yyyy-mm-dd hh:mm" if with_time else "yyyy-mm-dd"


def _write_export_duration(cell, hours) -> None:
    try:
        minutes = int(round(float(hours or 0) * 60))
    except (TypeError, ValueError):
        minutes = 0
    if minutes < 0:
        minutes = 0
    cell.value = minutes / 1440
    cell.number_format = "[h]:mm"


def _fmt_hours_hm(value) -> str:
    """Hours (float) → 'Xh Ym' for the TEMU export, mirroring the on-screen labels."""
    try:
        total_min = int(round(float(value) * 60))
    except (TypeError, ValueError):
        return "0m"
    if total_min < 0:
        total_min = 0
    h, m = divmod(total_min, 60)
    return f"{h}h {m}m" if h > 0 else f"{m}m"


def _build_kpi_temu_export(payload: dict) -> tuple[bytes, str]:
    """XLSX of the TEMU table, or the flagged-record detail export."""
    if isinstance(payload, dict):
        kind = str(payload.get("kind") or payload.get("type") or "table").strip().lower()
        if kind in {"flagged", "suspects", "flagged_records"}:
            return _build_kpi_temu_flagged_export(payload)
    return _build_kpi_temu_table_export(payload)


def _build_kpi_temu_table_export(payload: dict) -> tuple[bytes, str]:
    """XLSX of the TEMU stage-duration table: periods × stages + Total + Pcs."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    if not isinstance(payload, dict):
        raise ValueError("Missing export data.")
    raw_stages = payload.get("stages") or []
    raw_rows = payload.get("rows") or []
    if not raw_stages or not raw_rows:
        raise ValueError("No exportable TEMU data.")

    stages = []
    seen = set()
    for item in raw_stages[:12]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        stages.append({
            "key": key,
            "label": str(item.get("label") or key).strip()[:60],
            "color": _clean_export_color(item.get("color"), "94A3B8"),
        })
    if not stages:
        raise ValueError("No exportable stages.")

    rows = []
    for item in raw_rows[:1200]:
        if not isinstance(item, dict):
            continue
        vals = item.get("vals") if isinstance(item.get("vals"), dict) else {}
        rows.append({
            "label": str(item.get("label") or "").strip()[:40],
            "date": str(item.get("date") or item.get("d0") or item.get("dateLabel") or "").strip()[:40],
            "date_end": str(item.get("dateEnd") or item.get("d1") or "").strip()[:40],
            "count": _safe_export_number(item.get("count")),
            "vals": {s["key"]: float(vals.get(s["key"]) or 0.0) for s in stages},
            "total": float(item.get("total") or 0.0),
        })
    if not rows:
        raise ValueError("No exportable periods.")

    title = str(payload.get("title") or "TEMU KPI").strip()[:80]
    range_label = str(payload.get("rangeLabel") or "").strip()[:120]
    scope_label = str(payload.get("scopeLabel") or "").strip()[:80]
    mode_label = str(payload.get("modeLabel") or "").strip()[:40]
    period_type = str(payload.get("periodType") or payload.get("mode") or "").strip().lower()
    include_period = period_type == "week" or mode_label.lower().startswith("week")

    wb = Workbook()
    ws = wb.active
    ws.title = "TEMU KPI"

    thin = Side(style="thin", color="D6DEEA")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="1F2A44")
    total_fill = PatternFill("solid", fgColor="EEF2FA")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center")

    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=15, color="1F2A44")
    meta = " · ".join([p for p in (scope_label, mode_label, range_label) if p])
    ws["A2"] = meta
    ws["A2"].font = Font(size=10, color="64748B")

    header_row = 4
    headers = (["Week"] if include_period else []) + ["Date"] + [s["label"] for s in stages] + ["Total lead time", "Pcs"]
    for c, text in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=c, value=text)
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.alignment = center
        cell.border = border
        stage_idx = c - (3 if include_period else 2)
        if 0 <= stage_idx < len(stages):
            cell.fill = PatternFill("solid", fgColor=stages[stage_idx]["color"])
            cell.font = Font(bold=True, color=_font_color_for_bg(stages[stage_idx]["color"]), size=10)
        else:
            cell.fill = head_fill

    r = header_row + 1
    for row in rows:
        c = 1
        if include_period:
            ws.cell(row=r, column=c, value=row["label"]).alignment = center
            c += 1
        date_cell = ws.cell(row=r, column=c)
        _write_export_date(date_cell, row["date"])
        date_cell.alignment = center
        c += 1
        for i, s in enumerate(stages):
            cell = ws.cell(row=r, column=c + i)
            _write_export_duration(cell, row["vals"][s["key"]])
            cell.alignment = center
        total_col = c + len(stages)
        total_cell = ws.cell(row=r, column=total_col)
        _write_export_duration(total_cell, row["total"])
        total_cell.alignment = center
        ws.cell(row=r, column=total_col + 1, value=row["count"]).alignment = center
        for col_idx in range(1, total_col + 2):
            ws.cell(row=r, column=col_idx).border = border
        r += 1

    # Total row: per-stage average across the range = Σ(avg_i·cnt_i)/Σcnt_i is not
    # reconstructable here, so the client sends the precomputed totals row.
    totals = payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
    tvals = totals.get("vals") if isinstance(totals.get("vals"), dict) else {}
    ws.cell(row=r, column=1, value="TOTAL / AVG").font = Font(bold=True)
    if include_period:
        ws.cell(row=r, column=2, value="")
        first_stage_col = 3
    else:
        first_stage_col = 2
    for i, s in enumerate(stages):
        cell = ws.cell(row=r, column=first_stage_col + i)
        _write_export_duration(cell, tvals.get(s["key"]) or 0.0)
        cell.font = Font(bold=True)
        cell.alignment = center
    total_col = first_stage_col + len(stages)
    total_cell = ws.cell(row=r, column=total_col)
    _write_export_duration(total_cell, totals.get("total") or 0.0)
    total_cell.font = Font(bold=True)
    count_cell = ws.cell(row=r, column=total_col + 1, value=_safe_export_number(totals.get("count")))
    count_cell.font = Font(bold=True)
    for col_idx in range(1, total_col + 2):
        cell = ws.cell(row=r, column=col_idx)
        cell.fill = total_fill
        cell.border = border
        if col_idx >= first_stage_col:
            cell.alignment = center

    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    ws.column_dimensions["A"].width = 12 if include_period else 14
    if include_period:
        ws.column_dimensions["B"].width = 14
    for i in range(len(stages)):
        ws.column_dimensions[get_column_letter(first_stage_col + i)].width = max(12, min(28, len(stages[i]["label"]) + 2))
    ws.column_dimensions[get_column_letter(total_col)].width = 16
    ws.column_dimensions[get_column_letter(total_col + 1)].width = 8

    import re as _re
    import unicodedata as _ud
    safe = _ud.normalize("NFKD", f"{title} {scope_label} {mode_label}").encode("ascii", "ignore").decode()
    safe = _re.sub(r"[^A-Za-z0-9._-]+", "_", safe).strip("._") or "temu_kpi"
    filename = safe[:110] + ".xlsx"

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue(), filename


def _build_kpi_temu_flagged_export(payload: dict) -> tuple[bytes, str]:
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    if not isinstance(payload, dict):
        raise ValueError("Missing export data.")
    raw_items = payload.get("items") or []
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("No exportable flagged records.")

    title = str(payload.get("title") or "TEMU flagged records").strip()[:80]
    scope_label = str(payload.get("scopeLabel") or "").strip()[:80]
    mode_label = str(payload.get("modeLabel") or "").strip()[:40]
    range_label = str(payload.get("rangeLabel") or "").strip()[:120]

    wb = Workbook()
    ws = wb.active
    ws.title = "Flagged records"

    thin = Side(style="thin", color="D6DEEA")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="3A240D")
    warn_fill = PatternFill("solid", fgColor="FFF3DD")
    ctx_fill = PatternFill("solid", fgColor="F8FAFC")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # Mirror the on-screen Flagged-records table: one row per shipment, the full
    # milestone chain, with the flagged cells highlighted + reason as a comment.
    milestone_fields = list(_TEMU_EXPORT_FIELD_ORDER)
    milestone_labels = [_TEMU_EXPORT_FIELD_LABELS.get(k, k) for k in milestone_fields]
    headers = ["Country", "AWB", "LMP", "Anchor date"] + milestone_labels

    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=15, color="FFFFFF")
    ws["A1"].fill = head_fill
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    meta = " · ".join([p for p in (scope_label, mode_label, range_label) if p])
    ws["A2"] = meta
    ws["A2"].font = Font(size=10, color="7C4A12", bold=True)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))

    header_row = 4
    for c, text in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=c, value=text)
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = head_fill
        cell.alignment = center
        cell.border = border

    from openpyxl.comments import Comment

    r = header_row + 1
    for item in raw_items[:1200]:
        if not isinstance(item, dict):
            continue
        ms_map = {}
        for ms in item.get("milestones") or []:
            if isinstance(ms, dict):
                k = str(ms.get("key") or "").strip()
                if k:
                    ms_map[k] = ms
        # Fields the backend flagged but didn't emit as a milestone (rare) still mark.
        for field in item.get("fields") or []:
            if isinstance(field, dict):
                k = str(field.get("key") or "").strip()
                if k and k not in ms_map:
                    ms_map[k] = {"iso": field.get("iso") or "", "raw": field.get("raw") or "",
                                 "suspect": True, "reason": field.get("reason") or "flagged"}

        base = [
            str(item.get("country") or ""),
            str(item.get("awb") or ""),
            str(item.get("lmp") or ""),
            item.get("day") or "",
        ]
        for c, value in enumerate(base, start=1):
            cell = ws.cell(row=r, column=c)
            if c == 4:
                _write_export_date(cell, value)
            else:
                cell.value = value
            cell.border = border
            cell.alignment = center if c in (1, 4) else left

        for j, key in enumerate(milestone_fields):
            c = 5 + j
            ms = ms_map.get(key) or {}
            cell = ws.cell(row=r, column=c)
            iso = ms.get("iso") or ""
            if iso:
                _write_export_date(cell, iso, with_time=True)
            else:
                cell.value = ms.get("raw") or ""
            cell.border = border
            cell.alignment = center
            if ms.get("suspect"):
                cell.fill = warn_fill
                try:
                    cell.comment = Comment(f"Flagged: {ms.get('reason') or 'flagged'}", "TEMU date-guard")
                except Exception:
                    pass
        r += 1

    if r == header_row + 1:
        raise ValueError("No exportable flagged records.")

    ws.freeze_panes = ws.cell(row=header_row + 1, column=5)
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{r - 1}"
    widths = [10, 22, 18, 14] + [16] * len(milestone_fields)
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    import re as _re
    import unicodedata as _ud
    safe = _ud.normalize("NFKD", f"{title} {scope_label} {mode_label}").encode("ascii", "ignore").decode()
    safe = _re.sub(r"[^A-Za-z0-9._-]+", "_", safe).strip("._") or "temu_flagged_records"
    filename = safe[:110] + ".xlsx"

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue(), filename


@server.route("/kpi-temu-export", methods=["POST"])
def kpi_temu_export_route():
    from io import BytesIO
    from flask import jsonify, send_file

    payload = request.get_json(force=True, silent=True) or {}
    try:
        content, filename = _build_kpi_temu_export(payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        log.error("KPI TEMU XLSX export error: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": f"XLSX export error: {exc}"}), 500
    return send_file(
        BytesIO(content),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


def _fmt_dt(dt) -> str:
    if dt is None:
        return "–"
    try:
        import pandas as pd
        if pd.isnull(dt):
            return "–"
    except (TypeError, ValueError):
        pass
    try:
        return dt.strftime("%m.%d %H:%M")
    except Exception:
        return "–"


def _fmt_iso_dt(value) -> str:
    if isinstance(value, datetime):
        return _fmt_dt(value)
    raw = str(value or "").strip()
    if not raw:
        return "–"
    try:
        return _fmt_dt(datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None))
    except ValueError:
        return raw[:16].replace("T", " ")


def _fmt_wait(hours: float) -> str:
    try:
        hours = float(hours or 0)
    except (TypeError, ValueError):
        hours = 0.0
    if hours < 0.05:
        return ""
    h = int(hours)
    m = int((hours - h) * 60)
    if h and m:
        return f"várakozik {h} óra {m} perce"
    if h:
        return f"várakozik {h} órája"
    return f"várakozik {m} perce"


def _tracking_saturation(kpi: dict) -> dict:
    tracking = (kpi or {}).get("tracking") or {}
    saturation = tracking.get("current_warehouse_saturation") or {}
    try:
        kg = float(saturation.get("kg") or 0)
    except (TypeError, ValueError):
        kg = 0.0
    try:
        capacity = float(saturation.get("capacity") or 0)
    except (TypeError, ValueError):
        capacity = 0.0
    try:
        ratio = float(saturation.get("ratio"))
    except (TypeError, ValueError):
        ratio = (kg / capacity) if capacity else 0.0
    ratio = max(0.0, min(1.0, ratio if ratio == ratio else 0.0))
    return {"kg": kg, "capacity": capacity, "ratio": ratio}


def _saturation_color(pct: float) -> str:
    """One hue for the whole bar, by saturation level (mirrors the KPI page)."""
    if pct >= 95:
        return "#fb7185"   # kritikus
    if pct >= 85:
        return "#f97316"   # high
    if pct >= 70:
        return "#facc15"   # elevated
    return "#34d399"       # normal


def _header_saturation_style_and_title(kpi: dict) -> tuple[dict, str, str]:
    saturation = _tracking_saturation(kpi)
    pct = saturation["ratio"] * 100
    tip_pct = max(5.0, min(95.0, pct))
    kg_txt = _fmt_kg_copy(saturation["kg"])
    cap_txt = _fmt_kg_copy(saturation["capacity"])
    return (
        {
            "--warehouse-sat-pct": f"{pct:.2f}%",
            "--warehouse-sat-tip-x": f"{tip_pct:.2f}%",
            "--warehouse-sat-color": _saturation_color(pct),
            "--warehouse-sat-alpha": "1" if saturation["capacity"] or saturation["kg"] else "0.28",
        },
        f"Raktár telítettség: {kg_txt} / {cap_txt} ({pct:.1f}%)",
        # Hover plate text (% + kg) — shown via CSS attr(data-sat).
        f"{pct:.0f}% · {kg_txt}",
    )


def _inbound_stat_counts(state: dict) -> dict:
    df = _apply_live_arrival_state((state or {}).get("df"))
    empty = {
        "all": "–",
        "at_hu": "–",
        "driver": "–",
        "scheduled": "–",
        "shippable": "–",
        "betarolt": "–",
    }
    if df is None or not hasattr(df, "__len__") or len(df) == 0:
        return empty

    is_stored_col = df["is_stored"] if "is_stored" in df.columns else None
    active = df[~is_stored_col] if is_stored_col is not None else df
    stored_df = df[is_stored_col] if is_stored_col is not None else df.iloc[0:0]
    today = datetime.now().date()
    live_awbs = set(df["awb"].tolist()) if "awb" in df.columns else set()
    snapshot_today = 0
    if not (state or {}).get("priority_test_mode"):
        snapshot_today = len([
            r for r in storage_manager.get_snapshot_rows(live_awbs)
            if isinstance(r.get("am_time"), datetime) and r["am_time"].date() == today
        ])

    token_counts = {"all": 0, "at_hu": 0, "driver": 0, "scheduled": 0, "shippable": 0}
    for row in active.to_dict("records"):
        for token in _inbound_filter_tokens(row).split():
            if token in token_counts:
                token_counts[token] += 1

    token_counts["betarolt"] = int(len(stored_df)) + snapshot_today
    return {key: str(value) for key, value in token_counts.items()}


def _fmt_since(dt) -> str:
    if dt is None:
        return ""
    try:
        import pandas as pd
        if pd.isnull(dt):
            return ""
    except (TypeError, ValueError):
        pass
    try:
        elapsed = (datetime.now() - dt).total_seconds() / 3600
        if elapsed < 0:
            return ""
        h = int(elapsed)
        m = int((elapsed - h) * 60)
        if h and m:
            return f"Felvéve {h} óra {m} perce"
        if h:
            return f"Felvéve {h} órája"
        if m:
            return f"Felvéve {m} perce"
        return "Most vették fel"
    except Exception:
        return ""


def _arrival_time_from_am(am_time):
    if isinstance(am_time, datetime):
        return am_time + timedelta(minutes=_TRUCK_ARRIVAL_DELAY_MINUTES)
    return None


def _minutes_until_arrival(row: dict) -> int:
    arrival = row.get("truck_arrival_time") or _arrival_time_from_am(row.get("am_time"))
    if not isinstance(arrival, datetime):
        return 0
    return max(0, int(((arrival - datetime.now()).total_seconds() + 59) // 60))


def _dedupe_inbound_view_df(df):
    """Keep one visible inbound card per logical AWB per active/stored state.

    Some Excel/OneDrive saves can leave duplicate active rows with the same AWB in
    the cached DataFrame. The card grid is keyed by AWB on the client, so the
    visible UI effectively behaves as one card while server-side stat counts can
    double-count. Dedupe before both stats and card rendering so they share the
    exact same display set.
    """
    if df is None or getattr(df, "empty", True) or "awb" not in getattr(df, "columns", []):
        return df
    view = df.copy()
    view["_view_base_awb"] = view["awb"].apply(lambda value: _base_awb(str(value).strip()))
    if "is_stored" in view.columns:
        view["_view_stored"] = view["is_stored"].astype(bool)
    else:
        view["_view_stored"] = False
    if "rank" in view.columns:
        view = view.sort_values("rank", kind="stable")
    kept = view.drop_duplicates(subset=["_view_stored", "_view_base_awb"], keep="first")
    return kept.drop(columns=["_view_base_awb", "_view_stored"], errors="ignore").reset_index(drop=True)


def _apply_live_arrival_state(df):
    """Re-evaluate AN+30 minute arrival state at render time."""
    if df is None or getattr(df, "empty", True):
        return df
    view = df.copy()
    if "am_time" not in view.columns:
        return view
    view["truck_arrival_time"] = view["am_time"].apply(_arrival_time_from_am)
    now = datetime.now()
    view["is_en_route"] = view["truck_arrival_time"].apply(lambda t: isinstance(t, datetime) and now < t)
    view = _dedupe_inbound_view_df(view)
    return apply_priorities(view)


def _priority_test_df() -> pd.DataFrame:
    """In-memory inbound rows for the T-key priority test surface."""
    now = datetime.now()

    def row(
        awb: str,
        lmp: str,
        *,
        am_time: datetime,
        in_bud: bool = False,
        driver_checkin=None,
        driver_in_rest: bool = False,
        rest_until=None,
        shippable: int = 0,
        total: int = 0,
        glabs_id: str = "",
        plate: str = "",
        status: str = "Felvéve",
    ) -> dict:
        ratio = shippable / total if total else 0.0
        loading_items = [
            {
                "awb": f"{awb}-{index + 1}",
                "lmp": lmp,
                "status": "Kiadható" if index < shippable else status,
                "is_ready": index < shippable,
            }
            for index in range(total)
        ]
        waiting_hours = (
            max(0.0, (now - driver_checkin).total_seconds() / 3600)
            if isinstance(driver_checkin, datetime) else 0.0
        )
        return {
            "awb": awb,
            "lmp": lmp,
            "bud_lmp": lmp if in_bud else "",
            "boxes": max(total, 1) * 10,
            "weight": max(total, 1) * 125.0,
            "cargo_type": "PLT",
            "uld_number": "",
            "am_time": am_time,
            "status": status,
            "is_partial": False,
            "is_stored": False,
            "is_shippable": shippable > 0,
            "in_bud_pallets": in_bud,
            "driver_checkin": driver_checkin if driver_checkin is not None else pd.NaT,
            "driver_in_rest": driver_in_rest,
            "rest_until": rest_until if rest_until is not None else pd.NaT,
            "waiting_hours": waiting_hours,
            "glabs_id": glabs_id,
            "glabs_total": total,
            "glabs_shippable": shippable,
            "glabs_ratio": ratio,
            "glabs_loading_items": loading_items,
            "glabs_ready_items": loading_items,
            "rendszam": plate,
        }

    rows = [
        # Deliberately weak operational state: B2B must still be absolute #1.
        row("TESZT-B2B-001", "B2B", am_time=now - timedelta(minutes=35)),
        row(
            "TESZT-AT-HU-001", "AT HU", am_time=now - timedelta(hours=1),
            in_bud=True, driver_in_rest=True, rest_until=now + timedelta(hours=2),
            shippable=2, total=4, glabs_id="TESZT-GLABS-001", plate="TESZT-001",
        ),
        row("TESZT-AGED-001", "TESZT LMP", am_time=now - timedelta(hours=6)),
        row(
            "TESZT-DRIVER-001", "TEMU", am_time=now - timedelta(hours=1),
            in_bud=True, driver_checkin=now - timedelta(minutes=45),
            shippable=3, total=4, glabs_id="TESZT-GLABS-002", plate="TESZT-002",
        ),
        row(
            "TESZT-UTON-001", "MEEST MD", am_time=now + timedelta(hours=1),
            status="Értesítő",
        ),
    ]
    return apply_priorities(pd.DataFrame(rows))


def _has_due_arrival_transition(df) -> bool:
    if df is None or getattr(df, "empty", True):
        return False
    if "is_en_route" not in df.columns or "truck_arrival_time" not in df.columns:
        return False
    now = datetime.now()
    try:
        return any(
            bool(row.get("is_en_route")) and isinstance(row.get("truck_arrival_time"), datetime)
            and row.get("truck_arrival_time") <= now
            for row in df.to_dict("records")
        )
    except Exception:
        return False


def _clean_text(value, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "n/a"}:
        return fallback
    return text


def _fmt_num(value, suffix: str = "") -> str:
    try:
        num = float(value or 0)
    except (TypeError, ValueError):
        num = 0.0
    return f"{num:.0f}{suffix}" if num else ""


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _progress_bar(shippable: int, total: int, color: str, label: str | None = None) -> html.Div:
    pct = shippable / total * 100 if total > 0 else 0
    if label is None:
        # Numerator gets data-countup so number_flux.js tweens it on value change.
        label_el = html.Span(className="glabs-progress-label", style={"color": color}, children=[
            html.Span(str(shippable), className="glabs-progress-num",
                      **{"data-countup": str(shippable)}),
            html.Span(f"/{total} kiadható", className="glabs-progress-rest"),
        ])
    else:
        label_el = html.Span(label, className="glabs-progress-label", style={"color": color})
    return html.Div(className="glabs-progress-wrap", children=[
        html.Div(className="glabs-progress-track", children=[
            html.Div(className="glabs-progress-fill",
                     style={"width": f"{pct:.0f}%", "background": color}),
        ]),
        label_el,
    ])


def _ready_item_rows(items: list[dict]) -> list[html.Div]:
    clean_items = [item for item in items if isinstance(item, dict)]
    lmp_groups = {
        lmp: idx % 8
        for idx, lmp in enumerate(sorted({
            _clean_text(item.get("lmp"))
            for item in clean_items
            if _clean_text(item.get("lmp"))
        }))
    }
    rows = []
    for item in sorted(clean_items, key=lambda x: (_clean_text(x.get("lmp")), _clean_text(x.get("awb"))))[:80]:
        lmp = _clean_text(item.get("lmp"))
        awb = _clean_text(item.get("awb"))
        status = _clean_text(item.get("status"))
        lmp_group = lmp_groups.get(lmp, 0)
        status_cls = "is-manual" if item.get("is_manual") else "is-ready" if item.get("is_ready") else "is-open"
        rows.append(html.Div(className=f"outbound-awb-row {status_cls} lmp-g{lmp_group}", children=[
            html.Div(className="outbound-awb-main", children=[
                html.Div(className="outbound-awb-head", children=[
                    html.Span(awb, className="outbound-awb-code") if awb else html.Span(),
                    html.Span(lmp, className="outbound-lmp-chip") if lmp else html.Span(),
                ]),
            ]),
            html.Span(status, className="outbound-status-pill") if status else html.Span(),
        ]))
    return rows


_COLOR_BETAROLT        = "#f59e0b"
_COLOR_STORED_INSHIFT  = "#7888a0"
_COLOR_STORED_MANUAL   = "#e67e22"


def _loading_first_children(state: dict | None = None):
    state = state or {}
    progress = max(0, min(99, int(state.get("load_progress") or 1)))
    stage = str(state.get("load_stage") or "Betöltés indítása")
    return [
        html.Div(className="l-card", children=[
            html.Div(className="l-brand", children=[
                html.Span(className="l-brand-mark", **{"aria-hidden": "true"}),
                html.Span("Flow Manager", className="l-title"),
            ]),
            html.Div(className="l-progress-head", children=[
                html.Div(children=[
                    html.Div("Adatok betöltése", className="l-heading"),
                    html.Div(stage, id="loading-stage", className="l-msg", **{"aria-live": "polite"}),
                ]),
                html.Div(f"{progress}%", id="loading-percent", className="l-percent"),
            ]),
            html.Div(
                id="loading-track", className="l-track",
                role="progressbar",
                **{"aria-label": "Adatok betöltése", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": str(progress)},
                children=[html.Div(id="loading-fill", className="l-fill", style={"width": f"{progress}%"})],
            ),
        ]),
    ]


def _loading_refresh_children():
    return [
        html.Div(className="l-topbar", children=[html.Div(className="l-topbar-fill")]),
        html.Div(className="l-pill", children=[
            html.Span(className="l-spin"),
            html.Span("Adatok újratöltése", className="l-pill-text"),
        ]),
    ]


def _uld_note_style(uld_col: str) -> dict:
    """Inline colour for the ULD note pill, derived from the truck plate colour.
    Set directly (not via a CSS var or color-mix) so it renders on every engine —
    older WebView2 builds drop color-mix() and were falling back to amber."""
    s = str(uld_col or "").strip()
    if s.startswith("hsl(") and s.endswith(")"):
        inner = s[4:-1].strip()  # "h, s%, l%"
        return {
            "color": s,
            "background": f"hsla({inner}, 0.16)",
            "borderColor": f"hsla({inner}, 0.62)",
        }
    base = s or "#facc15"
    return {"color": base, "background": "rgba(255,255,255,0.10)", "borderColor": base}


def _css_alpha(color: str, alpha: float) -> str:
    s = str(color or "").strip()
    alpha = max(0.0, min(1.0, float(alpha)))
    if s.startswith("hsl(") and s.endswith(")"):
        return f"hsla({s[4:-1].strip()}, {alpha:.2f})"
    if len(s) == 7 and s.startswith("#"):
        try:
            r = int(s[1:3], 16)
            g = int(s[3:5], 16)
            b = int(s[5:7], 16)
            return f"rgba({r}, {g}, {b}, {alpha:.2f})"
        except ValueError:
            pass
    return f"rgba(139, 148, 158, {alpha:.2f})"


def _plate_key(value: str | None) -> str:
    return " ".join(str(value or "").strip().upper().split())


def _fmt_note_at(at: str) -> str:
    """ISO datetime -> '06.10 14:32' compact form for the note meta line."""
    try:
        dt = datetime.fromisoformat(str(at))
        return dt.strftime("%m.%d %H:%M")
    except ValueError:
        return str(at)[:16]


def _make_notes_block(awb: str) -> html.Details:
    """Minimal shared notes block on an inbound card: list + one input."""
    notes = notes_manager.get_notes(awb)
    note_rows = []
    for n in notes:
        note_rows.append(html.Div(className="note-row", children=[
            html.Div(n.get("text", ""), className="note-text"),
            html.Div(className="note-meta", children=[
                html.Span(f"{n.get('by', '?')} · {_fmt_note_at(n.get('at', ''))}",
                          className="note-meta-text"),
                html.Button("✕", id={"type": "note-del", "index": f"{awb}|{n.get('id', '')}"},
                            className="note-del-btn", n_clicks=0,
                            title="Megjegyzés törlése"),
            ]),
        ]))
    count = len(notes)
    return html.Details(
        className="card-notes-block" + (" has-notes" if count else ""),
        children=[
            html.Summary(className="card-notes-summary", children=[
                html.Span("Megjegyzés", className="card-notes-label"),
                html.Span(str(count), className="card-notes-count") if count else html.Span(),
            ]),
            html.Div(className="card-notes-body", children=[
                *note_rows,
                html.Div(className="note-input-row", children=[
                    dcc.Input(id={"type": "note-input", "index": awb}, type="text",
                              maxLength=notes_manager.MAX_NOTE_LEN,
                              placeholder="Új megjegyzés…", className="note-input",
                              n_submit=0, value=""),
                    html.Button("Hozzáad", id={"type": "note-add", "index": awb},
                                className="note-add-btn", n_clicks=0),
                ]),
            ]),
        ],
    )


def _make_card(
    row: dict,
    betarolt_view: bool = False,
    can_unstore: bool = True,
    filter_tokens: str | None = None,
    glabs_color_map: dict | None = None,
    plate_color_map: dict | None = None,
    test_mode: bool = False,
) -> html.Div:
    is_snapshot = row.get("_snapshot", False)
    ck = row.get("priority_color", "standard")
    awb = str(row.get("awb", ""))
    is_stored_flag = bool(row.get("is_stored", False))
    is_partial = bool(row.get("is_partial", False))
    is_en_route = bool(row.get("is_en_route", False))
    is_b2b_priority = bool(row.get("is_b2b_priority", False))

    live_stored = betarolt_view and is_stored_flag and not is_snapshot
    if live_stored:
        color    = _COLOR_STORED_MANUAL
        card_cls = "priority-card pcard-betarolt-stored"
    elif betarolt_view:
        color    = _COLOR_STORED_INSHIFT
        card_cls = "priority-card pcard-betarolt"
    else:
        color    = _COLOR.get(ck, "#8b949e")
        card_cls = f"priority-card pcard-{ck}" + (" partial-card" if is_partial else "")
        if is_en_route and not is_b2b_priority:
            color = "#e5e7eb"

    ship_badge = (html.Span("KIADHATÓ", className="badge-shippable")
                  if row.get("is_shippable") else html.Span())

    cargo_type = row.get("cargo_type", "PLT")
    cargo_badge = html.Span(
        cargo_type,
        className=f"badge-cargo badge-{'uld' if cargo_type == 'ULD' else 'plt'}",
    )
    partial_badge = html.Span("RÉSZBEN", className="badge-partial") if is_partial else html.Span()
    en_route_badge = html.Span(
        "Úton",
        className="badge-en-route",
    ) if is_en_route and not betarolt_view else html.Span()

    is_aged = bool(row.get("is_aged", False))
    aged_hours = _safe_int(row.get("hours_since_am"), 0)
    aged_badge = html.Span(
        f"{aged_hours}ó+",
        className="badge-aged",
        title=f"{aged_hours}+ órája felvéve — régóta vár, ezért előre soroltuk",
        **{"aria-label": f"{aged_hours}+ órája felvéve"},
    ) if is_aged and not betarolt_view and not is_en_route else html.Span()
    if is_aged and not betarolt_view and not is_en_route:
        card_cls += " is-aged-card"

    if test_mode:
        # Test rows are display-only and must never reach shared storage.
        store_el = html.Span()
    elif is_en_route and not betarolt_view:
        # Úton lévő tételt nem lehet betárolni — még meg sem érkezett.
        store_el = html.Span()
    elif is_snapshot or (betarolt_view and not can_unstore):
        store_el = html.Span("✓ Betárolva", className="badge-stored badge-stored-disabled")
    elif is_stored_flag:
        store_el = html.Button(
            "✓ Betárolva",
            id={"type": "store-btn", "index": awb},
            className="badge-stored badge-stored-active",
            n_clicks=0,
        )
    else:
        store_el = html.Button(
            "Betárolva",
            id={"type": "store-btn", "index": awb},
            className="badge-stored badge-stored-empty",
            n_clicks=0,
        )

    notes_count = (
        len(notes_manager.get_notes(awb))
        if awb and not betarolt_view and not is_en_route and not test_mode else 0
    )
    note_badge = (
        html.Span(str(notes_count), className="badge-note",
                  title=f"{notes_count} megjegyzés — nyisd le a kártyát")
        if notes_count else html.Span()
    )

    header = html.Div(className="card-strip", style={"background": color}, children=[
        html.Span("" if is_en_route and not betarolt_view and not is_b2b_priority else f"#{row.get('rank', '–')}", className="rank-num"),
        html.Span("" if is_en_route and not betarolt_view and not is_b2b_priority else row.get("priority_label", ""), className="priority-label-text"),
        store_el,
        aged_badge,
        ship_badge,
        cargo_badge,
        partial_badge,
        en_route_badge,
        note_badge,
    ])

    # ── Compute GLABS and plate info before compact body ───────────────────
    glabs_id        = row.get("glabs_id") or ""
    glabs_total     = _safe_int(row.get("glabs_total"), 0)
    glabs_shippable = _safe_int(row.get("glabs_shippable"), 0)
    glabs_col = (
        glabs_color_map.get(glabs_id, _glabs_color(glabs_id))
        if glabs_color_map and glabs_id else _glabs_color(glabs_id)
    )
    progress_str    = f"{glabs_shippable}/{glabs_total} kiadható" if glabs_total > 0 else ""
    plate           = str(row.get("rendszam") or "").strip()
    plate_key = _plate_key(plate)
    truck_turn_key = _truck_turn_key(plate, row.get("am_time"))
    plate_col = (
        plate_color_map.get(plate_key, _stable_distinct_color(plate_key))
        if plate_color_map and plate_key else (_stable_distinct_color(plate_key) if plate_key else "#8b949e")
    )
    uld_number = _clean_text(row.get("uld_number"))
    uld_col = plate_col
    driver_str      = ""

    # ── Compact visible body ────────────────────────────────────────────────
    colli = _safe_int(row.get("boxes"), 0)
    weight_val = row.get("weight") or 0
    colli_label = f"~{colli} colli" if is_partial else f"{colli} colli"
    weight_label = (f"~{weight_val:,.0f} kg" if is_partial else f"{weight_val:,.0f} kg").replace(",", " ")
    since = _fmt_since(row.get("am_time"))
    truck_focus_label = plate + (f" · {_fmt_dt(row.get('am_time'))}" if plate and row.get("am_time") else "")

    compact_body = html.Div(className="card-body-compact", children=[
        html.Div(className="card-awb", children=[
            html.Span(awb, className="awb-text"),
            html.Span(
                uld_number,
                className="inbound-uld-note",
                style=_uld_note_style(uld_col),
                **{
                    "data-plate": plate,
                    "data-plate-key": plate_key,
                    "data-truck-turn-key": truck_turn_key,
                    "data-focus-label": truck_focus_label,
                    "data-plate-color": plate_col,
                },
                title=f"ULD: {uld_number}" + (f" · Rendszám: {plate}" if plate else ""),
            ) if cargo_type == "ULD" and uld_number else html.Span(),
        ]),
        html.Div(className="card-quick-meta", children=[
            html.Span(row["lmp"], className="lmp-text"),
            html.Span("·", className="meta-sep"),
            html.Span(weight_label, className="weight-text", style={"color": color}),
            html.Span("·", className="meta-sep") if colli > 0 else html.Span(),
            html.Span(colli_label, className="colli-text") if colli > 0 else html.Span(),
        ]),
        html.Div(className="card-time-compact", children=[
            html.Span("Úton" if is_en_route else "Felvéve", className="row-label"),
            html.Span("" if is_en_route else _fmt_dt(row["am_time"]), className="row-value"),
            html.Span() if is_en_route else (html.Span(since, className="since-compact") if since else html.Span()),
        ]),
        html.Div(className="card-compact-glabs", children=[
            html.Span(glabs_id, className="glabs-id glabs-id-compact",
                      style={"color": glabs_col}),
        ]) if glabs_id else html.Span(),
    ])

    lp = []
    if row["in_bud_pallets"]:
        if glabs_id and glabs_total > 0:
            lp.append(_progress_bar(glabs_shippable, glabs_total, color))
            loading_items = row.get("glabs_loading_items") or row.get("glabs_ready_items") or []
            if loading_items:
                lp.append(html.Details(className="outbound-glabs-block inbound-ready-block", children=[
                    html.Summary(className="outbound-glabs-summary", children=[
                        html.Div(className="outbound-glabs-title", children=[
                            html.Span("Rakodás tételei", className="glabs-id"),
                            html.Span(f"{len(loading_items)} tétel", className="outbound-ready-pill"),
                        ]),
                    ]),
                    html.Div(className="outbound-awb-list inbound-ready-list",
                             children=_ready_item_rows(loading_items)),
                ]))

        drv = row["driver_checkin"]
        drv_valid = drv is not None and str(drv) not in ("NaT", "None", "")
        if drv_valid:
            wait = _fmt_wait(row["waiting_hours"])
            lp.append(html.Div(className="driver-row", children=[
                html.Div(className="driver-info", children=[
                    html.Span(f"Bejelentkezve: {_fmt_dt(drv)}", className="driver-text"),
                    html.Span(wait, className="wait-badge",
                              style={"color": color}) if wait else html.Span(),
                ]),
            ]))
            if row["driver_in_rest"]:
                try:
                    rest_str = row["rest_until"].strftime("%m.%d %H:%M") + "-ig" \
                               if row.get("rest_until") else "folyamatban"
                except Exception:
                    rest_str = "folyamatban"
                lp.append(html.Div(className="rest-warning",
                                   children=[html.Span(f"Pihenőn: {rest_str}",
                                                       className="rest-text")]))
            driver_str = f"Bejelentkezve: {_fmt_dt(drv)}"
        else:
            lp.append(html.Div(className="driver-row no-driver",
                               children=[html.Span("Sofőr nincs bejelentkezve",
                                                   className="no-driver-text")]))
            driver_str = "Sofőr nincs bejelentkezve"
    else:
        lp.append(html.Div(className="driver-row no-driver",
                           children=[html.Span("Nem szerepel rakodásban",
                                               className="no-driver-text")]))

    # Shared notes: only on active INBOUND cards (not on the Betárolt view)
    notes_block = (
        _make_notes_block(awb)
        if awb and not betarolt_view and not is_en_route and not test_mode
        else html.Span()
    )

    details_section = html.Div(className="card-details-section", children=[
        html.Div(className="card-details-inner", children=[
            html.Div(className="lp-section", children=lp) if lp else html.Span(),
            notes_block,
        ]),
    ])

    card_style = {"--card-color": color}
    if plate:
        card_style["--plate-color"] = plate_col
        card_style["--plate-color-soft"] = _css_alpha(plate_col, 0.18)
        card_style["--plate-color-glow"] = _css_alpha(plate_col, 0.34)

    return html.Div(
        className=card_cls,
        style=card_style,
        **{
            "data-card-key": awb,
            "data-filter": filter_tokens or "all",
            "data-tip-plate": plate,
            "data-plate-key": plate_key,
            "data-truck-turn-key": truck_turn_key,
            "data-focus-label": truck_focus_label,
            "data-tip-driver": driver_str,
            "data-tip-progress": progress_str,
            "data-plate-color": plate_col,
        },
        children=[header, compact_body, details_section],
    )


def _build_summary_table(df):
    """TOP 10 inbound priority rows as a compact table for the Összegzés modal."""
    if df is None or getattr(df, "empty", True):
        return html.Div("Nincs aktív tétel az összegzéshez.", className="summary-empty")
    active = df[~df["is_stored"]].copy() if "is_stored" in df.columns else df.copy()
    if active.empty:
        return html.Div("Nincs aktív tétel az összegzéshez.", className="summary-empty")
    rows = (active.sort_values("rank") if "rank" in active.columns else active).head(10).to_dict("records")

    header = html.Thead(html.Tr([
        html.Th("#"), html.Th("Prioritás"), html.Th("AWB"), html.Th("LMP"),
        html.Th("Típus"), html.Th("kg"), html.Th("Felvéve"),
    ]))

    body_rows = []
    for r in rows:
        color = _COLOR.get(r.get("priority_color", "standard"), "#8b949e")
        cargo = r.get("cargo_type", "PLT")
        uld_num = _clean_text(r.get("uld_number"))
        type_txt = f"{cargo} · {uld_num}" if cargo == "ULD" and uld_num else cargo
        label_txt = _clean_text(r.get("priority_label")) or "—"
        if r.get("is_en_route") and "Úton" not in label_txt:
            label_txt = f"{label_txt} · Úton"
        is_aged = bool(r.get("is_aged", False))
        weight_val = r.get("weight") or 0
        kg_txt = f"{weight_val:,.0f}".replace(",", " ")
        time_txt = _fmt_dt(r.get("am_time")) or "—"
        since_txt = _fmt_since(r.get("am_time"))
        row_cls = "summary-row" + (" summary-row-aged" if is_aged else "")
        prio_children = [
            html.Span(className="summary-prio-dot", style={"background": color}),
            html.Span(label_txt, className="summary-prio-label"),
        ]
        if is_aged:
            prio_children.append(
                html.Span(className="summary-aged-ico",
                          title="4+ órája felvéve — régóta vár"))
        body_rows.append(html.Tr(className=row_cls, style={"--row-accent": color}, children=[
            html.Td(html.Span(str(r.get("rank", "–")), className="summary-rank-badge",
                              style={"background": color}), className="summary-rank"),
            html.Td(html.Div(prio_children, className="summary-prio-inner"),
                    className="summary-prio-cell"),
            html.Td(_clean_text(r.get("awb")) or "—", className="summary-awb"),
            html.Td(_clean_text(r.get("lmp")) or "—", className="summary-lmp"),
            html.Td(type_txt, className="summary-type"),
            html.Td(kg_txt, className="summary-kg"),
            html.Td(children=[
                html.Span(time_txt, className="summary-time-main"),
                html.Span(since_txt, className="summary-time-since") if since_txt else html.Span(),
            ], className="summary-muted summary-time-cell"),
        ]))

    return html.Div(className="summary-table-wrap", children=[
        html.Table(
            id="summary-prio-table",
            className="summary-table",
            children=[header, html.Tbody(body_rows)],
        )
    ])


def _make_outbound_card(row: dict, filter_tokens: str | None = None) -> html.Div:
    ck = row.get("priority_color", "standard")
    color = _COLOR.get(ck, "#8b949e")
    glabs_items = row.get("glabs_items", [])

    badges = [
        html.Span(f"{row.get('lmp_count', 0)} lerakó", className="badge-cargo badge-uld")
    ]
    if row.get("in_rest"):
        badges.insert(0, html.Span("PIHENŐ", className="badge-shippable"))

    header = html.Div(className="card-strip", style={"background": color}, children=[
        html.Span(f"#{row.get('rank', '-')}", className="rank-num"),
        html.Span(_clean_text(row.get("status_label")), className="priority-label-text"),
        *badges,
    ])

    timing = []
    if row.get("checkin"):
        timing.append(html.Div(className="card-row", children=[
            html.Span("Bejelentkezés:", className="row-label"),
            html.Span(_fmt_dt(row.get("checkin")), className="row-value"),
        ]))
    if row.get("last_issue"):
        timing.append(html.Div(className="card-row", children=[
            html.Span("Utolsó kiadás:", className="row-label"),
            html.Span(_fmt_dt(row.get("last_issue")), className="row-value"),
        ]))
    if row.get("in_rest"):
        rest_label = "folyamatban"
        if row.get("rest_until"):
            try:
                rest_label = row["rest_until"].strftime("%m.%d %H:%M") + "-ig"
            except Exception:
                pass
        timing.append(html.Div(className="rest-warning", children=[
            html.Span(f"Pihenőn: {rest_label}", className="rest-text")
        ]))

    plate_clean = _clean_text(row.get("plate")) or ""
    glabs_blocks = []
    for glabs in glabs_items[:8]:
        awb_rows = []
        for item in glabs.get("awb_items", [])[:80]:
            status = _clean_text(item.get("status"))
            lmp = _clean_text(item.get("lmp"))
            lmp_group = int(item.get("lmp_group", 0) or 0) % 8
            status_cls = "is-issued" if item.get("is_issued") else "is-ready" if item.get("is_ready") else "is-open"
            detail_bits = [
                _fmt_num(item.get("boxes"), " colli"),
                _fmt_num(item.get("weight"), " kg"),
            ]
            detail_text = " · ".join(bit for bit in detail_bits if bit)
            awb_rows.append(html.Div(className=f"outbound-awb-row {status_cls} lmp-g{lmp_group}", children=[
                html.Div(className="outbound-awb-main", children=[
                    html.Div(className="outbound-awb-head", children=[
                        html.Span(_clean_text(item.get("awb")), className="outbound-awb-code"),
                        html.Span(lmp, className="outbound-lmp-chip") if lmp else html.Span(),
                    ]),
                    html.Span(detail_text, className="outbound-awb-meta") if detail_text else html.Span(),
                ]),
                html.Span(status, className="outbound-status-pill") if status else html.Span(),
            ]))

        glabs_label = _clean_text(glabs.get("glabs_id"))
        glabs_blocks.append(html.Details(className="outbound-glabs-block", children=[
            html.Summary(className="outbound-glabs-summary", children=[
                html.Div(className="outbound-glabs-title", children=[
                    html.Span(glabs_label, className="glabs-id") if glabs_label else html.Span(),
                    html.Span(f"{glabs.get('total', 0)} tétel", className="outbound-ready-pill"),
                ]),
                _progress_bar(
                    int(glabs.get("ready", 0)),
                    int(glabs.get("total", 0)),
                    color,
                    f"{glabs.get('ready', 0)}/{glabs.get('total', 0)} kiadható",
                ),
            ]),
            html.Div(className="outbound-awb-list", children=awb_rows),
        ]))

    # Quick-print buttons — one per GLABS, always visible in card top-right
    print_btns = [
        html.Button(
            "🖨 Rakodási terv",
            id={"type": "print-glabs-btn", "index": f"{plate_clean}|||{_clean_text(g.get('glabs_id')) or ''}"},
            className="outbound-qprint-btn",
            n_clicks=0,
            title=f"Rakodási terv — {_clean_text(g.get('glabs_id')) or ''}",
        )
        for g in glabs_items[:8]
    ]

    notes = [
        html.Span(note, className="outbound-note")
        for note in row.get("notes", [])[:3]
    ]

    body = html.Div(className="card-body-inner outbound-card-body", children=[
        html.Div(className="outbound-card-top", children=[
            html.Div(className="card-awb", children=[
                html.Span(_clean_text(row.get("plate")), className="awb-text outbound-plate")
            ]),
            html.Div(className="outbound-print-strip", children=print_btns) if print_btns else html.Span(),
        ]),
        html.Div(className="card-meta", children=[
            html.Span(_clean_text(row.get("lmp_summary")), className="lmp-text"),
            html.Span(f"{row.get('issued', 0)}/{row.get('total', 0)} sor",
                      className="weight-text", style={"color": color}),
        ]),
        *timing,
        html.Div(className="outbound-summary-grid", children=[
            html.Div([html.Span("Colli", className="row-label"), html.Strong(_fmt_num(row.get("boxes")))]),
            html.Div([html.Span("Súly", className="row-label"), html.Strong(_fmt_num(row.get("weight"), " kg"))]),
            html.Div([html.Span("Lokáció", className="row-label"), html.Strong(_clean_text(row.get("locations")))]),
        ]),
        html.Div(className="outbound-notes", children=notes) if notes else html.Span(),
        html.Div(className="lp-section outbound-glabs-list", children=glabs_blocks),
    ])

    # Rendszám nélküli tételek GLABS-onként külön kártyák, AZONOS "NINCS RENDSZÁM"
    # felirattal — a FLIP-kulcs (data-card-key) viszont legyen egyedi a GLABS-szal,
    # különben a card_fx.js animáció összekeverné a pozíciókat.
    _plate_text = _clean_text(row.get("plate"))
    _card_key = _plate_text
    if _plate_text == "NINCS RENDSZÁM":
        _gids = row.get("glabs_items") or []
        if _gids:
            _card_key = f"{_plate_text}|{_clean_text(_gids[0].get('glabs_id'))}"

    return html.Div(
        className=f"priority-card pcard-{ck} outbound-card",
        style={"--card-color": color},
        **{
            "data-card-key": f"outbound:{_card_key}",
            "data-filter": filter_tokens or "all",
        },
        children=[header, body],
    )


def _inbound_filter_tokens(row: dict, betarolt_view: bool = False) -> str:
    if betarolt_view:
        return "betarolt"

    tokens = ["all"]
    pg = row.get("priority_group")
    if bool(row.get("is_at_hu", row.get("is_at_hu_priority", False))):
        tokens.append("at_hu")
    if pg in (GRP_DRIVER_ACTIVE_HIGH, GRP_DRIVER_ACTIVE_LOW):
        tokens.append("driver")
    if pg in (GRP_DRIVER_PASSIVE_HIGH, GRP_DRIVER_PASSIVE_LOW, GRP_EN_ROUTE):
        tokens.append("scheduled")
    if bool(row.get("is_shippable", False)):
        tokens.append("shippable")
    return " ".join(tokens)


def _outbound_filter_tokens(row: dict) -> str:
    active = _safe_int(row.get("active"), 0)
    ready = _safe_int(row.get("ready"), 0)
    has_checkin = bool(row.get("checkin"))
    in_rest = bool(row.get("in_rest"))

    tokens = ["all"]
    if active > 0 and has_checkin and not in_rest:
        tokens.append("at_hu")
    if active > 0 and ready > 0 and not has_checkin and not in_rest:
        tokens.append("driver")
    if in_rest:
        tokens.append("scheduled")
    if active > 0 and not has_checkin and ready == 0 and not in_rest:
        tokens.append("shippable")
    if active == 0:
        tokens.append("betarolt")
    return " ".join(tokens)


def _cards_grid(cards, class_name: str = "cards-grid", empty_text: str = "Nincs tétel ebben a kategóriában.") -> html.Div:
    return html.Div(children=[
        html.Div(className=class_name, children=cards),
        html.Div(empty_text, className="empty-state filter-empty-state", style={"display": "none"}),
    ])


# (btn_id, filter_key, label, color, count_id)
_STAT_DEFS = [
    ("stat-btn-all",      "all",       "Aktív tétel",       None,      "scount-all"),
    ("stat-btn-athu",     "at_hu",     "AT/HU",             "#ff6b35", "scount-athu"),
    ("stat-btn-temu",     "temu",      "TEMU",              "#f43f5e", "scount-temu"),
    ("stat-btn-drv",      "driver",    "Sofőr helyszínen",  "#00d4aa", "scount-drv"),
    ("stat-btn-sched",    "scheduled", "Ütemezett",         "#4a9eff", "scount-sched"),
    ("stat-btn-ship",     "shippable", "Kiadható",          "#22c55e", "scount-ship"),
    ("stat-btn-betarolt", "betarolt",  "Betárolt tételek",  "#f59e0b", "scount-betarolt"),
]

_STAT_DEFS = [d for d in _STAT_DEFS if d[1] != "temu"]
_STAT_DEFS = [
    (
        btn_id,
        fkey,
        "Nincs itt / pihenőn" if fkey == "scheduled" else label,
        "#7c72dc" if fkey == "scheduled" else color,
        count_id,
    )
    for btn_id, fkey, label, color, count_id in _STAT_DEFS
]


def _make_stat_btn(btn_id, _fkey, label, color, count_id):
    glow = color + "38" if color else None   # ~22% opacity hex alpha
    label_id = count_id.replace("scount", "slabel")
    return html.Div(
        id=btn_id,
        className="stat-item",
        n_clicks=0,
        style={"--stat-color": color, "--stat-glow": glow} if color else {},
        children=[
            html.Span(
                className="stat-dot",
                style={"background": color} if color else {"background": "var(--border)"},
            ),
            html.Div(className="stat-nums", children=[
                html.Span("–", id=count_id, className="stat-value",
                          style={"color": color} if color else {}),
                html.Span(label, id=label_id, className="stat-label"),
            ]),
        ],
    )


# ---------------------------------------------------------------------------
# ULD view builders
# ---------------------------------------------------------------------------

_ULD_PREFIX_COLORS: dict[str, str] = {
    "PMC": "#6366f1",
    "PMD": "#8b5cf6",
    "PAG": "#ec4899",
    "PAP": "#f43f5e",
    "LD3": "#f97316",
    "LD7": "#fb923c",
    "LD1": "#eab308",
    "AKE": "#06b6d4",
    "AKH": "#0ea5e9",
    "DQF": "#10b981",
    "RKN": "#14b8a6",
    "NKA": "#3b82f6",
    "NKB": "#60a5fa",
    "HMA": "#a78bfa",
    "HMJ": "#c084fc",
}


def _uld_prefix_color(uld_type: str) -> str:
    return _ULD_PREFIX_COLORS.get(uld_type[:3].upper(), "#64748b")


def _hex_alpha(hex_color: str, alpha: str) -> str:
    raw = (hex_color or "").strip()
    if raw.startswith("#") and len(raw) == 7:
        return f"{raw}{alpha}"
    return raw or "#64748b"


def _contrast_text_for_hex(hex_color: str) -> str:
    raw = (hex_color or "").strip().lstrip("#")
    if len(raw) != 6:
        return "#ffffff"
    try:
        r = int(raw[0:2], 16)
        g = int(raw[2:4], 16)
        b = int(raw[4:6], 16)
    except ValueError:
        return "#ffffff"
    luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    return "#0f172a" if luminance > 0.58 else "#ffffff"


# GHA colors — distinct from ULD prefix colors; sequential to guarantee uniqueness
_GHA_COLOR_PALETTE = [
    "#ff6b35",   # orange-red
    "#ffd60a",   # yellow
    "#30d158",   # green
    "#64d2ff",   # sky
    "#ff375f",   # red-pink
    "#bf5af2",   # purple
    "#32ade6",   # blue
    "#ff9f0a",   # amber
    "#30b0c7",   # teal
    "#ff6961",   # salmon
    "#2ac8a5",   # green-teal
    "#ac8e68",   # brown
]

_GLABS_PALETTE = [
    "#60a5fa",  # blue
    "#a78bfa",  # violet
    "#34d399",  # emerald
    "#f59e0b",  # amber
    "#fb7185",  # rose
    "#38bdf8",  # sky
    "#fb923c",  # orange
    "#e879f9",  # fuchsia
    "#4ade80",  # green
    "#818cf8",  # indigo
    "#f472b6",  # pink
    "#2dd4bf",  # teal
]

_GOLDEN_ANGLE = 137.508  # degrees — maximises hue separation between consecutive values
_DISTANT_TRUCK_HUES = [
    8,    # red, only one red-family slot in the first palette cycle
    204,  # cyan-blue
    116,  # green
    286,  # violet
    64,   # yellow-green
    244,  # royal blue
    156,  # mint
    304,  # fuchsia-purple, kept away from the red/burgundy family
    88,   # lime
    180,  # cyan
    268,  # purple
    42,   # amber
    224,  # blue
    140,  # emerald
    318,  # violet-magenta, not a second red slot
    72,   # yellow-green
]


def _glabs_color(glabs_id: str | None) -> str:
    if not glabs_id:
        return "#8b949e"
    h = sum(ord(c) for c in str(glabs_id))
    return _GLABS_PALETTE[h % len(_GLABS_PALETTE)]


def _stable_distinct_color(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "#8b949e"
    digest = hashlib.blake2s(text.upper().encode("utf-8"), digest_size=4).digest()
    n = int.from_bytes(digest, "big")
    hue = (n * _GOLDEN_ANGLE) % 360
    sat = 72 + (n % 14)
    light = 54 + ((n >> 8) % 10)
    return f"hsl({hue:.1f}, {sat}%, {light}%)"


def _hue_distance(a: float, b: float) -> float:
    diff = abs((a - b) % 360)
    return min(diff, 360 - diff)


def _is_red_family_hue(hue: float) -> bool:
    normalized = hue % 360
    return normalized <= 32 or normalized >= 332


def _best_distinct_truck_hue(preferred: float, used_hues: list[float], seed: int) -> float:
    if not used_hues:
        return preferred % 360
    red_used = any(_is_red_family_hue(h) for h in used_hues)

    def min_distance(hue: float) -> float:
        return min(_hue_distance(hue, used) for used in used_hues)

    preferred = preferred % 360
    if min_distance(preferred) >= 34 and not (red_used and _is_red_family_hue(preferred)):
        return preferred

    candidates = [8.0]
    candidates.extend(float(h) for h in range(36, 332, 4))
    best = None
    best_score = (-1.0, -1.0)
    seed_target = seed % 360
    for hue in candidates:
        if red_used and _is_red_family_hue(hue):
            continue
        distance = min_distance(hue)
        seed_fit = 180.0 - _hue_distance(hue, seed_target)
        score = (distance, seed_fit)
        if score > best_score:
            best = hue
            best_score = score
    return preferred if best is None else best


def _make_id_color_map(values) -> dict[str, str]:
    """Assign stable, high-separation colors to distinct values."""
    unique = sorted({str(v).strip() for v in values if v and str(v).strip()})
    result: dict[str, str] = {}
    used_hues: list[float] = []
    for index, val in enumerate(unique):
        digest = hashlib.blake2s(val.upper().encode("utf-8"), digest_size=4).digest()
        seed = int.from_bytes(digest, "big")
        if index < len(_DISTANT_TRUCK_HUES):
            preferred_hue = float(_DISTANT_TRUCK_HUES[index])
        else:
            preferred_hue = (36.0 + (index - len(_DISTANT_TRUCK_HUES)) * _GOLDEN_ANGLE) % 360
            if _is_red_family_hue(preferred_hue):
                preferred_hue = (preferred_hue + 54) % 360
        best_hue = _best_distinct_truck_hue(preferred_hue, used_hues, seed)
        used_hues.append(best_hue)
        sat = 78 + ((seed + index) % 8)
        light = 56 + ((seed >> 5) % 7)
        result[val] = f"hsl({best_hue:.1f}, {sat}%, {light}%)"
    return result


def _active_truck_palette_rows(df: pd.DataFrame | None) -> list[dict]:
    """Rows that drive the right-side truck palette and matching ULD badges."""
    if df is None or df.empty:
        return []
    rows_df = df[~df["is_stored"]].copy() if "is_stored" in df.columns else df.copy()
    if "is_en_route" in rows_df.columns:
        rows_df = rows_df[~rows_df["is_en_route"].fillna(False).astype(bool)].copy()
    return rows_df.to_dict("records")


def _truck_plate_color_map(rows: list[dict]) -> dict[str, str]:
    return _make_id_color_map([_plate_key(r.get("rendszam")) for r in rows])


def _truck_turn_key(plate: str, am_time) -> str:
    plate_key = _plate_key(plate)
    if not plate_key:
        return ""
    if isinstance(am_time, datetime):
        return f"{plate_key}|{am_time.isoformat(timespec='minutes')}"
    raw = str(am_time or "").strip()
    return f"{plate_key}|{raw}" if raw else plate_key


def _truck_groups(rows: list[dict], plate_color_map: dict[str, str] | None = None) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows or []:
        plate = str(row.get("rendszam") or "").strip()
        if not plate:
            continue
        plate_key = _plate_key(plate)
        key = _truck_turn_key(plate, row.get("am_time"))
        info = grouped.setdefault(
            key,
            {
                "plate": plate,
                "plate_key": plate_key,
                "turn_key": key,
                "color": (plate_color_map or {}).get(plate_key, _stable_distinct_color(plate_key)),
                "ulds": set(),
                "rows_by_awb": {},
                "am_times": [],
                "arrival_times": [],
                "arrived": 0,
                "en_route": 0,
                "shippable": 0,
                "has_plt": False,
            },
        )
        awb = str(row.get("awb") or "").strip()
        if awb and awb not in info["rows_by_awb"]:
            info["rows_by_awb"][awb] = row
        am_time = row.get("am_time")
        if isinstance(am_time, datetime):
            info["am_times"].append(am_time)

        is_en_route = bool(row.get("is_en_route"))
        if is_en_route:
            info["en_route"] += 1
            arr = row.get("truck_arrival_time")
            if isinstance(arr, datetime):
                info["arrival_times"].append(arr)
        else:
            info["arrived"] += 1
            if row.get("is_shippable"):
                info["shippable"] += 1

        if row.get("cargo_type") == "ULD":
            raw_uld = row.get("uld_number")
            ulds = _parse_uld_numbers(raw_uld) or ([str(raw_uld).strip()] if str(raw_uld or "").strip() else [])
            for uld in ulds:
                uld_text = str(uld or "").strip().upper()
                if uld_text:
                    info["ulds"].add(uld_text)
        else:
            info["has_plt"] = True

    if not grouped:
        return []

    # Every arriving truck is listed now (ULD + PLT). A truck counts as "Úton"
    # only when *all* of its items are still en route; once any item has landed
    # it sorts up with the physically-present trucks.
    for info in grouped.values():
        info["is_en_route_truck"] = info["arrived"] == 0 and info["en_route"] > 0

    return sorted(
        grouped.values(),
        key=lambda info: (
            info["is_en_route_truck"],          # arrived trucks first, en-route last
            -info["shippable"],                 # most releasable first
            -len(info["rows_by_awb"]),          # then by item count
            min(info.get("am_times") or [datetime.max]),
            info["plate"].upper(),
        ),
    )


def _truck_drilldown_rows(info: dict) -> list:
    """Minimal per-truck hint list (AWB · LMP). The full detail lives on the big
    cards in the grid; here it's just a faint secondary reference."""
    items = []
    rows = sorted(
        info["rows_by_awb"].values(),
        key=lambda r: (
            bool(r.get("is_en_route")),
            0 if r.get("is_shippable") else 1,
            str(r.get("lmp") or ""),
        ),
    )
    for r in rows[:14]:
        awb = str(r.get("awb") or "").strip()
        lmp = str(r.get("lmp") or "").strip()
        items.append(html.Div(className="truck-drill-row", children=[
            html.Span(awb, className="truck-drill-awb"),
            html.Span(lmp, className="truck-drill-lmp") if lmp else html.Span(),
        ]))
    hidden = len(info["rows_by_awb"]) - len(rows[:14])
    if hidden > 0:
        items.append(html.Div(f"+{hidden} more", className="truck-drill-more"))
    return items


def _truck_sidebar_items(
    rows: list[dict],
    plate_color_map: dict[str, str],
    limit: int = 24,
    allow_store: bool = True,
) -> list:
    # Synthetic priority-view rows must never expose a shared-state action,
    # even if a stale callback invocation supplies the default flag.
    allow_store = allow_store and not any(
        str(row.get("awb") or "").startswith("TESZT-") for row in rows
    )
    ordered = _truck_groups(rows, plate_color_map)
    if not ordered:
        return [
            html.Div(className="truck-sidebar-title-row", children=[
                html.Span("Kamionok", className="truck-sidebar-title"),
                html.Span("0", className="truck-sidebar-count"),
            ]),
            html.Div("Nincs aktív érkező kamion.", className="truck-sidebar-empty"),
        ]

    shown = ordered[:limit]
    cards = []
    for info in shown:
        awb_count = len(info["rows_by_awb"])
        uld_count = len(info["ulds"])
        color = info["color"]
        en_route_truck = info["is_en_route_truck"]
        first_am = min(info.get("am_times") or [], default=None)
        first_eta = min(info.get("arrival_times") or [], default=None)

        # Composition chips: ULD szám (if any) + PLT marker, so it's clear at a
        # glance whether the truck carries ULDs, pallets, or a mix.
        comp_chips = []
        if uld_count:
            comp_chips.append(html.Span(f"{uld_count} ULD", className="truck-comp-chip is-uld"))
        if info["has_plt"]:
            comp_chips.append(html.Span("PLT", className="truck-comp-chip is-plt"))

        head_children = [
            html.Span(className="truck-sidebar-dot", style={"background": color, "color": color}),
            html.Span(info["plate"], className="truck-sidebar-plate"),
        ]
        if en_route_truck:
            head_children.append(html.Span("Úton", className="truck-route-badge"))
        elif allow_store:
            # Storing only makes sense once the truck is physically here.
            head_children.append(html.Button(
                "Betárolva",
                id={"type": "truck-store-btn", "index": info["turn_key"]},
                className="truck-store-btn",
                n_clicks=0,
                title=f"A kamion minden aktív tétele betárolva: {awb_count} tétel",
            ))

        fact_children = [
            *comp_chips,
            html.Span(
                [html.Strong(str(awb_count)), html.Span("tétel")],
                className="truck-awb-count",
            ),
        ]
        if not en_route_truck and info["shippable"]:
            fact_children.append(html.Span(
                [html.Strong(str(info["shippable"])), html.Span("kiadható")],
                className="truck-ship-count",
            ))
        pickup_time = None
        if en_route_truck and first_eta:
            pickup_time = html.Span([
                html.Span("Érk.", className="truck-time-label"),
                html.Span(_fmt_dt(first_eta), className="truck-time-value"),
            ], className="truck-pickup-time")
        elif first_am:
            pickup_time = html.Span([
                html.Span("Felvéve", className="truck-time-label"),
                html.Span(_fmt_dt(first_am), className="truck-time-value"),
            ], className="truck-pickup-time")
        focus_label = info["plate"] + (f" · {_fmt_dt(first_am)}" if first_am else "")

        card_cls = "truck-sidebar-card truck-focus-source" + (" is-en-route" if en_route_truck else "")
        cards.append(
            html.Div(
                className=card_cls,
                role="button",
                tabIndex=0,
                style={
                    "--truck-color": color,
                    "--truck-color-soft": _css_alpha(color, 0.16),
                    "--truck-color-glow": _css_alpha(color, 0.30),
                },
                title=f"{info['plate']} — tételek kiemelése (kattints), részletek hoverre",
                **{
                    "data-plate-key": _plate_key(info["plate"]),
                    "data-truck-turn-key": info["turn_key"],
                    "data-plate": info["plate"],
                    "data-focus-label": focus_label,
                    "data-plate-color": color,
                    "data-awb-count": str(awb_count),
                },
                children=[
                    html.Div(className="truck-sidebar-head", children=head_children),
                    html.Div(className="truck-sidebar-meta", children=[
                        html.Div(className="truck-sidebar-facts", children=fact_children),
                        pickup_time if pickup_time is not None else html.Span(),
                    ]),
                    html.Div(className="truck-sidebar-drill", children=[
                        html.Div(className="truck-sidebar-drill-inner", children=_truck_drilldown_rows(info)),
                    ]),
                ],
            )
        )
    hidden = len(ordered) - len(shown)
    children = [
        html.Div(className="truck-sidebar-title-row", children=[
            html.Span("Kamionok", className="truck-sidebar-title"),
            html.Span(str(len(ordered)), className="truck-sidebar-count"),
        ]),
        html.Div(className="truck-sidebar-list", children=cards),
    ]
    if hidden > 0:
        children.append(html.Div(f"+{hidden} további kamion", className="truck-sidebar-more"))
    return children


# Fixed brand-like colors per GHA so a given handler always renders the same
# hue everywhere (stack header, badges, copied table, dispatch popup).
_GHA_FIXED_COLORS = {
    "menzies":  "#ffd60a",   # sárga
    "celebi":   "#ff6b35",   # narancs
    "as cargo": "#32ade6",   # kék
}


def _compute_gha_color_map(all_uld: list[dict]) -> dict[str, str]:
    """Assign colors to each GHA. Known GHAs get a fixed brand color
    (Menzies = sárga, Celebi = narancs, AS Cargo = kék); any other GHA falls
    back to the palette by sorted position, skipping the fixed colors."""
    ghas = sorted({u.get("gha", "") for u in all_uld if u.get("gha")})
    used = set(_GHA_FIXED_COLORS.values())
    palette = [c for c in _GHA_COLOR_PALETTE if c not in used] or _GHA_COLOR_PALETTE
    result: dict[str, str] = {}
    idx = 0
    for g in ghas:
        fixed = _GHA_FIXED_COLORS.get(g.strip().lower())
        if fixed:
            result[g] = fixed
        else:
            result[g] = palette[idx % len(palette)]
            idx += 1
    return result


def _uld_gha_color(gha: str, color_map: dict | None = None) -> str:
    value = (gha or "").strip()
    if not value:
        return "#64748b"
    if color_map and value in color_map:
        return color_map[value]
    fixed = _GHA_FIXED_COLORS.get(value.lower())
    if fixed:
        return fixed
    # fallback: position-stable hash over sorted all-GHAs isn't available; use index of char sum
    h = sum(ord(c) for c in value)
    return _GHA_COLOR_PALETTE[h % len(_GHA_COLOR_PALETTE)]


def _search_blob(*values) -> str:
    text = " ".join(str(v or "") for v in values)
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _normalize_uld_search_token(value) -> str:
    return _normalize_uld_code(value)


# Visually-identical letter/digit lookalikes that are routinely confused when a
# ULD number is read off a label or typed in: "O" vs "0", "I" vs "1", etc. The
# same physical ULD can therefore appear as "PMC7051503" in Excel but be typed
# as "PMC70515O3" (or the reverse). Folding both sides to one representative makes
# the search tolerant of that. Used ONLY for search comparison — never for the
# stored uld_number, stack keys, dedup or rename, so distinct real ULDs are never
# merged in storage.
_ULD_SEARCH_FOLD = str.maketrans({
    "O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6",
})


def _uld_search_fold(value) -> str:
    """Glyph-tolerant ULD search key (lookalike letters/digits collapsed)."""
    return _normalize_uld_search_token(value).translate(_ULD_SEARCH_FOLD)


def _edit_distance_at_most(left: str, right: str, limit: int = 2) -> int | None:
    if left == right:
        return 0
    if not left or not right or abs(len(left) - len(right)) > limit:
        return None
    previous = list(range(len(right) + 1))
    for i, c1 in enumerate(left, 1):
        current = [i]
        row_min = current[0]
        for j, c2 in enumerate(right, 1):
            cost = 0 if c1 == c2 else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            current.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return None
        previous = current
    distance = previous[-1]
    return distance if distance <= limit else None


def _near_uld_matches(term: str, all_uld: list[dict], limit: int = 2, max_results: int = 1) -> list[dict]:
    term = _normalize_uld_search_token(term)
    if len(term) < 6:
        return []
    candidates: list[tuple[int, int, dict]] = []
    for idx, uld in enumerate(all_uld):
        uld_num = _normalize_uld_search_token(uld.get("uld_number"))
        if not uld_num or uld_num == term:
            continue
        if len(uld_num) >= 3 and len(term) >= 3 and uld_num[:3] != term[:3]:
            continue
        dist = _edit_distance_at_most(term, uld_num, limit)
        if dist is not None:
            candidates.append((dist, idx, uld))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [uld for _, _, uld in candidates[:max_results]]


def _bulk_uld_resolution(all_uld: list[dict], search_text: str) -> tuple[list[dict], dict[str, list[dict]], list[str]]:
    terms = _bulk_uld_search_terms(search_text or "")
    if not terms:
        return [], {}, []
    by_uld = {
        _normalize_uld_search_token(u.get("uld_number")): u
        for u in all_uld
        if u.get("uld_number")
    }
    exact: list[dict] = []
    near: dict[str, list[dict]] = {}
    missing: list[str] = []
    seen_ulds: set[str] = set()
    for term in terms:
        if term in by_uld:
            uld = by_uld[term]
            uld_key = _normalize_uld_search_token(uld.get("uld_number"))
            if uld_key not in seen_ulds:
                exact.append(uld)
                seen_ulds.add(uld_key)
            continue
        suggestions = []
        for uld in _near_uld_matches(term, all_uld):
            uld_key = _normalize_uld_search_token(uld.get("uld_number"))
            if uld_key not in seen_ulds:
                suggestions.append(uld)
                seen_ulds.add(uld_key)
        if suggestions:
            near[term] = suggestions
        else:
            missing.append(term)
    return exact, near, missing


def _bulk_uld_search_terms(query: str) -> list[str]:
    if not query or "\n" not in str(query):
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for line in str(query).splitlines():
        term = _normalize_uld_search_token(line)
        if term and term not in seen:
            terms.append(term)
            seen.add(term)
    return terms if len(terms) > 1 else []


def _uld_time_str(hours: float) -> str:
    abs_h = abs(hours)
    total_m = int(abs_h * 60 + 0.999999) if abs_h > 0 else 0
    h = total_m // 60
    m = total_m % 60
    if h <= 0:
        return f"{m}p"
    return f"{h}ó {m}p"


def _uld_matches_query(uld_num: str, info: dict, query: str) -> bool:
    bulk_terms = _bulk_uld_search_terms(query)
    if bulk_terms:
        return _normalize_uld_search_token(uld_num) in set(bulk_terms)
    q = _search_blob(query)
    q_uld = _uld_search_fold(query)
    if not q:
        return False
    return (q_uld and q_uld in _uld_search_fold(uld_num)) or q in _search_blob(
        uld_num,
        info.get("uld_type"),
        (uld_num or "")[:3],
        info.get("gha"),
        info.get("status"),
        " ".join(str(awb) for awb in info.get("awbs", [])),
    )


def _filter_uld_for_view(
    all_uld: list[dict],
    status_filter: str = "all",
    prefix_filter: str = "",
    gha_filter: str = "",
    search_text: str = "",
) -> tuple[list[dict], bool]:
    bulk_uld_terms = _bulk_uld_search_terms(search_text or "")
    if bulk_uld_terms:
        exact, near, _missing = _bulk_uld_resolution(all_uld, search_text or "")
        result = list(exact)
        for term in bulk_uld_terms:
            for uld in near.get(term, []):
                marked = dict(uld)
                marked["_search_requested_uld"] = term
                marked["_search_match_kind"] = "near"
                result.append(marked)
        return result, True

    filtered = all_uld
    if status_filter and status_filter != "all":
        filtered = [u for u in filtered if u.get("status") == status_filter]
    if prefix_filter:
        filtered = [u for u in filtered if u.get("uld_type", "")[:3].upper() == prefix_filter.upper()]
    if gha_filter:
        filtered = [u for u in filtered if u.get("gha") == gha_filter]
    if search_text:
        q = _search_blob(search_text)
        q_uld = _uld_search_fold(search_text)
        if q:
            def _matches(u: dict) -> bool:
                return (q_uld and q_uld in _uld_search_fold(u.get("uld_number"))) or q in _search_blob(
                    u.get("uld_number"),
                    u.get("uld_type"),
                    (u.get("uld_type") or "")[:3],
                    u.get("gha"),
                    u.get("status"),
                    " ".join(str(awb) for awb in u.get("awbs", [])),
                )
            matched = [u for u in filtered if _matches(u)]
            # Single-line fallback: a lone ULD that has no exact/substring hit still
            # deserves the same near-match resolution the multi-line (bulk) path gives,
            # so a slightly mistyped/aged ULD surfaces a "Hasonló találat" instead of
            # an empty result. Only kicks in for ULD-like terms (handled inside
            # _near_uld_matches: needs len ≥ 6 and a matching 3-char prefix), so plain
            # partial/text searches keep their normal substring behaviour.
            if not matched:
                term = _normalize_uld_search_token(search_text)
                # Single search → a single best hit. _near_uld_matches is already
                # sorted by edit distance, so the closest ULD is returned alone.
                # IMPORTANT: a single explicit search must only fall back to a
                # *genuine typo* (edit distance ≤ 1). Allowing distance 2 here meant
                # searching e.g. "PAG01291GH" (momentarily inactive) silently
                # surfaced "PAG01131GH" — a clearly different ULD that differs by two
                # characters — which reads as "the search returned the wrong ULD".
                # Bulk (multi-line) paste keeps the looser distance-2 suggestions.
                near = _near_uld_matches(term, filtered, limit=1, max_results=1)
                if near:
                    result = []
                    for uld in near:
                        marked = dict(uld)
                        marked["_search_requested_uld"] = term
                        marked["_search_match_kind"] = "near"
                        result.append(marked)
                    return result, True
            filtered = matched
    return filtered, False


def _make_uld_row(uld: dict, stacks: list[dict], gha_color_map: dict | None = None, stack_name_map: dict | None = None) -> html.Div:
    uld_num   = uld["uld_number"]
    uld_type  = uld["uld_type"]
    awbs_str  = ", ".join(uld["awbs"]) if uld["awbs"] else "—"
    gha_raw   = _normalize_uld_gha(uld.get("gha"))
    gha_conflict = bool(uld.get("gha_conflict"))
    gha_candidates = [str(item) for item in (uld.get("gha_candidates") or []) if str(item).strip()]
    gha_display = gha_raw or ("GHA eltérés" if gha_conflict else "Nincs GHA adat")
    status    = uld["status"]
    rem_h     = uld["remaining_hours"]
    expiry    = uld["expiry_time"]
    expired   = uld["is_expired"]
    search_requested = _normalize_uld_search_token(uld.get("_search_requested_uld"))
    is_near_match = uld.get("_search_match_kind") == "near" and search_requested

    rem_text  = _uld_time_str(rem_h)
    cd_label  = "Lejárt" if expired else "Hátra"

    pcolor = _uld_prefix_color(uld_type)
    badge_style = {
        "background": f"{pcolor}22",
        "color": pcolor,
        "border": f"1px solid {pcolor}55",
    }

    current_stack = next((s for s in stacks if uld_num in s.get("ulds", [])), None)
    gha_color = _uld_gha_color(gha_raw, gha_color_map) if gha_raw else None
    prepared = bool(current_stack and current_stack.get("prepared"))
    is_manual = bool(uld.get("is_manual"))
    # Show the countdown only when there's a real expiry. Manual ULDs added
    # without an arrival time carry no deadline, so no clock is shown.
    show_countdown = bool(expiry)

    row_classes = f"uld-row uld-status-{status}"
    if expired:
        # Past the 48h deadline but still on the active board until the 60h cutoff —
        # mark it clearly as overdue.
        row_classes += " uld-row-expired"
    row_style = None
    if is_near_match:
        row_classes += " uld-row-near-match"
    if current_stack:
        row_classes += " uld-row-in-stack"
        if gha_color:
            row_style = {"--stack-accent": gha_color, "--stack-accent-bg": f"{gha_color}14"}
    if prepared:
        row_classes += " uld-row-prepared"

    szerkesztve_at = str(uld.get("szerkesztve_at") or "").strip()
    szerkesztve_by = str(uld.get("szerkesztve_by") or "").strip()
    change_summary = str(uld.get("change_summary") or "").strip()
    meta_children = [html.Span(awbs_str, className="uld-meta-awb")]
    if _is_tk_uld(uld_num):
        meta_children.append(html.Span(
            "TK",
            className="uld-meta-tk",
            title="TK ULD - csak TK stackbe tehető",
        ))
    if uld.get("is_szerkesztve"):
        audit_title = _uld_edit_audit_text(szerkesztve_by, szerkesztve_at, change_summary)
        meta_children.append(html.Span("szerkesztve", className="uld-szerkesztve-tag", title=audit_title))
    if is_manual:
        # UI-only marker. Másolás/print render from stackJson and never see this.
        meta_children.append(html.Span(
            "kézi",
            className="uld-manual-tag",
            title="Kézzel hozzáadott ULD",
        ))
    if is_near_match:
        meta_children.append(html.Span(
            f"Közeli találat - keresve: {search_requested}",
            className="uld-near-match-badge",
            title=f"Nem pontos találat. Keresett ULD: {search_requested}",
        ))
    if current_stack:
        stack_name = (stack_name_map or {}).get(current_stack.get("id")) or current_stack.get("name") or "Stack"
        badge_style = {
            "--stack-gha-color":  gha_color,
            "--stack-gha-bg":     f"{gha_color}22",
            "--stack-gha-border": f"{gha_color}55",
        } if gha_color else None
        meta_children.append(html.Span(
            stack_name,
            className="uld-in-stack-badge",
            style=badge_style,
            title="Stackben van - a stack panelen távolítható el",
        ))
    else:
        gha_class = "uld-meta-gha"
        if not gha_raw:
            gha_class += " uld-meta-gha-issue"
        gha_title = (
            "Ellentmondó GHA adatok: " + ", ".join(gha_candidates)
            if gha_conflict and gha_candidates else
            ("Az E_COMM ULD-során és az azonos AWB más sorain sincs egyértelmű GHA." if not gha_raw else "")
        )
        meta_children.append(html.Span(
            gha_display,
            className=gha_class,
            style={"--gha-color": gha_color} if gha_color else None,
            title=gha_title,
        ))

    return html.Div(
        key=f"uld-row-{uld_num}",
        className=row_classes,
        style=row_style,
        **{
            "data-uld": uld_num,
            "data-prefix": uld_type[:3].upper(),
            "data-prefix-color": pcolor,
            # Never serialize the visual placeholder as a real GHA.  The old "—"
            # value passed the browser's truthy GHA gate, then the backend rejected
            # the drop, which looked like the ULD simply failed to stick.
            "data-gha": gha_raw,
            "data-gha-conflict": "1" if gha_conflict else "0",
            "data-awbs": ", ".join(uld["awbs"]) if uld["awbs"] else "",
            "data-status": status,
            "data-prepared": "1" if prepared else "0",
            "data-stack-id": (current_stack.get("id") if current_stack else ""),
            "data-szerkesztve": "1" if uld.get("is_szerkesztve") else "0",
            "data-szerkesztve-at": szerkesztve_at,
            "data-szerkesztve-by": szerkesztve_by,
            "data-change-summary": change_summary,
            "data-search-requested": search_requested if is_near_match else "",
            "data-search-match-kind": "near" if is_near_match else "",
            "data-expiry": _iso_or_str(uld.get("expiry_time")),
            "data-am": _iso_or_str(uld.get("am_time")),
            "draggable": "true",
        },
        children=[
            html.Div(className="uld-type-badge", style=badge_style, children=uld_type[:3]),
            html.Div(className="uld-row-body", children=[
                html.Div(className="uld-row-num", children=uld_num),
                html.Div(className="uld-row-meta", children=meta_children),
            ]),
            html.Button("✎", className="uld-row-edit", title="ULD adatok szerkesztése",
                        **{"aria-label": f"{uld_num} adatainak szerkesztése"}),
            html.Div(
                className=f"uld-countdown uld-cd-{status}",
                **{"data-expiry": expiry},
                children=[
                    html.Div(rem_text, className="uld-cd-time"),
                    html.Div(cd_label, className="uld-cd-label"),
                ],
            ) if show_countdown else html.Div("kézi", className="uld-row-noexpiry", title="Nincs lejárati idő - kézzel hozzáadva átadási idő nélkül"),
        ],
    )


def _uld_search_summary(all_uld: list[dict], search_text: str,
                        stacks: list[dict] | None = None,
                        uld_times_full: dict | None = None,
                        returned: set | None = None,
                        archive: list[dict] | None = None):
    """Aktív ULD keresés összegzője.

    Bulk (több soros) keresésnél pontos / hasonló / hiányzó bontást ad. Emellett
    minden keresésnél (egy ULD-re is) megnézi, hogy a keresett ULD valójában egy
    KIKÜLDÖTT stack-ben van-e — ilyenkor egy külön, kattintható figyelmeztető
    blokk jelzi (és a kiküldött nézetre ugrik), hogy a tétel nem hiányzik, csak
    már ki lett küldve.
    """
    raw = (search_text or "").strip()
    if not raw:
        return None

    # Kiadott keresztellenőrzés first, so bulk "missing" can exclude dispatched ULDs.
    dispatched_hits = _dispatched_search_hits(stacks or [], raw, uld_times_full or {}, all_uld, returned)
    dispatched_codes = {
        _normalize_uld_code(h.get("uld_number")) for h in dispatched_hits if not h.get("near")
    }

    children: list = []

    def _archive_callout(hits):
        return html.Div(className="uld-search-summary-archive", children=[
            html.Span("Archív (nem aktív):", className="uld-ss-archive-label"),
            html.Div(className="uld-ss-archive-list", children=[
                html.Span([
                    html.Span(term, className="uld-ss-near-query"),
                    html.Span(" → ", className="uld-ss-near-arrow"),
                    html.Span(hit.get("uld_number", ""), className="uld-ss-archive-hit"),
                    html.Span(f" · {int((hit.get('elapsed_hours') or 0) // 24)} napja",
                              className="uld-ss-archive-age"),
                ], className="uld-ss-archive-chip",
                   title="AWB: " + ", ".join(hit.get("awbs") or [])
                         + (" · GHA: " + hit.get("gha", "") if hit.get("gha") else ""))
                for term, hit in hits
            ]),
        ])

    def _awb_digits(value):
        return "".join(c for c in str(value or "") if c.isdigit())

    def _awb_matches(rec, digits):
        if len(digits) < 6:
            return False
        for a in (rec.get("awbs") or []):
            ad = _awb_digits(a)
            if ad and (ad == digits or ad.startswith(digits) or digits.startswith(ad)):
                return True
        return False

    def _archive_lookup(term):
        if not archive:
            return None
        key = _normalize_uld_search_token(term)
        hit = next((u for u in archive if _normalize_uld_search_token(u.get("uld_number")) == key), None)
        if not hit:
            digits = _awb_digits(term)   # the term may be an AWB of an archived ULD
            hit = next((u for u in archive if _awb_matches(u, digits)), None)
        if not hit:
            na = _near_uld_matches(term, archive, limit=2, max_results=1)
            hit = na[0] if na else None
        return hit

    terms = _bulk_uld_search_terms(raw)
    if terms:
        exact, near, missing = _bulk_uld_resolution(all_uld, raw)
        # A kiküldött stack-ben megtalált tétel nem "hiányzik" — kivesszük a missing
        # listából, hogy a számláló őszinte maradjon (a dispatched blokk mutatja meg).
        missing = [m for m in missing if _normalize_uld_code(m) not in dispatched_codes]
        # Archív (nem aktív, >60h) ULD-k: a hiányzó keresésekre megnézzük az archív
        # listát is (memóriából, extra Excel-olvasás nélkül), így megtalálható a régóta
        # nem aktív ULD is — "nem aktív" jelzéssel, a hiányzók közül kivéve.
        archive_hits: list = []
        if missing and archive:
            still_missing = []
            for m in missing:
                hit = _archive_lookup(m)
                if hit:
                    archive_hits.append((m, hit))
                else:
                    still_missing.append(m)
            missing = still_missing
        found = [_normalize_uld_search_token(u.get("uld_number")) for u in exact]
        near_count = sum(len(items) for items in near.values())
        children.append(html.Div(className="uld-search-summary-counts", children=[
            html.Span(f"Keresett: {len(terms)}", className="uld-ss-total"),
            html.Span(f"Pontos találat: {len(found)}", className="uld-ss-found"),
            html.Span(f"Közeli találat: {near_count}", className="uld-ss-near"),
            html.Span(f"Kiadva: {len(dispatched_codes)}", className="uld-ss-dispatched-count")
            if dispatched_codes else None,
            html.Span(f"Archív: {len(archive_hits)}", className="uld-ss-archive-count")
            if archive_hits else None,
            html.Span(
                f"Hiányzó: {len(missing)}",
                className="uld-ss-missing uld-ss-ok" if not missing else "uld-ss-missing",
            ),
        ]))
        if near:
            children.append(html.Div(className="uld-search-summary-near", children=[
                html.Span("Közeli találat:", className="uld-ss-near-label"),
                html.Div(className="uld-ss-near-list", children=[
                    html.Span([
                        html.Span(term, className="uld-ss-near-query"),
                        html.Span(" -> ", className="uld-ss-near-arrow"),
                        html.Span(", ".join(u.get("uld_number", "") for u in items), className="uld-ss-near-hit"),
                    ], className="uld-ss-near-chip")
                    for term, items in near.items()
                ]),
            ]))
        if archive_hits:
            children.append(_archive_callout(archive_hits))
        if missing:
            children.append(html.Div(className="uld-search-summary-missing", children=[
                html.Span("Nincs találat:", className="uld-ss-missing-label"),
                html.Div(className="uld-ss-missing-list", children=[
                    html.Span(m, className="uld-ss-missing-chip") for m in missing
                ]),
            ]))
    elif raw and archive:
        # Egy soros keresés: ha nincs aktív pontos VAGY közeli találat, az archív
        # listából keresünk — így a >60h-s ULD is előjön "nem aktív" jelzéssel.
        raw_key = _normalize_uld_search_token(raw)
        active_keys = {_normalize_uld_search_token(u.get("uld_number")) for u in all_uld}
        raw_digits = _awb_digits(raw)
        active_awb = any(_awb_matches(u, raw_digits) for u in all_uld)
        if (raw_key not in active_keys and not active_awb
                and not _near_uld_matches(raw, all_uld, limit=2, max_results=1)):
            hit = _archive_lookup(raw)
            if hit:
                children.append(_archive_callout([(raw, hit)]))

    if dispatched_hits:
        children.append(_dispatched_hits_callout(dispatched_hits))

    if not children:
        return None
    return html.Div(className="uld-search-summary", children=children)


def _dispatched_search_hits(stacks: list[dict], search_text: str,
                            uld_times_full: dict | None = None,
                            all_uld: list[dict] | None = None,
                            returned: set | None = None) -> list[dict]:
    """A keresésre illeszkedő ULD-k, amelyek egy KIKÜLDÖTT stack-ben vannak.

    Így az aktív listán kereső operátor látja, hogy egy ULD nem "hiányzik", hanem
    már ki lett küldve (és melyik stack-ben), üres/„nincs találat” helyett. A
    visszaadott (Vissza-dátumos) ULD-ket kiszűri, ugyanúgy mint a kiküldött nézet,
    hogy a blokk pontosan azt mutassa, ami a kiküldött nézetben is látszik.
    """
    raw = (search_text or "").strip()
    if not raw:
        return []
    times = uld_times_full or {}
    dispatched = _strip_returned_ulds(_dispatched_stacks(stacks), returned or set(), times)
    if not dispatched:
        return []
    records = _dispatched_uld_records(dispatched, times)
    if not records:
        return []
    # Stack display names — mirror the dispatched section's lookup so the callout
    # shows the same human name (GHA + number) the operator sees there.
    display_lookup = {u.get("uld_number"): u for u in (all_uld or []) if u.get("uld_number")}
    for uld_num, info in times.items():
        if uld_num not in display_lookup and isinstance(info, dict):
            display_lookup[uld_num] = {"uld_number": uld_num, "gha": info.get("gha") or ""}
    display_names = _stack_display_name_map(dispatched, display_lookup)
    stack_by_id = {str(s.get("id") or ""): s for s in dispatched}

    hits: list[dict] = []
    seen: set[tuple] = set()

    def _add(rec: dict, near: bool) -> None:
        sid = str(rec.get("_stack_id") or "")
        uld_num = rec.get("uld_number") or ""
        key = (_normalize_uld_code(uld_num), sid)
        if key in seen:
            return
        seen.add(key)
        stack = stack_by_id.get(sid, {})
        hits.append({
            "uld_number": uld_num,
            "stack_id": sid,
            "stack_name": display_names.get(stack.get("id")) or stack.get("name") or "Stack",
            "dispatch_plate": str(stack.get("dispatch_plate") or "").strip(),
            "dispatched_by": str(stack.get("dispatched_by") or stack.get("updated_by") or "").strip(),
            "dispatched_at": stack.get("dispatched_at") or stack.get("updated_at") or "",
            "near": bool(near),
        })

    # Dispatched stacks already hold the finalised, correct ULDs, so this callout
    # must surface ONLY an exact full match — never a near/substring guess. A fuzzy
    # hit here used to win over (and mask) the real exact match in the active list,
    # e.g. searching PAJ101708E surfaced PAJ101618E from a dispatched stack. Exact
    # only: the active list shows its own matches alongside.
    if _bulk_uld_search_terms(raw):
        exact, _near, _missing = _bulk_uld_resolution(records, raw)
        for rec in exact:
            _add(rec, False)
    else:
        q_fold = _uld_search_fold(raw)
        q_clean = raw.strip().upper()
        for rec in records:
            uld_num = rec.get("uld_number") or ""
            awbs = rec.get("awbs") if isinstance(rec.get("awbs"), list) else []
            # Full-code equality only (glyph-fold counts O/0 lookalikes as the same
            # physical ULD); plus an exact AWB match. No substring, no edit-distance.
            if q_fold and _uld_search_fold(uld_num) == q_fold:
                _add(rec, False)
            elif q_clean and any(q_clean == str(a).strip().upper() for a in awbs):
                _add(rec, False)
    return hits


def _dispatched_hits_callout(hits: list[dict]):
    """Kattintható figyelmeztető blokk: a keresett ULD-k kiküldött stack-ekben."""
    chips = []
    for h in hits[:12]:
        meta_bits = []
        if h.get("dispatch_plate"):
            meta_bits.append(h["dispatch_plate"])
        elif h.get("dispatched_by"):
            meta_bits.append(h["dispatched_by"])
        when = _fmt_iso_dt(h["dispatched_at"]) if h.get("dispatched_at") else ""
        if when:
            meta_bits.append(when)
        chips.append(html.Span(
            className="uld-ss-disp-chip" + (" is-near" if h.get("near") else ""),
            children=[
                html.Span("≈", className="uld-ss-disp-near") if h.get("near") else None,
                html.Span(h["uld_number"], className="uld-ss-disp-uld"),
                html.Span("→", className="uld-ss-disp-arrow"),
                html.Span(h["stack_name"], className="uld-ss-disp-stack"),
                html.Span(" · ".join(meta_bits), className="uld-ss-disp-meta") if meta_bits else None,
            ],
        ))
    extra = len(hits) - len(chips)
    if extra > 0:
        chips.append(html.Span(f"+{extra}", className="uld-ss-disp-chip uld-ss-disp-more"))
    n = len(hits)
    return html.Div(
        className="uld-search-dispatched-callout uld-ss-dispatched-jump",
        role="button",
        tabIndex=0,
        title="Kiküldött stackek megnyitása - a keresés megmarad és a találat kiemelve látszik",
        children=[
            html.Div(className="uld-ss-disp-head", children=[
                html.Span("⇥", className="uld-ss-disp-ico"),
                html.Span(
                    f"{n} keresett ULD kiküldött stackben van"
                    if n != 1 else "A keresett ULD kiküldött stackben van",
                    className="uld-ss-disp-title",
                ),
                html.Span("Megnyitás", className="uld-ss-disp-cta"),
            ]),
            html.Div(className="uld-ss-disp-chips", children=chips),
        ],
    )


def _dispatched_uld_records(dispatched_stacks: list[dict], uld_times_full: dict | None = None) -> list[dict]:
    """ULD-like records for searching only inside dispatched stacks."""
    records: list[dict] = []
    times = uld_times_full or {}
    for stack in dispatched_stacks or []:
        stack_id = str(stack.get("id") or "")
        stack_gha = str(stack.get("stack_gha") or "").strip()
        for uld_num in stack.get("ulds", []) or []:
            if not uld_num:
                continue
            info = times.get(uld_num, {}) or {}
            awbs = info.get("awbs") if isinstance(info.get("awbs"), list) else []
            records.append({
                "uld_number": uld_num,
                "uld_type": str(uld_num)[:3].upper(),
                "gha": info.get("gha") or stack_gha,
                "awbs": awbs,
                "_stack_id": stack_id,
            })
    return records


def _dispatched_uld_search_summary(stacks: list[dict], search_text: str,
                                   uld_times_full: dict | None = None,
                                   returned: set | None = None):
    """Bulk-search summary scoped to dispatched stacks only."""
    terms = _bulk_uld_search_terms(search_text or "")
    if not terms:
        return None
    visible = _strip_returned_ulds(
        _dispatched_stacks(stacks), returned or set(), uld_times_full or {}
    )
    records = _dispatched_uld_records(visible, uld_times_full or {})
    exact, near, missing = _bulk_uld_resolution(records, search_text or "")
    # Dispatched view = exact matches only; a near/edit-distance hit here would point
    # at a different (lookalike) ULD than the one searched. Fold near terms into the
    # "not dispatched" list so the count stays honest.
    missing = list(missing) + list(near.keys())
    near = {}
    children = [html.Div(className="uld-search-summary-counts", children=[
        html.Span(f"Keresett: {len(terms)}", className="uld-ss-total"),
        html.Span(f"Kiadva: {len(exact)}", className="uld-ss-found"),
        html.Span(
            f"Hiányzó: {len(missing)}",
            className="uld-ss-missing uld-ss-ok" if not missing else "uld-ss-missing",
        ),
    ])]
    if near:
        children.append(html.Div(className="uld-search-summary-near", children=[
            html.Span("Közeli kiküldött találat:", className="uld-ss-near-label"),
            html.Div(className="uld-ss-near-list", children=[
                html.Span([
                    html.Span(term, className="uld-ss-near-query"),
                    html.Span(" -> ", className="uld-ss-near-arrow"),
                    html.Span(", ".join(u.get("uld_number", "") for u in items), className="uld-ss-near-hit"),
                ], className="uld-ss-near-chip")
                for term, items in near.items()
            ]),
        ]))
    if missing:
        children.append(html.Div(className="uld-search-summary-missing", children=[
            html.Span("Nem kiküldött:", className="uld-ss-missing-label"),
            html.Div(className="uld-ss-missing-list", children=[
                html.Span(m, className="uld-ss-missing-chip") for m in missing
            ]),
        ]))
    return html.Div(className="uld-search-summary uld-search-summary-dispatched", children=children)

def _make_uld_list_section(uld_data: list[dict], stacks: list[dict], gha_color_map: dict | None = None, all_uld: list[dict] | None = None, preserve_order: bool = False, search_text: str = "") -> list:
    if not uld_data:
        term = _normalize_uld_search_token(search_text)
        if term:
            # If the searched ULD physically sits in a stack (even if it's no longer
            # an active list item — already returned, or in a prepared stack), point
            # the user to it: the stack IS shown and the ULD highlighted on the left.
            host_stack = next(
                (s for s in (stacks or [])
                 if any(_uld_matches_query(u, {}, search_text) for u in (s.get("ulds", []) or []))),
                None,
            )
            if host_stack is not None:
                lookup = {u["uld_number"]: u for u in (all_uld or [])}
                stack_name = (_stack_display_name_map(stacks, lookup).get(host_stack.get("id"))
                              or host_stack.get("name") or "stack")
                return [html.Div(className="uld-list-empty uld-list-empty-instack", key="uld-list-empty", children=[
                    html.Div(f"A keresett ULD ebben a stackben van: {stack_name}", className="uld-list-empty-title"),
                    html.Div("A stack panelen kiemelve. Az aktív lista csak a még kint lévő ULD-ket mutatja.",
                             className="uld-list-empty-sub"),
                ])]
            # A specific search with no hit: tell the user it isn't an *active* ULD,
            # rather than silently showing a different (near-match) ULD. Note that
            # the active board only lists ULDs still out (Vissza field empty);
            # an already-returned ULD exists in E_COMM but is intentionally not here.
            return [html.Div(className="uld-list-empty", key="uld-list-empty", children=[
                html.Div(f"Nincs aktív ULD erre a keresésre: {term}", className="uld-list-empty-title"),
                html.Div("Lehet, hogy már visszaérkezett (Vissza mező kitöltve), vagy nem aktív tétel. "
                         "Az aktív lista csak a még kint lévő ULD-ket mutatja.",
                         className="uld-list-empty-sub"),
            ])]
        return [html.Div("Nincs találat - minden ULD visszaérkezett, vagy túl szigorú a szűrés.", className="uld-list-empty", key="uld-list-empty")]
    uld_lookup = {u["uld_number"]: u for u in (all_uld or uld_data)}
    stack_name_map = _stack_display_name_map(stacks, uld_lookup)
    prepared_ulds = {u for s in stacks if s.get("prepared") for u in s.get("ulds", [])}
    # Stable sort: ULDs in prepared stacks go to the end, original order preserved otherwise.
    # Bulk searches keep the pasted line order, because each line is an explicit requested hit.
    ordered = uld_data if preserve_order else sorted(uld_data, key=lambda u: 1 if u.get("uld_number") in prepared_ulds else 0)
    return [_make_uld_row(u, stacks, gha_color_map, stack_name_map) for u in ordered]


_ULD_PAGE_SIZE = 25


def _make_pagination(total: int, page: int) -> html.Div | None:
    if total <= _ULD_PAGE_SIZE:
        return None
    total_pages = (total + _ULD_PAGE_SIZE - 1) // _ULD_PAGE_SIZE
    page = max(1, min(page, total_pages))
    start_item = (page - 1) * _ULD_PAGE_SIZE + 1
    end_item = min(page * _ULD_PAGE_SIZE, total)

    def _pgbtn(label: str, target: int, extra: str = "", disabled: bool = False, title: str = "") -> html.Button:
        cls = f"uld-pg-btn {extra}{' uld-pg-disabled' if disabled else ''}".strip()
        return html.Button(label, className=cls, title=title, disabled=disabled,
                           **{"data-page": str(target), "data-action": "go"})

    buttons: list = []
    buttons.append(_pgbtn("«", 1,            "uld-pg-first", disabled=(page == 1),            title="Első oldal"))
    buttons.append(_pgbtn("‹", page - 1,     "uld-pg-prev",  disabled=(page == 1),            title="Előző oldal"))

    lo = max(1, page - 2)
    hi = min(total_pages, page + 2)
    if lo > 1:
        buttons.append(html.Span("…", className="uld-pg-ellipsis"))
    for p in range(lo, hi + 1):
        cls = "uld-pg-num" + (" uld-pg-current" if p == page else "")
        buttons.append(html.Button(str(p), className=f"uld-pg-btn {cls}", disabled=(p == page),
                                   **{"data-page": str(p), "data-action": "go"}))
    if hi < total_pages:
        buttons.append(html.Span("…", className="uld-pg-ellipsis"))

    buttons.append(_pgbtn("›", page + 1,     "uld-pg-next",  disabled=(page == total_pages),  title="Következő oldal"))
    buttons.append(_pgbtn("»", total_pages,  "uld-pg-last",  disabled=(page == total_pages),  title="Utolsó oldal"))

    return html.Div(className="uld-pg-wrap", children=[
        html.Span(f"{start_item}–{end_item} / {total} ULD", className="uld-pg-info"),
        html.Div(className="uld-pg-controls", children=buttons),
    ])


def _make_uld_loading() -> list:
    skeletons = [
        html.Div(className="uld-row uld-skeleton", key=f"uld-skel-{i}", children=[
            html.Div(className="uld-type-badge uld-skel-box"),
            html.Div(className="uld-row-body", children=[
                html.Div(className="uld-row-num uld-skel-line uld-skel-wide"),
                html.Div(className="uld-row-meta", children=[
                    html.Span(className="uld-skel-line uld-skel-mid"),
                    html.Span(className="uld-skel-line uld-skel-narrow"),
                ]),
            ]),
            html.Div(className="uld-countdown uld-skel-box-sm"),
        ])
        for i in range(7)
    ]
    return [
        html.Div(className="uld-loading-bar-wrap", key="uld-loading-bar", children=[
            html.Div(className="uld-loading-bar"),
        ]),
        html.Div("ULD adatok betöltése folyamatban…", className="uld-loading-hint", key="uld-loading-hint"),
    ] + skeletons


def _make_uld_empty() -> list:
    """Empty state for the active ULD list.

    Shown only after a ULD refresh has completed but there is genuinely nothing
    inside the active window (every ULD is already returned or aged past the
    cutoff). This is distinct from _make_uld_loading(): the loading skeleton must
    never persist once a refresh has finished, otherwise an honest "no active ULD"
    state looks like a stuck spinner.
    """
    return [
        html.Div(className="uld-list-empty", key="uld-list-empty", children=[
            html.Div("Nincs aktív ULD", className="uld-list-empty-title"),
            html.Div(
                "Jelenleg nincs az aktív ablakon belüli, még vissza nem adott ULD. "
                "Amint egy új ULD Áttár ideje megjelenik az E_COMM-ben, automatikusan itt lesz.",
                className="uld-list-empty-sub",
            ),
        ]),
    ]


def _is_default_stack_name(name: str) -> bool:
    value = (name or "").strip()
    if not value:
        return True
    if _canonical_manual_gha(value):
        return True
    for prefix in ("Stack", "GHA", *_MANUAL_GHA_OPTIONS):
        stem = f"{prefix} "
        if value.casefold().startswith(stem.casefold()) and value[len(stem):].strip().isdigit():
            return True
    return False


def _default_stack_number(name: str) -> int | None:
    value = (name or "").strip()
    for prefix in ("Stack", "GHA", *_MANUAL_GHA_OPTIONS):
        stem = f"{prefix} "
        if value.casefold().startswith(stem.casefold()):
            suffix = value[len(stem):].strip()
            if suffix.isdigit():
                return int(suffix)
    return None


def _stack_single_gha(stack: dict, uld_lookup: dict) -> str:
    ghas = sorted({
        (uld_lookup.get(uld_num, {}).get("gha") or "").strip()
        for uld_num in stack.get("ulds", [])
        if (uld_lookup.get(uld_num, {}).get("gha") or "").strip()
    })
    if len(ghas) == 1:
        return ghas[0]
    if not ghas:
        return str(stack.get("stack_gha") or "").strip() or _canonical_manual_gha(stack.get("name"))
    return ""


def _stack_display_name(stack: dict, single_gha: str, _gha_number: int | None = None) -> str:
    stored_name = (stack.get("name") or "").strip()
    if single_gha and _is_default_stack_name(stored_name):
        stored_number = _default_stack_number(stored_name) or _gha_number
        return f"{single_gha} {stored_number}" if stored_number else single_gha
    return stored_name or "Stack"


def _stack_display_name_map(stacks: list[dict], uld_lookup: dict, number_source_stacks: list[dict] | None = None) -> dict:
    # Default-named GHA stacks keep the number stored on the stack itself
    # (e.g. "GHA 5" -> "Menzies 5"). Never derive the number from the current
    # visible order, otherwise deleting or dispatching earlier stacks renumbers
    # the survivors.
    source_stacks = number_source_stacks or stacks
    single_ghas = {stack.get("id"): _stack_single_gha(stack, uld_lookup) for stack in source_stacks}
    used_numbers = {
        number
        for stack in source_stacks
        for number in [_default_stack_number(stack.get("name"))]
        if number is not None
    }
    next_number = (max(used_numbers) + 1) if used_numbers else 1
    fallback_numbers: dict[str, int] = {}
    for stack in source_stacks:
        stack_id = stack.get("id")
        if not stack_id:
            continue
        if not single_ghas.get(stack_id):
            continue
        if not _is_default_stack_name(stack.get("name")) or _default_stack_number(stack.get("name")) is not None:
            continue
        while next_number in used_numbers:
            next_number += 1
        fallback_numbers[stack_id] = next_number
        used_numbers.add(next_number)
        next_number += 1
    return {
        stack.get("id"): _stack_display_name(
            stack,
            single_ghas.get(stack.get("id"), "") or _stack_single_gha(stack, uld_lookup),
            fallback_numbers.get(stack.get("id")),
        )
        for stack in stacks
    }


_ULD_DEFAULT_EXPIRY_H = 48


def _uld_recency_key(uld: dict) -> datetime:
    """Sortable key for 'newest first' ordering by áttár (am) idő.

    Missing/invalid am_time sorts oldest so it lands at the bottom when the
    caller reverses for descending order.
    """
    am_iso = _iso_or_str(uld.get("am_time"))
    if am_iso:
        try:
            dt = datetime.fromisoformat(am_iso.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt
        except Exception:
            pass
    return datetime.min


def _fmt_audit_dt(value) -> str:
    """ISO timestamp/datetime → 'YYYY.MM.DD HH:MM' (drops seconds/microseconds)."""
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y.%m.%d %H:%M")
        except Exception:
            return ""
    s = str(value).strip()
    if not s:
        return ""
    try:
        return datetime.fromisoformat(s).strftime("%Y.%m.%d %H:%M")
    except ValueError:
        return s


def _uld_edit_audit_text(szerkesztve_by: str, szerkesztve_at: str, change_summary: str = "") -> str:
    audit_when = _fmt_audit_dt(szerkesztve_at)
    base = f"Utolsó módosítás: {szerkesztve_by or 'ismeretlen'} - {audit_when or 'ismeretlen idő'}"
    change_summary = str(change_summary or "").strip()
    return f"{base}; {change_summary}" if change_summary else base


def _iso_or_str(value) -> str:
    """Coerce a datetime-or-ISO-string into an ISO string, or return ''."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return ""
    s = str(value).strip()
    return s


def _stack_item_expiry(info: dict) -> str:
    """Return the ULD expiry as ISO string. Falls back to am_time + 48h."""
    raw = info.get("expiry_time")
    out = _iso_or_str(raw)
    if out:
        return out
    am_raw = info.get("am_time")
    am_iso = _iso_or_str(am_raw)
    if not am_iso:
        return ""
    try:
        if hasattr(am_raw, "isoformat"):
            am_dt = am_raw
        else:
            am_dt = datetime.fromisoformat(am_iso)
        if getattr(am_dt, "tzinfo", None) is not None:
            am_dt = am_dt.astimezone().replace(tzinfo=None)
        return (am_dt + timedelta(hours=_ULD_DEFAULT_EXPIRY_H)).isoformat()
    except Exception:
        return ""


def _make_stack_column(stack: dict, uld_lookup: dict, search_text: str = "", gha_numbers: dict | None = None, forced_display_name: str | None = None, gha_color_map: dict | None = None, uld_times_full: dict | None = None) -> html.Div:
    uld_times_full = uld_times_full or {}
    uld_overrides = uld_stack_manager.get_uld_overrides()
    ulds = stack.get("ulds", [])
    top_to_bottom = list(reversed(ulds))
    active_count = sum(1 for uld_num in ulds if uld_num in uld_lookup)
    inactive_count = len(ulds) - active_count
    single_gha = _stack_single_gha(stack, uld_lookup)
    display_name = forced_display_name or _stack_display_name(stack, single_gha, (gha_numbers or {}).get(stack.get("id")))
    created_at = str(stack.get("created_at") or "").strip()
    created_by = str(stack.get("created_by") or "").strip()
    created_text = _uld_edit_audit_text(created_by, created_at).replace("Utolsó módosítás:", "Készítette:")
    gha_c = (gha_color_map or {}).get(single_gha, "#4f46e5")
    is_tk_stack = bool(ulds) and all(_is_tk_uld(uld_num) for uld_num in ulds)
    stack_classes = "uld-stack uld-stack-accordion" + (" uld-stack-tk" if is_tk_stack else "")
    if ulds and active_count == 0:
        stack_classes += " uld-stack-inactive"
    # Accordion: every non-empty stack collapses to just its header so ~4 stacks
    # fit on screen. Empty stacks stay open so their drop hint is always visible.
    # Click the header to expand/collapse; multiple stacks can be open at once.
    # JS (uld_manager.js) measures heights and drives the smooth animation.
    is_collapsible = bool(ulds)
    if is_collapsible:
        stack_classes += " uld-stack-collapsible"
    is_prepared = bool(stack.get("prepared"))
    if is_prepared:
        stack_classes += " uld-stack-prepared"
    has_search_hit = bool(search_text) and any(_uld_matches_query(uld_num, uld_lookup.get(uld_num, {}), search_text) for uld_num in ulds)
    if has_search_hit:
        stack_classes += " uld-stack-search-hit"
    gha_col = (gha_color_map or {}).get(single_gha, "") if single_gha else ""
    stack_style = {
        "--stack-gha-border": f"{gha_col}77",
        "--stack-gha-glow":   f"{gha_col}18",
    } if gha_col else None
    items = []
    for i, uld_num in enumerate(top_to_bottom):
        is_top    = (i == 0)
        is_bottom = (i == len(ulds) - 1)
        info   = _stack_uld_info(uld_num, uld_lookup, uld_times_full, uld_overrides)
        is_active = uld_num in uld_lookup
        status = info.get("status", "inactive")
        pcolor = _uld_prefix_color(uld_num[:3])
        pos    = "teteje" if is_top else ("alja" if is_bottom and len(ulds) > 1 else "")
        idx_classes = "uld-si-index"
        if is_top:
            idx_classes += " uld-si-index-top"
        elif is_bottom and len(ulds) > 1:
            idx_classes += " uld-si-index-bottom"
        pos_title = (f"{i + 1}. - stack teteje" if is_top
                     else (f"{i + 1}. - stack alja" if is_bottom and len(ulds) > 1
                           else f"{i + 1}. felülről"))
        is_search_match = bool(search_text) and _uld_matches_query(uld_num, info, search_text)
        item_classes = f"uld-si uld-si-{status}" + ("" if is_active else " uld-si-inactive") \
            + (" uld-si-search-match" if is_search_match else "")
        # If the ULD aged out of the active list (>60h), fall back to the full
        # lookup so the "Lejárati idő" stays populated in the copy.
        info_for_expiry = info if info else uld_times_full.get(uld_num, {})
        item_expiry = _stack_item_expiry(info_for_expiry)
        item_am = _iso_or_str(info_for_expiry.get("am_time"))
        szerkesztve_at = str(info.get("szerkesztve_at") or "").strip()
        szerkesztve_by = str(info.get("szerkesztve_by") or "").strip()
        change_summary = str(info.get("change_summary") or "").strip()
        is_szerkesztve = bool(info.get("is_szerkesztve"))
        edit_badge = html.Span(
            "szerkesztve",
            className="uld-si-szerkesztve-tag",
            title=_uld_edit_audit_text(szerkesztve_by, szerkesztve_at, change_summary),
        ) if is_szerkesztve else None
        items.append(html.Div(
            key=f"uld-si-{uld_num}",
            className=item_classes,
            **{
                "data-uld": uld_num,
                "data-prefix": uld_num[:3].upper(),
                "data-prefix-color": pcolor,
                "data-gha": info.get("gha", ""),
                "data-awbs": ", ".join(info.get("awbs", [])) if info.get("awbs") else "",
                "data-status": status,
                "data-expiry": item_expiry,
                "data-am": item_am,
                "data-match": "1" if is_search_match else "0",
                "data-szerkesztve": "1" if is_szerkesztve else "0",
                "data-szerkesztve-at": szerkesztve_at,
                "data-szerkesztve-by": szerkesztve_by,
                "data-change-summary": change_summary,
                "data-pos": str(i + 1),
                "draggable": "true",
            },
            children=[
                # Explicit 1-based stacking order (stack teteje = 1). This is the
                # single, clear order cue — tinted by the ULD prefix colour so the
                # 3-letter type box is no longer needed (the number already starts
                # with the prefix), keeping each row uncluttered even in columns.
                html.Span(str(i + 1), className=idx_classes, title=pos_title,
                          style={
                              "--uld-index-text": _contrast_text_for_hex(pcolor),
                              "--uld-index-ring": _hex_alpha(pcolor, "d9"),
                              "--uld-index-bg": _hex_alpha(pcolor, "e8"),
                              "--uld-index-bg-soft": _hex_alpha(pcolor, "8a"),
                          }),
                html.Div(className="uld-si-top", children=[
                    html.Div(className="uld-si-title-line", children=[
                        html.Span(uld_num, className="uld-si-num",
                                  title="Dupla kattintás az átnevezéshez"),
                        html.Span("inaktív", className="uld-si-inactive-badge") if not is_active else None,
                        html.Span("kézi", className="uld-si-kézi-tag",
                                  title="Kézzel hozzáadott ULD") if status == "kézi" else None,
                        edit_badge,
                    ]),
                    html.Span(pos, className="uld-si-pos") if pos else None,
                ]),
                html.Button(
                    "×",
                    className="uld-si-remove",
                    title="Eltávolítás stackből",
                    **{"data-uld": uld_num},
                ),
            ],
        ))

    if not items:
        items = [html.Div("Üres - húzz ide ULD-t", className="uld-stack-empty-hint", key=f"uld-stack-empty-{stack['id']}")]

    def _info_or_full(uld_num: str) -> dict:
        return _stack_uld_info(uld_num, uld_lookup, uld_times_full, uld_overrides)

    stack_json = json.dumps([
        {
            "pos": i + 1,
            "uld": uld_num,
            "gha":  _info_or_full(uld_num).get("gha", ""),
            "awbs": _info_or_full(uld_num).get("awbs", []),
            "active": uld_num in uld_lookup,
            "am":     _iso_or_str(_info_or_full(uld_num).get("am_time")),
            "expiry": _stack_item_expiry(_info_or_full(uld_num)),
            "is_szerkesztve": bool(_info_or_full(uld_num).get("is_szerkesztve")),
            "szerkesztve_at": _info_or_full(uld_num).get("szerkesztve_at", ""),
            "szerkesztve_by": _info_or_full(uld_num).get("szerkesztve_by", ""),
            "change_summary": _info_or_full(uld_num).get("change_summary", ""),
        }
        for i, uld_num in enumerate(top_to_bottom)
    ], ensure_ascii=False)

    return html.Div(
        key=f"uld-stack-{stack['id']}",
        className=stack_classes,
        style=stack_style,
        **{
            "data-stack-id": stack["id"],
            "data-stack-name": display_name,
            "data-raw-stack-name": stack.get("name", "") or "Stack",
            "data-stack-json": stack_json,
            "data-stack-revision": stack.get("revision", 0),
            "data-created-at": created_at,
            "data-created-by": created_by,
            "data-gha": single_gha,
            "data-tk": "1" if is_tk_stack else "0",
            "data-prepared": "1" if is_prepared else "0",
        },
        children=[
            html.Div(className="uld-stack-hdr", children=[
                html.Div(className="uld-stack-head-main", children=[
                    # Accordion caret — CSS hides it for empty/non-collapsible stacks
                    # (those stay open with their drop hint visible).
                    html.Span("▸", className="uld-stack-caret", **{"aria-hidden": "true"}),
                    # GHA badge is now built INTO the stack name: the name pill carries
                    # the per-GHA colour (same scheme as the old separate GHA badge).
                    html.Span(
                        display_name,
                        className="uld-stack-name" + (" uld-stack-name-gha" if single_gha else ""),
                        title="Dupla kattintás az átnevezéshez",
                        style=({
                            "--gha-color": gha_c,
                            "--gha-bg": f"{gha_c}22",
                            "--gha-border": f"{gha_c}59",
                        } if single_gha else {}),
                    ),
                    # TK stacks are physically handled apart — mark them so the
                    # operator can tell a TK stack from a normal GHA stack at a glance.
                    html.Span("TK", className="uld-stack-tk-badge",
                              title="TK készlet - külön kezelendő; ide csak TK ULD kerülhet")
                    if is_tk_stack else None,
                    # Always-visible count so a collapsed header still tells the story.
                    # Warn tone when the stack holds inaktív ULD.
                    html.Span(
                        (f"{active_count}/{len(ulds)}" if inactive_count else f"{len(ulds)} db"),
                        className="uld-stack-cnt" + (" uld-stack-cnt-warn" if inactive_count else ""),
                        title=(f"{inactive_count} inaktív ULD ebben a stackben" if inactive_count
                               else f"{len(ulds)} ULD ebben a stackben"),
                    ) if ulds else None,
                ]),
                html.Div(className="uld-stack-actions", children=[
                    html.Button("Kijelölés", className="uld-stack-act uld-stack-select", title="Stack kijelölése"),
                    html.Button(
                        ("Kész" if is_prepared else "Készre jelöl"),
                        className=(
                            "uld-stack-act uld-stack-prep-btn uld-stack-prep-active" if is_prepared
                            else ("uld-stack-act uld-stack-prep-btn uld-stack-prep-empty"
                                  + (" uld-stack-prep-disabled" if not ulds else ""))
                        ),
                        title=(
                            "Kész jelölés törlése" if is_prepared
                            else ("Üres stack - előbb adj hozzá ULD-t" if not ulds
                                  else "Stack készre jelölése")
                        ),
                        disabled=(not ulds and not is_prepared),
                        **{"data-stack-id": stack["id"]},
                    ),
                    html.Button(
                        "Kiadás",
                        className="uld-stack-act uld-stack-dispatch-btn" + (" uld-stack-dispatch-disabled" if not ulds else ""),
                        title=("Üres stack - előbb adj hozzá ULD-t" if not ulds else "Stack kiadása autóra"),
                        disabled=(not ulds),
                        **{"data-stack-id": stack["id"]},
                    ),
                    html.Button(className="uld-stack-act uld-stack-ico uld-stack-print",
                                title="Nyomtatás", **{"aria-label": "Nyomtatás"}),
                    html.Button(className="uld-stack-act uld-stack-ico uld-stack-copy",
                                title="Másolás vágólapra", **{"aria-label": "Másolás"}),
                    html.Button(className="uld-stack-act uld-stack-ico uld-stack-del",
                                title="Stack törlése", **{"data-stack-id": stack["id"], "aria-label": "Stack törlése"}),
                ]),
            ]),
            html.Div(
                className="uld-stack-body",
                children=[
                    html.Div(created_text, className="uld-stack-created-meta"),
                    html.Div(
                        className="uld-stack-col",
                        **{"data-stack-id": stack["id"]},
                        children=items,
                    ),
                ],
            ),
        ]
    )


def _make_stacks_section(stacks: list[dict], all_uld: list[dict], search_text: str = "") -> list:
    visible_stacks = _visible_stacks_for_uld_view(_active_stacks(stacks), all_uld, search_text)
    uld_lookup = {u["uld_number"]: u for u in all_uld}
    # Full am/expiry lookup (includes aged-out ULDs) — used by stack items so the
    # "Lejárati idő" stays correct even after a ULD passes the 60h active cutoff.
    try:
        uld_times_full = data_cache.get_state().get("uld_times_full") or {}
    except Exception:
        uld_times_full = {}
    gha_color_map = _compute_gha_color_map(all_uld)
    if not visible_stacks:
        return [html.Div("Hozz létre stacket a gombbal.", className="uld-stacks-empty", key="uld-stacks-empty")]
    display_names = _stack_display_name_map(visible_stacks, uld_lookup, stacks)
    # Prepared stacks sort to the END so active ones stay visible at the top.
    ordered = sorted(visible_stacks, key=lambda s: (1 if s.get("prepared") else 0,))
    return [
        _make_stack_column(s, uld_lookup, search_text, None,
                            display_names.get(s.get("id")), gha_color_map,
                            uld_times_full=uld_times_full)
        for s in ordered
    ]


def _make_dispatched_stack_card(stack: dict, uld_times_full: dict, display_name: str, gha_color_map: dict | None = None, matched_ulds: set | None = None) -> html.Div:
    """One minimal list row for a dispatched stack (no heavy glass/animation cost).

    matched_ulds: normalized ULD codes that matched the current search — their
    chips are highlighted and the row gets a "N találat" badge so the operator
    sees exactly which stack holds the searched ULD(s).
    """
    matched_ulds = matched_ulds or set()
    ulds = list(stack.get("ulds", []) or [])
    top_to_bottom = list(reversed(ulds))
    single_gha = str(stack.get("stack_gha") or "").strip()
    if not single_gha:
        for uld_num in reversed(ulds):
            info = uld_times_full.get(uld_num, {}) or {}
            if info.get("gha"):
                single_gha = str(info.get("gha") or "").strip()
                break
    gha_col = (gha_color_map or {}).get(single_gha, "#64748b") if single_gha else "#64748b"
    dispatched_at = stack.get("dispatched_at") or stack.get("updated_at") or ""
    dispatched_by = stack.get("dispatched_by") or stack.get("updated_by") or ""
    prepared_at = stack.get("prepared_at") or ""
    prepared_by = stack.get("prepared_by") or ""
    dispatch_plate = str(stack.get("dispatch_plate") or "").strip()
    touchbar_items = []
    match_count = 0
    for idx, uld_num in enumerate(top_to_bottom):
        info = uld_times_full.get(uld_num, {}) or {}
        awb_text = ", ".join(info.get("awbs") or []) if isinstance(info.get("awbs"), list) else ""
        is_match = _normalize_uld_code(uld_num) in matched_ulds
        if is_match:
            match_count += 1
        touchbar_items.append(html.Span(
            className="uld-dlist-touch-item" + (" uld-dlist-touch-item-match" if is_match else ""),
            style={
                "--touch-color": gha_col,
                "--touch-bg": f"{gha_col}1f",
                "--touch-border": f"{gha_col}66",
            },
            title=awb_text or uld_num,
            **{
                "data-uld": uld_num,
                "data-awbs": awb_text,
                "data-am": _iso_or_str(info.get("am_time")),
                "data-prepared-at": _iso_or_str(prepared_at),
                "data-prepared-by": prepared_by,
                "data-stack-name": display_name,
                "data-stack-gha": single_gha,
                "data-dispatched-at": _iso_or_str(dispatched_at),
                "data-dispatch-plate": dispatch_plate,
                "data-match": "1" if is_match else "0",
            },
            children=[
                html.Span(str(idx + 1), className="uld-dlist-touch-pos"),
                html.Span(uld_num, className="uld-dlist-touch-uld"),
            ],
        ))
    return html.Div(
        key=f"uld-dlist-{stack.get('id')}",
        className="uld-dlist-row" + (" uld-dlist-row-match" if match_count else ""),
        style={"--dc": gha_col, "--gha-color": gha_col, "--gha-bg": f"{gha_col}22", "--gha-border": f"{gha_col}59"},
        **{"data-stack-id": stack.get("id", ""), "data-match-count": str(match_count)},
        children=[
            html.Div(className="uld-dlist-name-wrap", children=[
                html.Span(
                    display_name,
                    className="uld-dlist-name" + (" uld-dlist-name-gha" if single_gha else ""),
                    title=display_name,
                ),
                html.Span(f"{match_count} találat", className="uld-dlist-match-badge",
                          title="A keresett ULD ebben a stackben van") if match_count else None,
            ]),
            html.Span(f"{len(ulds)} db", className="uld-dlist-count"),
            html.Div(className="uld-dlist-touchbar", children=touchbar_items),
            html.Span(_fmt_iso_dt(dispatched_at) if dispatched_at else "—", className="uld-dlist-time"),
            html.Span(dispatched_by or "—", className="uld-dlist-by", title=(f"Rendszám: {dispatch_plate}" if dispatch_plate else f"Felhasználó: {dispatched_by}") if (dispatched_by or dispatch_plate) else ""),
            html.Button(
                "Vissza",
                className="uld-dlist-revert",
                title="Kiadás visszavonása - a stack visszakerül az aktív nézetbe",
                **{"data-stack-id": stack.get("id", ""), "data-stack-name": display_name},
            ),
        ],
    )


def _dispatched_signature(stacks: list[dict], search_text: str, uld_times_full: dict, returned: set | None = None) -> str:
    """Compact fingerprint of everything the dispatched view renders.

    Used to skip re-rendering the dispatched grid when nothing it shows actually
    changed. Without this, any data_version bump (e.g. an active-ULD time recompute
    that has nothing to do with dispatched stack) replaced the whole grid subtree
    every few seconds — the visible "flicker" the user reported. Covers each
    dispatched stack's identity, dispatch metadata, ULD order and their AWBs, plus
    the current search term.
    """
    parts: list[str] = []
    for s in sorted(_dispatched_stacks(stacks), key=lambda x: str(x.get("id") or "")):
        ulds = list(s.get("ulds", []) or [])
        uld_meta = []
        for u in ulds:
            info = uld_times_full.get(u, {}) or {}
            uld_meta.append("|".join([
                str(u),
                ",".join(info.get("awbs") or []) if isinstance(info.get("awbs"), list) else "",
                str(info.get("am_time") or ""),
                str(info.get("gha") or ""),
            ]))
        parts.append("|".join([
            str(s.get("id") or ""), str(s.get("revision") or ""),
            str(s.get("dispatched_at") or ""), str(s.get("dispatched_by") or ""),
            str(s.get("dispatched_on") or ""),
            str(s.get("dispatch_plate") or ""), str(s.get("name") or ""),
            str(s.get("stack_gha") or ""),
            str(s.get("prepared_at") or ""), str(s.get("prepared_by") or ""),
            ";".join(uld_meta),
        ]))
    blob = "\n".join(parts) + "##" + str(search_text or "") + "##" + ",".join(sorted(returned or ()))
    return hashlib.md5(blob.encode("utf-8")).hexdigest()


def _make_dispatched_stacks_section(stacks: list[dict], all_uld: list[dict], search_text: str = "") -> list:
    try:
        _st = data_cache.get_state()
    except Exception:
        _st = {}
    uld_times_full = _st.get("uld_times_full") or {}
    dispatched = _strip_returned_ulds(
        _dispatched_stacks(stacks), _returned_uld_set(_st), uld_times_full
    )
    if not dispatched:
        return [html.Div(className="uld-dlist-empty", key="uld-dispatched-empty", children=[
            html.Span("⇥", className="uld-dlist-empty-ico"),
            html.Span("Nincs kiküldött stack.", className="uld-dlist-empty-txt"),
        ])]
    # ── Keresés: ULD-aware, with per-ULD match highlighting ─────────────────────
    # Single term  → substring/glyph-folded ULD match OR text match (name/GHA/user).
    # Multi-line    → bulk ULD lookup (paste many ULDs, see which dispatched stacks
    #                 hold them). Matched ULD codes are returned per stack so their
    #                 chips light up in the touchbar and the row shows "N találat".
    raw = search_text or ""
    searching = bool(raw.strip())
    matched_by_stack: dict[str, set] = {}
    if searching:
        bulk_terms_list = _bulk_uld_search_terms(raw)
        bulk_terms = set(bulk_terms_list)
        q_blob = _search_blob(raw)
        q_fold = _uld_search_fold(raw)
        filtered = []
        if bulk_terms:
            # Dispatched stacks hold the finalised, correct ULDs → EXACT matches only,
            # plus an explicitly highlighted same-prefix near hit for typo recovery.
            # The near hit remains display-only and cannot trigger an operation.
            exact, near, _missing = _bulk_uld_resolution(
                _dispatched_uld_records(dispatched, uld_times_full), raw
            )
            for record in exact:
                sid = record.get("_stack_id", "")
                if sid:
                    matched_by_stack.setdefault(sid, set()).add(_normalize_uld_code(record.get("uld_number")))
            # A same-prefix, one-character near hit is a visual search aid only:
            # it highlights the chip but never selects or mutates the ULD.
            for records in near.values():
                for record in records:
                    sid = record.get("_stack_id", "")
                    if sid:
                        matched_by_stack.setdefault(sid, set()).add(
                            _normalize_uld_code(record.get("uld_number"))
                        )
            matched_ids = set(matched_by_stack)
            filtered = [stack for stack in dispatched if stack.get("id", "") in matched_ids]
        else:
            for stack in dispatched:
                sid = stack.get("id", "")
                matched: set = set()
                for uld_num in stack.get("ulds", []) or []:
                    info = uld_times_full.get(uld_num, {}) or {}
                    awbs = " ".join(info.get("awbs") or []) if isinstance(info.get("awbs"), list) else ""
                    if (q_fold and q_fold in _uld_search_fold(uld_num)) or (q_blob and q_blob in _search_blob(uld_num, awbs)):
                        matched.add(_normalize_uld_code(uld_num))
                stack_text_match = False
                if q_blob:
                    stack_text_match = q_blob in _search_blob(
                        stack.get("name") or "", stack.get("stack_gha") or "",
                        stack.get("dispatched_by") or "", stack.get("dispatch_plate") or "",
                        stack.get("dispatched_at") or "",
                    )
                if matched or stack_text_match:
                    matched_by_stack[sid] = matched
                    filtered.append(stack)
        dispatched = filtered
        if not dispatched:
            return [html.Div(className="uld-dlist-empty", key="uld-dispatched-empty-search", children=[
                html.Span("⌕", className="uld-dlist-empty-ico"),
                html.Span("Nincs találat a kiküldött stackek között.", className="uld-dlist-empty-txt"),
                html.Span("A keresett ULD lehet, hogy nincs kiküldve, vagy más a helyesírása.",
                          className="uld-dlist-empty-sub"),
            ])]
    gha_color_map = _compute_gha_color_map(all_uld)
    display_lookup = {u.get("uld_number"): u for u in all_uld if u.get("uld_number")}
    for uld_num, info in uld_times_full.items():
        if uld_num not in display_lookup and isinstance(info, dict):
            display_lookup[uld_num] = {"uld_number": uld_num, "gha": info.get("gha") or ""}
    display_names = _stack_display_name_map(dispatched, display_lookup)
    # When searching, stacks with the most matched ULDs float to the top; otherwise
    # newest dispatch first.
    if searching:
        ordered = sorted(
            dispatched,
            key=lambda s: (len(matched_by_stack.get(s.get("id", ""), ())), str(s.get("dispatched_at") or s.get("updated_at") or "")),
            reverse=True,
        )
    else:
        ordered = sorted(dispatched, key=lambda s: str(s.get("dispatched_at") or s.get("updated_at") or ""), reverse=True)
    total_ulds = sum(len(s.get("ulds", []) or []) for s in ordered)
    total_matches = sum(len(m) for m in matched_by_stack.values())
    stack_count_text = f"{len(ordered)} stack / {total_ulds} db"
    match_stack_count_text = f"{len(matched_by_stack)} stack"
    rows = [
        _make_dispatched_stack_card(stack, uld_times_full, display_names.get(stack.get("id")) or stack.get("name") or "Stack", gha_color_map,
                                    matched_ulds=matched_by_stack.get(stack.get("id", "")))
        for stack in ordered
    ]
    return [html.Div(className="uld-dlist", key="uld-dlist-wrap", children=[
        html.Div(className="uld-dlist-top", children=[
            html.Div(className="uld-dlist-top-info", children=[
                html.Span("Kiadott stackek", className="uld-dlist-title"),
                html.Span(stack_count_text, className="uld-dlist-sub"),
                html.Span(f"Keresés: {total_matches} ULD találat {match_stack_count_text}",
                          className="uld-dlist-sub uld-dlist-sub-match") if (searching and total_matches) else None,
            ]),
            # Bulk actions — mirror of the active page's selection logic: select rows
            # (click), then revert the selected, or revert every dispatched stack.
            html.Div(className="uld-dlist-tools", children=[
                html.Span("0 kijelölve", className="uld-dlist-selcount", **{"data-count": "0"}),
                html.Button("Mind kijelölése", className="uld-dlist-bulk-btn uld-dlist-select-all",
                            title="Kiadott stackek kijelölése / törlése"),
                html.Button("Kijelöltek vissza", className="uld-dlist-bulk-btn uld-dlist-revert-selected",
                            disabled=True,
                            title="Kijelölt kiadott stackek visszaállítása az aktív nézetbe"),
                html.Button("Mind vissza", className="uld-dlist-bulk-btn uld-dlist-revert-all",
                            title="Minden kiadott stack visszaállítása az aktív nézetbe"),
            ]),
        ]),
        html.Div(className="uld-dlist-head", children=[
            html.Span("Stack", className="uld-dlist-h-name"),
            html.Span("ULD szám", className="uld-dlist-h-count"),
            html.Span("ULD-k", className="uld-dlist-h-touch"),
            html.Span("Kiadás ideje", className="uld-dlist-h-time"),
            html.Span("Felhasználó", className="uld-dlist-h-by"),
            html.Span("", className="uld-dlist-h-act"),
        ]),
        html.Div(className="uld-dlist-body", children=rows),
    ])]


def _make_uld_stats(uld_data: list[dict]) -> list:
    counts = {"ok": 0, "warning": 0, "critical": 0, "expired": 0, "kézi": 0}
    for u in uld_data:
        status = u.get("status", "ok")
        counts[status if status in counts else "ok"] += 1
    total = len(uld_data)
    items = [html.Span(f"{total} aktív ULD", className="uld-stat-total")]
    # Lejárt ULD nem kerül az aktív listába (a parser kiszűri), így a "kritikus"
    # szám mindig szinkronban van a Kritikus szűrőchippel.
    if counts["critical"]:
        items.append(html.Span(f"{counts['critical']} kritikus", className="uld-stat-crit"))
    if counts["warning"]:
        items.append(html.Span(f"{counts['warning']} figyelmeztetés", className="uld-stat-warn"))
    if counts["ok"]:
        items.append(html.Span(f"{counts['ok']} rendben", className="uld-stat-ok"))
    return items


def _make_filter_chips(
    all_uld: list[dict],
    status_filter: str,
    prefix_filter: str,
    gha_filter: str,
    gha_color_map: dict | None = None,
) -> list:
    """Render multi-dimensional filter chips with cross-filter dimming.

    A chip is dimmed when no items exist at the intersection of that chip's value
    and the currently active OTHER-dimension filters.
    """
    sf = status_filter if status_filter and status_filter != "all" else ""
    pf = prefix_filter.upper() if prefix_filter else ""
    gf = gha_filter if gha_filter else ""

    # Compute available values per dimension given the other two active filters
    avail_status: set[str] = set()
    avail_prefix: set[str] = set()
    avail_gha:    set[str] = set()
    for u in all_uld:
        u_status = u.get("status", "")
        u_prefix = (u.get("uld_type") or "")[:3].upper()
        u_gha    = u.get("gha") or ""
        if (not pf or u_prefix == pf) and (not gf or u_gha == gf):
            avail_status.add(u_status)
        if (not sf or u_status == sf) and (not gf or u_gha == gf):
            avail_prefix.add(u_prefix)
        if (not sf or u_status == sf) and (not pf or u_prefix == pf):
            avail_gha.add(u_gha)

    # Status row
    status_chips = []
    for key, label, cls in [
        ("all",      "Mind",            "uld-fb-all"),
        ("critical", "Kritikus",       "uld-fb-critical"),
        ("warning",  "Figyelmeztetés",        "uld-fb-warning"),
        ("ok",       "OK",             "uld-fb-ok"),
    ]:
        active = (status_filter == key) or (not status_filter and key == "all")
        unavail = key != "all" and key not in avail_status and (pf or gf)
        klass = f"uld-chip uld-chip-status {cls}"
        if active:
            klass += " uld-chip-active"
        elif unavail:
            klass += " uld-chip-dim"
        status_chips.append(html.Button(
            label, className=klass,
            **{"data-filter": "status", "data-value": key},
        ))

    # Prefix row
    prefixes = sorted({(u.get("uld_type") or "")[:3].upper() for u in all_uld if u.get("uld_type")})
    prefix_chips = [html.Button(
        "Mind",
        className=f"uld-chip uld-chip-prefix{' uld-chip-active' if not pf else ''}",
        **{"data-filter": "prefix", "data-value": ""},
    )]
    for p in prefixes:
        if not p:
            continue
        color   = _uld_prefix_color(p)
        active  = (pf == p)
        unavail = p not in avail_prefix and (sf or gf)
        klass   = "uld-chip uld-chip-prefix"
        if active:
            klass += " uld-chip-active"
        elif unavail:
            klass += " uld-chip-dim"
        prefix_chips.append(html.Button(
            p, className=klass,
            style={"--chip-color": color, "--chip-bg": f"{color}22"},
            title="Kattintás: szűrés / Ctrl+kattintás: ULD típus kijelölése",
            **{"data-filter": "prefix", "data-value": p, "data-quick-select": "prefix"},
        ))

    # GHA row
    ghas = sorted({u.get("gha", "") for u in all_uld if u.get("gha")})
    gha_chips = [html.Button(
        "Mind",
        className=f"uld-chip uld-chip-gha{' uld-chip-active' if not gf else ''}",
        **{"data-filter": "gha", "data-value": ""},
    )]
    color_map = gha_color_map or {}
    for i, g in enumerate(ghas):
        color   = color_map.get(g) or _GHA_COLOR_PALETTE[i % len(_GHA_COLOR_PALETTE)]
        active  = (gf == g)
        unavail = (bool(gf) and gf != g) or (not gf and g not in avail_gha and (sf or pf))
        klass   = "uld-chip uld-chip-gha"
        if active:
            klass += " uld-chip-active"
        elif unavail:
            klass += " uld-chip-dim"
        gha_chips.append(html.Button(
            g, className=klass,
            style={"--chip-color": color, "--chip-bg": f"{color}1f"},
            title="Kattintás: szűrés / Ctrl+kattintás: GHA ULD-k kijelölése",
            **{"data-filter": "gha", "data-value": g, "data-quick-select": "gha"},
        ))

    return [
        html.Div(className="uld-chip-row", children=[
            html.Span("Állapot:", className="uld-chip-label"),
            html.Div(className="uld-chip-group", children=status_chips),
        ]),
        html.Div(className="uld-chip-row", children=[
            html.Span("Típus:", className="uld-chip-label"),
            html.Div(className="uld-chip-group", children=prefix_chips),
        ]),
        html.Div(className="uld-chip-row", children=[
            html.Span("GHA:", className="uld-chip-label"),
            html.Div(className="uld-chip-group", children=gha_chips),
        ]),
    ]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

app.layout = html.Div(id="layout-root", children=[
    dcc.Interval(id="poll", interval=5000, n_intervals=0),
    dcc.Interval(id="loading-poll", interval=750, n_intervals=0),
    dcc.Interval(id="tick", interval=10000, n_intervals=0),
    dcc.Interval(id="activity-tick", interval=30000, n_intervals=0),
    dcc.Store(id="last-refresh-ts"),
    dcc.Store(id="data-version", data=0),
    dcc.Store(id="theme-store", data="dark"),
    dcc.Store(id="theme-applied"),
    dcc.Store(id="view-mode", data="ops"),
    dcc.Store(id="view-mode-applied"),
    dcc.Store(id="flow-mode", data="inbound"),
    dcc.Store(id="flow-mode-applied"),
    dcc.Store(id="active-filter", data="all"),
    dcc.Store(id="active-filter-applied"),
    dcc.Store(id="priority-test-mode", data=False),
    dcc.Store(id="loader-color-applied"),
    dcc.Store(id="overlay-state", data="first"),
    dcc.Store(id="kézi-refresh-store"),
    dcc.Store(id="kpi-bec-pin-store"),
    dcc.Store(id="kpi-muszak-pin-store"),
    dcc.Store(id="kpi-muszak-mode-store", data="inbound"),
    dcc.Store(id="kpi-muszak-hourly-store"),
    dcc.Store(id="kpi-muszak-chart-dummy"),
    dcc.Store(id="kpi-bec-prefixes-store"),
    dcc.Store(id="kpi-bec-prefixes-sink"),
    dcc.Store(id="kpi-page-chart-store"),
    dcc.Store(id="kpi-page-chart-dummy"),
    dcc.Store(id="kpi-subview-store", data="tracking"),
    dcc.Store(id="kpi-subview-applied"),
    dcc.Store(id="store-action"),
    dcc.Store(id="note-action"),
    dcc.Store(id="dev-auth"),
    dcc.Store(id="dev-tab", data="ops"),
    dcc.Store(id="dev-version", data=0),
    dcc.Store(id="user-activity"),
    dcc.Store(id="user-activity-applied"),
    dcc.Store(id="print-glabs-store", data=None),
    dcc.Store(id="print-action-dummy"),
    dcc.Store(id="uld-filter-store",        data="all"),
    dcc.Store(id="uld-trigger-store",       data=0),
    # Bumped by ops that change which ULDs are IN the list (e.g. kézi add creates
    # a brand-new row). Forces an immediate full re-render so the new/removed ULD
    # shows up at once instead of waiting ~6–9s for the stacks file watcher.
    dcc.Store(id="uld-list-dirty-store",    data=0),
    dcc.Store(id="uld-search-store",        data=""),
    dcc.Store(id="uld-view-store",          data="active"),
    dcc.Store(id="uld-view-applied"),
    dcc.Store(id="uld-dispatched-sig",      data=""),
    dcc.Store(id="uld-prefix-filter-store", data=""),
    dcc.Store(id="uld-gha-filter-store",    data=""),
    dcc.Store(id="uld-page-store",          data=1),

    # Nyomtatás-only container — populated by JS before window.print(), emptied after
    html.Div(id="print-only", style={"display": "none"}),

        # ULD action feedback toast — managed entirely by uld_manager.js
        html.Div(id="uld-toast", className="uld-toast"),

        # ULD stack delete confirmation — custom modal managed by uld_manager.js
        html.Div(id="uld-delete-modal", className="uld-delete-modal", **{"aria-hidden": "true"}, children=[
            html.Div(className="uld-delete-dialog", children=[
                html.Div(className="uld-delete-kicker", children="Stack törlése"),
                html.Div(id="uld-delete-title", className="uld-delete-title", children="Törlöd ezt a stacket?"),
                html.Div(id="uld-delete-text", className="uld-delete-text"),
                html.Div(className="uld-delete-actions", children=[
                    html.Button("Mégse", id="uld-delete-cancel", className="uld-delete-cancel"),
                    html.Button("Törlés", id="uld-delete-confirm", className="uld-delete-confirm"),
                ]),
            ]),
        ]),

        # ULD stack dispatch confirmation — custom modal managed by uld_manager.js
        html.Div(id="uld-dispatch-modal", className="uld-delete-modal uld-dispatch-modal", **{"aria-hidden": "true"}, children=[
            html.Div(className="uld-delete-dialog uld-dispatch-dialog", children=[
                html.Div(className="uld-delete-kicker uld-dispatch-kicker", children="Kiadás stack"),
                html.Div(id="uld-dispatch-title", className="uld-delete-title", children="Kiadod ezt a stacket?"),
                html.Div(id="uld-dispatch-text", className="uld-delete-text"),
                html.Label(className="uld-dispatch-plate-field", children=[
                    html.Span("Rendszám"),
                    dcc.Input(
                        id="uld-dispatch-plate",
                        className="uld-dispatch-plate-input",
                        type="text",
                        placeholder="Opcionális",
                        spellCheck=False,
                        **{"autoComplete": "off"},
                    ),
                ]),
                html.Div(className="uld-delete-actions", children=[
                    html.Button("Mégse", id="uld-dispatch-cancel", className="uld-delete-cancel"),
                    html.Button("Kiadás", id="uld-dispatch-confirm", className="uld-dispatch-confirm"),
                ]),
            ]),
        ]),

        html.Div(id="uld-info-modal", className="uld-delete-modal uld-info-modal", **{"aria-hidden": "true"}, children=[
            html.Div(className="uld-delete-dialog uld-info-dialog", children=[
                html.Div(className="uld-delete-kicker uld-info-kicker", children="ULD információ"),
                html.Div(id="uld-info-title", className="uld-delete-title", children="ULD"),
                html.Div(id="uld-info-body", className="uld-info-body"),
                html.Div(className="uld-delete-actions", children=[
                    html.Button("Bezárás", id="uld-info-close", className="uld-delete-cancel"),
                ]),
            ]),
        ]),

        html.Div(id="uld-manual-modal", className="uld-manual-modal", **{"aria-hidden": "true"}, children=[
            html.Div(className="uld-manual-dialog", children=[
                html.Div(className="uld-manual-head", children=[
                    html.Div(children=[
                        html.Div("Kézi ULD", className="uld-manual-kicker"),
                        html.Div(id="uld-manual-title", className="uld-manual-title", children="Hozzáadás to stack"),
                    ]),
                    html.Button("X", id="uld-manual-close", className="uld-manual-close", title="Bezárás"),
                ]),
                html.Div(className="uld-manual-grid", children=[
                    html.Label(className="uld-manual-field", children=[
                        html.Span(["Stack ", html.Em("kötelező")]),
                        html.Select(id="uld-manual-stack", className="uld-manual-input uld-manual-select"),
                    ]),
                    html.Label(className="uld-manual-field", children=[
                        html.Span(["GHA ", html.Em("kötelező")]),
                        dcc.Input(
                            id="uld-manual-gha",
                            className="uld-manual-input",
                            type="text",
                            list="uld-manual-gha-list",
                            spellCheck=False,
                            **{"autoComplete": "off"},
                        ),
                        html.Datalist(id="uld-manual-gha-list", children=[
                            html.Option(value="Menzies"),
                            html.Option(value="AS Cargo"),
                            html.Option(value="Celebi"),
                        ]),
                        html.Div(id="uld-manual-gha-note", className="uld-manual-note"),
                    ]),
                    html.Label(className="uld-manual-field", children=[
                        html.Span(["AWB ", html.Em("opcionális")]),
                        dcc.Input(
                            id="uld-manual-awb",
                            className="uld-manual-input",
                            type="text",
                            spellCheck=False,
                            **{"inputMode": "numeric", "autoComplete": "off"},
                        ),
                    ]),
                    html.Label(className="uld-manual-field", children=[
                        html.Span(["ULD ", html.Em("kötelező")]),
                        dcc.Input(
                            id="uld-manual-uld",
                            className="uld-manual-input",
                            type="text",
                            spellCheck=False,
                            **{"autoComplete": "off"},
                        ),
                    ]),
                    html.Div(className="uld-manual-field uld-manual-time-field", children=[
                        html.Span(["Átadás ideje ", html.Em("opcionális")]),
                        html.Div(className="uld-manual-time-wrap", children=[
                            dcc.Input(
                                id="uld-manual-datetime",
                                className="uld-manual-input uld-manual-datetime",
                                type="datetime-local",
                                **{"autoComplete": "off"},
                            ),
                            html.Button(
                                "×",
                                id="uld-manual-datetime-clear",
                                className="uld-manual-datetime-clear",
                                title="Idő törlése - mentés idő nélkül",
                                type="button",
                            ),
                        ]),
                    ]),
                ]),
                html.Div(id="uld-manual-duplicate", className="uld-manual-duplicate", **{"aria-live": "polite"}),
                html.Div(className="uld-manual-actions", children=[
                    html.Button("Mégse", id="uld-manual-cancel", className="uld-manual-cancel"),
                    html.Button("Hozzáadás", id="uld-manual-save", className="uld-manual-save"),
                ]),
            ]),
        ]),

        # ULD long-stay figyelmeztetés modal removed (user request) — hidden stubs kept
        # only so any leftover client-side reference does not 404 / break callbacks.
        dcc.Store(id="uld-alert-dismissed", data=True),
        html.Div(id="uld-alert-overlay", style={"display": "none"}),
        html.Div(id="uld-alert-body", style={"display": "none"}),
        html.Button(id="uld-alert-ok", style={"display": "none"}, n_clicks=0),

    # TV mode overlay — single fixed screen, day-adaptive gradient, rotating content
    html.Div(id="tv-overlay", className="tv-overlay", style={"display": "none"}, children=[

        # Animated day-adaptive background
        html.Div(className="tv-bg", **{"aria-hidden": "true"}, children=[
            html.Div(className="tv-bg-blob tv-blob-1"),
            html.Div(className="tv-bg-blob tv-blob-2"),
            html.Div(className="tv-bg-blob tv-blob-3"),
        ]),

        # Fix 1920×1080 "színpad": a teljes TV-tartalom fix méretű, és egységesen
        # átskálázódik a képernyőre (tv_mode.js _fitStage) → SEMMI nem adaptív,
        # ablakos/teljes képernyős nézetben is azonos arányok.
        html.Div(className="tv-stage", id="tv-stage", children=[

        # ── Header (dark glass) ─────────────────────────────────────────────
        html.Div(className="tv-header", children=[
            # Logo
            html.Div(className="tv-hdr-logo", children=[
                html.Div("Flow Manager", className="tv-hdr-logotext"),
                html.Div("HGL Group Hungary · Ecommerce", className="tv-hdr-logosub"),
                html.Div(className="tv-hdr-meta", children=[
                    html.Span(id="tv-shift-name", className="tv-hdr-shift-nm"),
                ]),
            ]),
            # Center balról jobbra: kamion kiadva · rakodásra vár ·
            #   műszak inbound kg (narancs) + inbound/óra · műszak outbound kg (lila) + outbound/óra
            html.Div(className="tv-hdr-center", children=[
                html.Div(className="tv-hdr-kpis", children=[
                    html.Div(className="tv-kpi-cell tv-kpi-issued", children=[
                        html.Div(id="tv-hdr-issued",  className="tv-kpi-val tv-col-green"),
                        html.Div("kamion kiadva",      className="tv-kpi-lbl"),
                        html.Div(className="tv-kpi-accent tv-kpi-accent-green"),
                    ]),
                    html.Div(className="tv-kpi-cell tv-kpi-waiting", children=[
                        html.Div(id="tv-hdr-waiting", className="tv-kpi-val tv-col-blue"),
                        html.Div("rakodásra váró",     className="tv-kpi-lbl"),
                        html.Div(className="tv-kpi-accent tv-kpi-accent-blue"),
                    ]),
                    html.Div(className="tv-kpi-cell tv-kpi-warehouse", children=[
                        html.Div(id="tv-hdr-wh-active", className="tv-kpi-val tv-col-warehouse"),
                        html.Div("raktárban aktív", className="tv-kpi-lbl"),
                        html.Div(className="tv-kpi-subval", children=[
                            html.Span(id="tv-hdr-wh-kg", className="tv-kpi-subnum"),
                            html.Span(" kg", className="tv-kpi-subunit"),
                        ]),
                        html.Div(className="tv-kpi-accent tv-kpi-accent-warehouse"),
                    ]),
                ]),
                # INBOUND: műszak kg (narancs vonal) + óradiagram
                html.Div(className="tv-hdr-flow tv-hdr-flow-in", children=[
                    html.Div(className="tv-kpi-cell tv-kpi-in", children=[
                        html.Div(id="tv-hdr-in-kg",  className="tv-kpi-val"),
                        html.Div("műszak inbound kg", className="tv-kpi-lbl"),
                        html.Div(className="tv-kpi-accent tv-kpi-accent-orange"),
                    ]),
                    html.Div(className="tv-hdr-chart tv-hdr-chart-in", children=[
                        html.Div("inbound / óra", className="tv-hdr-chart-cap"),
                        html.Div(id="tv-hdr-chart-in", className="tv-hdr-chart-svg"),
                    ]),
                ]),
                # OUTBOUND: műszak kg (lila vonal) + óradiagram
                html.Div(className="tv-hdr-flow tv-hdr-flow-out", children=[
                    html.Div(className="tv-kpi-cell tv-kpi-out", children=[
                        html.Div(id="tv-hdr-out-kg",  className="tv-kpi-val"),
                        html.Div("műszak outbound kg", className="tv-kpi-lbl"),
                        html.Div(className="tv-kpi-accent tv-kpi-accent-purple"),
                    ]),
                    html.Div(className="tv-hdr-chart tv-hdr-chart-out", children=[
                        html.Div("outbound / óra", className="tv-hdr-chart-cap"),
                        html.Div(id="tv-hdr-chart-out", className="tv-hdr-chart-svg"),
                    ]),
                ]),
            ]),  # /tv-hdr-center
            # Clock
            html.Div(className="tv-hdr-clock", children=[
                html.Div(id="tv-clock", className="tv-clock"),
                html.Div(id="tv-date",  className="tv-date"),
                html.Div(className="tv-hdr-prev", children=[
                    html.Span("Előző: ", className="tv-hdr-prev-lbl"),
                    html.Span(id="tv-prev-name", className="tv-hdr-prev-nm"),
                    html.Span(" · ", className="tv-hdr-prev-sep"),
                    html.Span(id="tv-prev-kg",  className="tv-hdr-prev-val"),
                    html.Span(" kg",            className="tv-hdr-prev-unit"),
                ]),
            ]),
        ]),

        # ── Main content ─────────────────────────────────────────────────
        # A header fix; ez a középső .tv-main sáv vált a
        # két oldal (RAKODÁSOK / MŰSZAK RIPORT) között finom áttűnéssel (tv_mode.js).
        html.Div(className="tv-main", children=[

            # Oldalváltó pillek — fixen a tv-main jobb felső sarkában (a stage-en
            # belül, így együtt skálázódik). Kattintásra rögzít (pin); R gomb vált,
            # P gomb szünetelteti az auto-váltást.
            html.Div(id="tv-page-switch", className="tv-page-switch", children=[
                html.Button("RAKODÁSOK", id="tv-pg-ops", className="tv-pg-pill is-active",
                            **{"data-page": "0"}, n_clicks=0),
                html.Button("MŰSZAK RIPORT", id="tv-pg-report", className="tv-pg-pill",
                            **{"data-page": "1"}, n_clicks=0),
                html.Span(className="tv-pg-lock", **{"aria-hidden": "true"}),
            ]),

            # ══ OLDAL 1 — RAKODÁSOK (meglévő nézet) ══
            html.Div(id="tv-page-ops", className="tv-page tv-page-ops is-active", children=[

            # Trucks section
            html.Div(className="tv-trucks-section", children=[
                html.Div(className="tv-sec-hdr", children=[
                    html.Span("RAKODÁSOK", className="tv-sec-label"),
                    html.Div(className="tv-sec-hdr-right", children=[
                        html.Span(id="tv-out-summary",   className="tv-sec-summary"),
                    ]),
                ]),
                html.Div(id="tv-trucks-grid", className="tv-trucks-grid"),
                html.Div(className="tv-truck-page-footer", children=[
                    html.Div(id="tv-page-dots", className="tv-page-dots"),
                ]),
            ]),
            ]),  # /tv-page-ops

            # ══ OLDAL 2 — MŰSZAK RIPORT (outbound-vezérelt, minimális inbound) ══
            html.Div(id="tv-page-report", className="tv-page tv-page-report", children=[

                # Fejléc-sáv: cím + műszak (bal) és élő rakodás-kontextus (jobb).
                # A gauge-ok a stabil nagy atlaghoz mernek; lasd /api/tv/ops report blokk.
                html.Div(className="tv-report-head", children=[
                    html.Div(className="tv-report-title", children=[
                        html.Span("MŰSZAK RIPORT", className="tv-sec-label"),
                        html.Div(id="tv-report-shift", className="tv-report-shift"),
                    ]),
                    html.Div(id="tv-report-live", className="tv-report-live"),
                ]),

                # 2×2 fő rács: 3 fő műszer | összegzés + kis rakodás műszer // óradiagram | inbound
                html.Div(className="tv-report-grid", children=[

                    # Bal-fent: három fix outbound speedometer
                    html.Div(className="tv-report-cell tv-report-gauges", children=[
                        html.Div(id="tv-gauge-kg", className="tv-gauge tv-gauge-out"),
                        html.Div(id="tv-gauge-colli", className="tv-gauge tv-gauge-out"),
                        html.Div(id="tv-gauge-parcel", className="tv-gauge tv-gauge-out"),
                    ]),

                    # Jobb-fent: műszak-összegzés + kisebb GLABS-alapú rakodás/óra műszer
                    html.Div(className="tv-report-cell tv-report-stats-cell", children=[
                        html.Div(className="tv-sec-hdr", children=[
                            html.Span("MŰSZAK ÖSSZEGZÉS · OUTBOUND", className="tv-sec-label"),
                        ]),
                        html.Div(id="tv-gauge-loadings", className="tv-gauge tv-gauge-out tv-gauge-loadings tv-gauge-small"),
                        html.Div(id="tv-report-stats", className="tv-report-stats"),
                    ]),

                    # Bal-lent: óránkénti outbound kiadás diagram (csak aktuális műszak)
                    html.Div(className="tv-report-cell tv-report-hourly", children=[
                        html.Div(className="tv-sec-hdr", children=[
                            html.Span("ÓRÁNKÉNTI KIADÁS · KG", className="tv-sec-label"),
                            html.Span(id="tv-report-hourly-sub", className="tv-sec-sub"),
                        ]),
                        html.Div(id="tv-report-chart", className="tv-report-chart"),
                    ]),

                    # Jobb-lent: inbound műszak nagyobb statblokkokkal
                    html.Div(className="tv-report-cell tv-report-inbound", children=[
                        html.Div(className="tv-sec-hdr", children=[
                            html.Span("INBOUND MŰSZAK", className="tv-sec-label"),
                        ]),
                        html.Div(className="tv-report-inb-body", children=[
                            html.Div(id="tv-report-inb", className="tv-report-inb"),
                        ]),
                    ]),

                ]),
            ]),  # /tv-page-report

        ]),
        ]),  # /tv-stage

        # Refresh countdown arc (SVG injected by tv_mode.js on enter)
        html.Div(id="tv-refresh-fab", className="tv-refresh-fab"),

        # Exit FAB
        html.Button("✕", id="tv-exit-btn", className="tv-exit-btn", n_clicks=0,
                    title="Kilépés TV módból"),

        # Last refresh timestamp
        html.Div(id="tv-last-refresh", className="tv-last-refresh"),

        # Sync-stall / stale-data warning — shown by tv_mode.js ONLY when the
        # background refresh is stuck. The TV keeps showing the last good data,
        # so this banner makes the staleness visible instead of silent.
        html.Div(id="tv-stale-banner", className="tv-stale-banner", **{"data-on": "0"}),
    ]),

    # Background orbs — outside app-root so z-index is clean
    html.Div(className="bg-orbs", children=[
        html.Div(className="bg-orb orb-1"),
        html.Div(className="bg-orb orb-2"),
        html.Div(className="bg-orb orb-3"),
    ]),

    # KPI-page background field — a dedicated, slow GPU-drift blob layer that owns
    # the reporting surface. Two colour environments (blue/cyan/turquoise for the
    # Tracking subpage, warm sunset for Riport) are switched by body classes; the
    # drift pauses during snap/scroll (body.kpi-bg-paused) and when the tab is
    # hidden, per the documented weak-machine FPS rule. Sits outside app-root, so
    # it stays behind all KPI content. Only visible in view-kpi-mode.
    html.Div(className="kpi-bg", **{"aria-hidden": "true"}, children=[
        html.Div(className="kpi-bg-blob kpi-bg-blob-1"),
        html.Div(className="kpi-bg-blob kpi-bg-blob-2"),
        html.Div(className="kpi-bg-blob kpi-bg-blob-3"),
        html.Div(className="kpi-bg-blob kpi-bg-blob-4"),
        html.Div(className="kpi-bg-blob kpi-bg-blob-5"),
        html.Div(className="kpi-bg-veil"),
    ]),

    # Liquid glass scroll fade edge
    html.Div(className="scroll-fade"),

    # Keep both overlay modes mounted so progress and animation don't restart on each poll.
    html.Div(id="overlay", className="loading-overlay loading-first",
             children=[
                 html.Div(className="l-first-wrap", children=_loading_first_children()),
                 html.Div(className="l-refresh-wrap", children=_loading_refresh_children()),
             ]),

    # INBOUND "Összegzés" — TOP 10 priority modal
    html.Div(id="summary-overlay", className="summary-overlay", style={"display": "none"}, children=[
        html.Div(className="summary-dialog", children=[
            html.Div(className="summary-head", children=[
                html.Div(children=[
                    html.Div("Összegzés — TOP 10 prioritás", className="summary-title"),
                    html.Div("Az aktív inbound lista legfontosabb tételei", className="summary-sub"),
                ]),
                html.Div(className="summary-actions", children=[
                    html.Button("Másolás", id="summary-copy-btn", className="summary-copy-btn", n_clicks=0),
                    html.Button("X", id="summary-close", className="summary-close", n_clicks=0),
                ]),
            ]),
            html.Div(id="summary-body", className="summary-content"),
        ]),
    ]),

    # Nyomtatás preview — full-screen editable loading plan preview
    html.Div(id="print-preview-overlay", className="print-preview-overlay", style={"display": "none"}, children=[
        html.Div(className="ppt-toolbar no-print", children=[
            html.Span(id="ppt-info-text", className="ppt-info"),
            html.Div(className="ppt-actions", children=[
                html.Button("+ Sor", id="ppt-add-row", className="ppt-btn", n_clicks=0),
                html.Button("🖨 Nyomtatás", id="ppt-print", className="ppt-btn ppt-btn-primary", n_clicks=0),
                html.Button("✕ Bezárás", id="ppt-close", className="ppt-btn ppt-btn-close", n_clicks=0),
            ]),
        ]),
        html.Div(id="print-preview-paper", className="ppt-paper"),
    ]),

    html.Div(id="settings-overlay", className="settings-overlay", style={"display": "none"}, children=[
        html.Div(className="settings-dialog", children=[
            html.Div(className="settings-head", children=[
                html.Div(children=[
                    html.Div("Beállítások", className="settings-title"),
                    html.Div("Forrásfájlok és frissítési ütemezés", className="settings-sub"),
                ]),
                html.Button("X", id="settings-close", className="settings-close", n_clicks=0),
            ]),
            html.Div(className="settings-body", children=[
                html.Div(className="settings-intro", children=[
                    html.Div("Excel források", className="settings-section-title"),
                    html.Div("Válaszd ki a két élő táblát. A módosítás mentés után, újraindítással lép életbe.",
                             className="settings-section-note"),
                ]),
                html.Div(className="settings-source-grid", children=[
                    html.Div(className="settings-source-card", children=[
                        html.Div(className="settings-source-head", children=[
                            html.Div(children=[
                                html.Div("E_COMM nyomonkövetés", className="settings-source-title"),
                                html.Div("Inbound státuszok, súlyok és várható beérkezők", className="settings-source-desc"),
                            ]),
                            html.Span(id="settings-ecomm-status", className="settings-status-dot"),
                        ]),
                        dcc.Input(id="settings-ecomm-path", className="settings-input settings-path-input",
                                  type="text", readOnly=True),
                        html.Div(className="settings-source-actions", children=[
                            html.Button("Fájl kiválasztása", id="settings-ecomm-browse",
                                        className="settings-browse", n_clicks=0,
                                        **{"data-kind": "ecomm"}),
                            html.Div(id="settings-ecomm-active", className="settings-active-path"),
                        ]),
                    ]),
                    html.Div(className="settings-source-card", children=[
                        html.Div(className="settings-source-head", children=[
                            html.Div(children=[
                                html.Div("BUD-Pallets", className="settings-source-title"),
                                html.Div("Outbound rakodások, kiadás és GLABS adatok", className="settings-source-desc"),
                            ]),
                            html.Span(id="settings-pallets-status", className="settings-status-dot"),
                        ]),
                        dcc.Input(id="settings-pallets-path", className="settings-input settings-path-input",
                                  type="text", readOnly=True),
                        html.Div(className="settings-source-actions", children=[
                            html.Button("Fájl kiválasztása", id="settings-pallets-browse",
                                        className="settings-browse", n_clicks=0,
                                        **{"data-kind": "pallets"}),
                            html.Div(id="settings-pallets-active", className="settings-active-path"),
                        ]),
                    ]),
                ]),
                html.Div(className="settings-source-card settings-shared-state-card", children=[
                    html.Div(className="settings-source-head", children=[
                        html.Div(children=[
                            html.Div("Közös állapotmappa", className="settings-source-title"),
                            html.Div("Betárolva, megjegyzések, stackek és közös állapotfájlok helye",
                                     className="settings-source-desc"),
                        ]),
                        html.Span(id="settings-shared-state-status", className="settings-status-dot"),
                    ]),
                    dcc.Input(id="settings-shared-state-path", className="settings-input settings-path-input",
                              type="text", readOnly=True, placeholder="Automatikus OneDrive-felderítés"),
                    html.Div(className="settings-source-actions", children=[
                        html.Button("Mappa kiválasztása", id="settings-shared-state-browse",
                                    className="settings-browse", n_clicks=0,
                                    **{"data-kind": "shared_state"}),
                        html.Div(id="settings-shared-state-active", className="settings-active-path"),
                    ]),
                ]),
                html.Div(className="settings-refresh-card", children=[
                    html.Div(children=[
                        html.Div("Automatikus frissítés", className="settings-source-title"),
                        html.Div("Ritkább frissítés stabilabb adatmegjelenítést ad Excel mentések közben.",
                                 className="settings-source-desc"),
                    ]),
                    html.Label(className="settings-field settings-field-small", children=[
                        html.Span("Perc"),
                        dcc.Input(id="settings-refresh-minutes", className="settings-input",
                                  type="number", min=1, max=60, step=1),
                    ]),
                ]),
                html.Div(id="settings-message", className="settings-message"),
            ]),
            html.Div(className="settings-actions", children=[
                html.Button("Developer", id="dev-open-btn", className="dev-open-btn",
                            n_clicks=0, title="Developer mód (PIN)"),
                html.Button("Mégsem", id="settings-cancel", className="settings-cancel", n_clicks=0),
                html.Button("Mentés", id="settings-save", className="settings-save", n_clicks=0),
            ]),
        ]),
    ]),

    # Developer PIN gate
    html.Div(id="dev-pin-overlay", className="settings-overlay dev-pin-overlay",
             style={"display": "none"}, children=[
        html.Div(className="settings-dialog dev-pin-dialog", children=[
            html.Div(className="settings-head", children=[
                html.Div(children=[
                    html.Div("Developer mód", className="settings-title"),
                    html.Div("Hozzáadás meg a PIN kódot", className="settings-sub"),
                ]),
                html.Button("X", id="dev-pin-close", className="settings-close", n_clicks=0),
            ]),
            html.Div(className="dev-pin-body", children=[
                dcc.Input(id="dev-pin-input", type="password", placeholder="PIN",
                          className="settings-input dev-pin-input", n_submit=0, value=""),
                html.Button("Belépés", id="dev-pin-submit", className="settings-save", n_clicks=0),
                html.Div(id="dev-pin-msg", className="dev-pin-msg"),
            ]),
        ]),
    ]),

    # Developer panel
    html.Div(id="dev-overlay", className="settings-overlay dev-overlay",
             style={"display": "none"}, children=[
        html.Div(className="settings-dialog dev-dialog", children=[
            html.Div(className="settings-head", children=[
                html.Div(children=[
                    html.Div("Developer menü", className="settings-title"),
                    html.Div(id="dev-sub", className="settings-sub"),
                ]),
                html.Button("X", id="dev-close", className="settings-close", n_clicks=0),
            ]),
            html.Div(className="dev-tabs", children=[
                html.Button("Műveletek", id="dev-tab-ops", className="dev-tab", n_clicks=0),
                html.Button("Állapotok", id="dev-tab-state", className="dev-tab", n_clicks=0),
                html.Button("Felülbírálás", id="dev-tab-ovr", className="dev-tab", n_clicks=0),
                html.Button("Napló", id="dev-tab-log", className="dev-tab", n_clicks=0),
            ]),
            html.Div(id="dev-msg", className="dev-msg"),
            html.Div(id="dev-content", className="dev-content"),
        ]),
    ]),

    html.Div(className="app-root", children=[

        # Header
        html.Div(className="app-header", children=[
            html.Div(className="header-left", children=[
                html.Div(className="header-titles", children=[
                    html.H1(
                        [html.Span(" " if c == " " else c, className="title-letter")
                         for c in "Flow Manager"],
                        className="app-title",
                    ),
                    html.P("HGL Group Hungary · Ecommerce Operations", className="app-subtitle"),
                ]),
            ]),
            html.Div(id="header-kpi", className="header-kpi",
                     children=_kpi_static_layout()),
            # KPI-only: the KPI Tracking ⇄ Riport switch lives in the header centre
            # (the header-kpi block is hidden in view-kpi-mode, freeing the centre).
            # The active button doubles as the "which subpage" indicator.
            html.Div(id="kpi-header-switch", className="kpi-header-switch", children=[
                html.Div(className="kpi-subview-switch", role="tablist",
                         **{"aria-label": "KPI nézet váltó"}, children=[
                    html.Button("KPI Tracking", id="kpi-subview-tracking-btn",
                                className="kpi-subview-btn kpi-subview-btn-active",
                                n_clicks=0, **{"role": "tab", "aria-selected": "true"}),
                    html.Button("Riport", id="kpi-subview-report-btn",
                                className="kpi-subview-btn", n_clicks=0,
                                **{"role": "tab", "aria-selected": "false"}),
                    html.Span(className="kpi-subview-glow"),
                ]),
                html.Div(id="kpi-page-updated", className="kpi-page-updated", children=""),
            ]),
            html.Div(className="header-right", children=[
                html.Div(id="refresh-info", className="refresh-info"),
                html.Button("Összegzés", id="summary-btn",
                            className="summary-btn", n_clicks=0,
                            title="TOP 10 prioritás összegzése táblázatban"),
                html.Button("⟳ Frissítés", id="refresh-btn",
                            className="refresh-btn", n_clicks=0),
                html.Button("KPI", id="kpi-page-btn",
                            className="kpi-page-btn", n_clicks=0,
                            title="KPI riport oldal"),
                html.Button(
                    html.Span(className="tv-launch-icon", **{"aria-hidden": "true"}),
                    id="tv-mode-open-btn",
                    className="tv-launch-btn",
                    n_clicks=0,
                    title="TV mód (Alt+T)",
                    **{"aria-label": "TV mód megnyitása"},
                ),
                html.Button("⚙", id="settings-btn",
                            className="settings-btn", n_clicks=0,
                            title="Beállítások", **{"aria-label": "Beállítások"}),
            ]),
            html.Div(
                id="header-saturation-indicator",
                className="header-saturation-indicator",
                title="Raktár telítettség: –",
                **{"data-sat": "–"},
            ),
        ]),

        _kpi_page_layout(),

        html.Div(className="stats-bar", children=[
            *[_make_stat_btn(*d) for d in _STAT_DEFS],
            html.Div(className="flow-switch", children=[
                html.Button("INBOUND",  id="flow-inbound",  className="flow-option flow-active", n_clicks=0),
                html.Button("ULD",      id="flow-uld",      className="flow-option", n_clicks=0),
                html.Span(className="flow-switch-glow"),
            ]),
        ]),

        # ULD sub-view switch (aktív ULD / dispatched stack) — centered directly
        # under the inbound/uld flow switch; only visible in ULD mode.
        html.Div(className="uld-view-switch-row", children=[
            html.Div(className="uld-view-switch", children=[
                html.Button("Aktív ULD-k", id="uld-view-active-btn",
                            className="uld-view-btn uld-view-btn-active", n_clicks=0),
                html.Button("Kiadott stackek", id="uld-view-dispatched-btn",
                            className="uld-view-btn", n_clicks=0),
            ]),
        ]),

        html.Div(id="legend-bar", className="legend-bar", children=[
            html.Div(className="legend-item", children=[
                html.Span(className="legend-dot", style={"background": c}),
                html.Span(label),
            ])
            for c, label in [
                ("#ff6b35", "Kategórián belül: AT/HU > TEMU > MD"),
                ("#00d4aa", "Sofőr helyszínen · sok kiadható"),
                ("#4a9eff", "Sofőr helyszínen · kevés kiadható"),
                ("#7c72dc", "Nincs itt / pihenőn · sok kiadható"),
                ("#8b949e", "Nincs itt / pihenőn · kevés kiadható"),
            ]
        ]),

        html.Div(className="inbound-main-shell", children=[
            html.Div(id="cards-area", className="cards-area"),
            html.Aside(id="truck-sidebar", className="truck-sidebar"),
        ]),
        html.Div(id="error-area"),

        # ULD view — hidden unless flow-mode == "uld"
        html.Div(id="uld-area", className="uld-area", children=[
            html.Div(className="uld-header glass-panel", children=[
                html.Div(className="uld-header-top", children=[
                    html.Div(id="uld-stats-bar", className="uld-stats-bar"),
                    html.Div(className="uld-search-box", children=[
                        html.Textarea(
                            id="uld-search-input",
                            title="Tömeges keresés: egy ULD soronként",
                            placeholder="ULD szám vagy AWB keresése...",
                            rows=2,
                            spellCheck=False,
                            className="uld-search-input",
                        ),
                        html.Button("Mind kijelölése", className="uld-select-all-btn", title="Látható ULD-k kijelölése"),
                        html.Button("↺", className="uld-reset-btn", title="Szűrők törlése", id="uld-reset-btn"),
                    ]),
                ]),
                # Bulk-search summary (requested / found / missing) — under the search row
                html.Div(className="uld-search-underbar", children=[
                    html.Div(id="uld-search-summary", className="uld-search-summary-wrap"),
                    html.Button("+ ULD", id="uld-manual-open-btn", className="uld-manual-open-btn", title="Kézi ULD hozzáadása"),
                ]),
                html.Div(id="uld-selection-bar", className="uld-selection-bar", children=[
                    html.Div(className="uld-selection-main", children=[
                        html.Span("0", className="uld-selection-count"),
                        html.Span("ULD kijelölve", className="uld-selection-label"),
                    ]),
                    html.Div(id="uld-selection-targets", className="uld-selection-targets"),
                    html.Div(className="uld-selection-actions", children=[
                        html.Button(
                            "Nyomtatás",
                            className="uld-selection-btn uld-selection-stack-action uld-selection-stack-print-btn",
                            title="Kijelölt stackek nyomtatása",
                        ),
                        html.Button(
                            "Másolás",
                            className="uld-selection-btn uld-selection-stack-action uld-selection-stack-copy-btn",
                            title="Kijelölt stackek másolása",
                        ),
                        html.Button(
                            "Kész",
                            className="uld-selection-btn uld-selection-stack-action uld-selection-stack-prep-btn",
                            title="Kijelölt stackek készre jelölése",
                        ),
                        html.Button(
                            "Kiadás",
                            className="uld-selection-btn uld-selection-stack-action uld-selection-stack-dispatch-btn",
                            title="Kijelölt stackek kiadása",
                        ),
                        html.Button(
                            [html.Span("+", className="uld-newstack-plus"),
                             html.Span("Új stack", className="uld-newstack-label")],
                            className="uld-selection-btn uld-selection-newstack-btn",
                            title="Új üres stack létrehozása és a kijelölt ULD-k áthelyezése",
                        ),
                        html.Button("×", className="uld-selection-btn uld-selection-btn-clear uld-clear-selection-btn", title="Kijelölés törlése"),
                    ]),
                ]),
                html.Div(id="uld-filter-chips", className="uld-filter-chips"),
            ]),
            html.Div(className="uld-main", children=[
                # LEFT: stack panel (2-3 cards per row)
                html.Div(className="uld-stacks-panel glass-panel", children=[
                    html.Div(className="uld-stacks-hdr", children=[
                        html.H3("Stackek", className="uld-stacks-title"),
                        html.Div(className="uld-stacks-tools", children=[
                            html.Button("Mind nyitása", id="uld-expand-all-btn",
                                        className="uld-stack-tool-btn", disable_n_clicks=True,
                                        title="Minden stack nyitása / zárása"),
                            html.Button("Mind kijelölése", id="uld-select-all-stacks-btn",
                                        className="uld-stack-tool-btn", disable_n_clicks=True,
                                        title="Stackek kijelölése / törlése"),
                            html.Button("+ Új stack", id="uld-new-stack-btn", className="uld-new-stack-btn", title="Új stack létrehozása", disable_n_clicks=True),
                        ]),
                    ]),
                    html.Div(id="uld-stacks-grid", className="uld-stacks-grid"),
                ]),
                # RIGHT: ULD list (2 columns)
                html.Div(className="uld-list-panel glass-panel", children=[
                    html.Div(className="uld-list-hdr", children=[
                        html.H3("Aktív ULD-k", className="uld-list-title"),
                    ]),
                    html.Div(id="uld-pagination-top", className="uld-pagination-wrap uld-pagination-top"),
                    html.Div(id="uld-list", className="uld-list"),
                    html.Div(id="uld-pagination", className="uld-pagination-wrap"),
                ]),
            ]),
        ]),
    ]),
])

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

app.clientside_callback(
    """
    function(theme) {
        document.body.classList.remove('theme-light');
        return "dark";
    }
    """,
    Output("theme-applied", "data"),
    Input("theme-store", "data"),
)

app.clientside_callback(
    """
    function(overlayStyle) {
        var visible = !!(overlayStyle && overlayStyle.display !== 'none');
        if (!visible) {
            // Triggers all card opening animations via CSS .app-ready selector.
            document.body.classList.add('app-ready');
        }
        return null;
    }
    """,
    Output("loader-color-applied", "data"),
    Input("overlay", "style"),
)


app.clientside_callback(
    """
    function(inboundClicks, uldClicks, current) {
        var cb = dash_clientside.callback_context || {};
        var triggered = cb.triggered_id || (cb.triggered && cb.triggered[0] && cb.triggered[0].prop_id.split('.')[0]);
        if (triggered === "flow-uld")      return "uld";
        if (triggered === "flow-inbound")  return "inbound";
        return current || "inbound";
    }
    """,
    Output("flow-mode", "data"),
    Input("flow-inbound", "n_clicks"),
    Input("flow-uld", "n_clicks"),
    State("flow-mode", "data"),
    prevent_initial_call=True,
)


@app.callback(
    Output("view-mode", "data"),
    Input("kpi-page-btn", "n_clicks"),
    Input("flow-inbound", "n_clicks"),
    Input("flow-uld", "n_clicks"),
    Input("tv-mode-open-btn", "n_clicks"),
    Input("tv-exit-btn", "n_clicks"),
    State("view-mode", "data"),
    prevent_initial_call=True,
)
def toggle_view_mode(_kpi, _inbound, _uld, _tv_open, _tv_exit, current_mode):
    triggered = ctx.triggered_id
    if triggered == "tv-mode-open-btn":
        return "tv"
    if triggered == "tv-exit-btn":
        return "ops"
    if triggered in {"flow-inbound", "flow-uld"}:
        return "ops"
    if triggered == "kpi-page-btn":
        return "ops" if (current_mode or "ops") == "kpi" else "kpi"
    return current_mode or "ops"


@app.callback(
    Output("kpi-page-btn", "children"),
    Output("kpi-page-btn", "className"),
    Input("view-mode", "data"),
)
def update_kpi_page_button(view_mode):
    active = (view_mode or "ops") == "kpi"
    if active:
        return "Operatív", "kpi-page-btn kpi-page-btn-active"
    return "KPI", "kpi-page-btn"


app.clientside_callback(
    """
    function(mode) {
        mode = mode || 'ops';
        var isKpi = mode === 'kpi';
        var isTv  = mode === 'tv';
        var changed = window._viewAppliedMode !== mode;
        window._viewAppliedMode = mode;
        document.body.classList.toggle('view-kpi-mode', isKpi);
        document.body.classList.toggle('view-tv-mode',  isTv);
        if (changed) {
            // KPI transition
            cancelAnimationFrame(window._kpiViewFrame);
            clearTimeout(window._kpiViewTimer);
            document.body.classList.remove('view-kpi-switching');
            if (isKpi) {
                window._kpiViewFrame = requestAnimationFrame(function() {
                    document.body.classList.add('view-kpi-switching');
                    window._kpiViewTimer = setTimeout(function() {
                        document.body.classList.remove('view-kpi-switching');
                    }, 760);
                });
                clearTimeout(window._kpiEnterTimer);
                window._kpiEnterTimer = setTimeout(function() {
                    window._kpiSubSeen = window._kpiSubSeen || {};
                    var cur = document.body.classList.contains('kpi-sub-report') ? 'report' : 'tracking';
                    window._kpiSubSeen[cur] = true;
                    window.dispatchEvent(new CustomEvent('kpi-view-entered'));
                }, 90);
            }
            // TV mode enter/exit
            if (typeof window.__tvModeEnter === 'function') {
                if (isTv) window.__tvModeEnter();
                else       window.__tvModeExit();
            }
        }
        return mode;
    }
    """,
    Output("view-mode-applied", "data"),
    Input("view-mode", "data"),
)


app.clientside_callback(
    """
    function(view) {
        view = view || 'active';
        var dispatched = view === 'dispatched';
        var changed = window._uldViewApplied !== view;
        window._uldViewApplied = view;
        document.body.classList.toggle('uld-view-dispatched', dispatched);
        if (changed) {
            cancelAnimationFrame(window._uldViewFrame);
            clearTimeout(window._uldViewTimer);
            document.body.classList.remove('uld-view-switching');
            window._uldViewFrame = requestAnimationFrame(function() {
                document.body.classList.add('uld-view-switching');
                window._uldViewTimer = setTimeout(function() {
                    document.body.classList.remove('uld-view-switching');
                }, 620);
            });
        }
        return view;
    }
    """,
    Output("uld-view-applied", "data"),
    Input("uld-view-store", "data"),
    prevent_initial_call=False,
)


# KPI subpage switch (KPI Tracking ⇄ Riport) — fully clientside for instant feel,
# mirroring the flow-switch. Click → store; store → body class + button state +
# replay of the newly shown subpage's opening reveals.
app.clientside_callback(
    """
    function(nTrack, nReport) {
        var trg = '';
        try { trg = ((window.dash_clientside.callback_context.triggered || [])[0] || {}).prop_id || ''; }
        catch (e) {}
        if (trg.indexOf('kpi-subview-report-btn') === 0) return 'report';
        if (trg.indexOf('kpi-subview-tracking-btn') === 0) return 'tracking';
        return window.dash_clientside.no_update;
    }
    """,
    Output("kpi-subview-store", "data"),
    Input("kpi-subview-tracking-btn", "n_clicks"),
    Input("kpi-subview-report-btn", "n_clicks"),
    prevent_initial_call=True,
)


app.clientside_callback(
    """
    function(view) {
        view = (view === 'report') ? 'report' : 'tracking';
        var report = view === 'report';
        var changed = window._kpiSubApplied !== view;
        window._kpiSubApplied = view;
        document.body.classList.toggle('kpi-sub-report', report);
        document.body.classList.toggle('kpi-sub-tracking', !report);
        var tb = document.getElementById('kpi-subview-tracking-btn');
        var rb = document.getElementById('kpi-subview-report-btn');
        if (tb) {
            tb.classList.toggle('kpi-subview-btn-active', !report);
            tb.setAttribute('aria-selected', report ? 'false' : 'true');
        }
        if (rb) {
            rb.classList.toggle('kpi-subview-btn-active', report);
            rb.setAttribute('aria-selected', report ? 'true' : 'false');
        }
        var sw = document.querySelector('.kpi-subview-switch');
        if (sw) {
            sw.style.setProperty('--kpi-subview-index', report ? '1' : '0');
            sw.classList.remove('is-morphing');
            void sw.offsetWidth;
            sw.classList.add('is-morphing');
            clearTimeout(sw.__kpiSubMorphTimer);
            sw.__kpiSubMorphTimer = setTimeout(function () {
                sw.classList.remove('is-morphing');
            }, 420);
        }
        if (changed) {
            cancelAnimationFrame(window._kpiSubFrame);
            clearTimeout(window._kpiSubTimer);
            document.body.classList.remove('kpi-sub-switching');
            window._kpiSubFrame = requestAnimationFrame(function () {
                document.body.classList.add('kpi-sub-switching');
                window._kpiSubTimer = setTimeout(function () {
                    document.body.classList.remove('kpi-sub-switching');
                }, 760);
            });
            // The previous subpage may have been scrolled to its 2nd/3rd panel; the
            // new subpage has fewer/other panels, so reset to the top. Openings play
            // ONCE per subpage: the first time a subpage is shown, fire the full
            // 'kpi-view-entered' (snap reveals + chart draw-on); every later re-show
            // fires the lighter 'kpi-subview-shown' (snap/dots only — no replay).
            clearTimeout(window._kpiSubEnterTimer);
            window._kpiSubEnterTimer = setTimeout(function () {
                try { window.scrollTo({ top: 0, behavior: 'auto' }); } catch (e) {}
                window._kpiSubSeen = window._kpiSubSeen || {};
                if (!window._kpiSubSeen[view]) {
                    window._kpiSubSeen[view] = true;
                    window.dispatchEvent(new CustomEvent('kpi-view-entered'));
                } else {
                    window.dispatchEvent(new CustomEvent('kpi-subview-shown'));
                }
            }, 60);
        }
        return view;
    }
    """,
    Output("kpi-subview-applied", "data"),
    Input("kpi-subview-store", "data"),
    prevent_initial_call=False,
)


app.clientside_callback(
    """
    function(mode) {
        mode = mode || 'inbound';
        var changed = window._flowAppliedMode !== mode;
        window._flowAppliedMode = mode;
        document.body.classList.toggle('flow-outbound-mode', mode === 'outbound');
        document.body.classList.toggle('flow-uld-mode',      mode === 'uld');
        cancelAnimationFrame(window._flowSwitchFrame);
        clearTimeout(window._flowSwitchTimer);
        document.body.classList.remove('flow-switching');
        if (changed) {
            window._flowSwitchFrame = requestAnimationFrame(function() {
                document.body.classList.add('flow-switching');
                window._flowSwitchTimer = setTimeout(function() {
                    document.body.classList.remove('flow-switching');
                }, 900);
            });
        }
        var inbound  = document.getElementById('flow-inbound');
        var uldBtn   = document.getElementById('flow-uld');
        var outbound = document.getElementById('flow-outbound');
        if (inbound)  inbound.classList.toggle('flow-active',  mode === 'inbound');
        if (uldBtn)   uldBtn.classList.toggle('flow-active',   mode === 'uld');
        if (outbound) outbound.classList.toggle('flow-active', mode === 'outbound');
        var colors = mode === 'outbound' ? {
            'stat-btn-all':      null,
            'stat-btn-athu':     '#00d4aa',
            'stat-btn-drv':      '#4a9eff',
            'stat-btn-sched':    '#7c72dc',
            'stat-btn-ship':     '#8b949e',
            'stat-btn-betarolt': '#8b949e'
        } : {
            'stat-btn-all':      null,
            'stat-btn-athu':     '#ff6b35',
            'stat-btn-drv':      '#00d4aa',
            'stat-btn-sched':    '#7c72dc',
            'stat-btn-ship':     '#22c55e',
            'stat-btn-betarolt': '#f59e0b'
        };
        Object.entries(colors).forEach(function(entry) {
            var id = entry[0], color = entry[1];
            var el = document.getElementById(id);
            if (!el) return;
            if (color) {
                el.style.setProperty('--stat-color', color);
                el.style.setProperty('--stat-glow', color + '38');
            } else {
                el.style.removeProperty('--stat-color');
                el.style.removeProperty('--stat-glow');
            }
            var dot = el.querySelector('.stat-dot');
            var value = el.querySelector('.stat-value');
            if (dot) dot.style.background = color || 'var(--border)';
            if (value) value.style.color = color || '';
        });
        return mode;
    }
    """,
    Output("flow-mode-applied", "data"),
    Input("flow-mode", "data"),
)


@app.callback(
    Output("legend-bar", "children"),
    Input("flow-mode", "data"),
    Input("data-version", "data"),
)
def update_legend(flow_mode, _data_version):
    if flow_mode == "outbound":
        items = [
            ("#00d4aa", "Sofőr a helyszínen"),
            ("#4a9eff", "Kiadásra vár"),
            ("#7c72dc", "Pihenőn"),
            ("#8b949e", "Lezárt"),
        ]
    else:
        items = [
            ("#ef4444", "B2B · abszolút első prioritás"),
            ("#ff6b35", "Kategórián belül: AT/HU > TEMU > MD"),
            ("#00d4aa", "Sofőr helyszínen · sok kiadható"),
            ("#4a9eff", "Sofőr helyszínen · kevés kiadható"),
            ("#7c72dc", "Nincs itt / pihenőn · sok kiadható"),
            ("#8b949e", "Nincs itt / pihenőn · kevés kiadható"),
        ]
    legend = [
        html.Div(className="legend-item", children=[
            html.Span(className="legend-dot", style={"background": c}),
            html.Span(label),
        ])
        for c, label in items
    ]
    return legend


@app.callback(
    Output("truck-sidebar", "children"),
    Input("flow-mode", "data"),
    Input("data-version", "data"),
    Input("store-action", "data"),
    Input("priority-test-mode", "data"),
)
def update_truck_sidebar(flow_mode, _data_version, _store_action, priority_test_mode):
    if flow_mode != "inbound":
        return html.Span()
    state = data_cache.get_state()
    if not priority_test_mode and state.get("status") == "loading" and state.get("refresh_count", 0) == 0:
        return html.Span()
    df = _priority_test_df() if priority_test_mode else _apply_live_arrival_state(state.get("df"))
    if df is None or df.empty:
        return _truck_sidebar_items([], {})
    if "awb" in df.columns:
        stored_awbs = storage_manager.get_stored_awbs()
        df = df.copy()
        df["is_stored"] = df["awb"].astype(str).isin(stored_awbs)
    # Sidebar lists every arriving truck (ULD + PLT, plus en-route); colour map is
    # derived from the physically-present rows so a plate keeps the same hue as its
    # main card, and en-route-only plates fall back to a stable hashed colour.
    not_stored = df[~df["is_stored"]].copy() if "is_stored" in df.columns else df.copy()
    rows = not_stored.to_dict("records")
    plate_color_map = _truck_plate_color_map(_active_truck_palette_rows(df))
    return _truck_sidebar_items(rows, plate_color_map, allow_store=not bool(priority_test_mode))


# ---------------------------------------------------------------------------
# ULD callbacks
# ---------------------------------------------------------------------------

@app.callback(
    Output("uld-view-store", "data"),
    Output("uld-view-active-btn", "className"),
    Output("uld-view-dispatched-btn", "className"),
    Input("uld-view-active-btn", "n_clicks"),
    Input("uld-view-dispatched-btn", "n_clicks"),
    prevent_initial_call=False,
)
def set_uld_view(_active_clicks, _dispatched_clicks):
    view = "active"
    if ctx.triggered_id == "uld-view-dispatched-btn":
        view = "dispatched"
    return (
        view,
        "uld-view-btn" + (" uld-view-btn-active" if view == "active" else ""),
        "uld-view-btn" + (" uld-view-btn-active" if view == "dispatched" else ""),
    )

@app.callback(
    Output("uld-list",          "children"),
    Output("uld-stacks-grid",   "children"),
    Output("uld-stats-bar",     "children"),
    Output("uld-filter-chips",  "children"),
    Output("uld-pagination-top", "children"),
    Output("uld-pagination",    "children"),
    Output("uld-dispatched-sig", "data"),
    Input("poll",                     "n_intervals"),
    Input("uld-filter-store",         "data"),
    Input("uld-trigger-store",        "data"),
    Input("flow-mode",                "data"),
    Input("uld-view-store",           "data"),
    Input("uld-search-store",         "data"),
    Input("uld-prefix-filter-store",  "data"),
    Input("uld-gha-filter-store",     "data"),
    Input("uld-page-store",           "data"),
    Input("uld-list-dirty-store",     "data"),
    State("data-version",             "data"),
    State("uld-dispatched-sig",       "data"),
    prevent_initial_call=False,
)
def render_uld_view(_n, status_filter, _trigger, flow_mode, uld_view,
                    search_text, prefix_filter, gha_filter, page, _list_dirty,
                    current_data_version, current_dispatched_sig):
    _no = (no_update,) * 7
    if flow_mode != "uld":
        return _no

    state    = data_cache.get_state()
    triggered_ids = set()
    try:
        triggered_ids = {prop_id.split(".")[0] for prop_id in (ctx.triggered_prop_ids or {})}
    except Exception:
        if ctx.triggered_id:
            triggered_ids = {ctx.triggered_id}

    stack_triggered = "uld-trigger-store" in triggered_ids
    # A list-membership change forces the full render (list included) even though
    # the stack trigger usually fires alongside it.
    list_dirty = "uld-list-dirty-store" in triggered_ids
    poll_only = triggered_ids == {"poll"} or (not triggered_ids and ctx.triggered_id == "poll")

    if "flow-mode" in triggered_ids and not state.get("uld_refreshing"):
        threading.Thread(target=data_cache.refresh_uld_data, kwargs={"force": False}, daemon=True).start()
    data_version = state.get("data_version", 0)
    if poll_only and data_version == current_data_version:
        return _no

    # Display render: cached read avoids locking/reparsing the shared JSON on every
    # poll-driven re-render. Any write (local or remote) busts the cache, and the
    # caller already short-circuits unchanged polls above, so this is always fresh
    # enough for rendering. Mutations/validation elsewhere use authoritative reads.
    stacks   = uld_stack_manager.get_stacks_cached()
    all_uld  = _active_uld_records(state, stacks)
    active_stacks = _active_stacks(stacks)

    if (uld_view or "active") == "dispatched":
        uld_times_full = state.get("uld_times_full") or {}
        returned = _returned_uld_set(state)
        sig = _dispatched_signature(stacks, search_text, uld_times_full, returned)
        # If nothing the dispatched view shows changed AND we're not arriving from a
        # view switch, keep the existing DOM untouched — no re-render, no flicker.
        view_switched = "uld-view-store" in triggered_ids
        if not view_switched and sig == current_dispatched_sig:
            return _no
        dispatched_cards = _make_dispatched_stacks_section(stacks, all_uld, search_text)
        visible_dispatched = _strip_returned_ulds(
            _dispatched_stacks(stacks), returned, uld_times_full
        )
        dispatched_ulds = sum(len(stack.get("ulds", []) or []) for stack in visible_dispatched)
        stats = [
            html.Span(f"{len(visible_dispatched)} kiküldött stack", className="uld-stat-total"),
            html.Span(f"{dispatched_ulds} Db", className="uld-stat-ok"),
        ]
        return (
            [html.Div("A kiküldött stack-ek nem jelennek meg az aktív ULD listában.", className="uld-dispatched-list-note")],
            dispatched_cards,
            stats,
            [],
            None,
            None,
            sig,
        )

    # Stack-only re-render: trigger store fires after drag/drop operations.
    # The ULD list is expensive (25+ rows); skip it on stack-only events — unless a
    # list-membership change was signalled, in which case we fall through to the
    # full render so the new/removed ULD appears in the list immediately.
    if stack_triggered and not list_dirty:
        return (
            no_update,
            _make_stacks_section(active_stacks, all_uld, search_text or ""),
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
        )

    if not all_uld:
        refreshing  = state.get("uld_refreshing", state.get("refreshing"))
        # A ULD refresh has completed for this process once a timestamp exists.
        # After that, an empty list means genuinely "no active ULD" — NOT "still
        # loading" — so we must render the empty state instead of the skeleton.
        # Kicking a refresh on every empty poll (the old behaviour) re-read the
        # Excel endlessly and left the skeleton spinning forever; the periodic
        # worker and the flow-mode switch already keep ULD data fresh.
        ever_loaded = bool(state.get("uld_last_refresh"))
        if refreshing or not ever_loaded:
            if not refreshing and not ever_loaded:
                threading.Thread(target=data_cache.refresh_uld_data, kwargs={"force": False}, daemon=True).start()
            list_body = _make_uld_loading()
        else:
            list_body = _make_uld_empty()
        return (
            list_body,
            _make_stacks_section(active_stacks, all_uld, search_text or ""),
            _make_uld_stats(all_uld),
            [],
            None,
            None,
            no_update,
        )

    filtered, bulk_mode = _filter_uld_for_view(
        all_uld,
        status_filter or "all",
        prefix_filter or "",
        gha_filter or "",
        search_text or "",
    )

    # Pagination
    total = len(filtered)
    if bulk_mode:
        cur_page = 1
        page_items = filtered
        pagination = None
    else:
        # Newest áttárolt ULD-k a lista tetején.
        filtered = sorted(filtered, key=_uld_recency_key, reverse=True)
        total_pages = max(1, (total + _ULD_PAGE_SIZE - 1) // _ULD_PAGE_SIZE)
        cur_page = max(1, min(int(page or 1), total_pages))
        p_start = (cur_page - 1) * _ULD_PAGE_SIZE
        page_items = filtered[p_start: p_start + _ULD_PAGE_SIZE]
        pagination = _make_pagination(total, cur_page)

    gha_color_map = _compute_gha_color_map(all_uld)
    chips = _make_filter_chips(all_uld, status_filter or "all", prefix_filter or "", gha_filter or "", gha_color_map)

    return (
        _make_uld_list_section(page_items, active_stacks, gha_color_map, all_uld, preserve_order=bulk_mode, search_text=search_text or ""),
        _make_stacks_section(active_stacks, all_uld, search_text or ""),
        _make_uld_stats(filtered),
        chips,
        pagination,
        pagination,
        no_update,
    )


@app.callback(
    Output("uld-search-summary", "children"),
    Input("uld-search-store", "data"),
    Input("flow-mode", "data"),
    Input("uld-view-store", "data"),
    Input("uld-trigger-store", "data"),
    Input("data-version", "data"),
)
def render_uld_search_summary(search_text, flow_mode, uld_view, _trigger, _data_version):
    if flow_mode != "uld":
        return None
    stacks = uld_stack_manager.get_stacks_cached()
    state = data_cache.get_state()
    if (uld_view or "active") == "dispatched":
        return _dispatched_uld_search_summary(
            stacks,
            search_text,
            state.get("uld_times_full") or {},
            _returned_uld_set(state),
        )
    all_uld = _active_uld_records(state, stacks)
    return _uld_search_summary(all_uld, search_text, stacks=stacks,
                               uld_times_full=state.get("uld_times_full") or {},
                               returned=_returned_uld_set(state),
                               archive=state.get("uld_archive") or [])


# "Régóta nálunk lévő ULD-k" figyelmeztetés removed by user request.
# Hidden stub elements remain in the layout so other callbacks/JS don't 404 on them.


@app.callback(
    Output("kézi-refresh-store", "data"),
    Input("refresh-btn", "n_clicks"),
    State("flow-mode", "data"),
    prevent_initial_call=True,
)
def kézi_refresh(n_clicks, flow_mode):
    data_cache.mark_refresh_started()
    data_cache.trigger_refresh()
    return n_clicks


# Visual feedback on the refresh button — no server roundtrip needed
app.clientside_callback(
    """
    function(n) {
        if (!n) return '⟳ Frissítés';
        var btn = document.getElementById('refresh-btn');
        var overlay = document.getElementById('overlay');
        if (overlay) {
            overlay.className = 'loading-overlay loading-refresh';
            overlay.style.display = 'flex';
            overlay.innerHTML =
                '<div class="l-topbar"><div class="l-topbar-fill"></div></div>' +
                '<div class="l-pill"><span class="l-spin"></span><span class="l-pill-text">Adatok újratöltése</span></div>';
        }
        if (btn) {
            btn.disabled = true;
            btn.style.opacity = '0.55';
        }
        setTimeout(function() {
            if (btn) {
                btn.disabled = false;
                btn.style.opacity = '';
                btn.textContent = '⟳ Frissítés';
            }
        }, 3000);
        return 'Indítás...';
    }
    """,
    Output("refresh-btn", "children"),
    Input("refresh-btn", "n_clicks"),
    prevent_initial_call=True,
)


@app.callback(
    Output("scount-all",      "children"),
    Output("scount-athu",     "children"),
    Output("scount-drv",      "children"),
    Output("scount-sched",    "children"),
    Output("scount-ship",     "children"),
    Output("scount-betarolt", "children"),
    Output("slabel-all",      "children"),
    Output("slabel-athu",     "children"),
    Output("slabel-drv",      "children"),
    Output("slabel-sched",    "children"),
    Output("slabel-ship",     "children"),
    Output("slabel-betarolt", "children"),
    Input("poll",         "n_intervals"),
    Input("store-action", "data"),
    Input("flow-mode",    "data"),
    Input("priority-test-mode", "data"),
    State("data-version", "data"),
)
def update_stat_counts(_poll, _store, flow_mode, priority_test_mode, current_data_version):
    flow_mode = flow_mode or "inbound"
    state = data_cache.get_state()
    data_version = state.get("data_version", 0)
    try:
        triggered_ids = {prop_id.split(".")[0] for prop_id in (ctx.triggered_prop_ids or {})}
    except Exception:
        triggered_ids = {ctx.triggered_id} if ctx.triggered_id else set()
    poll_only = triggered_ids == {"poll"} or (not triggered_ids and ctx.triggered_id == "poll")
    if poll_only and data_version == current_data_version and not _has_due_arrival_transition(state.get("df")):
        return (no_update,) * 12

    if flow_mode == "uld":
        return (no_update,) * 12

    if flow_mode == "outbound":
        cards = state.get("outbound_cards") or []
        onsite_cards = [c for c in cards if c.get("active", 0) > 0 and c.get("checkin") and not c.get("in_rest")]
        ready_cards = [c for c in cards if c.get("active", 0) > 0 and c.get("ready", 0) > 0 and not c.get("checkin") and not c.get("in_rest")]
        rest_cards = [c for c in cards if c.get("in_rest")]
        waiting_cards = [c for c in cards if c.get("active", 0) > 0 and not c.get("checkin") and c.get("ready", 0) == 0 and not c.get("in_rest")]
        issued_cards = [c for c in cards if c.get("active", 0) == 0]
        return (
            str(len(cards)),
            str(len(onsite_cards)),
            str(len(ready_cards)),
            str(len(rest_cards)),
            str(len(waiting_cards)),
            str(len(issued_cards)),
            "Összes",
            "Sofőr itt",
            "Kiadásra vár",
            "Pihenőn",
            "Várakozik",
            "Lezárt",
        )

    count_state = (
        {"df": _priority_test_df(), "priority_test_mode": True}
        if priority_test_mode else state
    )
    counts = _inbound_stat_counts(count_state)
    if counts["all"] == "–":
        return "–", "–", "–", "–", "–", "–", "Aktív tétel", "AT/HU", "Sofőr helyszínen", "Nincs itt / pihenőn", "Kiadható", "Betárolt tételek"
    return (
        counts["all"],
        counts["at_hu"],
        counts["driver"],
        counts["scheduled"],
        counts["shippable"],
        counts["betarolt"],
        "Aktív tétel",
        "AT/HU",
        "Sofőr helyszínen",
        "Nincs itt / pihenőn",
        "Kiadható",
        "Betárolt tételek",
    )


app.clientside_callback(
    """
    function(allClicks, atHuClicks, driverClicks, scheduledClicks, shippableClicks, storedClicks, flowMode) {
        var cb = dash_clientside.callback_context || {};
        var triggered = cb.triggered_id || (cb.triggered && cb.triggered[0] && cb.triggered[0].prop_id.split('.')[0]);
        if (!triggered) return window.dash_clientside.no_update;
        if (triggered === "flow-mode") return "all";
        var map = {
            "stat-btn-all":      "all",
            "stat-btn-athu":     "at_hu",
            "stat-btn-drv":      "driver",
            "stat-btn-sched":    "scheduled",
            "stat-btn-ship":     "shippable",
            "stat-btn-betarolt": "betarolt"
        };
        return map[triggered] || "all";
    }
    """,
    Output("active-filter", "data"),
    Input("stat-btn-all",      "n_clicks"),
    Input("stat-btn-athu",     "n_clicks"),
    Input("stat-btn-drv",      "n_clicks"),
    Input("stat-btn-sched",    "n_clicks"),
    Input("stat-btn-ship",     "n_clicks"),
    Input("stat-btn-betarolt", "n_clicks"),
    Input("flow-mode",         "data"),
    prevent_initial_call=True,
)


app.clientside_callback(
    """
    function(f) {
        f = f || "all";
        var map = {
            "all":       "stat-btn-all",
            "at_hu":     "stat-btn-athu",
            "driver":    "stat-btn-drv",
            "scheduled": "stat-btn-sched",
            "shippable": "stat-btn-ship",
            "betarolt":  "stat-btn-betarolt"
        };
        Object.values(map).forEach(function(id) {
            var el = document.getElementById(id);
            if (el) el.classList.remove("stat-active");
        });
        var a = document.getElementById(map[f] || map["all"]);
        if (a) a.classList.add("stat-active");
        window._flowActiveFilter = f;
        if (typeof window.applyFlowFilter === "function") {
            window.applyFlowFilter(f);
        }
        return f;
    }
    """,
    Output("active-filter-applied", "data"),
    Input("active-filter", "data"),
)


@app.callback(
    Output("overlay",             "className"),
    Output("overlay",             "style"),
    Output("overlay-state",       "data"),
    Input("loading-poll",         "n_intervals"),
    Input("kézi-refresh-store", "data"),
    Input("store-action",         "data"),
    Input("priority-test-mode",   "data"),
    State("overlay-state",        "data"),
)
def update_overlay(_poll, _kézi, _store, priority_test_mode, current_ov_state):
    state = data_cache.get_state()
    status       = state["status"]
    refresh_count = state.get("refresh_count", 0)
    refreshing   = state.get("refreshing", False)

    if priority_test_mode:
        # Test data is deliberately independent from the source read. Operators
        # can open it during a cold start or an empty operational shift.
        new_state = "test"
        new_cls = no_update
        new_style = {"display": "none"}
    elif status == "loading" and refresh_count == 0:
        # First load — compact progress panel over the existing UI.
        new_state = "first"
        new_cls   = "loading-overlay loading-first"
        new_style = {"display": "flex"}
    elif refreshing and refresh_count >= 1:
        # Background refresh (kézi or timer) — non-blocking pill + topbar
        new_state = "refresh"
        new_cls   = "loading-overlay loading-refresh"
        new_style = {"display": "flex"}
    else:
        # Kész / idle
        new_state = "done"
        new_cls   = no_update
        new_style = {"display": "none"}

    if new_state == current_ov_state:
        # Even when the overlay state has not changed, keep re-asserting the hidden
        # style for "done". A manual refresh shows the overlay CLIENT-side (instant
        # feedback, see the refresh-btn clientside callback) WITHOUT touching
        # overlay-state; if the background refresh finishes before the next poll
        # observes refreshing=True, the state never leaves "done" and that
        # client-shown overlay would otherwise stay stuck on screen. Re-sending
        # display:none every poll guarantees it gets hidden within one tick.
        if new_state in {"done", "test"}:
            return no_update, {"display": "none"}, no_update
        return no_update, no_update, no_update
    return new_cls, new_style, new_state


@app.callback(
    Output("loading-stage", "children"),
    Output("loading-percent", "children"),
    Output("loading-fill", "style"),
    Output("loading-track", "aria-valuenow"),
    Input("loading-poll", "n_intervals"),
)
def update_loading_progress(_poll):
    state = data_cache.get_state()
    update = updater.get_status()
    if update.get("phase") in {"checking", "downloading", "verifying", "installing"}:
        progress = max(0, min(99, int(update.get("progress") or 1)))
        stage = str(update.get("message") or "GitHub Releases ellenőrzése")
    else:
        progress = max(0, min(99, int(state.get("load_progress") or 1)))
        stage = str(state.get("load_stage") or "Betöltés indítása")
    # Keep the existing four-output callback contract; the stage line carries
    # the update-specific status while the compact heading remains stable.
    return stage, f"{progress}%", {"width": f"{progress}%"}, str(progress)


@app.callback(
    Output("cards-area",            "children"),
    Output("error-area",            "children"),
    Output("last-refresh-ts",       "data"),
    Output("data-version",          "data"),
    Input("poll",                   "n_intervals"),
    Input("flow-mode",              "data"),
    Input("kézi-refresh-store",   "data"),
    Input("store-action",           "data"),
    Input("note-action",            "data"),
    Input("priority-test-mode",     "data"),
    State("data-version",           "data"),
)
def update_dashboard(_poll, flow_mode, _kézi, _store, _note, priority_test_mode, current_data_version):
    _last_poll_time[0] = time.time()
    flow_mode = flow_mode or "inbound"
    state = data_cache.get_state()
    data_version = state.get("data_version", 0)
    try:
        triggered_ids = {prop_id.split(".")[0] for prop_id in (ctx.triggered_prop_ids or {})}
    except Exception:
        triggered_ids = {ctx.triggered_id} if ctx.triggered_id else set()
    poll_only = triggered_ids == {"poll"} or (not triggered_ids and ctx.triggered_id == "poll")
    test_active = bool(priority_test_mode) and flow_mode == "inbound"

    if not test_active and state["status"] == "loading" and state.get("refresh_count", 0) == 0:
        return html.Span(), html.Span(), None, current_data_version

    ts = state["last_refresh"].isoformat() if state["last_refresh"] else None
    if poll_only and data_version == current_data_version and not _has_due_arrival_transition(state.get("df")):
        return no_update, no_update, no_update, no_update

    if flow_mode == "uld":
        return no_update, no_update, ts, data_version

    if flow_mode == "outbound":
        err_msg = (state.get("outbound_errors") or {}).get("error")
        if err_msg:
            err = html.Div(className="error-toast", children=err_msg)
            return html.Div("Nincs megjeleníthető outbound rakodás.", className="empty-state"), \
                   err, ts, data_version

        cards_data = list(state.get("outbound_cards") or [])
        if not cards_data:
            return html.Div("Nincs megjeleníthető outbound rakodás.", className="empty-state"), \
                   html.Span(), ts, data_version

        cards = [_make_outbound_card(r, filter_tokens=_outbound_filter_tokens(r)) for r in cards_data]
        return _cards_grid(cards, "cards-grid outbound-grid", "Nincs rakodás ebben a kategóriában."), html.Span(), ts, data_version

    if not test_active and state["status"] == "error":
        err = html.Div(className="error-toast",
                       children=state["errors"].get("error", "Ismeretlen hiba"))
        return html.Div("Nincs megjeleníthető adat.", className="empty-state"), \
               err, None, data_version

    df = _priority_test_df() if test_active else _apply_live_arrival_state(state["df"])
    if df is None or df.empty:
        return html.Div("Nincs aktív tétel.", className="empty-state"), \
               html.Span(), None, data_version

    live_awbs = set(df["awb"].tolist())
    today = datetime.now().date()

    active_df = df[~df["is_stored"]].copy() if "is_stored" in df.columns else df.copy()
    stored_df = df[df["is_stored"]].copy() if "is_stored" in df.columns else df.iloc[0:0].copy()
    if not stored_df.empty:
        stored_df["_am_sort"] = stored_df["am_time"].apply(lambda v: v.timestamp() if isinstance(v, datetime) else float("inf"))
        stored_df = stored_df.sort_values("_am_sort", ascending=True).drop(columns=["_am_sort"])

    # Snapshot items are stored but no longer present in the live sheet; background refresh handles cleanup.
    snapshots = [] if test_active else [
        r for r in storage_manager.get_snapshot_rows(live_awbs)
        if isinstance(r.get("am_time"), datetime) and r["am_time"].date() == today
    ]
    snapshots.sort(key=lambda r: r.get("am_time") if isinstance(r.get("am_time"), datetime) else datetime.max)

    all_rows = (
        active_df.to_dict("records")
        + stored_df.to_dict("records")
        + snapshots
    )
    glabs_color_map = _make_id_color_map([r.get("glabs_id") for r in all_rows])
    plate_color_map = _truck_plate_color_map(_active_truck_palette_rows(df))

    cards = [
        _make_card(r, filter_tokens=_inbound_filter_tokens(r),
                   glabs_color_map=glabs_color_map, plate_color_map=plate_color_map,
                   test_mode=test_active)
        for r in active_df.to_dict("records")
    ]
    cards += [
        _make_card(r, betarolt_view=True, can_unstore=True,
                   filter_tokens=_inbound_filter_tokens(r, True),
                   glabs_color_map=glabs_color_map, plate_color_map=plate_color_map)
        for r in stored_df.to_dict("records")
    ]
    cards += [
        _make_card(r, betarolt_view=True, can_unstore=False,
                   filter_tokens=_inbound_filter_tokens(r, True),
                   glabs_color_map=glabs_color_map, plate_color_map=plate_color_map)
        for r in snapshots
    ]

    if not cards:
        return html.Div("Nincs aktív tétel.", className="empty-state"), html.Span(), ts, data_version
    grid = _cards_grid(cards, "cards-grid", "Nincs tétel ebben a kategóriában.")
    if test_active:
        grid = html.Div(className="priority-test-view", children=[
            html.Div(className="priority-test-banner", role="status", children=[
                html.Strong("TESZTNÉZET"),
                html.Span("Szintetikus prioritási tételek · kilépés: T"),
            ]),
            grid,
        ])
    return grid, html.Span(), ts, data_version


# Open/close is clientside so the overlay reacts instantly (no server round-trip).
app.clientside_callback(
    """
    function(openN, closeN) {
        var t = (window.dash_clientside.callback_context.triggered[0] || {}).prop_id || "";
        if (t.indexOf("summary-close") === 0) return {display: "none"};
        return {display: "flex"};
    }
    """,
    Output("summary-overlay", "style"),
    Input("summary-btn", "n_clicks"),
    Input("summary-close", "n_clicks"),
    prevent_initial_call=True,
)


@app.callback(
    Output("summary-body", "children"),
    Input("summary-btn", "n_clicks"),
    State("priority-test-mode", "data"),
    prevent_initial_call=True,
)
def fill_summary(_open_clicks, priority_test_mode):
    state = data_cache.get_state()
    df = _priority_test_df() if priority_test_mode else _apply_live_arrival_state(state.get("df"))
    return _build_summary_table(df)


@app.callback(
    Output("refresh-info", "children"),
    Input("tick", "n_intervals"),
    Input("last-refresh-ts", "data"),
)
def update_countdown(_tick, ts):
    source_badge = _ecomm_source_badge()
    pallets_badge = _pallets_source_badge()
    stall_badge = _sync_stall_badge()
    if not ts:
        state = data_cache.get_state()
        if state.get("last_refresh"):
            ts = state["last_refresh"].isoformat()
        elif state["status"] == "loading":
            return html.Span("Betöltés folyamatban...", className="last-refresh-text")
        else:
            return [c for c in [
                html.Span("Frissítésre vár", className="last-refresh-text"),
                stall_badge,
                source_badge,
                pallets_badge,
                html.Span("Következő: hamarosan", className="countdown-text"),
            ] if c is not None]
    try:
        last = datetime.fromisoformat(ts)
        elapsed = (datetime.now() - last).total_seconds()
        remaining = max(0, REFRESH_INTERVAL_MS / 1000 - elapsed)
        m, s = int(remaining // 60), int(remaining % 60)
        return [c for c in [
            html.Span(f"Frissítve: {last.strftime('%H:%M:%S')}",
                      className="last-refresh-text"),
            stall_badge,
            source_badge,
            pallets_badge,
            html.Span(f"Következő: {m}:{s:02d}", className="countdown-text"),
        ] if c is not None]
    except Exception:
        return [c for c in [stall_badge, source_badge, pallets_badge] if c is not None]


# KPI card values — separate callback so the card DOM is never replaced
# (preserves kpi-bec-pinned CSS class across data refreshes)
@app.callback(
    Output("kpi-muszak-mode-store", "data"),
    Input("kpi-mode-inbound", "n_clicks"),
    Input("kpi-mode-outbound", "n_clicks"),
    Input("flow-mode", "data"),
    State("kpi-muszak-mode-store", "data"),
    prevent_initial_call=True,
)
def update_kpi_shift_mode(_inbound_clicks, _outbound_clicks, flow_mode, current_mode):
    triggered = ctx.triggered_id
    if triggered == "kpi-mode-outbound":
        return "outbound"
    if triggered == "kpi-mode-inbound":
        return "inbound"
    if triggered == "flow-mode" and flow_mode in ("inbound", "outbound"):
        return flow_mode
    return current_mode or "inbound"


@app.callback(
    Output("kpi-muszak-label", "children"),
    Output("kpi-muszak-value", "children"),
    Output("kpi-muszak-sub",   "children"),
    Output("kpi-bec-total",    "children"),
    Output("kpi-bec-felveve",     "children"),
    Output("kpi-bec-ertesito",    "children"),
    Output("kpi-bec-megerkezett", "children"),
    Output("kpi-bec-szemles",     "children"),
    Output("kpi-bec-mini-felveve",     "children"),
    Output("kpi-bec-mini-ertesito",    "children"),
    Output("kpi-bec-mini-megerkezett", "children"),
    Output("kpi-bec-mini-szemles",     "children"),
    Output("kpi-bec-mini-felveve",     "title"),
    Output("kpi-bec-mini-ertesito",    "title"),
    Output("kpi-bec-mini-megerkezett", "title"),
    Output("kpi-bec-mini-szemles",     "title"),
    Output("kpi-chart-felveve-segment",     "style"),
    Output("kpi-chart-ertesito-segment",    "style"),
    Output("kpi-chart-megerkezett-segment", "style"),
    Output("kpi-chart-szemles-segment",     "style"),
    Output("kpi-chart-felveve-segment",     "title"),
    Output("kpi-chart-ertesito-segment",    "title"),
    Output("kpi-chart-megerkezett-segment", "title"),
    Output("kpi-chart-szemles-segment",     "title"),
    Output("kpi-chart-felveve-segment",     "data-tooltip"),
    Output("kpi-chart-ertesito-segment",    "data-tooltip"),
    Output("kpi-chart-megerkezett-segment", "data-tooltip"),
    Output("kpi-chart-szemles-segment",     "data-tooltip"),
    Output("kpi-bec-felveve-uld",     "children"),
    Output("kpi-bec-felveve-plt",     "children"),
    Output("kpi-bec-ertesito-uld",    "children"),
    Output("kpi-bec-ertesito-plt",    "children"),
    Output("kpi-bec-megerkezett-uld", "children"),
    Output("kpi-bec-megerkezett-plt", "children"),
    Output("kpi-bec-szemles-uld",     "children"),
    Output("kpi-bec-szemles-plt",     "children"),
    Output("kpi-muszak-hourly-store", "data"),
    Output("kpi-muszak-copy", "data-copy-text"),
    Output("kpi-bec-copy", "data-copy-text"),
    Input("poll",                 "n_intervals"),
    Input("kézi-refresh-store", "data"),
    Input("kpi-muszak-mode-store", "data"),
    Input("flow-mode",            "data"),
    State("data-version",         "data"),
)
def update_kpi_cards(_poll, _kézi, shift_mode, flow_mode, current_data_version):
    if (flow_mode or "inbound") == "uld":
        return (no_update,) * 39
    state = data_cache.get_state()
    data_version = state.get("data_version", 0)
    try:
        triggered_ids = {prop_id.split(".")[0] for prop_id in (ctx.triggered_prop_ids or {})}
    except Exception:
        triggered_ids = {ctx.triggered_id} if ctx.triggered_id else set()
    poll_only = triggered_ids == {"poll"} or (not triggered_ids and ctx.triggered_id == "poll")
    if poll_only and data_version == current_data_version and not _has_due_arrival_transition(state.get("df")):
        return (no_update,) * 39
    kpi = state.get("kpi", {})
    if "flow-mode" in triggered_ids and flow_mode in ("inbound", "outbound"):
        shift_mode = flow_mode
    shift_mode = "outbound" if shift_mode == "outbound" else "inbound"
    shift_prefix = "outbound_" if shift_mode == "outbound" else ""
    shift_name_key = f"{shift_prefix}shift_name"
    shift_kg_key = f"{shift_prefix}shift_kg"
    shift_range_key = f"{shift_prefix}shift_range"
    shift_hourly_key = f"{shift_prefix}shift_hourly"
    shift_prev_name_key = f"{shift_prefix}shift_prev_name"
    shift_prev_range_key = f"{shift_prefix}shift_prev_range"
    shift_prev_hourly_key = f"{shift_prefix}shift_prev_hourly"
    bec_felveve     = float(kpi.get("bec_felveve")     or 0)
    bec_ertesito    = float(kpi.get("bec_ertesito")    or 0)
    bec_megerkezett = float(kpi.get("bec_megerkezett") or 0)
    bec_szemles     = float(kpi.get("bec_szemles")     or 0)
    values = [bec_felveve, bec_ertesito, bec_megerkezett, bec_szemles]
    labels = ["Felvéve", "Értesítő", "Megérkezett", "Szemlés"]
    total = sum(values)
    if total > 0:
        raw_parts = [v / total * 100 for v in values]
        visible_total = sum(max(4, p) if v > 0 else 0 for v, p in zip(values, raw_parts))
        widths = [
            (max(4, p) / visible_total * 100) if v > 0 else 0
            for v, p in zip(values, raw_parts)
        ]
    else:
        widths = [0, 0, 0, 0]
    chart_styles = [
        {"width": f"{w:.2f}%"} if v > 0 else {"width": "0%", "display": "none"}
        for v, w in zip(values, widths)
    ]
    split_raw = [
        (kpi.get("bec_felveve_uld"),     kpi.get("bec_felveve_plt")),
        (kpi.get("bec_ertesito_uld"),    kpi.get("bec_ertesito_plt")),
        (kpi.get("bec_megerkezett_uld"), kpi.get("bec_megerkezett_plt")),
        (kpi.get("bec_szemles_uld"),     kpi.get("bec_szemles_plt")),
    ]
    tooltips = [
        f"{lbl}: {_fmt_kg(v)} ({(v / total * 100 if total else 0):.0f}%) | ULD {_fmt_kg(uld)} / PLT {_fmt_kg(plt)}"
        for lbl, v, (uld, plt) in zip(labels, values, split_raw)
    ]
    split = [
        value
        for uld, plt in split_raw
        for value in (_fmt_kg_num(uld), _fmt_kg_num(plt))
    ]
    current_hours = list(kpi.get(shift_hourly_key) or [])
    prev_hours = list(kpi.get(shift_prev_hourly_key) or [])
    current_kg = _numeric_payload_value(kpi.get(shift_kg_key), None)
    prev_kg = _numeric_payload_value(kpi.get(f"{shift_prefix}shift_prev_kg"), None)
    if current_kg is None:
        current_kg = _sum_shift_hours(current_hours)
    if prev_kg is None:
        prev_kg = _sum_shift_hours(prev_hours)
    return (
        kpi.get(shift_name_key,  "Műszak"),
        _fmt_kg(current_kg),
        kpi.get(shift_range_key, ""),
        _fmt_kg(kpi.get("bec_total")),
        _fmt_kg(bec_felveve),
        _fmt_kg(bec_ertesito),
        _fmt_kg(bec_megerkezett),
        _fmt_kg(bec_szemles),
        *[_fmt_tonnes(v) for v in values],
        *tooltips,
        *chart_styles,
        *tooltips,
        *tooltips,
        *split,
        {
            "mode": shift_mode,
            "current": {
                "name":  kpi.get(shift_name_key, "Műszak"),
                "range": kpi.get(shift_range_key, ""),
                "kg":    current_kg,
                "hours": current_hours,
            },
            "prev": {
                "name":  kpi.get(shift_prev_name_key, "Előző műszak"),
                "range": kpi.get(shift_prev_range_key, ""),
                "kg":    prev_kg,
                "hours": prev_hours,
            },
        },
        _kpi_shift_copy_text(kpi, shift_mode),
        _kpi_bec_copy_text(kpi),
    )


@app.callback(
    Output("header-saturation-indicator", "style"),
    Output("header-saturation-indicator", "title"),
    Output("header-saturation-indicator", "data-sat"),
    Input("poll", "n_intervals"),
    Input("kézi-refresh-store", "data"),
    State("data-version", "data"),
)
def update_header_saturation(_poll, _kézi, current_data_version):
    state = data_cache.get_state()
    data_version = state.get("data_version", 0)
    try:
        triggered_ids = {prop_id.split(".")[0] for prop_id in (ctx.triggered_prop_ids or {})}
    except Exception:
        triggered_ids = {ctx.triggered_id} if ctx.triggered_id else set()
    poll_only = triggered_ids == {"poll"} or (not triggered_ids and ctx.triggered_id == "poll")
    if poll_only and data_version == current_data_version:
        return no_update, no_update, no_update
    return _header_saturation_style_and_title(state.get("kpi", {}))


@app.callback(
    Output("kpi-bec-prefixes-store", "data"),
    Input("poll", "n_intervals"),
    Input("kézi-refresh-store", "data"),
    State("data-version", "data"),
)
def update_bec_prefixes(_poll, _kézi, current_data_version):
    state = data_cache.get_state()
    data_version = state.get("data_version", 0)
    try:
        triggered_ids = {prop_id.split(".")[0] for prop_id in (ctx.triggered_prop_ids or {})}
    except Exception:
        triggered_ids = {ctx.triggered_id} if ctx.triggered_id else set()
    poll_only = triggered_ids == {"poll"} or (not triggered_ids and ctx.triggered_id == "poll")
    if poll_only and data_version == current_data_version:
        return no_update
    return (state.get("kpi") or {}).get("bec_prefixes") or {}


# Push the prefix breakdown to a window global so the Ctrl+click prefix inspector
# (assets/kpi_prefix_panel.js) can render instantly without a server roundtrip.
app.clientside_callback(
    "function(d){ window.__becPrefixes = d || {}; return ''; }",
    Output("kpi-bec-prefixes-sink", "data"),
    Input("kpi-bec-prefixes-store", "data"),
)


@app.callback(
    Output("kpi-page-chart-store", "data"),
    Input("poll", "n_intervals"),
    Input("kézi-refresh-store", "data"),
    Input("view-mode", "data"),
    State("data-version", "data"),
)
def update_kpi_page_charts(_poll, _kézi, view_mode, current_data_version):
    if (view_mode or "ops") != "kpi":
        return no_update
    state = data_cache.get_state()
    data_version = state.get("data_version", 0)
    try:
        triggered_ids = {prop_id.split(".")[0] for prop_id in (ctx.triggered_prop_ids or {})}
    except Exception:
        triggered_ids = {ctx.triggered_id} if ctx.triggered_id else set()
    poll_only = triggered_ids == {"poll"} or (not triggered_ids and ctx.triggered_id == "poll")
    if poll_only and data_version == current_data_version:
        return no_update
    return _kpi_page_payload(state.get("kpi", {}), state.get("last_refresh"))


app.clientside_callback(
    """
    function(payload) {
        if (window.__renderKpiPageCharts) {
            window.__renderKpiPageCharts(payload);
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("kpi-page-chart-dummy", "data"),
    Input("kpi-page-chart-store", "data"),
    prevent_initial_call=False,
)


# Click-to-pin toggle for the beérkezhető card — purely clientside, no server roundtrip
app.clientside_callback(
    """
    function(n) {
        if (!n) return window.dash_clientside.no_update;
        var wrapper = document.getElementById('kpi-bec-wrapper');
        if (wrapper) {
            wrapper.classList.toggle('kpi-bec-pinned');
            if (wrapper.classList.contains('kpi-bec-pinned')) {
                wrapper.classList.remove('kpi-bec-just-pinned');
                void wrapper.offsetWidth;
                wrapper.classList.add('kpi-bec-just-pinned');
                setTimeout(function() { wrapper.classList.remove('kpi-bec-just-pinned'); }, 900);
            }
        }
        return n;
    }
    """,
    Output("kpi-bec-pin-store", "data"),
    Input("kpi-bec-card", "n_clicks"),
    prevent_initial_call=True,
)


# Click-to-pin toggle for the műszak card — mirrors the beérkezhető pin behaviour.
app.clientside_callback(
    """
    function(n) {
        if (!n) return window.dash_clientside.no_update;
        var wrapper = document.getElementById('kpi-muszak-wrapper');
        if (wrapper) {
            wrapper.classList.toggle('kpi-muszak-pinned');
            if (wrapper.classList.contains('kpi-muszak-pinned')) {
                wrapper.classList.remove('kpi-muszak-just-pinned');
                void wrapper.offsetWidth;
                wrapper.classList.add('kpi-muszak-just-pinned');
                setTimeout(function() { wrapper.classList.remove('kpi-muszak-just-pinned'); }, 900);
            }
        }
        return n;
    }
    """,
    Output("kpi-muszak-pin-store", "data"),
    Input("kpi-muszak-card", "n_clicks"),
    prevent_initial_call=True,
)


# Render the hourly shift chart (SVG) from the store payload. Built in JS so the
# smooth path-draw + area gradient animate; data only changes on a data refresh.
app.clientside_callback(
    """
    function(payload) {
        if (window.__renderShiftHourlyChart) {
            window.__renderShiftHourlyChart(payload);
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("kpi-muszak-chart-dummy", "data"),
    Input("kpi-muszak-hourly-store", "data"),
    prevent_initial_call=False,
)


app.clientside_callback(
    """
    function(n) {
        if (!window._flowActivityHooked) {
            window._flowLastActivity = Date.now();
            var mark = function() { window._flowLastActivity = Date.now(); };
            ['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart', 'scroll'].forEach(function(evt) {
                window.addEventListener(evt, mark, {passive: true});
            });
            window._flowActivityHooked = true;
        }
        return window._flowLastActivity || Date.now();
    }
    """,
    Output("user-activity", "data"),
    Input("activity-tick", "n_intervals"),
)


@app.callback(
    Output("user-activity-applied", "data"),
    Input("user-activity", "data"),
)
def update_user_activity(ts):
    if ts:
        _last_user_activity_time[0] = time.time()
    return ts or no_update


@app.callback(
    Output("store-action", "data"),
    Input({"type": "store-btn", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def handle_store_click(n_clicks_list):
    triggered = ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        return no_update
    # Guard: n_clicks=0 means a freshly-rendered component, not a real click.
    # ctx.triggered[0]["value"] is the actual n_clicks of the button that fired.
    triggered_value = (ctx.triggered or [{}])[0].get("value")
    if not triggered_value:
        return no_update
    state = data_cache.get_state()
    df = _apply_live_arrival_state(state.get("df"))

    awb = triggered.get("index", "")
    if not awb:
        return no_update
    row = {}
    if df is not None and not df.empty and "awb" in df.columns:
        rows = df[df["awb"] == awb]
        if not rows.empty:
            row = rows.iloc[0].to_dict()
    is_now_stored, stored_awbs = storage_manager.toggle_stored(awb, row)
    data_cache.update_stored_flags(stored_awbs)
    activity_log.log_event("store_toggle", {"awb": awb, "stored": is_now_stored})
    return {"awb": awb, "stored": is_now_stored, "ts": time.time()}


@app.callback(
    Output("store-action", "data", allow_duplicate=True),
    Input({"type": "truck-store-btn", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def handle_truck_store_click(n_clicks_list):
    triggered = ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        return no_update
    triggered_value = (ctx.triggered or [{}])[0].get("value")
    if not triggered_value:
        return no_update
    raw_index = str(triggered.get("index", "") or "").strip()
    plate_key = _plate_key(raw_index.split("|", 1)[0])
    turn_key = raw_index if "|" in raw_index else ""
    if not plate_key:
        return no_update

    state = data_cache.get_state()
    df = _apply_live_arrival_state(state.get("df"))
    if df is None or df.empty or "rendszam" not in df.columns:
        return no_update

    if turn_key:
        mask = df.apply(lambda row: _truck_turn_key(row.get("rendszam"), row.get("am_time")) == turn_key, axis=1)
    else:
        mask = df["rendszam"].apply(_plate_key) == plate_key
    if "is_stored" in df.columns:
        mask &= ~df["is_stored"].fillna(False).astype(bool)
    if "is_en_route" in df.columns:
        mask &= ~df["is_en_route"].fillna(False).astype(bool)
    rows_by_awb = {}
    for row in df[mask].to_dict("records"):
        awb = str(row.get("awb") or "").strip()
        if awb:
            rows_by_awb[awb] = row
    if not rows_by_awb:
        return no_update

    stored_count, stored_awbs = storage_manager.mark_many_stored(rows_by_awb)
    if not stored_count:
        return no_update
    data_cache.update_stored_flags(stored_awbs)
    activity_log.log_event(
        "truck_store",
        {
            "plate_key": plate_key,
            "turn_key": turn_key or plate_key,
            "count": stored_count,
            "awbs": list(rows_by_awb.keys())[:25],
        },
    )
    return {"plate_key": plate_key, "turn_key": turn_key or plate_key, "stored_count": stored_count, "ts": time.time()}


@app.callback(
    Output("note-action", "data"),
    Input({"type": "note-add", "index": ALL}, "n_clicks"),
    Input({"type": "note-input", "index": ALL}, "n_submit"),
    Input({"type": "note-del", "index": ALL}, "n_clicks"),
    State({"type": "note-input", "index": ALL}, "value"),
    State({"type": "note-input", "index": ALL}, "id"),
    prevent_initial_call=True,
)
def handle_note_action(_adds, _submits, _dels, input_values, input_ids):
    triggered = ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        return no_update
    # Guard: 0/None means a freshly-rendered component, not a real interaction.
    triggered_value = (ctx.triggered or [{}])[0].get("value")
    if not triggered_value:
        return no_update

    if triggered.get("type") == "note-del":
        awb, _, note_id = str(triggered.get("index", "")).partition("|")
        if not notes_manager.delete_note(awb, note_id):
            return no_update
        activity_log.log_event("note_delete", {"awb": awb, "note_id": note_id})
        data_cache.bump_data_version()
        return {"awb": awb, "op": "del", "ts": time.time()}

    # add: triggered by the button or Enter in the input
    awb = str(triggered.get("index", ""))
    text = ""
    for vid, value in zip(input_ids or [], input_values or []):
        if isinstance(vid, dict) and vid.get("index") == awb:
            text = str(value or "").strip()
            break
    if not awb or not text:
        return no_update
    note = notes_manager.add_note(awb, text)
    if not note:
        return no_update
    activity_log.log_event("note_add", {"awb": awb, "note_id": note["id"]})
    data_cache.bump_data_version()
    return {"awb": awb, "op": "add", "ts": time.time()}


# ---------------------------------------------------------------------------
# Developer menu (PIN-gated)
# ---------------------------------------------------------------------------

_HOSTNAME = socket.gethostname()
_DEV_PIN_SALT = "FlowMgrDev::"
_DEV_PIN_HASH = "c7129d642bb7bcb29d08a3eb6d869596155e4501a30173a391750f3ed3c093ef"
_DEV_TOKENS: set[str] = set()


def _dev_authorized(token) -> bool:
    return isinstance(token, str) and token in _DEV_TOKENS


@app.callback(
    Output("dev-auth", "data"),
    Output("dev-pin-msg", "children"),
    Output("dev-pin-input", "value"),
    Input("dev-pin-submit", "n_clicks"),
    Input("dev-pin-input", "n_submit"),
    State("dev-pin-input", "value"),
    prevent_initial_call=True,
)
def dev_pin_check(_n, _s, pin):
    if not (ctx.triggered or [{}])[0].get("value"):
        return no_update, no_update, no_update
    pin = str(pin or "").strip()
    if not pin:
        return no_update, "", no_update
    if hashlib.sha256((_DEV_PIN_SALT + pin).encode()).hexdigest() == _DEV_PIN_HASH:
        token = uuid.uuid4().hex
        _DEV_TOKENS.add(token)
        activity_log.log_event("dev_login", {"ok": True})
        return token, "", ""
    activity_log.log_event("dev_login", {"ok": False})
    return no_update, "Hibás PIN.", ""


@app.callback(
    Output("dev-pin-overlay", "style"),
    Output("dev-overlay", "style"),
    Input("dev-open-btn", "n_clicks"),
    Input("dev-pin-close", "n_clicks"),
    Input("dev-close", "n_clicks"),
    Input("dev-auth", "data"),
    prevent_initial_call=True,
)
def dev_overlays(_open, _pin_close, _close, token):
    hidden = {"display": "none"}
    shown = {"display": "flex"}
    trig = ctx.triggered_id
    if trig == "dev-open-btn":
        if not (ctx.triggered or [{}])[0].get("value"):
            return no_update, no_update
        if _dev_authorized(token):
            return hidden, shown
        return shown, hidden
    if trig == "dev-auth":
        return (hidden, shown) if _dev_authorized(token) else (no_update, no_update)
    if trig == "dev-pin-close":
        return hidden, no_update
    if trig == "dev-close":
        return no_update, hidden
    return no_update, no_update


@app.callback(
    Output("dev-tab", "data"),
    Output("dev-tab-ops", "className"),
    Output("dev-tab-state", "className"),
    Output("dev-tab-ovr", "className"),
    Output("dev-tab-log", "className"),
    Input("dev-tab-ops", "n_clicks"),
    Input("dev-tab-state", "n_clicks"),
    Input("dev-tab-ovr", "n_clicks"),
    Input("dev-tab-log", "n_clicks"),
    State("dev-tab", "data"),
)
def dev_switch_tab(_a, _b, _c, _d, current):
    mapping = {
        "dev-tab-ops": "ops", "dev-tab-state": "state",
        "dev-tab-ovr": "ovr", "dev-tab-log": "log",
    }
    tab = mapping.get(ctx.triggered_id, current or "ops")

    def cls(name):
        return "dev-tab dev-tab-active" if tab == name else "dev-tab"

    return tab, cls("ops"), cls("state"), cls("ovr"), cls("log")


def _dev_row(left, meta, del_id) -> html.Div:
    return html.Div(className="dev-row", children=[
        html.Div(className="dev-row-main", children=left),
        html.Div(className="dev-row-side", children=[
            html.Span(meta, className="dev-row-meta"),
            html.Button("Törlés", id=del_id, className="dev-del-btn", n_clicks=0),
        ]),
    ])


def _dev_ops_tab() -> html.Div:
    state = data_cache.get_state()
    last = state.get("last_refresh")
    info_rows = [
        ("Felhasználó", f"{os.environ.get('USERNAME', '?')} @ {_HOSTNAME}"),
        ("Shared mappa", str(storage_manager.get_storage_path().parent)),
        ("Utolsó frissítés", last.strftime("%Y-%m-%d %H:%M:%S") if isinstance(last, datetime) else "—"),
        ("Data version", str(state.get("data_version", "—"))),
        ("Aktív tételek", str(len(state["df"])) if state.get("df") is not None else "0"),
        ("Státusz", str(state.get("status", "—"))),
    ]
    return html.Div(children=[
        html.Div(className="dev-info-grid", children=[
            html.Div(className="dev-info-item", children=[
                html.Span(k, className="dev-info-key"),
                html.Span(v, className="dev-info-val"),
            ]) for k, v in info_rows
        ]),
        html.Div(className="dev-ops-btns", children=[
            html.Button("Kényszerített újraolvasás", id={"type": "dev-op", "name": "force-refresh"},
                        className="dev-op-btn", n_clicks=0),
            html.Button("Dashboard cache törlés", id={"type": "dev-op", "name": "clear-cache"},
                        className="dev-op-btn", n_clicks=0),
            html.Button("Lock fájlok feloldása", id={"type": "dev-op", "name": "unlock"},
                        className="dev-op-btn dev-op-warn", n_clicks=0),
        ]),
        html.Div("A lock-feloldás csak beragadt .lock fájl esetén kell "
                 "(pl. lefagyott gép után).", className="dev-note"),
    ])


def _dev_state_tab() -> html.Div:
    sections = []

    stored = storage_manager.load_stored()
    stored_rows = [
        _dev_row(
            html.Span(awb, className="dev-mono"),
            f"{e.get('stored_by', '?')} · {str(e.get('stored_at', ''))[:16].replace('T', ' ')}",
            {"type": "dev-del", "kind": "stored", "index": awb},
        )
        for awb, e in sorted(stored.items())
    ]
    sections.append(html.Details(open=True, className="dev-section", children=[
        html.Summary(f"Betárolva bejegyzések ({len(stored_rows)})", className="dev-section-title"),
        html.Div(stored_rows or [html.Div("Nincs bejegyzés.", className="dev-empty")]),
    ]))

    notes = notes_manager.get_all_notes()
    note_rows = []
    for awb in sorted(notes):
        for n in notes[awb]:
            note_rows.append(_dev_row(
                [html.Span(awb, className="dev-mono"), html.Span(f" — {n.get('text', '')}")],
                f"{n.get('by', '?')} · {_fmt_note_at(n.get('at', ''))}",
                {"type": "dev-del", "kind": "note", "index": f"{awb}|{n.get('id', '')}"},
            ))
    sections.append(html.Details(open=True, className="dev-section", children=[
        html.Summary(f"Megjegyzések ({len(note_rows)})", className="dev-section-title"),
        html.Div(note_rows or [html.Div("Nincs megjegyzés.", className="dev-empty")]),
    ]))

    stacks = uld_stack_manager.get_stacks()
    stack_rows = [
        _dev_row(
            [html.Span(s.get("name") or s.get("id", "?"), className="dev-mono"),
             html.Span(f" — {len(s.get('ulds', []))} ULD"
                       + (" · kiküldve" if s.get("dispatched") else "")
                       + (" · előkészítve" if s.get("prepared") else ""))],
            str(s.get("id", ""))[:12],
            {"type": "dev-del", "kind": "stack", "index": s.get("id", "")},
        )
        for s in stacks
    ]
    sections.append(html.Details(className="dev-section", children=[
        html.Summary(f"ULD stack-ek ({len(stack_rows)})", className="dev-section-title"),
        html.Div(stack_rows or [html.Div("Nincs stack.", className="dev-empty")]),
    ]))

    renames = uld_stack_manager.get_uld_renames()
    rename_rows = [
        _dev_row(
            html.Span(f"{old} → {new}", className="dev-mono"),
            "átnevezés",
            {"type": "dev-del", "kind": "rename", "index": old},
        )
        for old, new in sorted(renames.items())
    ]
    sections.append(html.Details(className="dev-section", children=[
        html.Summary(f"ULD átnevezések ({len(rename_rows)})", className="dev-section-title"),
        html.Div(rename_rows or [html.Div("Nincs átnevezés.", className="dev-empty")]),
    ]))

    return html.Div(sections)


def _dev_ovr_tab() -> html.Div:
    overrides = overrides_manager.get_all_overrides()
    rows = []
    for awb in sorted(overrides):
        entry = overrides[awb]
        for field, value in (entry.get("fields") or {}).items():
            rows.append(_dev_row(
                [html.Span(awb, className="dev-mono"),
                 html.Span(f" · {field} = {value!r}")],
                f"{entry.get('set_by', '?')} · {str(entry.get('set_at', ''))[:16].replace('T', ' ')}",
                {"type": "dev-del", "kind": "override", "index": f"{awb}|{field}"},
            ))
    return html.Div(children=[
        html.Div(className="dev-ovr-form", children=[
            dcc.Input(id={"type": "dev-ovr-fld", "name": "awb"}, type="text",
                      placeholder="AWB", className="settings-input dev-ovr-input"),
            dcc.Dropdown(
                id={"type": "dev-ovr-fld", "name": "field"},
                options=[{"label": f, "value": f} for f in sorted(overrides_manager.OVERRIDABLE_FIELDS)],
                placeholder="Mező", className="dev-ovr-dd", clearable=False,
            ),
            dcc.Input(id={"type": "dev-ovr-fld", "name": "value"}, type="text",
                      placeholder="Új érték", className="settings-input dev-ovr-input"),
            html.Button("Mentés", id={"type": "dev-op", "name": "ovr-save"},
                        className="dev-op-btn", n_clicks=0),
        ]),
        html.Div("A felülbírálás csak a dashboardon látszik (az Excelt nem írja), "
                 "és a következő törlésig él. A rangsor újraszámolódik.",
                 className="dev-note"),
        html.Div(rows or [html.Div("Nincs aktív felülbírálás.", className="dev-empty")]),
    ])


def _dev_log_tab(sel_day: str, sel_user: str, sel_search: str) -> html.Div:
    days = activity_log.list_log_days()
    day = sel_day if sel_day in days else (days[0] if days else "")
    users = activity_log.list_users(day) if day else []
    user = sel_user if sel_user in users else ""
    events = activity_log.read_events(day, user=user, search=sel_search or "", limit=500) if day else []

    table_rows = [
        html.Tr([
            html.Td(str(e.get("ts", ""))[11:19], className="dev-log-ts",
                    title=str(e.get("ts", ""))),
            html.Td(str(e.get("user", ""))),
            html.Td(str(e.get("host", "")), className="dev-log-host"),
            html.Td(str(e.get("action", "")), className="dev-mono"),
            html.Td(json.dumps(e.get("detail"), ensure_ascii=False)
                    if e.get("detail") is not None else "", className="dev-log-detail"),
        ])
        for e in events
    ]
    return html.Div(children=[
        html.Div(className="dev-log-controls", children=[
            dcc.Dropdown(id={"type": "dev-log-ctl", "name": "day"},
                         options=[{"label": d, "value": d} for d in days],
                         value=day or None, placeholder="Dátum",
                         className="dev-log-dd", clearable=False),
            dcc.Dropdown(id={"type": "dev-log-ctl", "name": "user"},
                         options=[{"label": "Mindenki", "value": ""}]
                                 + [{"label": u, "value": u} for u in users],
                         value=user, placeholder="Felhasználó",
                         className="dev-log-dd"),
            dcc.Input(id={"type": "dev-log-ctl", "name": "search"}, type="text",
                      placeholder="Keresés…", value=sel_search or "", debounce=True,
                      className="settings-input dev-log-search"),
            html.Span(f"{len(events)} esemény", className="dev-log-count"),
        ]),
        html.Div(className="dev-log-table-wrap", children=[
            html.Table(className="dev-log-table", children=[
                html.Thead(html.Tr([
                    html.Th("Idő"), html.Th("Felhasználó"), html.Th("Gép"),
                    html.Th("Művelet"), html.Th("Részletek"),
                ])),
                html.Tbody(table_rows or [html.Tr(html.Td(
                    "Nincs esemény.", colSpan=5, className="dev-empty"))]),
            ]),
        ]),
    ])


@app.callback(
    Output("dev-content", "children"),
    Output("dev-sub", "children"),
    Input("dev-tab", "data"),
    Input("dev-version", "data"),
    Input("dev-auth", "data"),
    Input({"type": "dev-log-ctl", "name": ALL}, "value"),
    State({"type": "dev-log-ctl", "name": ALL}, "id"),
    prevent_initial_call=True,
)
def dev_render(tab, _version, token, log_ctl_values, log_ctl_ids):
    if not _dev_authorized(token):
        return html.Div("Nincs jogosultság.", className="dev-empty"), ""
    sub = f"{os.environ.get('USERNAME', '?')} · aktív munkamenet"
    tab = tab or "ops"
    if tab == "ops":
        return _dev_ops_tab(), sub
    if tab == "state":
        return _dev_state_tab(), sub
    if tab == "ovr":
        return _dev_ovr_tab(), sub
    ctl = {}
    for cid, value in zip(log_ctl_ids or [], log_ctl_values or []):
        if isinstance(cid, dict):
            ctl[cid.get("name")] = value
    return _dev_log_tab(
        str(ctl.get("day") or ""), str(ctl.get("user") or ""), str(ctl.get("search") or ""),
    ), sub


def _dev_clear_cache() -> int:
    removed = 0
    for path in data_cache._cache_paths():
        try:
            if path.exists():
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def _dev_unlock() -> list[str]:
    removed = []
    shared_dir = storage_manager.get_storage_path().parent
    for pattern in ("*.lock", "activity_logs/*.lock"):
        for lock in shared_dir.glob(pattern):
            try:
                lock.unlink()
                removed.append(lock.name)
            except OSError:
                pass
    return removed


@app.callback(
    Output("dev-version", "data"),
    Output("dev-msg", "children"),
    Input({"type": "dev-op", "name": ALL}, "n_clicks"),
    Input({"type": "dev-del", "kind": ALL, "index": ALL}, "n_clicks"),
    State({"type": "dev-ovr-fld", "name": ALL}, "value"),
    State({"type": "dev-ovr-fld", "name": ALL}, "id"),
    State("dev-auth", "data"),
    State("dev-version", "data"),
    prevent_initial_call=True,
)
def dev_action(_ops, _dels, ovr_values, ovr_ids, token, version):
    triggered = ctx.triggered_id
    if not triggered or not isinstance(triggered, dict):
        return no_update, no_update
    if not (ctx.triggered or [{}])[0].get("value"):
        return no_update, no_update
    if not _dev_authorized(token):
        return no_update, "Nincs jogosultság."
    version = (version or 0) + 1

    if triggered.get("type") == "dev-op":
        name = triggered.get("name", "")
        if name == "force-refresh":
            data_cache.mark_refresh_started()
            data_cache.trigger_refresh()
            activity_log.log_event("dev_force_refresh")
            return version, "Újraolvasás elindítva."
        if name == "clear-cache":
            removed = _dev_clear_cache()
            activity_log.log_event("dev_clear_cache", {"removed": removed})
            return version, f"Cache törölve ({removed} fájl)."
        if name == "unlock":
            removed = _dev_unlock()
            activity_log.log_event("dev_unlock", {"removed": removed})
            return version, f"Lock fájlok feloldva: {', '.join(removed) if removed else 'nem volt'}."
        if name == "ovr-save":
            fields = {}
            for cid, value in zip(ovr_ids or [], ovr_values or []):
                if isinstance(cid, dict):
                    fields[cid.get("name")] = value
            awb = str(fields.get("awb") or "").strip()
            field = str(fields.get("field") or "").strip()
            value = fields.get("value")
            if not awb or not field:
                return version, "Hiányzó AWB vagy mező."
            ok, err = overrides_manager.set_override(awb, field, value)
            if not ok:
                return version, err
            activity_log.log_event("dev_override_set",
                                   {"awb": awb, "field": field, "value": str(value)})
            data_cache.update_stored_flags()
            return version, f"Felülbírálás mentve: {awb}.{field}"
        return no_update, no_update

    # dev-del
    kind = triggered.get("kind", "")
    index = str(triggered.get("index", ""))
    if kind == "stored":
        storage_manager.mark_unstored(index)
        data_cache.update_stored_flags()
        activity_log.log_event("dev_del_stored", {"awb": index})
        return version, f"Betárolva bejegyzés törölve: {index}"
    if kind == "note":
        awb, _, note_id = index.partition("|")
        notes_manager.delete_note(awb, note_id)
        data_cache.bump_data_version()
        activity_log.log_event("dev_del_note", {"awb": awb, "note_id": note_id})
        return version, f"Megjegyzés törölve: {awb}"
    if kind == "stack":
        uld_stack_manager.delete_stack(index)
        activity_log.log_event("dev_del_stack", {"stack_id": index})
        return version, "Stack törölve."
    if kind == "rename":
        uld_stack_manager.delete_rename(index)
        data_cache.bump_data_version()
        activity_log.log_event("dev_del_rename", {"old": index})
        return version, f"Átnevezés törölve: {index}"
    if kind == "override":
        awb, _, field = index.partition("|")
        overrides_manager.clear_override(awb, field or None)
        data_cache.update_stored_flags()
        activity_log.log_event("dev_del_override", {"awb": awb, "field": field})
        return version, f"Felülbírálás törölve: {awb}.{field}"
    return no_update, no_update


# ---------------------------------------------------------------------------
# Nyomtatás preview callbacks
# ---------------------------------------------------------------------------

@app.callback(
    Output("print-glabs-store", "data"),
    Input({"type": "print-glabs-btn", "index": ALL}, "n_clicks"),
    Input("ppt-close", "n_clicks"),
    prevent_initial_call=True,
)
def manage_print_preview(print_clicks, _close):
    tid = ctx.triggered_id
    if tid == "ppt-close":
        return None
    if not isinstance(tid, dict):
        return no_update
    triggered_value = (ctx.triggered or [{}])[0].get("value")
    if not triggered_value:
        return no_update
    idx = tid.get("index", "")
    parts = idx.split("|||", 1)
    if len(parts) != 2:
        return no_update
    plate, glabs_id = parts

    state = data_cache.get_state()
    cards = state.get("outbound_cards") or []
    card = next(
        (c for c in cards if (_clean_text(c.get("plate")) or "") == plate),
        None,
    )
    if card is None:
        return no_update
    glabs = next(
        (g for g in card.get("glabs_items", []) if (_clean_text(g.get("glabs_id")) or "") == glabs_id),
        None,
    )
    if glabs is None:
        return no_update

    return {
        "plate": plate,
        "glabs_id": glabs_id,
        "awb_items": glabs.get("awb_items", []),
    }


app.clientside_callback(
    """
    function(data) {
        if (typeof window.flowPrintPreview === 'function') {
            window.flowPrintPreview(data);
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("print-action-dummy", "data"),
    Input("print-glabs-store", "data"),
    prevent_initial_call=True,
)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_browser_opened = False
# Loopback-only bind: the dashboard is used on the same machine via the browser,
# and cross-user sharing happens through the shared JSON files (filesystem), not
# over the network. Binding to 127.0.0.1 means no LAN-exposed port, so Windows
# Firewall never prompts. (Set back to "0.0.0.0" only if remote/LAN access is needed.)
_SERVER_HOST = "127.0.0.1"

# ---------------------------------------------------------------------------
# Runtime activity tracking
# ---------------------------------------------------------------------------

_INACTIVITY_TIMEOUT_MINUTES = 30
_INACTIVITY_SHUTDOWN_SECONDS = _INACTIVITY_TIMEOUT_MINUTES * 60
_NO_POST_SHUTDOWN_SECONDS = _INACTIVITY_TIMEOUT_MINUTES * 60
_last_poll_time: list[float] = [0.0]  # updated by update_dashboard on every poll
_last_post_time: list[float] = [time.time()]
_last_user_activity_time: list[float] = [time.time()]


def _shutdown_watchdog():
    """Exit when the browser stops sending POSTs or has no user activity."""
    time.sleep(60)
    while True:
        time.sleep(15)
        post_idle_seconds = time.time() - _last_post_time[0]
        if post_idle_seconds >= _NO_POST_SHUTDOWN_SECONDS:
            print(f"\n  {_INACTIVITY_TIMEOUT_MINUTES} perc POST nelkuli allapot - szerver leallitasa...", flush=True)
            activity_log.log_session_end(f"{_INACTIVITY_TIMEOUT_MINUTES} perc POST hiány")
            time.sleep(0.5)
            os._exit(0)

        idle_seconds = time.time() - _last_user_activity_time[0]
        if idle_seconds >= _INACTIVITY_SHUTDOWN_SECONDS:
            print(f"\n  {_INACTIVITY_TIMEOUT_MINUTES} perc bongeszo inaktivitas - szerver leallitasa...", flush=True)
            activity_log.log_session_end(f"{_INACTIVITY_TIMEOUT_MINUTES} perc inaktivitás")
            time.sleep(0.5)
            os._exit(0)


def _open_browser():
    global _browser_opened
    if _browser_opened:
        return
    _browser_opened = True
    time.sleep(1.5)
    webbrowser.open(f"http://127.0.0.1:{PORT}")


def _register_exit_handlers():
    """Register OS-level exit handlers so the process always terminates cleanly.

    Handles: Ctrl+C, Ctrl+Break, CMD window X-button, taskkill, SIGTERM.
    Without this, the Python process can survive after the CMD window is closed
    because Windows does not automatically kill child processes.
    """
    def _hard_exit(reason: str):
        print(f"\n  {reason} - leállítás...", flush=True)
        try:
            activity_log.log_session_end(reason)
        except Exception:
            pass
        os._exit(0)

    # SIGINT (Ctrl+C) and SIGTERM (taskkill without /F)
    signal.signal(signal.SIGINT,  lambda s, f: _hard_exit("Ctrl+C"))
    signal.signal(signal.SIGTERM, lambda s, f: _hard_exit("SIGTERM"))

    # Windows console events: Ctrl+C, Ctrl+Break, window close button
    try:
        _CTRL_CLOSE_EVENT = 2
        _HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.DWORD)

        def _win_console_handler(ctrl_type):
            _hard_exit("Konzol bezárva")
            return True  # unreachable, but kötelező by signature

        _handler_ref = _HandlerRoutine(_win_console_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, True)
        # Keep a reference so the callback isn't garbage-collected
        _register_exit_handlers._win_handler = _handler_ref
    except Exception:
        pass  # non-Windows or no console — signal handlers above are enough


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _register_exit_handlers()
    activity_log.cleanup_old()
    activity_log.log_event("app_start", {"pid": os.getpid()})

    print("+----------------------------------------------+", flush=True)
    print("|   FLOW MANAGER                               |", flush=True)
    print("|   HGL Group Hungary  -  Ecommerce Flow       |", flush=True)
    print("+----------------------------------------------+", flush=True)
    print(flush=True)
    print("  [1/2] Vendor assets ellenőrzése...", flush=True)
    vendor_ok = os.path.exists(_VENDOR_CSS) and os.path.exists(_VENDOR_FONT)
    print(f"  {'[OK]' if vendor_ok else '[!]'} Bootstrap + Inter font "
          f"{'megvan' if vendor_ok else 'hiányzik, letöltés folyamatban'}", flush=True)
    print(flush=True)
    print("  [2/2] Adatcache + webszerver indítása...", flush=True)

    data_cache.start()
    updater.start_async()
    threading.Thread(target=_open_browser,        daemon=True).start()
    threading.Thread(target=_shutdown_watchdog,   daemon=True, name="InactivityWatchdog").start()

    print(flush=True)
    print(f"  -> Megnyitás:  http://127.0.0.1:{PORT}", flush=True)
    print(
        f"  -> Leállítás:  Ctrl+C, CMD ablak bezárása, {_INACTIVITY_TIMEOUT_MINUTES} perc POST hiány "
        f"vagy {_INACTIVITY_TIMEOUT_MINUTES} perc böngésző inaktivitás",
        flush=True,
    )
    print(flush=True)

    app.run(debug=False, port=PORT, host=_SERVER_HOST, use_reloader=False, threaded=True)
