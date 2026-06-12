#!/usr/bin/env bash
# Wrapper used by .claude/launch.json: keeps all runtime writes (yfinance
# tz/cookie cache lives under $HOME) inside the project so sandboxed preview
# runners can execute the server.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data/home
export HOME="$PWD/data/home"
export PYTHONUNBUFFERED=1
exec venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
