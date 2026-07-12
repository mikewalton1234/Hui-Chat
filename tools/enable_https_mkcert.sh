#!/usr/bin/env bash
set -euo pipefail

# Hui Chat trusted localhost/LAN HTTPS setup using mkcert.
# - Generates a locally trusted cert/key under ./certs/
# - Updates (or creates) ./server_config.json to enable https
#
# Why mkcert?
# - Self-signed certs still show browser warnings.
# - mkcert creates a local development CA and installs it into your machine/browser trust store,
#   so Chrome/Firefox stop showing the red "Not secure" warning for the generated localhost cert.
#
# Usage:
#   bash tools/enable_https_mkcert.sh
#   python main.py
#   open https://localhost:5000/login

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="${ROOT_DIR}/certs"
CFG="${ROOT_DIR}/server_config.json"
EXAMPLE="${ROOT_DIR}/settings.example.json"
SERVER_NAME="$(python3 - "${CFG}" <<'PYNAME'
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

mkdir -p "${CERT_DIR}"

if ! command -v mkcert >/dev/null 2>&1; then
  echo "❌ mkcert is not installed."
  echo
  echo "Install one of these ways:"
  echo "  Arch:   sudo pacman -S mkcert nss"
  echo "  Debian: sudo apt install mkcert libnss3-tools"
  echo "  macOS:  brew install mkcert nss"
  echo
  echo "Then run:"
  echo "  mkcert -install"
  exit 1
fi

CRT="${CERT_DIR}/hui-localhost.pem"
KEY="${CERT_DIR}/hui-localhost-key.pem"

HOSTNAME="$(hostname 2>/dev/null || echo hui)"
IPS="$(hostname -I 2>/dev/null || true)"

# Build mkcert SAN args: localhost + loopbacks + hostname + detected IPv4s
SAN_ARGS=(localhost 127.0.0.1 ::1 "${HOSTNAME}")
for ip in ${IPS}; do
  [[ "${ip}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
  SAN_ARGS+=("${ip}")
done

# Install the local CA if needed (safe to run repeatedly)
mkcert -install >/dev/null 2>&1 || true

echo "🔐 Generating mkcert certificate for ${SERVER_NAME}: ${SAN_ARGS[*]}"
mkcert -cert-file "${CRT}" -key-file "${KEY}" "${SAN_ARGS[@]}"
chmod 600 "${KEY}"
chmod 644 "${CRT}"

# Create config if missing
if [[ ! -f "${CFG}" ]]; then
  if [[ -f "${EXAMPLE}" ]]; then
    echo "📄 Creating server_config.json from settings.example.json"
    cp -f "${EXAMPLE}" "${CFG}"
  else
    echo "{}" > "${CFG}"
  fi
fi

python3 - <<PY2
import json
from pathlib import Path

cfg_path = Path(r"${CFG}")
data = json.loads(cfg_path.read_text("utf-8") or "{}")

data["https"] = True
data["ssl_cert_file"] = "certs/hui-localhost.pem"
data["ssl_key_file"] = "certs/hui-localhost-key.pem"
data["cookie_secure"] = True

cfg_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "
", "utf-8")
print("✅ Updated", cfg_path)
PY2

echo
echo "✅ ${SERVER_NAME} HTTPS helper finished."
echo "Next:"
echo "  1) python main.py"
echo "  2) Open: https://localhost:5000/login"
echo "  3) If you still see the old HTTP tab, close it and reopen using https://localhost:5000"
echo
