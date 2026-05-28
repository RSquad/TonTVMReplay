#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d "my_venv" ]; then
  source my_venv/bin/activate
elif [ -d ".venv" ]; then
  source .venv/bin/activate
else
  echo "ERROR: virtual environment not found (my_venv or .venv)"
  exit 1
fi

mkdir -p reports
exec python3 cycle_web_server.py
