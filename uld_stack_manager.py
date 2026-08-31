"""
ULD Stack Manager — shared physical stack state for all users.

JSON: _shared_state/uld_stacks.shared.json
Format:
{
    "stacks": [
        {
            "id":         "<uuid>",
            "name":       "Stack 1",
            "created_at": "<ISO>",
            "created_by": "<user>",
            "ulds":       ["PMD34212MB", "AKE12345LH"]  # index 0 = bottom, last = top
        }
    ]
}
"""
import json
import logging
import os
import shutil
import socket
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from data_reader import _normalize_uld_code

log = logging.getLogger(__name__)

_STACKS_FILE = "uld_stacks.shared.json"
# Network shares can be slow and several users may save at once. A 2s acquire
# timeout surfaced spurious "foglalt, próbáld újra" toasts under light contention,
# so give writers more room before giving up. Stale-lock reclaim is kept well
# above the timeout so a crashed holder's lock is still recovered, but a healthy
# (merely slow) holder is never stolen from mid-write.
_LOCK_TIMEOUT_S = 4.0
_LOCK_POLL_S    = 0.05
_LOCK_STALE_S   = 15.0
_LOCAL_LOCK = threading.RLock()
_LOCK_LOCAL = threading.local()

# Display-only read cache (see get_stacks_cached). Guarded by the file's
# (mtime, size) signature so any write — local or from another user — busts it.
_READ_CACHE_LOCK = threading.Lock()
_read_cache_sig = None
_read_cache_value = None
_CANONICAL_STACK_GHAS = ("AS Cargo", "Menzies", "Celebi")


def _invalidate_read_cache() -> None:
    global _read_cache_sig, _read_cache_value
    with _READ_CACHE_LOCK:
        _read_cache_sig = None
        _read_cache_value = None


class StackConflict(Exception):
    """Raised when a client tries to mutate a stale stack revision."""


class StackValidationError(Exception):
    """Raised when a requested mutation would violate persisted stack identity."""


def _shared_dir() -> Path:
    import storage_manager
    return storage_manager._shared_base_dir()


def get_stacks_path() -> Path:
    return _shared_dir() / _STACKS_FILE


def get_stacks_mtime() -> float:
    try:
        return get_stacks_path().stat().st_mtime
    except OSError:
        return 0.0


def _actor() -> dict:
    return {
        "updated_at": datetime.now().isoformat(),
        "updated_by": os.environ.get("USERNAME", "unknown"),
        "updated_on": socket.gethostname(),
    }


def _touch_stack(stack: dict) -> None:
    try:
        stack["revision"] = int(stack.get("revision") or 0) + 1
    except (TypeError, ValueError):
        stack["revision"] = 1
    stack.update(_actor())


def _format_change_value(value) -> str:
    if isinstance(value, (list, tuple, set)):
        items = [str(item or "").strip() for item in value if str(item or "").strip()]
        return ", ".join(items) if items else "-"
    text = str(value or "").strip()
    return text if text else "-"


def _build_uld_change_summary(
    old_uld: str,
    new_uld: str,
    *,
    old_awbs: list[str] | None = None,
    new_awbs: list[str] | None = None,
    old_gha: str = "",
    new_gha: str = "",
) -> str:
    parts: list[str] = []
    if _clean_uld(old_uld) != _clean_uld(new_uld):
        parts.append(f"ULD: {_format_change_value(old_uld)} -> {_format_change_value(new_uld)}")
    clean_old_awbs = [str(a or "").strip() for a in (old_awbs or []) if str(a or "").strip()]
    clean_new_awbs = [str(a or "").strip() for a in (new_awbs or []) if str(a or "").strip()]
    if clean_old_awbs != clean_new_awbs:
        parts.append(f"AWB: {_format_change_value(clean_old_awbs)} -> {_format_change_value(clean_new_awbs)}")
    if str(old_gha or "").strip() != str(new_gha or "").strip():
        parts.append(f"GHA: {_format_change_value(old_gha)} -> {_format_change_value(new_gha)}")
    return "; ".join(parts) or "ULD adatok módosítva"


def _expected_revision(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _assert_revision(stack: dict, expected_revision=None) -> None:
    expected = _expected_revision(expected_revision)
    if expected is None or expected <= 0:   # 0 = "don't know" → skip check
        return
    current = _expected_revision(stack.get("revision")) or 0
    if current != expected:
        raise StackConflict("Stack közben módosult, frissítsd a nézetet.")


class StackLocked(Exception):
    """Raised when an operation targets an 'Összekészítve' (prepared) stack
    or moves a ULD currently sitting in such a stack."""


def _assert_not_prepared(stack: dict, action: str = "módosítás") -> None:
    if stack and stack.get("prepared"):
        name = stack.get("name") or "stack"
        raise StackLocked(f"'{name}' össze van készítve — előbb vedd le a jelölést.")
    if stack and stack.get("dispatched"):
        name = stack.get("name") or "stack"
        raise StackLocked(f"'{name}' már ki van küldve — az aktív stack nem módosítható.")


def _assert_ulds_not_in_prepared(
    stacks: list[dict],
    ulds: list[str],
    allow_dispatched_ulds=None,
) -> None:
    """Refuse if any of the supplied ULDs is currently in a prepared stack."""
    if not ulds:
        return
    uld_set = {_clean_uld(u) for u in ulds if u}
    if not uld_set:
        return
    allowed = {_clean_uld(u) for u in (allow_dispatched_ulds or []) if u}
    for s in stacks:
        if not s.get("prepared"):
            continue
        held = {_clean_uld(u) for u in s.get("ulds", [])}
        clash = uld_set & held
        # A dispatched stack can belong to an older lifecycle of a reusable ULD.
        # The app only supplies this allow-list after comparing its dispatch time
        # with the current E_COMM Áttár time. Active prepared stacks stay locked.
        if s.get("dispatched") and allowed:
            clash -= allowed
        if clash:
            sample = sorted(clash)[0]
            name = s.get("name") or "stack"
            raise StackLocked(
                f"'{sample}' '{name}' összekészített stackben van — előbb vedd le a jelölést."
            )


def _clean_uld(value) -> str:
    return _normalize_uld_code(value)


def _canonical_stack_gha(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    folded = text.casefold()
    for gha in _CANONICAL_STACK_GHAS:
        if folded == gha.casefold():
            return gha
    return ""


def _bind_stack_gha(stack: dict, incoming_gha: str) -> None:
    """Persist the first confirmed GHA and reject a later cross-GHA mutation."""
    incoming = _canonical_stack_gha(incoming_gha) or str(incoming_gha or "").strip()
    if not incoming:
        return
    existing_raw = str(stack.get("stack_gha") or "").strip()
    existing = _canonical_stack_gha(existing_raw) or existing_raw
    if existing and existing.casefold() != incoming.casefold():
        raise StackValidationError(
            f"Más GHA nem tehető ebbe a stackbe ({existing} != {incoming})."
        )
    if not existing_raw:
        stack["stack_gha"] = incoming


def _normalize_manual_ulds(value) -> dict[str, dict]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict] = {}
    for raw_uld, raw_info in value.items():
        uld = _clean_uld(raw_uld)
        if not uld or not isinstance(raw_info, dict):
            continue
        awb = str(raw_info.get("awb") or "").strip()
        raw_awbs = raw_info.get("awbs") if isinstance(raw_info.get("awbs"), list) else []
        awbs = []
        seen_awbs = set()
        for item in raw_awbs:
            clean_awb = str(item or "").strip()
            if clean_awb and clean_awb not in seen_awbs:
                awbs.append(clean_awb)
                seen_awbs.add(clean_awb)
        if awb and awb not in seen_awbs:
            awbs.insert(0, awb)
        gha = str(raw_info.get("gha") or "").strip()
        am_time = str(raw_info.get("am_time") or "").strip()
        edited_at = str(raw_info.get("edited_at") or "").strip()
        edited_by = str(raw_info.get("edited_by") or "").strip()
        change_summary = str(raw_info.get("change_summary") or "").strip()
        result[uld] = {
            "uld_number": uld,
            "awb": awbs[0] if awbs else "",
            "awbs": awbs,
            "gha": gha,
            "am_time": am_time,
            "is_manual": True,
        }
        if edited_at:
            result[uld]["edited_at"] = edited_at
        if edited_by:
            result[uld]["edited_by"] = edited_by
        if change_summary:
            result[uld]["change_summary"] = change_summary
    return result


def _normalize_stack(stack, idx: int, seen_ulds: set[str]) -> tuple[dict, bool]:
    changed = False
    if not isinstance(stack, dict):
        stack = {}
        changed = True

    stack_id = str(stack.get("id") or "").strip()
    if not stack_id:
        stack_id = str(uuid.uuid4())
        changed = True

    name = str(stack.get("name") or "").strip() or f"Stack {idx}"
    if name != stack.get("name"):
        changed = True

    created_at = str(stack.get("created_at") or "").strip() or datetime.now().isoformat()
    created_by = str(stack.get("created_by") or "").strip() or "unknown"

    try:
        revision = int(stack.get("revision") or 0)
    except (TypeError, ValueError):
        revision = 0
        changed = True
    if revision < 0:
        revision = 0
        changed = True

    raw_ulds = stack.get("ulds", [])
    if not isinstance(raw_ulds, list):
        raw_ulds = []
        changed = True

    clean_ulds: list[str] = []
    local_seen: set[str] = set()
    for raw_uld in raw_ulds:
        uld = _clean_uld(raw_uld)
        if not uld or uld in local_seen or uld in seen_ulds:
            changed = True
            continue
        clean_ulds.append(uld)
        local_seen.add(uld)
        seen_ulds.add(uld)
        if uld != raw_uld:
            changed = True

    normalized = {
        "id": stack_id,
        "name": name,
        "created_at": created_at,
        "created_by": created_by,
        "updated_at": str(stack.get("updated_at") or created_at),
        "updated_by": str(stack.get("updated_by") or created_by),
        "updated_on": str(stack.get("updated_on") or ""),
        "revision": revision,
        "ulds": clean_ulds,
    }
    manual_ulds = _normalize_manual_ulds(stack.get("manual_ulds"))
    if manual_ulds:
        normalized["manual_ulds"] = {
            uld: info for uld, info in manual_ulds.items()
            if uld in clean_ulds
        }
    create_client_op_id = str(stack.get("create_client_op_id") or "").strip()
    if create_client_op_id:
        normalized["create_client_op_id"] = create_client_op_id
    create_action = str(stack.get("create_action") or "").strip()
    if create_action:
        normalized["create_action"] = create_action
    create_selection_key = str(stack.get("create_selection_key") or "").strip()
    if create_selection_key:
        normalized["create_selection_key"] = create_selection_key
    raw_stack_gha = str(stack.get("stack_gha") or "").strip()
    stack_gha = _canonical_stack_gha(raw_stack_gha) or raw_stack_gha
    if stack_gha:
        normalized["stack_gha"] = stack_gha
    # Preserve "prepared" flag and its audit fields across normalization passes —
    # otherwise the status would be wiped on every disk read.
    if stack.get("prepared"):
        normalized["prepared"] = True
        prepared_at = str(stack.get("prepared_at") or "").strip()
        if prepared_at:
            normalized["prepared_at"] = prepared_at
        prepared_by = str(stack.get("prepared_by") or "").strip()
        if prepared_by:
            normalized["prepared_by"] = prepared_by
    if stack.get("dispatched"):
        normalized["dispatched"] = True
        dispatched_at = str(stack.get("dispatched_at") or "").strip()
        if dispatched_at:
            normalized["dispatched_at"] = dispatched_at
        dispatched_by = str(stack.get("dispatched_by") or "").strip()
        if dispatched_by:
            normalized["dispatched_by"] = dispatched_by
        dispatched_on = str(stack.get("dispatched_on") or "").strip()
        if dispatched_on:
            normalized["dispatched_on"] = dispatched_on
        dispatch_plate = str(stack.get("dispatch_plate") or "").strip()
        if dispatch_plate:
            normalized["dispatch_plate"] = dispatch_plate
    for key, value in normalized.items():
        if stack.get(key) != value:
            changed = True
            break
    return normalized, changed


def _normalize_stacks(stacks) -> tuple[list[dict], bool]:
    if not isinstance(stacks, list):
        return [], True
    seen_ulds: set[str] = set()
    normalized: list[dict] = []
    changed = False
    seen_ids: set[str] = set()
    for idx, stack in enumerate(stacks, 1):
        clean_stack, stack_changed = _normalize_stack(stack, idx, seen_ulds)
        if clean_stack["id"] in seen_ids:
            clean_stack["id"] = str(uuid.uuid4())
            stack_changed = True
        seen_ids.add(clean_stack["id"])
        normalized.append(clean_stack)
        changed = changed or stack_changed
    return normalized, changed


def _backup_corrupt_file(path: Path) -> None:
    try:
        if not path.exists():
            return
        backup = path.with_name(f"{path.stem}.corrupt.{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
        shutil.copy2(path, backup)
        log.warning("ULD stacks corrupt backup written: %s", backup)
    except Exception as exc:
        log.warning("ULD stacks corrupt backup failed: %s", exc)


def _lock_path() -> Path:
    return _shared_dir() / "uld_stacks.shared.lockstate"


def _lock_is_stale(lock: Path) -> bool:
    try:
        return (time.time() - lock.stat().st_mtime) > _LOCK_STALE_S
    except FileNotFoundError:
        return False


def _acquire_lock():
    lock = _lock_path()
    deadline = time.time() + _LOCK_TIMEOUT_S
    _LOCAL_LOCK.acquire()
    handle = None
    while True:
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            handle = open(lock, "a+b")
            handle.seek(0)
            try:
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                handle = None
                if time.time() >= deadline:
                    _LOCAL_LOCK.release()
                    raise TimeoutError(f"ULD stacks lock timeout: {lock}")
                time.sleep(_LOCK_POLL_S)
                continue
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps({"pid": os.getpid(), "at": datetime.now().isoformat()}).encode("utf-8"))
            handle.flush()
            _LOCK_LOCAL.handle = handle
            return
        except TimeoutError:
            raise
        except Exception:
            if handle:
                try:
                    handle.close()
                except OSError:
                    pass
            _LOCAL_LOCK.release()
            raise


def _release_lock():
    handle = getattr(_LOCK_LOCAL, "handle", None)
    try:
        if handle:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
                _LOCK_LOCAL.handle = None
    finally:
        _LOCAL_LOCK.release()


def _read_unlocked(repair: bool = False) -> list[dict]:
    p = get_stacks_path()
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                stacks = data.get("stacks", [])
                if isinstance(stacks, list):
                    cleaned, changed = _normalize_stacks(stacks)
                    if repair and changed:
                        log.warning("ULD stacks normalized/self-healed before use")
                        _write_unlocked(cleaned)
                    return cleaned
        except Exception as exc:
            log.error("ULD stacks read error: %s", exc)
            _backup_corrupt_file(p)
    return []


def _write_unlocked(stacks: list[dict]):
    stacks, _ = _normalize_stacks(stacks)
    target = get_stacks_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="uld_stacks.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"stacks": stacks, "saved_at": datetime.now().isoformat()},
                      f, indent=2, ensure_ascii=False, default=str)
            f.flush()
        try:
            os.replace(tmp, target)
        except OSError:
            with open(target, "w", encoding="utf-8") as f:
                json.dump({"stacks": stacks, "saved_at": datetime.now().isoformat()},
                          f, indent=2, ensure_ascii=False, default=str)
        _invalidate_read_cache()
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def get_stacks() -> list[dict]:
    _acquire_lock()
    try:
        return _read_unlocked(repair=True)
    finally:
        _release_lock()


def get_stacks_cached() -> list[dict]:
    """Read-only, display-oriented accessor.

    Reuses the last parse while the file's (mtime, size) signature is unchanged,
    so high-frequency render paths don't lock + reparse the shared JSON on every
    poll. Any write — by this process or another user — changes the file, busts
    the signature, and forces a fresh authoritative read. The worst case is a
    render that is at most one file-change stale, which the next poll self-heals.

    Correctness-sensitive callers (GHA validation, mutations, the post-mutation
    response state) MUST use get_stacks() — never this — so they always act on
    the authoritative, freshly locked state.
    """
    global _read_cache_sig, _read_cache_value
    try:
        st = get_stacks_path().stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    if sig is not None:
        with _READ_CACHE_LOCK:
            if _read_cache_sig == sig and _read_cache_value is not None:
                return _read_cache_value
    value = get_stacks()
    if sig is not None:
        with _READ_CACHE_LOCK:
            _read_cache_sig = sig
            _read_cache_value = value
    return value


def save_stacks(stacks: list[dict]) -> None:
    _acquire_lock()
    try:
        _write_unlocked(stacks)
    finally:
        _release_lock()


# ── ULD number renames (display overrides) ──────────────────────────────────
# A small shared map of normalized_original → new_number. Excel rebuilds the ULD
# list from source on every refresh, so to make a manual correction stick we
# persist it here and re-apply it at display time. Stacks themselves are renamed
# in place (their stored numbers become the new value).
_RENAMES_FILE = "uld_renames.shared.json"
_OVERRIDES_FILE = "uld_overrides.shared.json"


def get_renames_path() -> Path:
    return _shared_dir() / _RENAMES_FILE


def get_uld_renames() -> dict:
    """normalized_original → new_number (already canonicalised, no identity rows)."""
    p = get_renames_path()
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                raw = data.get("renames", {})
                if isinstance(raw, dict):
                    out = {}
                    for k, v in raw.items():
                        ck, cv = _clean_uld(k), _clean_uld(v)
                        if ck and cv and ck != cv:
                            out[ck] = cv
                    return out
        except Exception as exc:
            log.error("ULD renames read error: %s", exc)
    return {}


def _write_renames_unlocked(renames: dict) -> None:
    clean = {}
    for k, v in renames.items():
        ck, cv = _clean_uld(k), _clean_uld(v)
        if ck and cv and ck != cv:
            clean[ck] = cv
    target = get_renames_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="uld_renames.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"renames": clean, "saved_at": datetime.now().isoformat()},
                      f, indent=2, ensure_ascii=False)
            f.flush()
        try:
            os.replace(tmp, target)
        except OSError:
            with open(target, "w", encoding="utf-8") as f:
                json.dump({"renames": clean, "saved_at": datetime.now().isoformat()},
                          f, indent=2, ensure_ascii=False)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def delete_rename(old_display: str) -> bool:
    """Remove one persisted rename override (developer menu)."""
    old = _clean_uld(old_display)
    if not old:
        return False
    _acquire_lock()
    try:
        renames = get_uld_renames()
        if old not in renames:
            return False
        del renames[old]
        _write_renames_unlocked(renames)
        return True
    finally:
        _release_lock()


def rename_uld_everywhere(old_display: str, new_display: str) -> bool:
    """Rename a ULD number in every stack and record a persistent display override.

    Returns False on a no-op (empty/identical after normalization) or when the new
    number already exists as a *different* ULD in some stack (would collide)."""
    old = _clean_uld(old_display)
    new = _clean_uld(new_display)
    if not old or not new or old == new:
        return False

    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        # Block collisions: the target number must not already be a distinct ULD.
        for stack in stacks:
            for u in _clean_uld_list(stack.get("ulds", [])):
                if u == new and old != new:
                    # Allow it only if it's the very ULD we're renaming (no-op guard
                    # already returned above), otherwise it's a real collision.
                    return False

        changed = False
        for stack in stacks:
            replaced = False
            new_ulds = []
            for u in stack.get("ulds", []):
                if _clean_uld(u) == old:
                    new_ulds.append(new)
                    replaced = True
                else:
                    new_ulds.append(u)
            if replaced:
                stack["ulds"] = new_ulds
                manual = _normalize_manual_ulds(stack.get("manual_ulds"))
                if old in manual:
                    manual[new] = manual.pop(old)
                    stack["manual_ulds"] = manual
                _touch_stack(stack)
                changed = True
        if changed:
            _write_unlocked(stacks)

        # Persist / re-point the display override. If an existing override already
        # resolves to `old` (a prior rename of the same physical ULD), re-point it
        # so the chain stays a single hop; otherwise add original→new.
        renames = get_uld_renames()
        repointed = False
        for k, v in list(renames.items()):
            if v == old:
                renames[k] = new
                repointed = True
        if not repointed:
            renames[old] = new
        _write_renames_unlocked(renames)
        _invalidate_read_cache()
        return True
    finally:
        _release_lock()


def get_overrides_path() -> Path:
    return _shared_dir() / _OVERRIDES_FILE


def get_uld_overrides() -> dict[str, dict]:
    p = get_overrides_path()
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                raw = data.get("overrides", {})
                if isinstance(raw, dict):
                    out: dict[str, dict] = {}
                    for key, info in raw.items():
                        uld = _clean_uld(key)
                        if not uld or not isinstance(info, dict):
                            continue
                        display_uld = _clean_uld(info.get("uld_number") or uld)
                        awbs = info.get("awbs") if isinstance(info.get("awbs"), list) else []
                        clean_awbs = []
                        seen_awbs = set()
                        for awb in awbs:
                            clean_awb = str(awb or "").strip()
                            if clean_awb and clean_awb not in seen_awbs:
                                clean_awbs.append(clean_awb)
                                seen_awbs.add(clean_awb)
                        gha = str(info.get("gha") or "").strip()
                        if not display_uld or not gha or not clean_awbs:
                            continue
                        out[uld] = {
                            "uld_number": display_uld,
                            "awbs": clean_awbs,
                            "gha": gha,
                            "edited_at": str(info.get("edited_at") or ""),
                            "edited_by": str(info.get("edited_by") or ""),
                            "change_summary": str(info.get("change_summary") or ""),
                        }
                    return out
        except Exception as exc:
            log.error("ULD overrides read error: %s", exc)
    return {}


def _write_overrides_unlocked(overrides: dict[str, dict]) -> None:
    clean: dict[str, dict] = {}
    for key, info in overrides.items():
        uld = _clean_uld(key)
        if not uld or not isinstance(info, dict):
            continue
        display_uld = _clean_uld(info.get("uld_number") or uld)
        awbs = []
        seen_awbs = set()
        for awb in (info.get("awbs") if isinstance(info.get("awbs"), list) else []):
            clean_awb = str(awb or "").strip()
            if clean_awb and clean_awb not in seen_awbs:
                awbs.append(clean_awb)
                seen_awbs.add(clean_awb)
        gha = str(info.get("gha") or "").strip()
        if not display_uld or not awbs or not gha:
            continue
        clean[uld] = {
            "uld_number": display_uld,
            "awbs": awbs,
            "gha": gha,
            "edited_at": str(info.get("edited_at") or datetime.now().isoformat()),
            "edited_by": str(info.get("edited_by") or os.environ.get("USERNAME", "unknown")),
            "change_summary": str(info.get("change_summary") or "").strip(),
        }
    target = get_overrides_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="uld_overrides.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"overrides": clean, "saved_at": datetime.now().isoformat()},
                      f, indent=2, ensure_ascii=False)
            f.flush()
        try:
            os.replace(tmp, target)
        except OSError:
            with open(target, "w", encoding="utf-8") as f:
                json.dump({"overrides": clean, "saved_at": datetime.now().isoformat()},
                          f, indent=2, ensure_ascii=False)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def update_uld_details(
    old_display: str,
    new_display: str,
    *,
    awbs: list[str],
    gha: str,
    revert: bool = False,
    previous_awbs: list[str] | None = None,
    previous_gha: str = "",
) -> bool:
    old = _clean_uld(old_display)
    new = _clean_uld(new_display)
    clean_awbs = []
    seen_awbs = set()
    for awb in awbs or []:
        clean_awb = str(awb or "").strip()
        if clean_awb and clean_awb not in seen_awbs:
            clean_awbs.append(clean_awb)
            seen_awbs.add(clean_awb)
    gha = str(gha or "").strip()
    if not old or not new or not clean_awbs or not gha:
        return False
    edited_at = datetime.now().isoformat()
    edited_by = os.environ.get("USERNAME", "unknown")
    change_summary = _build_uld_change_summary(
        old,
        new,
        old_awbs=previous_awbs,
        new_awbs=clean_awbs,
        old_gha=previous_gha,
        new_gha=gha,
    )

    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        for stack in stacks:
            for uld in _clean_uld_list(stack.get("ulds", [])):
                if uld == new and old != new:
                    return False

        changed = False
        for stack in stacks:
            replaced = False
            new_ulds = []
            for uld in stack.get("ulds", []):
                if _clean_uld(uld) == old:
                    new_ulds.append(new)
                    replaced = True
                else:
                    new_ulds.append(uld)
            if replaced:
                stack["ulds"] = new_ulds
                manual = _normalize_manual_ulds(stack.get("manual_ulds"))
                if old in manual:
                    info = manual.pop(old)
                    manual_old_awbs = list(info.get("awbs") or [])
                    manual_old_gha = str(info.get("gha") or "")
                    manual_change_summary = _build_uld_change_summary(
                        old,
                        new,
                        old_awbs=previous_awbs if previous_awbs is not None else manual_old_awbs,
                        new_awbs=clean_awbs,
                        old_gha=previous_gha or manual_old_gha,
                        new_gha=gha,
                    )
                    info.update({
                        "uld_number": new,
                        "awb": clean_awbs[0] if clean_awbs else "",
                        "awbs": clean_awbs,
                        "gha": gha,
                    })
                    if revert:
                        info.pop("edited_at", None)
                        info.pop("edited_by", None)
                        info.pop("change_summary", None)
                    else:
                        info["edited_at"] = edited_at
                        info["edited_by"] = edited_by
                        info["change_summary"] = manual_change_summary
                    manual[new] = info
                    stack["manual_ulds"] = manual
                _touch_stack(stack)
                changed = True
        if changed:
            _write_unlocked(stacks)

        renames = get_uld_renames()
        if revert:
            # Edit landed back on the source values → drop the rename + override so
            # the ULD returns to its pristine identity (no "szerk." marker).
            for key, value in list(renames.items()):
                if key in (old, new) or value in (old, new):
                    renames.pop(key, None)
            _write_renames_unlocked(renames)
            overrides = get_uld_overrides()
            for key in (old, new):
                overrides.pop(key, None)
            for key, info in list(overrides.items()):
                if _clean_uld(info.get("uld_number")) in (old, new):
                    overrides.pop(key, None)
            _write_overrides_unlocked(overrides)
            _invalidate_read_cache()
            return True

        if old != new:
            repointed = False
            for key, value in list(renames.items()):
                if value == old:
                    renames[key] = new
                    repointed = True
            if not repointed:
                renames[old] = new
            _write_renames_unlocked(renames)

        overrides = get_uld_overrides()
        override_key = old
        for key, value in list(renames.items()):
            if value == new:
                override_key = key
                break
        if old != new:
            overrides.pop(old, None)
            overrides.pop(new, None)
        overrides[override_key] = {
            "uld_number": new,
            "awbs": clean_awbs,
            "gha": gha,
            "edited_at": edited_at,
            "edited_by": edited_by,
            "change_summary": change_summary,
        }
        _write_overrides_unlocked(overrides)
        _invalidate_read_cache()
        return True
    finally:
        _release_lock()


def _next_gha_number(stacks: list[dict]) -> int:
    """Return max(existing GHA/Stack numbers) + 1 for auto-naming."""
    max_n = 0
    for s in stacks:
        sname = str(s.get("name") or "").strip()
        for prefix in ("GHA", "Stack", *_CANONICAL_STACK_GHAS):
            stem = f"{prefix} "
            if sname.casefold().startswith(stem.casefold()):
                suffix = sname[len(stem):].strip()
                if suffix.isdigit():
                    max_n = max(max_n, int(suffix))
    return max_n + 1


def _resolve_created_stack_identity(name: str, stack_gha: str, next_number: int) -> tuple[str, str]:
    requested_name = str(name or "").strip()
    requested_gha = str(stack_gha or "").strip()
    name_as_gha = _canonical_stack_gha(requested_name)
    if name_as_gha and (not requested_gha or requested_gha.casefold() == name_as_gha.casefold()):
        requested_gha = name_as_gha
        requested_name = ""
    return requested_name or f"GHA {next_number}", requested_gha


def _selection_key(uld_numbers: list[str]) -> str:
    return "|".join(sorted(set(_clean_uld_list(uld_numbers))))


def _stack_has_exact_ulds(stack: dict, clean_ulds: list[str]) -> bool:
    wanted = set(_clean_uld_list(clean_ulds))
    existing = set(_clean_uld_list(stack.get("ulds", [])))
    return bool(wanted) and existing == wanted and len(existing) == len(wanted)


def _set_stack_gha_if_missing(stack: dict, stack_gha: str) -> bool:
    stack_gha = str(stack_gha or "").strip()
    if not stack_gha or str(stack.get("stack_gha") or "").strip():
        return False
    stack["stack_gha"] = stack_gha
    _touch_stack(stack)
    return True


def create_stack(name: str = "", client_op_id: str | None = None, stack_gha: str = "") -> dict:
    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        op_id = str(client_op_id or "").strip()
        if op_id:
            for stack in stacks:
                if (str(stack.get("create_client_op_id") or "") == op_id
                        and not _clean_uld_list(stack.get("ulds", []))
                        and str(stack.get("create_action") or "empty") in {"", "empty"}):
                    return stack
        n = _next_gha_number(stacks)
        stack_name, resolved_stack_gha = _resolve_created_stack_identity(name, stack_gha, n)
        new_stack = {
            "id":         str(uuid.uuid4()),
            "name":       stack_name,
            "created_at": datetime.now().isoformat(),
            "created_by": os.environ.get("USERNAME", "unknown"),
            "updated_at": datetime.now().isoformat(),
            "updated_by": os.environ.get("USERNAME", "unknown"),
            "updated_on": socket.gethostname(),
            "revision":   1,
            "ulds":       [],
        }
        if op_id:
            new_stack["create_client_op_id"] = op_id
            new_stack["create_action"] = "empty"
        if resolved_stack_gha:
            new_stack["stack_gha"] = resolved_stack_gha
        stacks.append(new_stack)
        _write_unlocked(stacks)
        return new_stack
    finally:
        _release_lock()


def create_stack_with_ulds(
    name: str = "",
    uld_numbers: list[str] | None = None,
    position: int | None = None,
    client_op_id: str | None = None,
    stack_gha: str = "",
    allow_dispatched_ulds=None,
) -> dict | None:
    clean_ulds = _clean_uld_list(uld_numbers or [])
    if not clean_ulds:
        return None

    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        op_id = str(client_op_id or "").strip()
        selection_key = _selection_key(clean_ulds)
        if op_id:
            for stack in stacks:
                if (str(stack.get("create_client_op_id") or "") == op_id
                        and _stack_has_exact_ulds(stack, clean_ulds)
                        and str(stack.get("create_action") or "with_ulds") in {"", "with_ulds"}
                        and (not str(stack.get("create_selection_key") or "").strip()
                             or str(stack.get("create_selection_key") or "").strip() == selection_key)):
                    changed = _set_stack_gha_if_missing(stack, stack_gha)
                    if changed:
                        _write_unlocked(stacks)
                    return stack
            stacks = [
                stack for stack in stacks
                if not (
                    str(stack.get("create_client_op_id") or "") == op_id
                    and not _clean_uld_list(stack.get("ulds", []))
                    and str(stack.get("create_action") or "empty") in {"", "empty"}
                )
            ]

        allowed_dispatched = {_clean_uld(u) for u in (allow_dispatched_ulds or []) if u}
        for stack in stacks:
            if _stack_has_exact_ulds(stack, clean_ulds):
                if stack.get("dispatched") and set(clean_ulds) & allowed_dispatched:
                    continue
                changed = _set_stack_gha_if_missing(stack, stack_gha)
                if changed:
                    _write_unlocked(stacks)
                return stack

        _assert_ulds_not_in_prepared(stacks, clean_ulds, allowed_dispatched)
        moving = set(clean_ulds)
        manual_meta = _collect_manual_meta(stacks, moving)
        before_counts = {s.get("id"): len(_clean_uld_list(s.get("ulds", []))) for s in stacks}
        for stack in stacks:
            before = list(stack.get("ulds", []))
            stack["ulds"] = [uld for uld in before if uld not in moving]
            if stack["ulds"] != before:
                _drop_manual_meta(stack, moving)
                _touch_stack(stack)
        # A selection can pull ULDs out of stacks that then hold nothing. Building
        # a fresh stack must not leave those husks behind (operator confusion +
        # stray empty cards), so drop any stack emptied by this move. Stacks that
        # were already empty before this op (deliberate drag targets) are kept.
        stacks = _prune_emptied_source_stacks(stacks, before_counts)

        n = _next_gha_number(stacks)
        stack_gha = str(stack_gha or "").strip()
        stack_name, resolved_stack_gha = _resolve_created_stack_identity(name, stack_gha, n)
        new_stack = {
            "id":         str(uuid.uuid4()),
            "name":       stack_name,
            "created_at": datetime.now().isoformat(),
            "created_by": os.environ.get("USERNAME", "unknown"),
            "updated_at": datetime.now().isoformat(),
            "updated_by": os.environ.get("USERNAME", "unknown"),
            "updated_on": socket.gethostname(),
            "revision":   1,
            "ulds":       _insert_top_down([], clean_ulds, position),
        }
        if op_id:
            new_stack["create_client_op_id"] = op_id
            new_stack["create_action"] = "with_ulds"
            new_stack["create_selection_key"] = selection_key
        if resolved_stack_gha:
            new_stack["stack_gha"] = resolved_stack_gha
        _attach_manual_meta(new_stack, manual_meta)
        stacks.append(new_stack)
        _write_unlocked(stacks)
        return new_stack
    finally:
        _release_lock()


def delete_stack(stack_id: str, expected_revision=None) -> bool:
    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        target = next((s for s in stacks if s.get("id") == stack_id), None)
        if target is None:
            return False
        _assert_revision(target, expected_revision)
        stacks = [s for s in stacks if s.get("id") != stack_id]
        _write_unlocked(stacks)
        return True
    finally:
        _release_lock()


def ensure_stack_gha(stack_id: str, stack_gha: str) -> dict | None:
    stack_gha = str(stack_gha or "").strip()
    if not stack_id or not stack_gha:
        return None
    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        for stack in stacks:
            if stack.get("id") == stack_id:
                changed = _set_stack_gha_if_missing(stack, stack_gha)
                if changed:
                    _write_unlocked(stacks)
                return stack
        return None
    finally:
        _release_lock()


def _clean_uld_list(uld_numbers) -> list[str]:
    values = uld_numbers if isinstance(uld_numbers, list) else [uld_numbers]
    clean_ulds: list[str] = []
    seen: set[str] = set()
    for uld in values:
        clean = _clean_uld(uld)
        if clean and clean not in seen:
            clean_ulds.append(clean)
            seen.add(clean)
    return clean_ulds


def _insert_top_down(ulds_bottom_to_top: list[str], incoming_top_to_bottom: list[str], position: int | None) -> list[str]:
    result = list(ulds_bottom_to_top)
    insert_bottom_to_top = list(reversed(incoming_top_to_bottom))
    if position is None or position < 0:
        result.extend(insert_bottom_to_top)
    else:
        bottom_idx = max(0, min(len(result), len(result) - int(position)))
        result[bottom_idx:bottom_idx] = insert_bottom_to_top
    return result


def _collect_manual_meta(stacks: list[dict], ulds: set[str]) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    for stack in stacks:
        manual = _normalize_manual_ulds(stack.get("manual_ulds"))
        for uld in ulds:
            if uld in manual and uld not in meta:
                meta[uld] = manual[uld]
    return meta


def _drop_manual_meta(stack: dict, ulds: set[str]) -> None:
    manual = _normalize_manual_ulds(stack.get("manual_ulds"))
    changed = False
    for uld in list(manual):
        if uld in ulds or uld not in set(stack.get("ulds", [])):
            manual.pop(uld, None)
            changed = True
    if manual:
        stack["manual_ulds"] = manual
    elif changed or "manual_ulds" in stack:
        stack.pop("manual_ulds", None)


def _attach_manual_meta(stack: dict, meta: dict[str, dict]) -> None:
    if not meta:
        return
    manual = _normalize_manual_ulds(stack.get("manual_ulds"))
    for uld, info in meta.items():
        if uld in stack.get("ulds", []):
            manual[uld] = info
    if manual:
        stack["manual_ulds"] = manual


def _prune_emptied_source_stacks(stacks: list[dict], before_counts: dict[str, int], protect_ids=()) -> list[dict]:
    """Drop stacks that held ULDs before a move but are now empty as a side
    effect of it. A deliberately-empty stack (before_count == 0, e.g. a freshly
    created drag target) and any protected id (the move target) are kept, so
    this never removes an intentional empty stack."""
    protect = set(protect_ids or ())
    kept: list[dict] = []
    for stack in stacks:
        sid = stack.get("id")
        if (sid not in protect
                and before_counts.get(sid, 0) > 0
                and not _clean_uld_list(stack.get("ulds", []))):
            continue  # emptied by the move → remove the husk
        kept.append(stack)
    return kept


def add_from_list_to_stack(
    stack_id: str,
    uld_numbers: list[str],
    position: int | None = None,
    expected_revision=None,
    allow_dispatched_ulds=None,
    stack_gha: str = "",
) -> bool:
    """Add ULDs to a target stack using UI top-to-bottom order.

    This is intentionally named for the list-to-stack operation. It still removes
    the same ULDs from any existing stack first, so duplicate stack entries cannot
    survive stale client state.
    """
    return add_ulds_to_stack(
        stack_id,
        _clean_uld_list(uld_numbers),
        position=position,
        expected_revision=expected_revision,
        allow_dispatched_ulds=allow_dispatched_ulds,
        stack_gha=stack_gha,
    )


def move_between_stacks(
    source_stack_id: str,
    target_stack_id: str,
    uld_numbers: list[str],
    position: int | None = None,
    source_expected_revision=None,
    target_expected_revision=None,
    stack_gha: str = "",
) -> bool:
    """Move ULDs from one stack to another, preserving UI top-to-bottom order."""
    clean_ulds = _clean_uld_list(uld_numbers)
    if not clean_ulds or not target_stack_id:
        return False
    moving = set(clean_ulds)

    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        manual_meta = _collect_manual_meta(stacks, moving)
        source = next((s for s in stacks if s.get("id") == source_stack_id), None) if source_stack_id else None
        target = next((s for s in stacks if s.get("id") == target_stack_id), None)
        if target is None:
            return False
        if source_stack_id and source is None:
            return False
        if source is target:
            return False

        if source is not None:
            _assert_revision(source, source_expected_revision)
            _assert_not_prepared(source, "ULD elmozdítása")
            source_ulds = set(source.get("ulds", []))
            if not moving.issubset(source_ulds):
                return False
        _assert_revision(target, target_expected_revision)
        _assert_not_prepared(target, "ULD áthelyezés")
        _bind_stack_gha(target, stack_gha)
        _assert_ulds_not_in_prepared(stacks, list(clean_ulds))

        for s in stacks:
            before = list(s.get("ulds", []))
            s["ulds"] = [u for u in before if u not in moving]
            if s["ulds"] != before:
                _drop_manual_meta(s, moving)
                _touch_stack(s)

        target["ulds"] = _insert_top_down(list(target.get("ulds", [])), clean_ulds, position)
        _attach_manual_meta(target, manual_meta)
        _touch_stack(target)
        _write_unlocked(stacks)
        return True
    finally:
        _release_lock()


def remove_from_stack(stack_id: str, uld_numbers: list[str], expected_revision=None) -> bool:
    """Remove ULDs from one explicit stack. Missing ULDs are idempotent success."""
    clean_ulds = set(_clean_uld_list(uld_numbers))
    if not clean_ulds or not stack_id:
        return False

    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        moving = set(clean_ulds)
        target = next((s for s in stacks if s.get("id") == stack_id), None)
        if target is None:
            return False
        _assert_revision(target, expected_revision)
        _assert_not_prepared(target, "ULD kivétel")
        before = list(target.get("ulds", []))
        target["ulds"] = [u for u in before if u not in clean_ulds]
        if target["ulds"] != before:
            _drop_manual_meta(target, moving)
            _touch_stack(target)
            _write_unlocked(stacks)
        return True
    finally:
        _release_lock()


def add_uld_to_stack(stack_id: str, uld_number: str, position: int | None = None, expected_revision=None) -> bool:
    """Add or move a ULD into a stack.

    Removes the ULD from any other stack first (move semantics).

    Args:
        position: top-to-bottom index (0 = top). None or negative → append to top.
                  ULD list is stored bottom-to-top, so we translate.
    """
    uld_number = _clean_uld(uld_number)
    if not uld_number:
        return False
    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        moving = {uld_number}
        manual_meta = _collect_manual_meta(stacks, moving)
        target = next((s for s in stacks if s.get("id") == stack_id), None)
        if target is None:
            return False
        _assert_revision(target, expected_revision)
        _assert_not_prepared(target, "ULD hozzáadás")
        _assert_ulds_not_in_prepared(stacks, [uld_number])
        # Remove from every stack first (including target, so we can re-position)
        for s in stacks:
            if uld_number in s.get("ulds", []):
                s["ulds"] = [u for u in s["ulds"] if u != uld_number]
                _drop_manual_meta(s, moving)
                _touch_stack(s)
        # Insert in target stack
        for s in stacks:
            if s.get("id") == stack_id:
                ulds = list(s.get("ulds", []))
                if position is None or position < 0:
                    ulds.append(uld_number)  # bottom-to-top tail = visual top
                else:
                    # top-to-bottom index N → bottom-to-top index = len - N
                    bottom_idx = max(0, len(ulds) - int(position))
                    ulds.insert(bottom_idx, uld_number)
                s["ulds"] = ulds
                _attach_manual_meta(s, manual_meta)
                _touch_stack(s)
                _write_unlocked(stacks)
                return True
        return False
    finally:
        _release_lock()


def add_ulds_to_stack(
    stack_id: str,
    uld_numbers: list[str],
    position: int | None = None,
    expected_revision=None,
    allow_dispatched_ulds=None,
    stack_gha: str = "",
) -> bool:
    """Add or move multiple ULDs into a stack, preserving the given top-to-bottom order."""
    clean_ulds: list[str] = []
    seen: set[str] = set()
    for uld in uld_numbers:
        uld = _clean_uld(uld)
        if uld and uld not in seen:
            clean_ulds.append(uld)
            seen.add(uld)
    if not clean_ulds:
        return False

    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        moving = set(clean_ulds)
        manual_meta = _collect_manual_meta(stacks, moving)
        target = next((s for s in stacks if s.get("id") == stack_id), None)
        if target is None:
            return False
        _assert_revision(target, expected_revision)
        _assert_not_prepared(target, "ULD hozzáadás")
        _bind_stack_gha(target, stack_gha)
        _assert_ulds_not_in_prepared(stacks, clean_ulds, allow_dispatched_ulds)
        for s in stacks:
            if s.get("ulds"):
                before = list(s["ulds"])
                s["ulds"] = [u for u in s["ulds"] if u not in seen]
                if s["ulds"] != before:
                    _drop_manual_meta(s, moving)
                    _touch_stack(s)

        for s in stacks:
            if s.get("id") == stack_id:
                ulds = list(s.get("ulds", []))
                insert_bottom_to_top = list(reversed(clean_ulds))
                if position is None or position < 0:
                    ulds.extend(insert_bottom_to_top)
                else:
                    bottom_idx = max(0, len(ulds) - int(position))
                    ulds[bottom_idx:bottom_idx] = insert_bottom_to_top
                s["ulds"] = ulds
                _attach_manual_meta(s, manual_meta)
                _touch_stack(s)
                _write_unlocked(stacks)
                return True
        return False
    finally:
        _release_lock()


def add_manual_uld_to_stack(
    stack_id: str,
    *,
    uld_number: str,
    awb: str,
    gha: str,
    am_time: str = "",
    position: int | None = None,
    expected_revision=None,
) -> bool:
    uld = _clean_uld(uld_number)
    awb = str(awb or "").strip()
    gha = str(gha or "").strip()
    am_time = str(am_time or "").strip()
    if not stack_id or not uld or not gha:
        return False

    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        target = next((s for s in stacks if s.get("id") == stack_id), None)
        if target is None:
            return False
        _assert_revision(target, expected_revision)
        if gha and not str(target.get("stack_gha") or "").strip():
            target["stack_gha"] = gha
        _assert_not_prepared(target, "egyedi ULD hozzáadás")
        _assert_ulds_not_in_prepared(stacks, [uld])

        moving = {uld}
        for s in stacks:
            if s.get("ulds"):
                before = list(s["ulds"])
                s["ulds"] = [item for item in s["ulds"] if item != uld]
                if s["ulds"] != before:
                    _drop_manual_meta(s, moving)
                    _touch_stack(s)

        target["ulds"] = _insert_top_down(list(target.get("ulds", [])), [uld], position)
        manual = _normalize_manual_ulds(target.get("manual_ulds"))
        manual[uld] = {
            "uld_number": uld,
            "awb": awb,
            "awbs": [awb] if awb else [],
            "gha": gha,
            "am_time": am_time,
            "is_manual": True,
        }
        target["manual_ulds"] = manual
        _touch_stack(target)
        _write_unlocked(stacks)
        return True
    finally:
        _release_lock()


def remove_uld_from_stack(uld_number: str) -> bool:
    """Remove ULD from whichever stack it's in. Blocks if the host stack is prepared."""
    uld_number = _clean_uld(uld_number)
    if not uld_number:
        return False
    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        # Refuse if the ULD is in a prepared stack (the lock).
        _assert_ulds_not_in_prepared(stacks, [uld_number])
        changed = False
        for s in stacks:
            before = len(s.get("ulds", []))
            s["ulds"] = [u for u in s.get("ulds", []) if u != uld_number]
            if len(s["ulds"]) != before:
                _drop_manual_meta(s, {uld_number})
                changed = True
                _touch_stack(s)
        if changed:
            _write_unlocked(stacks)
        return changed
    finally:
        _release_lock()


def remove_ulds_from_stack(uld_numbers: list[str]) -> bool:
    """Remove multiple ULDs from whichever stacks contain them. Locked stacks refuse."""
    clean_ulds = {_clean_uld(u) for u in uld_numbers}
    clean_ulds.discard("")
    if not clean_ulds:
        return False

    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        _assert_ulds_not_in_prepared(stacks, list(clean_ulds))
        changed = False
        for s in stacks:
            before = len(s.get("ulds", []))
            s["ulds"] = [u for u in s.get("ulds", []) if u not in clean_ulds]
            if len(s["ulds"]) != before:
                _drop_manual_meta(s, clean_ulds)
                changed = True
                _touch_stack(s)
        if changed:
            _write_unlocked(stacks)
        return changed
    finally:
        _release_lock()


def reorder_stack(stack_id: str, ordered_ulds: list[str], expected_revision=None) -> bool:
    """Replace a stack's ULD order (bottom-to-top). Blocked on prepared stacks."""
    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        for s in stacks:
            if s.get("id") == stack_id:
                _assert_revision(s, expected_revision)
                _assert_not_prepared(s, "sorrend módosítása")
                clean_order: list[str] = []
                seen: set[str] = set()
                for uld in ordered_ulds:
                    clean = _clean_uld(uld)
                    if clean and clean not in seen:
                        clean_order.append(clean)
                        seen.add(clean)
                current = _clean_uld_list(s.get("ulds", []))
                if len(clean_order) != len(current) or set(clean_order) != set(current):
                    raise StackConflict(
                        "A stack tartalma közben megváltozott; a sorrend nem írhatja felül a tételeket."
                    )
                if clean_order == current:
                    return True
                s["ulds"] = clean_order
                _touch_stack(s)
                _write_unlocked(stacks)
                return True
        return False
    finally:
        _release_lock()


def rename_stack(stack_id: str, name: str, expected_revision=None) -> bool:
    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        for s in stacks:
            if s.get("id") == stack_id:
                _assert_revision(s, expected_revision)
                s["name"] = name
                _touch_stack(s)
                _write_unlocked(stacks)
                return True
        return False
    finally:
        _release_lock()


def set_stack_prepared(stack_id: str, prepared: bool, expected_revision=None) -> bool:
    """Mark a stack as prepared ("Összekészítve") or unmark.

    Prepared stacks render faded and sort to the end of the stack grid;
    ULDs in prepared stacks sort to the end of the ULD list.
    """
    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        for s in stacks:
            if s.get("id") == stack_id:
                _assert_revision(s, expected_revision)
                if prepared:
                    s["prepared"] = True
                    s["prepared_at"] = datetime.now().isoformat()
                    s["prepared_by"] = os.environ.get("USERNAME", "unknown")
                else:
                    s.pop("prepared", None)
                    s.pop("prepared_at", None)
                    s.pop("prepared_by", None)
                _touch_stack(s)
                _write_unlocked(stacks)
                return True
        return False
    finally:
        _release_lock()


def set_stack_dispatched(stack_id: str, dispatched: bool = True, expected_revision=None, dispatch_plate: str = "") -> bool:
    """Move a stack between active and dispatched history."""
    _acquire_lock()
    try:
        stacks = _read_unlocked(repair=True)
        for s in stacks:
            if s.get("id") == stack_id:
                _assert_revision(s, expected_revision)
                if dispatched:
                    s["dispatched"] = True
                    s["dispatched_at"] = datetime.now().isoformat()
                    s["dispatched_by"] = os.environ.get("USERNAME", "unknown")
                    s["dispatched_on"] = socket.gethostname()
                    plate = str(dispatch_plate or "").strip().upper()
                    if plate:
                        s["dispatch_plate"] = plate
                    else:
                        s.pop("dispatch_plate", None)
                    s["prepared"] = True
                    s.setdefault("prepared_at", s["dispatched_at"])
                    s.setdefault("prepared_by", s["dispatched_by"])
                else:
                    s.pop("dispatched", None)
                    s.pop("dispatched_at", None)
                    s.pop("dispatched_by", None)
                    s.pop("dispatched_on", None)
                    s.pop("dispatch_plate", None)
                    # Revert also unlocks the stack (dispatch had marked it prepared),
                    # so it returns fully editable to the active view.
                    s.pop("prepared", None)
                    s.pop("prepared_at", None)
                    s.pop("prepared_by", None)
                _touch_stack(s)
                _write_unlocked(stacks)
                return True
        return False
    finally:
        _release_lock()
