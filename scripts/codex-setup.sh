#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip install -r requirements.txt
python -c "import dash, dash_bootstrap_components, pandas, openpyxl, pyxlsb, oracledb; print('Flow Manager dependencies OK')"
