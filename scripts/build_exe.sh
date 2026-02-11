#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python -m pip install pyinstaller
pyinstaller \
  --name inspectelement \
  --onefile \
  --windowed \
  --add-data "assets/icon.png:assets" \
  src/inspectelement/__main__.py

echo "Built executable in dist/inspectelement"
