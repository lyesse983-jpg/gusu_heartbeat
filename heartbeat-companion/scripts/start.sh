#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
fi
if [[ ! -f .env ]]; then
    cp .env.example .env
fi
printf '%s\n' 'Heartbeat Companion: http://127.0.0.1:8765' 'Keep this terminal running. Press Ctrl+C to stop.'
exec .venv/bin/python -m companion
