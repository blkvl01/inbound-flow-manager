"""Write the version metadata embedded in a frozen one-file executable."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version = str(args.version or "").strip()
    if not version:
        parser.error("A build version cannot be empty")
    args.output.write_text(version + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
