#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
python scripts/codex-test.py
