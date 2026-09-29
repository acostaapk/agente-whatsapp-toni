#!/bin/bash
# Sobe a interface admin do agente (FastAPI).
# Uso: ./run.sh   (ou: ./run.sh --port 8090)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8090}"
if [ ! -d "$DIR/.venv" ]; then
  python3 -m venv "$DIR/.venv"
  "$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"
fi
cd "$DIR"
exec "$DIR/.venv/bin/uvicorn" main:app --host 0.0.0.0 --port "$PORT"
