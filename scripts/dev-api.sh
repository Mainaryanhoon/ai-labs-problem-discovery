#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -x "$ROOT/.venv/bin/uvicorn" ]]; then
  echo "Missing .venv. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
exec "$ROOT/.venv/bin/uvicorn" app.main:app --reload --host 0.0.0.0 --port 8000
