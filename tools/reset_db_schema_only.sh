#!/usr/bin/env bash
set -euo pipefail

# Hui Chat: wipe ALL tables without dropping the database.
# Useful when you don't have permission to DROP/CREATE DATABASE.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="$ROOT_DIR/server_config.json"
SERVER_NAME="$(python3 - "${CONFIG_FILE}" <<'PYNAME'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}
except Exception:
    data = {}
raw = str(data.get("server_name") or "Hui Chat").replace("\r", " ").replace("\n", " ").strip()
print(raw or "Hui Chat")
PYNAME
)"
trap 'echo "❌ Schema reset failed. Ensure ${SERVER_NAME} is stopped and your Postgres role can DROP/CREATE SCHEMA public." >&2' ERR

# DSN resolution priority:
#   1) DB_CONNECTION_STRING
#   2) DATABASE_URL
#   3) server_config.json -> database_url
DSN="${DB_CONNECTION_STRING:-${DATABASE_URL:-}}"
if [[ -z "${DSN}" && -f "${CONFIG_FILE}" ]]; then
  DSN="$(python - <<PY2
import json
try:
    with open("${CONFIG_FILE}", "r", encoding="utf-8") as f:
        s = json.load(f)
    print(s.get("database_url", "") or "")
except Exception:
    print("")
PY2
)"
fi

if [[ -z "${DSN}" ]]; then
  echo "❌ Could not determine database DSN." >&2
  echo "Set DB_CONNECTION_STRING or DATABASE_URL, or ensure server_config.json has database_url." >&2
  exit 1
fi

eval "$(python - "${DSN}" <<'PY2'
import sys, urllib.parse as up, shlex

dsn = sys.argv[1]
u = up.urlparse(dsn)
if u.scheme not in ("postgresql", "postgres"):
    raise SystemExit(f"Unsupported DSN scheme: {u.scheme}")

dbname = (u.path or "").lstrip("/") or "postgres"
user = up.unquote(u.username or "")
password = up.unquote(u.password or "")
host = u.hostname or "localhost"
port = str(u.port or 5432)

print("DBNAME=" + shlex.quote(dbname))
print("DBUSER=" + shlex.quote(user))
print("DBPASS=" + shlex.quote(password))
print("DBHOST=" + shlex.quote(host))
print("DBPORT=" + shlex.quote(port))
PY2
)"

if [[ -z "${DBUSER}" || ! "${DBUSER}" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "❌ Unsafe DBUSER '${DBUSER}'. Use a simple Postgres role name." >&2
  exit 1
fi

if [[ "${DBNAME,,}" == "postgres" || "${DBNAME,,}" == "template0" || "${DBNAME,,}" == "template1" ]]; then
  echo "❌ Refusing to wipe protected PostgreSQL database '${DBNAME}'. Choose a dedicated Hui Chat database." >&2
  exit 1
fi

if [[ "${HUI_RESET_CONFIRM:-}" != "1" ]]; then
  echo "⚠️  This will DROP and recreate the public schema in database '${DBNAME}' for ${SERVER_NAME}." >&2
  read -r -p "Type RESET ${DBNAME} to continue: " CONFIRM
  if [[ "${CONFIRM}" != "RESET ${DBNAME}" ]]; then
    echo "Cancelled." >&2
    exit 1
  fi
fi

PSQL_BASE=(psql -h "${DBHOST}" -p "${DBPORT}" -U "${DBUSER}" -v ON_ERROR_STOP=1)

if [[ -n "${DBPASS}" ]]; then
  export PGPASSWORD="${DBPASS}"
fi

echo "🧨 Wiping schema in '${DBNAME}'…"
"${PSQL_BASE[@]}" -d "${DBNAME}" -c "DROP SCHEMA IF EXISTS public CASCADE;"
"${PSQL_BASE[@]}" -d "${DBNAME}" -c "CREATE SCHEMA public AUTHORIZATION \"${DBUSER}\";"
"${PSQL_BASE[@]}" -d "${DBNAME}" -c "GRANT ALL ON SCHEMA public TO \"${DBUSER}\";"

echo "✅ Schema wiped for ${SERVER_NAME}. Next: start it (python main.py) to recreate tables."
