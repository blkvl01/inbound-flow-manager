"""
Per-AWB display overrides for the developer menu.

Stored as a per-machine ledger (see shared_ledger.py), one record per
(AWB, field):

  item_overrides.<HOST>.json  records: {"[\"<awb>\", \"<field>\"]": {
      "awb": "...", "field": "...", "value": ..., "set_by": "<user>",
      "set_on": "<computer>", "set_at": "<ISO>", "ts": "...", "deleted": false}}

The frozen legacy ``item_overrides.shared.json`` ({awb: {"fields": {...}}}) is
merged in.  get_all_overrides() keeps the old {awb: {"fields": {...}, ...}} shape.

The overrides change only what the dashboard shows: they are applied to the
in-memory DataFrame AFTER the Excel read and BEFORE the priority engine, so
ranking/filters recompute naturally. The Excel sources are never written.
"""
import json
import logging
import os
import socket
from datetime import datetime
from pathlib import Path

from shared_ledger import Ledger
from storage_manager import _shared_base_dir

log = logging.getLogger(__name__)

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



def _key(awb: str, field: str) -> str:
    return json.dumps([awb, field], ensure_ascii=False)


def _legacy_records(data: dict) -> dict:
    records = {}
    for awb, entry in data.items():
        fields = entry.get("fields") if isinstance(entry, dict) else None
        if not isinstance(fields, dict):
            continue
        for field, value in fields.items():
            records[_key(str(awb), str(field))] = {
                "awb": str(awb), "field": str(field), "value": value,
                "set_by": entry.get("set_by"), "set_on": entry.get("set_on"),
                "set_at": entry.get("set_at"), "deleted": False, "ts": entry.get("set_at", ""),
            }
    return records


_ledger = Ledger("item_overrides", lambda: _shared_base_dir(), _legacy_records)


def get_overrides_path() -> Path:
    """This machine's ledger file."""
    return _ledger.own_path()


def state_signature() -> tuple:
    return _ledger.signature()


def get_overrides_mtime() -> float:
    return max((entry[1] for entry in state_signature()[1:]), default=0) / 1e9


def compact() -> int:
    return _ledger.compact()


def get_all_overrides() -> dict:
    """All overrides keyed by AWB: {awb: {"fields": {...}, "set_by", "set_on", "set_at"}}.
    Rebuilt only when any machine's overrides file changes; treat as read-only."""
    return _ledger.view("by_awb", _group_by_awb)


def _group_by_awb(records: dict) -> dict:
    grouped: dict = {}
    for rec in records.values():
        if rec.get("deleted"):
            continue
        awb, field = str(rec.get("awb") or ""), str(rec.get("field") or "")
        if not awb or not field:
            continue
        entry = grouped.setdefault(awb, {"fields": {}})
        entry["fields"][field] = rec.get("value")
        if str(rec.get("set_at") or "") >= str(entry.get("set_at") or ""):
            entry.update(set_by=rec.get("set_by"), set_on=rec.get("set_on"), set_at=rec.get("set_at"))
    return grouped


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
    with _ledger.edit() as txn:
        txn.put(_key(awb, field), {
            "awb": awb, "field": field, "value": coerced,
            "set_by": os.environ.get("USERNAME", "unknown"),
            "set_on": socket.gethostname(),
            "set_at": datetime.now().isoformat(timespec="seconds"),
        })
    log.info("Felülbírálás: %s.%s = %r", awb, field, coerced)
    return True, ""


def clear_override(awb: str, field: str | None = None) -> bool:
    """Remove one field override, or every field of the AWB when field is None."""
    awb = str(awb or "").strip()
    if not awb:
        return False
    removed = False
    with _ledger.edit() as txn:
        for key, rec in list(txn.live.items()):
            if str(rec.get("awb") or "") != awb:
                continue
            if field is None or str(rec.get("field") or "") == field:
                removed = txn.delete(key) or removed
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
