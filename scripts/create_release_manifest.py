"""Create the strict manifest consumed by the Flow Manager updater."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from version import APP_NAME, PACKAGE_NAME  # noqa: E402


def build_manifest(exe: Path, version: str) -> dict:
    digest = hashlib.sha256()
    size = 0
    with exe.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {
        "format": 1,
        "app_name": APP_NAME,
        "version": version,
        "package": {
            "name": PACKAGE_NAME,
            "size": size,
            "sha256": digest.hexdigest(),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.exe.is_file():
        parser.error(f"EXE not found: {args.exe}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(build_manifest(args.exe, args.version), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
