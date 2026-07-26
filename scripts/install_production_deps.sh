#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BOOTSTRAP_PYTHON="${PYTHON_BOOTSTRAP:-${PYTHON:-python3}}"
VENV_DIR="${HUI_VENV_DIR:-$ROOT_DIR/.venv}"
PYTHON_BIN="$VENV_DIR/bin/python"

if ! command -v "$BOOTSTRAP_PYTHON" >/dev/null 2>&1; then
  echo "Python 3 was not found. Install Python 3 and rerun this script." >&2
  exit 1
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Creating Hui Chat virtual environment: $VENV_DIR"
  "$BOOTSTRAP_PYTHON" -m venv "$VENV_DIR"
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Virtual environment creation failed: $PYTHON_BIN does not exist." >&2
  exit 1
fi

echo "Installing Hui Chat production dependencies into: $($PYTHON_BIN -c 'import sys; print(sys.executable)')"
"$PYTHON_BIN" -m pip install --upgrade pip setuptools wheel
"$PYTHON_BIN" -m pip install -r requirements.txt
"$PYTHON_BIN" - <<'PY2'
from gunicorn.util import load_class
import psycopg2
import redis
import simple_websocket
load_class('gthread')
print('OK: Gunicorn gthread, simple-websocket, PostgreSQL, and Redis clients are available')
PY2

cat <<MSG

Production dependencies are ready in:
  $VENV_DIR

Next checks:
  $PYTHON_BIN main.py --production-config-check --production-live-check
  $PYTHON_BIN main.py --redis-socketio-check --redis-live-check
  $PYTHON_BIN main.py --preflight

Default production mode uses threading + gthread with one worker per instance.
Optional Eventlet mode is advanced-only; install requirements-eventlet.txt first.
MSG
