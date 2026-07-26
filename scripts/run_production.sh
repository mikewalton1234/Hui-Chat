#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
[[ -f "$ROOT_DIR/main.py" ]] || { echo "Hui Chat main.py not found in: $ROOT_DIR" >&2; exit 1; }

VENV_DIR="${HUI_VENV_DIR:-$ROOT_DIR/.venv}"
PYTHON_BIN="$VENV_DIR/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Hui Chat's project virtual environment is missing: $PYTHON_BIN" >&2
  echo "Run: $ROOT_DIR/scripts/install_production_deps.sh" >&2
  exit 1
fi
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

export HUI_CONFIG="${HUI_CONFIG:-$ROOT_DIR/server_config.json}"
export HUI_RUN_MODE="${HUI_RUN_MODE:-production}"
export HUI_PRODUCTION_MODE="${HUI_PRODUCTION_MODE:-1}"
export HUI_WORKERS="${HUI_WORKERS:-1}"
export HUI_PRODUCTION_WORKERS="${HUI_PRODUCTION_WORKERS:-$HUI_WORKERS}"
export HUI_SOCKETIO_ASYNC="${HUI_SOCKETIO_ASYNC:-threading}"
export HUI_GUNICORN_WORKER_CLASS="${HUI_GUNICORN_WORKER_CLASS:-gthread}"
export HUI_FORWARDED_ALLOW_IPS="${HUI_FORWARDED_ALLOW_IPS:-127.0.0.1}"

"$PYTHON_BIN" main.py --config "$HUI_CONFIG" --production-config-check --production-config-blocking-only --production-live-check
"$PYTHON_BIN" main.py --config "$HUI_CONFIG" --redis-socketio-check --redis-blocking-only --redis-live-check
exec "$PYTHON_BIN" main.py --config "$HUI_CONFIG" --production
