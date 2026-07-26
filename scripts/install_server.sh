#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${HUI_INSTALL_DIR:-/opt/hui/hui-chat}"
SERVICE_USER="${HUI_SERVICE_USER:-hui}"
SERVICE_GROUP="${HUI_SERVICE_GROUP:-hui}"
DB_NAME="${HUI_DB_NAME:-hui_chat}"
DB_USER="${HUI_DB_USER:-hui_chat}"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NONINTERACTIVE=0
SKIP_PACKAGES=0
SKIP_SETUP=0

for arg in "$@"; do
  case "$arg" in
    --non-interactive) NONINTERACTIVE=1 ;;
    --skip-packages) SKIP_PACKAGES=1 ;;
    --skip-setup) SKIP_SETUP=1 ;;
    -h|--help)
      echo "Usage: sudo bash scripts/install_server.sh [--skip-packages] [--skip-setup] [--non-interactive]"
      exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run this installer with sudo/root." >&2
  exit 2
fi

log() { printf '\n==> %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 2; }; }

install_packages() {
  [[ $SKIP_PACKAGES -eq 1 ]] && return
  if command -v pacman >/dev/null 2>&1; then
    log "Installing Arch/EndeavourOS packages"
    pacman -Syu --needed --noconfirm python python-pip postgresql valkey rsync
  elif command -v apt-get >/dev/null 2>&1; then
    log "Installing Debian/Ubuntu packages"
    apt-get update
    if apt-cache show valkey-server >/dev/null 2>&1; then
      DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip postgresql valkey-server valkey-tools rsync
    else
      DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip postgresql redis-server redis-tools rsync
    fi
  else
    echo "Unsupported package manager. Install Python 3, PostgreSQL, Redis/Valkey, and rsync, then rerun with --skip-packages." >&2
    exit 2
  fi
}

enable_first_available_service() {
  local unit
  for unit in "$@"; do
    if systemctl list-unit-files "$unit" --no-legend 2>/dev/null | grep -q "^${unit}"; then
      systemctl enable --now "$unit"
      printf '%s' "$unit"
      return 0
    fi
  done
  return 1
}

start_services() {
  if command -v pacman >/dev/null 2>&1; then
    if [[ ! -s /var/lib/postgres/data/PG_VERSION ]]; then
      log "Initializing PostgreSQL data directory"
      install -d -o postgres -g postgres -m 0700 /var/lib/postgres/data
      sudo -u postgres initdb -D /var/lib/postgres/data --locale=C.UTF-8 --encoding=UTF8
    fi
  fi
  enable_first_available_service postgresql.service >/dev/null || { echo "PostgreSQL systemd service was not found." >&2; exit 2; }
  KV_SERVICE="$(enable_first_available_service valkey.service valkey-server.service redis.service redis-server.service)" || {
    echo "Valkey/Redis systemd service was not found." >&2
    exit 2
  }
  log "Key-value service ready: $KV_SERVICE"
}

install_packages
need python3
need systemctl
need sudo
need psql
need rsync

getent group "$SERVICE_GROUP" >/dev/null || groupadd --system "$SERVICE_GROUP"
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --gid "$SERVICE_GROUP" --home-dir /opt/hui --shell /usr/bin/nologin "$SERVICE_USER"
start_services

# A reinstall is also an upgrade. Stop every active Hui Chat process before
# replacing code so Python never runs a mixture of old and new modules.
systemctl stop hui-chat.service hui-chat-janitor.service 2>/dev/null || true
while read -r active_unit; do
  [[ -n "$active_unit" ]] && systemctl stop "$active_unit" 2>/dev/null || true
done < <(systemctl list-units --all 'hui-chat@*.service' --no-legend 2>/dev/null | awk '{print $1}')

log "Installing Hui Chat files"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 "$INSTALL_DIR"
rsync -a --delete \
  --exclude '.git/' --exclude '.venv/' --exclude '.env' --exclude 'server_config.json' \
  --exclude 'logs/' --exclude 'uploads/' --exclude 'private_uploads/' --exclude 'instance/' \
  "$PROJECT_DIR/" "$INSTALL_DIR/"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"

log "Creating virtual environment and installing Python dependencies"
if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  sudo -u "$SERVICE_USER" python3 -m venv "$INSTALL_DIR/.venv"
fi
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

ENV_DIR="${HUI_ENV_DIR:-/etc/hui}"
ENV_PATH="$ENV_DIR/hui-chat.env"
install -d -o root -g "$SERVICE_GROUP" -m 0750 "$ENV_DIR"

EXISTING_DATABASE_URL=""
if [[ -f "$ENV_PATH" && "${HUI_RESET_DATABASE_CREDENTIALS:-0}" != "1" ]]; then
  EXISTING_DATABASE_URL="$(HUI_ENV_PATH="$ENV_PATH" "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import os
from pathlib import Path
from secret_manager import read_env_value
print(read_env_value(Path(os.environ['HUI_ENV_PATH']), 'DATABASE_URL'))
PY
)"
fi

if [[ -n "$EXISTING_DATABASE_URL" ]]; then
  DATABASE_URL_VALUE="$EXISTING_DATABASE_URL"
  log "Preserving the existing PostgreSQL connection and credentials"
else
  DB_PASSWORD="$("$INSTALL_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  log "Creating PostgreSQL role and database"
  sudo -u postgres psql --set=role="$DB_USER" --set=password="$DB_PASSWORD" --set=db="$DB_NAME" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'role', :'password') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SQL
  DATABASE_URL_VALUE="postgresql://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}"
fi

log "Creating or updating the protected environment file"
cd "$INSTALL_DIR"
INSTALL_DIR="$INSTALL_DIR" DATABASE_URL_VALUE="$DATABASE_URL_VALUE" HUI_ENV_PATH="$ENV_PATH" \
  "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import os
from pathlib import Path
from secret_manager import generate_secret_bundle, read_env_value, write_env_secrets

p = Path(os.environ['HUI_ENV_PATH'])
updates = {
    'HUI_PERSIST_SECRETS': '0',
    'HUI_CONFIG': f"{os.environ['INSTALL_DIR']}/server_config.json",
    'DATABASE_URL': os.environ['DATABASE_URL_VALUE'],
    'HUI_RUN_MODE': 'production',
    'HUI_PRODUCTION_MODE': '1',
    'HUI_WORKERS': '1',
    'HUI_PRODUCTION_WORKERS': '1',
    'HUI_SOCKETIO_ASYNC': 'threading',
    'HUI_GUNICORN_WORKER_CLASS': 'gthread',
}
for key, generated in generate_secret_bundle(include_crypto=True).items():
    updates[key] = read_env_value(p, key) or generated
write_env_secrets(updates, path=p)
PY
chown root:"$SERVICE_GROUP" "$ENV_PATH"
chmod 0640 "$ENV_PATH"
ln -sfn "$ENV_PATH" "$INSTALL_DIR/.env"
chown -h "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR/.env"

install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0750 \
  "$INSTALL_DIR/logs" "$INSTALL_DIR/uploads" "$INSTALL_DIR/private_uploads" "$INSTALL_DIR/instance" "$INSTALL_DIR/static/uploads"

# JSON is authoritative for non-secret tuning. Remove stale installer-generated
# local defaults before setup/production checks so they cannot override corrected
# values. Preserve custom Redis URLs (including credentials/remote hosts) and
# non-default proxy trust values as intentional administrator overrides.
HUI_ENV_PATH="$ENV_PATH" "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import os
import re
from pathlib import Path
from secret_manager import read_env_value

p = Path(os.environ['HUI_ENV_PATH'])
remove = {
    'HUI_BIND', 'HUI_PRODUCTION_BIND', 'HUI_PRODUCTION_INSTANCES',
    'HUI_INSTANCE_BASE_PORT', 'HUI_GUNICORN_THREADS',
    'HUI_DB_POOL_WAIT_SECONDS',
}
installer_defaults = {
    'HUI_RATE_LIMIT_STORAGE_URI': {'redis://127.0.0.1:6379/0'},
    'HUI_SIMPLE_RATE_LIMIT_STORAGE_URI': {'redis://127.0.0.1:6379/0'},
    'HUI_SOCKETIO_MESSAGE_QUEUE': {'redis://127.0.0.1:6379/1'},
    'HUI_SHARED_STATE_REDIS_URL': {'redis://127.0.0.1:6379/2'},
    'HUI_FORWARDED_ALLOW_IPS': {'127.0.0.1'},
}
for key, known_values in installer_defaults.items():
    if read_env_value(p, key) in known_values:
        remove.add(key)
assignment = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=')
lines = []
for line in p.read_text(encoding='utf-8').splitlines():
    match = assignment.match(line)
    if match and match.group(1) in remove:
        continue
    lines.append(line)
p.write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
PY
chown root:"$SERVICE_GROUP" "$ENV_PATH"
chmod 0640 "$ENV_PATH"

if [[ $SKIP_SETUP -eq 0 ]]; then
  if [[ $NONINTERACTIVE -eq 1 ]]; then
    [[ -f "$INSTALL_DIR/server_config.json" ]] || cp "$INSTALL_DIR/server_config.example.json" "$INSTALL_DIR/server_config.json"
    chown "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR/server_config.json"
  else
    log "Launching the Hui Chat setup wizard"
    sudo -u "$SERVICE_USER" env TERM="${TERM:-xterm-256color}" HUI_SETUP_TUI=1 \
      "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/main.py" --config "$INSTALL_DIR/server_config.json" --setup-only
  fi
fi

log "Auto-correcting safe production conflicts"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/main.py" --config "$INSTALL_DIR/server_config.json" --production-config-fix --production-config-blocking-only --production-live-check

log "Running setup checks and migrations"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/main.py" --config "$INSTALL_DIR/server_config.json" --redis-socketio-check --redis-blocking-only --redis-live-check
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/tools/setup_readiness_doctor.py" --config "$INSTALL_DIR/server_config.json" --live
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/main.py" --config "$INSTALL_DIR/server_config.json" --migrate

log "Installing systemd units"
SYSTEMD_DIR="${HUI_SYSTEMD_DIR:-/etc/systemd/system}"
"$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/tools/render_systemd_units.py" \
  --project-root "$INSTALL_DIR" \
  --output-dir "$SYSTEMD_DIR" \
  --env-path "$ENV_PATH" \
  --service-user "$SERVICE_USER" \
  --service-group "$SERVICE_GROUP"
systemctl daemon-reload

read -r INSTANCE_COUNT BASE_PORT PORT_STEP < <(
  sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/python" - "$INSTALL_DIR/server_config.json" <<'PY'
import json, sys
cfg = json.load(open(sys.argv[1], encoding='utf-8'))
count = max(1, min(10, int(cfg.get('production_instance_count') or 1)))
base = max(1, min(65535, int(cfg.get('production_instance_base_port') or cfg.get('server_port') or 5000)))
step = max(1, int(cfg.get('production_instance_port_step') or 1))
print(count, base, step)
PY
)

# Disable any previous topology before enabling the newly validated one.
systemctl disable --now hui-chat.service 2>/dev/null || true
while read -r old_unit; do
  [[ -n "$old_unit" ]] && systemctl disable --now "$old_unit" 2>/dev/null || true
done < <(systemctl list-units --all 'hui-chat@*.service' --no-legend 2>/dev/null | awk '{print $1}')

ACTIVE_WEB_UNITS=()
if [[ "$INSTANCE_COUNT" -eq 1 ]]; then
  systemctl enable hui-chat.service
  systemctl restart hui-chat.service
  ACTIVE_WEB_UNITS=(hui-chat.service)
else
  for ((index=0; index<INSTANCE_COUNT; index++)); do
    port=$((BASE_PORT + index * PORT_STEP))
    unit="hui-chat@${port}.service"
    systemctl enable "$unit"
    systemctl restart "$unit"
    ACTIVE_WEB_UNITS+=("$unit")
  done
fi
systemctl enable hui-chat-janitor.service
systemctl restart hui-chat-janitor.service
systemctl --no-pager --full status "${ACTIVE_WEB_UNITS[@]}" hui-chat-janitor.service

cat <<EOF

Hui Chat installation completed.
Project: $INSTALL_DIR
Config:  $INSTALL_DIR/server_config.json
Secrets: $ENV_PATH
Logs:    journalctl -u 'hui-chat*' -f
EOF
