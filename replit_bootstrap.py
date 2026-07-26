#!/usr/bin/env python3
"""One-shot Replit bootstrap: write server_config.json, init DB schema, create admin.

Admin credentials are read from environment variables — never hardcoded here.
Set these before running:

    HUI_ADMIN_USER=admin
    HUI_ADMIN_PASS=<your password>
    HUI_ADMIN_PIN=<your 4-8 digit PIN>

If the env vars are absent the script aborts with a clear error.
"""
import json, os, sys
from pathlib import Path
from datetime import datetime, timezone

# Load .env first
from env_loader import load_project_dotenv
from runtime_timing import DEFAULT_RUNTIME_TIMING
load_project_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
DEV_DOMAIN   = os.environ.get("REPLIT_DEV_DOMAIN", "")
PUBLIC_URL   = f"https://{DEV_DOMAIN}" if DEV_DOMAIN else "http://127.0.0.1:5000"

# ── Admin credentials from env — no defaults, fail fast if missing ────────────
USERNAME = os.environ.get("HUI_ADMIN_USER", "").strip()
PASSWORD = os.environ.get("HUI_ADMIN_PASS", "").strip()
PIN      = os.environ.get("HUI_ADMIN_PIN", "").strip()

missing = [k for k, v in [
    ("HUI_ADMIN_USER", USERNAME),
    ("HUI_ADMIN_PASS", PASSWORD),
    ("HUI_ADMIN_PIN",  PIN),
] if not v]

if missing:
    print(f"❌  Missing required env vars: {', '.join(missing)}")
    print("    Set them before running this script.")
    sys.exit(1)

# ── 1. Write server_config.json ──────────────────────────────────────────────
config = {
    "server_name": "Hui Chat",
    "server_host": "0.0.0.0",
    "server_port": 5000,
    "run_mode": "development",
    "production_mode": False,
    # database_url intentionally omitted — app reads DATABASE_URL from env var
    "public_base_url": PUBLIC_URL,
    "hosting_mode": "lan",
    "cors_allowed_origins": [
        PUBLIC_URL,
        "http://127.0.0.1:5000",
        "http://localhost:5000",
    ],
    "auto_allow_lan_origins": True,
    "cookie_secure": False,
    "cookie_samesite": "Lax",
    "allow_insecure_lan_cookie_fallback": True,
    "trust_proxy_headers": True,
    "proxy_fix_hops": 1,
    "proxy_fix_x_for": 1,
    "proxy_fix_x_proto": 1,
    "proxy_fix_x_host": 1,
    "proxy_fix_x_port": 1,
    "proxy_fix_x_prefix": 0,
    "access_token_minutes": 30,
    "refresh_token_days": 7,
    "rate_limit_storage_uri": "memory://",
    "rate_limit_storage": "memory://",
    "socketio_message_queue": "",
    "shared_state_redis_url": "",
    "production_workers": 1,
    "production_async_mode": "threading",
    "production_worker_class": "gthread",
    "giphy_enabled": False,
    "giphy_api_key": "",
    "smtp_enabled": False,
    "voice_enabled": True,
    "p2p_file_enabled": True,
    "emoticons_enabled": True,
    "emoticons_local_enabled": True,
    "emoticons_external_enabled": True,
    "log_level": "INFO",
    "enable_health_check_endpoint": True,
    "health_check_endpoint": "/health",
    "admin_reauth_once_per_session": True,
    "admin_fresh_auth_window_seconds": 28800,
    "encrypt_sensitive_profile_fields": True,
    "encrypt_email_at_rest": True,
    "encrypt_security_backups": True,
    "privacy_retention_enabled": True,
    "require_dm_e2ee": True,
    "allow_plaintext_dm_fallback": False,
    "require_group_e2ee": True,
    "require_private_room_e2ee": True,
    "require_room_e2ee": False,
    "revoke_all_tokens_on_start": False,
    "p2p_ice_servers": [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
    ],
    "voice_ice_servers": [],
    "max_message_length": 1000,
    "max_attachment_size": 10485760,
    "max_dm_file_bytes": 10485760,
    "max_user_file_storage_bytes": 262144000,
    "autoscale_rooms_enabled": True,
    "autoscale_room_capacity": 30,
    "autoscale_room_idle_minutes": 30,
    "janitor_interval_seconds": 60,
    "dynamic_dns_enabled": False,
    "auto_configure_scaled_redis": False,
}
# Replit must use the same Socket.IO, Redis, voice, P2P, janitor, and IP-ban
# timing policy as the normal setup wizard.
config.update(DEFAULT_RUNTIME_TIMING)

config_path = Path("server_config.json")
config_path.write_text(json.dumps(config, indent=2))
print(f"✅ Wrote {config_path}")

# ── 2. Bootstrap DB schema ────────────────────────────────────────────────────
import psycopg2
from db.schema import _create_full_schema, _seed_roles_permissions

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = False

print("🔧 Creating schema...")
_create_full_schema(conn, commit=False)
print("🔧 Seeding roles/permissions...")
_seed_roles_permissions(conn, commit=False)
conn.commit()
print("✅ Schema ready")

# ── 3. Create admin user ──────────────────────────────────────────────────────
from db.auth import user_exists, create_user_with_keys
from security import hash_password

if user_exists(conn, USERNAME):
    print(f"ℹ️  User '{USERNAME}' already exists, skipping creation.")
else:
    password_hash = hash_password(PASSWORD)
    pin_hash      = hash_password(PIN)
    pin_ts        = datetime.now(timezone.utc)

    create_user_with_keys(
        conn,
        username=USERNAME,
        raw_password=PASSWORD,
        password_hash=password_hash,
        is_admin=True,
        recovery_pin_hash=pin_hash,
        recovery_pin_set_at=pin_ts,
        field_encryption_settings=None,
        commit=True,
    )
    print(f"✅ Admin user '{USERNAME}' created")

conn.close()
print("\n🎉 Bootstrap complete — ready to start the server.")
