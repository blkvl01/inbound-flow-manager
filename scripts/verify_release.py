"""Verify that a release directory contains only intended artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from updater import validate_manifest, verify_file  # noqa: E402


def verify_release_directory(directory: Path) -> list[str]:
    errors: list[str] = []
    if not directory.is_dir():
        return [f"release directory not found: {directory}"]
    exe = directory / "FlowManager.exe"
    manifest_path = directory / "manifest.json"
    if not exe.is_file():
        errors.append("FlowManager.exe is missing")
    if not manifest_path.is_file():
        errors.append("manifest.json is missing")
    if errors:
        return errors
    try:
        manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
        package = manifest["package"]
        if not verify_file(exe, package["size"], package["sha256"]):
            errors.append("FlowManager.exe does not match manifest size/SHA-256")
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        errors.append(str(exc))
    allowed = {"FlowManager.exe", "manifest.json", "README.md", "RELEASE_NOTES.md", "UPDATER.md"}
    for child in directory.iterdir():
        if child.name not in allowed:
            errors.append(f"unexpected release artifact: {child.name}")
    if any(child.is_dir() for child in directory.iterdir()):
        errors.append("release directory must not contain subdirectories/_internal")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    errors = verify_release_directory(args.directory)
    if errors:
        for error in errors:
            print(f"[HIBA] {error}")
        return 1
    print(f"[OK] Egyfájlos kiadás ellenőrizve: {args.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
