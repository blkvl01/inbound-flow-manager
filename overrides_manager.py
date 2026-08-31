"""
Per-AWB display overrides for the developer menu.

item_overrides.shared.json format:
  {
    "<awb>": {
      "fields": {"lmp": "TEMU", "weight": 123.0, ...},
      "set_by": "<username>",
      "set_on": "<computer>",
      "set_at": "<ISO datetime>"
    }
  }

The overrides change only what the dashboard shows: they are applied to the
in-memory DataFrame AFTER the Excel read and BEFORE the priority engine, so
ranking/filters recompute naturally. The Excel sources are never written.
"""
import json
import logging
import os
import socket
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from storage_manager import _shared_base_dir

log = logging.getLogger(__name__)

_LOCK_TIMEOUT_S = 15.0
_LOCK_POLL_S = 0.20
_LOCK_STALE_S = 120.0

# field -> coercion callable; only these may be overridden
def _to_bool(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "igen", "yes", "i")


OVERRIDABLE_FIELDS: dict = {
    "lmp": str,
    "weight": float,
    "boxes": int,
    "cargo_type": str,
    "uld_number": str,
    "glabs_id": str,
    "rendszam": str,
    "is_shippable": _to_bool,
}

_cache: dict = {"mtime": None, "data": {}}


def _path() -> Path:
    return _shared_base_dir() / "item_overrides.shared.json"


def _lock_path() -> Path:
    return _shared_base_dir() / "item_overrides.shared.lock"


def get_overrides_path() -> Path:
    return _path()


def get_overrides_mtime() -> float:
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
                return {k: v for k, v in data.items() if isinstance(v, dict)}
            log.warning("item_overrides shared file is not a dict, ignoring: %s", p)
        except Exception as exc:
            log.error("item_overrides shared read error: %s", exc)
    return {}


def _atomic_write(data: dict):
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.stem + ".", suffix=".tmp", dir=str(target.parent),
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
                f.write(json.dumps({
                    "created_at": datetime.now().isoformat(),
                    "user": os.environ.get("USERNAME", "unknown"),
                    "host": socket.gethostname(),
                    "pid": os.getpid(),
                }, ensure_ascii=False))
            return
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
                if age > _LOCK_STALE_S:
                    log.warning("Removing stale overrides lock: %s", lock)
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() >= deadline:
                raise TimeoutError(f"Shared overrides lock timeout: {lock}")
            time.sleep(_LOCK_POLL_S)


def _release_lock():
    try:
        _lock_path().unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Shared overrides lock release error: %s", exc)


@contextmanager
def _locked_data():
    _acquire_lock()
    try:
        data = _read_unlocked()
        yield data
        _atomic_write(data)
        _cache["mtime"] = None
    finally:
        _release_lock()


def get_all_overrides() -> dict:
    """All overrides keyed by AWB. mtime-cached."""
    mtime = get_overrides_mtime()
    if _cache["mtime"] == mtime:
        return _cache["data"]
    data = _read_unlocked()
    _cache["mtime"] = mtime
    _cache["data"] = data
    return data


def set_override(awb: str, field: str, value) -> tuple[bool, str]:
    """Set one field override. Returns (ok, error_message)."""
    awb = str(awb or "").strip()
    field = str(field or "").strip()
    if not awb:
        return False, "Hiányzó AWB"
    coerce = OVERRIDABLE_FIELDS.get(field)
    if coerce is None:
        return False, f"Nem felülbírálható mező: {field}"
    raw = value if isinstance(value, bool) else str(value).strip()
    if coerce in (int, float) and isinstance(raw, str):
        raw = raw.replace(",", ".").replace(" ", "")
        if coerce is int:
            raw = raw.split(".")[0] or "0"
    try:
        coerced = coerce(raw)
    except (TypeError, ValueError):
        return False, f"Érvénytelen érték a(z) {field} mezőhöz: {value!r}"
    with _locked_data() as data:
        entry = data.setdefault(awb, {"fields": {}})
        entry.setdefault("fields", {})[field] = coerced
        entry["set_by"] = os.environ.get("USERNAME", "unknown")
        entry["set_on"] = socket.gethostname()
        entry["set_at"] = datetime.now().isoformat(timespec="seconds")
    log.info("Felülbírálás: %s.%s = %r", awb, field, coerced)
    return True, ""


def clear_override(awb: str, field: str | None = None) -> bool:
    """Remove one field override, or the whole AWB entry when field is None."""
    awb = str(awb or "").strip()
    if not awb:
        return False
    removed = False
    with _locked_data() as data:
        entry = data.get(awb)
        if not isinstance(entry, dict):
            return False
        if field is None:
            del data[awb]
            removed = True
        else:
            fields = entry.get("fields", {})
            if field in fields:
                del fields[field]
                removed = True
            if not fields:
                del data[awb]
    if removed:
        log.info("Felülbírálás törölve: %s.%s", awb, field or "*")
    return removed


def apply_overrides_to_df(df):
    """Apply overrides onto a DataFrame (pandas imported lazily for tests).

    Original values are backed up into '<field>__orig' columns on first
    override, and restored before each application — so removing an override
    brings back the Excel value without a re-read.
    """
    import pandas as pd  # noqa: F401  (lazy: keep module import light)

    if df is None or getattr(df, "empty", True) or "awb" not in df.columns:
        return df
    overrides = get_all_overrides()
    df = df.copy()

    # restore everything first (handles removed overrides)
    for col in [c for c in df.columns if c.endswith("__orig")]:
        base = col[:-6]
        if base in df.columns:
            df[base] = df[col]

    if not overrides:
        return df

    for awb, entry in overrides.items():
        fields = entry.get("fields") if isinstance(entry, dict) else None
        if not isinstance(fields, dict):
            continue
        mask = df["awb"] == awb
        if not mask.any():
            continue
        for field, value in fields.items():
            if field not in OVERRIDABLE_FIELDS or field not in df.columns:
                continue
            orig_col = f"{field}__orig"
            if orig_col not in df.columns:
                df[orig_col] = df[field]
            df.loc[mask, field] = value
    return df
