#!/usr/bin/env bash
# Launch the stock tracker locally.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    python3.12 -m venv venv
    venv/bin/pip install -r requirements.txt
fi

exec venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 "$@"
