#!/usr/bin/env python3
"""main.py

Echo-Chat server entrypoint.

Echo-Chat treats ``server_config.json`` as a plaintext settings file.
Production/public mode now keeps secrets out of that file by default. Put
credentials in environment variables or a secret manager, or explicitly set
``ECHOCHAT_PERSIST_SECRETS=1`` if you need legacy config-file persistence.
"""

from __future__ import annotations

from env_loader import load_project_dotenv

load_project_dotenv()

import os
import shutil
import subprocess
import tempfile

# Centralized async bootstrap. In auto mode EchoChat now defaults to the
# built-in threading runtime; set ECHOCHAT_SOCKETIO_ASYNC=eventlet only when
# you explicitly want Eventlet and its monkey-patched runtime model.
from socketio_async_bootstrap import ECHOCHAT_SOCKETIO_ASYNC

import argparse
from datetime import datetime
import getpass
import json
import logging
import sys
from pathlib import Path

from constants import CONFIG_FILE, sanitize_postgres_dsn
from constants import DEFAULT_SERVER_NAME, server_display_name
from secrets_policy import persist_secrets_enabled, scrub_secrets_for_persist
from secret_manager import (
    ensure_core_runtime_secrets,
    ensure_secret,
    format_env_bundle,
    generate_secret_bundle,
    is_placeholder_secret,
    resolve_secret,
    write_env_secrets,
)
from scaled_redis_autoconfig import apply_scaled_runtime_safety_defaults, scaled_realtime_requested, scaled_redis_summary_lines


def _load_setup_helpers():
    """Import setup helpers lazily so read-only CLI tools can run without DB deps."""
    from interactive_setup import get_default_settings, interactive_setup, normalize_setup_settings
    return get_default_settings, interactive_setup, normalize_setup_settings


def _fallback_default_settings() -> dict:
    return {
        "server_name": DEFAULT_SERVER_NAME,
        "server_host": "0.0.0.0",
        "server_port": 5000,
        "host": "0.0.0.0",
        "port": 5000,
        "public_base_url": "",
        "hosting_mode": "lan",
        "allowed_origins": ["http://127.0.0.1:5000", "http://localhost:5000"],
        "cors_allowed_origins": ["http://127.0.0.1:5000", "http://localhost:5000"],
        "cookie_secure": False,
        "https": False,
        "trust_proxy_headers": False,
        "proxy_fix_hops": 1,
        "auto_allow_lan_origins": True,
        "rate_limit_storage_uri": "memory://",
        "rate_limit_storage": "memory://",
        "rate_limit_public_key": "120 per minute",
        "socketio_message_queue": "",
        "shared_state_redis_url": "",
        "production_workers": 1,
        "production_async_mode": "threading",
        "production_worker_class": "gthread",
        "health_check_endpoint": "/health",
        "enable_health_check_endpoint": False,
        "max_request_bytes": 31457280,
        "require_dm_e2ee": True,
        "allow_plaintext_dm_fallback": False,
        "require_group_e2ee": True,
        "allow_legacy_numeric_group_history": False,
        "disable_legacy_group_file_upload": True,
        "require_private_room_e2ee": True,
        "require_room_e2ee": False,
        "encrypt_sensitive_profile_fields": True,
        "encrypt_email_at_rest": True,
        "encrypt_security_backups": True,
        "privacy_retention_enabled": True,
        "privacy_ip_user_agent_retention_days": 30,
        "privacy_audit_detail_retention_days": 90,
        "privacy_retention_batch_limit": 500,
        "cleanup_expired_auth_enabled": True,
        "cleanup_orphan_auth_enabled": True,
        "auth_token_retention_days": 30,
        "revoked_session_retention_days": 30,
        "password_reset_token_retention_days": 7,
        "orphan_auth_retention_days": 1,
        "auth_cleanup_batch_limit": 500,
        "cleanup_revoked_private_files_enabled": True,
        "cleanup_orphan_private_file_blobs_enabled": True,
        "revoked_private_file_retention_days": 7,
        "orphan_private_file_grace_minutes": 60,
        "private_file_cleanup_batch_limit": 500,
        "all_room_e2ee_impact_acknowledged": False,
    }


def _safe_default_settings() -> dict:
    try:
        get_default_settings, _, _ = _load_setup_helpers()
        settings = get_default_settings()
    except Exception:
        settings = _fallback_default_settings()
    apply_scaled_runtime_safety_defaults(settings)
    return settings


def _safe_normalize_setup_settings(settings: dict) -> dict:
    try:
        _, _, normalize_setup_settings = _load_setup_helpers()
        out = normalize_setup_settings(settings)
    except Exception:
        fallback = _fallback_default_settings()
        fallback.update(settings or {})
        out = fallback
    apply_scaled_runtime_safety_defaults(out)
    return out


def _build_postgres_dsn(parts: dict) -> str:
    try:
        from db.bootstrap import build_postgres_dsn
        return build_postgres_dsn(parts)
    except Exception:
        from urllib.parse import quote, urlunparse
        user = quote(str(parts.get("user") or ""))
        password = str(parts.get("password") or "")
        auth = user + ((":" + quote(password)) if password else "")
        host = str(parts.get("host") or "localhost")
        port = str(parts.get("port") or "5432")
        db = quote(str(parts.get("db") or "echochat"))
        return urlunparse((str(parts.get("scheme") or "postgresql"), f"{auth}@{host}:{port}", f"/{db}", "", str(parts.get("query") or ""), str(parts.get("fragment") or "")))


def _server_display_name(settings: dict | None = None) -> str:
    return server_display_name(settings, default=DEFAULT_SERVER_NAME)


def configure_logging(settings: dict) -> None:
    """Configure file logging."""
    log_level_str = str(settings.get("log_level", "INFO")).upper()
    log_format = settings.get(
        "log_format",
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    log_file_path = settings.get("log_file_path", "logs/server.log")

    log_dir = os.path.dirname(log_file_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    log_level = getattr(logging, log_level_str, logging.INFO)
    logging.basicConfig(level=log_level, format=log_format, filename=log_file_path, filemode="a")
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
    logging.info("Logging configured (level=%s)", log_level_str)


def load_settings(path: Path) -> dict:
    """Load settings from JSON. Returns defaults if missing."""
    if not path.exists():
        return _safe_default_settings()

    try:
        with path.open("r", encoding="utf-8") as fp:
            loaded = json.load(fp)
        if not isinstance(loaded, dict):
            raise ValueError("top-level JSON value must be an object")
        return _safe_normalize_setup_settings(loaded)
    except Exception as exc:
        print(f"⚠️  Could not parse {path} as JSON: {exc}")
        # If the settings file is corrupted, proactively back it up so the server
        # can safely persist generated secrets (secret_key / jwt_secret) into a
        # fresh JSON file.
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        bad_path = path.with_suffix(path.suffix + f".bad-{ts}")
        try:
            path.rename(bad_path)
            print(f"⚠️  Backed up invalid settings file to: {bad_path}")
        except Exception as e2:
            print(f"⚠️  Could not back up invalid settings file: {e2}")
        print("⚠️  Falling back to defaults (run with --setup to rewrite config).")
        return _safe_default_settings()


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically so setup never leaves a half-written config file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent or Path(".")))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_path, path)
        try:
            dir_fd = os.open(str(path.parent or Path(".")), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            # Directory fsync is best-effort and not portable on every platform.
            pass
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        finally:
            raise


def save_settings(path: Path, settings: dict) -> None:
    # In production/public mode, or when ECHOCHAT_PERSIST_SECRETS=0, do not write
    # secrets (DB DSNs, API keys, SMTP/Twilio/TURN credentials, etc.) into
    # server_config.json. Keep them in env/.env or a secret manager instead.
    to_save = scrub_secrets_for_persist(settings)
    _atomic_write_json(path, to_save)


def _env_any_present(*names: str) -> bool:
    return any((str(os.getenv(name) or "").strip() and not is_placeholder_secret(os.getenv(name))) for name in names)


def _missing_runtime_env_after_secret_scrub(settings: dict) -> list[str]:
    """Return env hints needed after production/public setup scrubs secrets.

    The setup wizard may have enough in-memory secrets to finish DB/admin work,
    but production startup re-execs through Gunicorn and the child process reloads
    ``server_config.json``. If secret persistence is disabled, any required secret
    removed from JSON must already be available through environment variables.
    """
    if persist_secrets_enabled(settings):
        return []
    saved = scrub_secrets_for_persist(settings)
    missing: list[str] = []

    if str(settings.get("database_url") or "").strip() and not str(saved.get("database_url") or "").strip():
        if not _env_any_present("DATABASE_URL", "DB_CONNECTION_STRING"):
            missing.append("DATABASE_URL or DB_CONNECTION_STRING for the PostgreSQL application database")

    if str(settings.get("database_bootstrap_url") or "").strip() and not str(saved.get("database_bootstrap_url") or "").strip():
        if not _env_any_present("ECHOCHAT_DB_BOOTSTRAP_URL", "DATABASE_BOOTSTRAP_URL"):
            missing.append("ECHOCHAT_DB_BOOTSTRAP_URL if setup/database repair still needs a bootstrap/admin PostgreSQL DSN")

    if bool(settings.get("smtp_enabled")) and str(settings.get("smtp_password") or "").strip() and not str(saved.get("smtp_password") or "").strip():
        if not _env_any_present("ECHOCHAT_SMTP_PASSWORD", "SMTP_PASSWORD", "SMTP_PASS", "MAIL_PASSWORD", "EMAIL_PASSWORD"):
            missing.append("ECHOCHAT_SMTP_PASSWORD or SMTP_PASSWORD for password-reset email")

    sms_enabled = bool(settings.get("enable_sms_two_factor") or settings.get("enable_two_factor_beta"))
    if sms_enabled:
        twilio_pairs = [
            ("twilio_account_sid", ("ECHOCHAT_TWILIO_ACCOUNT_SID", "TWILIO_ACCOUNT_SID")),
            ("twilio_auth_token", ("ECHOCHAT_TWILIO_AUTH_TOKEN", "TWILIO_AUTH_TOKEN")),
            ("twilio_verify_service_sid", ("ECHOCHAT_TWILIO_VERIFY_SERVICE_SID", "TWILIO_VERIFY_SERVICE_SID")),
        ]
        for key, env_names in twilio_pairs:
            if str(settings.get(key) or "").strip() and not str(saved.get(key) or "").strip() and not _env_any_present(*env_names):
                missing.append("/".join(env_names) + f" for SMS 2FA ({key})")

    if str(settings.get("giphy_api_key") or "").strip() and not str(saved.get("giphy_api_key") or "").strip():
        if not _env_any_present("ECHOCHAT_GIPHY_API_KEY", "GIPHY_API_KEY"):
            missing.append("ECHOCHAT_GIPHY_API_KEY or GIPHY_API_KEY for GIF search")

    return missing


def _reload_saved_settings_for_runtime(path: Path) -> dict:
    """Reload the saved setup output and reapply env overrides before server start."""
    reloaded = load_settings(path)
    apply_env_overrides(reloaded)
    return _sync_run_mode_settings(reloaded)


def apply_env_overrides(settings: dict) -> None:
    """Apply env overrides for secrets and runtime deployment."""

    def _bool_env(*names: str) -> bool | None:
        for n in names:
            v = os.getenv(n)
            if v is None:
                continue
            v = v.strip().lower()
            if v in ("1", "true", "yes", "y", "on"):
                return True
            if v in ("0", "false", "no", "n", "off"):
                return False
        return None

    def _str_env(*names: str) -> str | None:
        for n in names:
            v = os.getenv(n)
            if v is not None and v.strip() != "":
                return v.strip()
        return None

    def _int_env(*names: str) -> int | None:
        v = _str_env(*names)
        if v is None:
            return None
        try:
            return int(v)
        except ValueError:
            return None

    # Prefer DB env vars for safety.
    db = os.getenv("DB_CONNECTION_STRING") or os.getenv("DATABASE_URL")
    bootstrap_db = os.getenv("ECHOCHAT_DB_BOOTSTRAP_URL") or os.getenv("DATABASE_BOOTSTRAP_URL")
    if bootstrap_db:
        settings["database_bootstrap_url"] = str(sanitize_postgres_dsn(bootstrap_db))
    if db:
        settings["database_url"] = str(sanitize_postgres_dsn(db))

    secret = resolve_secret(settings, "secret_key")
    if secret:
        settings["secret_key"] = secret

    jwt_secret = resolve_secret(settings, "jwt_secret")
    if jwt_secret:
        # Keep behavior consistent across the codebase while ignoring placeholder values.
        settings["jwt_secret"] = jwt_secret

    # Echo media env override.
    av_mode = _str_env("ECHOCHAT_AV_MODE", "AV_MODE")
    if av_mode and av_mode.strip().lower().replace("-", "_") in {"standard", "echo", "webrtc", "built_in", "builtin"}:
        mode = av_mode.strip().lower().replace("-", "_")
        settings["av_mode"] = "echo" if mode in {"webrtc", "built_in", "builtin"} else mode

    # WebRTC STUN/TURN env overrides.  The browser must receive ICE credentials
    # to use TURN; keep long-lived credentials in env/secret management, not JSON.
    from webrtc_ice_config import apply_turn_credentials, env_ice_servers, parse_ice_servers_text

    p2p_ice = env_ice_servers("ECHOCHAT_P2P_ICE_SERVERS_JSON", "ECHOCHAT_WEBRTC_ICE_SERVERS_JSON", "WEBRTC_ICE_SERVERS_JSON")
    voice_ice = env_ice_servers("ECHOCHAT_VOICE_ICE_SERVERS_JSON", "ECHOCHAT_WEBCAM_ICE_SERVERS_JSON")
    turn_urls = _str_env("ECHOCHAT_TURN_URLS", "TURN_URLS")
    turn_username = _str_env("ECHOCHAT_TURN_USERNAME", "TURN_USERNAME")
    turn_credential = _str_env("ECHOCHAT_TURN_CREDENTIAL", "ECHOCHAT_TURN_PASSWORD", "TURN_CREDENTIAL", "TURN_PASSWORD")
    if turn_urls:
        parsed_turn = parse_ice_servers_text(turn_urls)
        if parsed_turn:
            parsed_turn = apply_turn_credentials(parsed_turn, turn_username, turn_credential, keep_existing=True)
            p2p_ice = p2p_ice or parsed_turn
            voice_ice = voice_ice or parsed_turn
    if p2p_ice:
        settings["p2p_ice_servers"] = p2p_ice
    if voice_ice:
        settings["voice_ice_servers"] = voice_ice

    # Torrent scraping can be enabled for local/LAN testing without editing JSON.
    torrent_scrape_enabled = _bool_env("ECHOCHAT_TORRENT_SCRAPE_ENABLED", "TORRENT_SCRAPE_ENABLED")
    if torrent_scrape_enabled is not None:
        settings["torrent_scrape_enabled"] = torrent_scrape_enabled

    # GIPHY (prefer env for production)
    giphy_key = _str_env("ECHOCHAT_GIPHY_API_KEY", "GIPHY_API_KEY")
    if giphy_key:
        settings["giphy_api_key"] = giphy_key

    # SMTP (optional) — keep secrets out of server_config.json if desired.
    smtp_enabled = _bool_env("ECHOCHAT_SMTP_ENABLED", "SMTP_ENABLED")
    if smtp_enabled is not None:
        settings["smtp_enabled"] = smtp_enabled

    smtp_host = _str_env("ECHOCHAT_SMTP_HOST", "SMTP_HOST")
    if smtp_host:
        settings["smtp_host"] = smtp_host

    smtp_port = _int_env("ECHOCHAT_SMTP_PORT", "SMTP_PORT")
    if smtp_port:
        settings["smtp_port"] = smtp_port

    smtp_user = _str_env("ECHOCHAT_SMTP_USERNAME", "ECHOCHAT_SMTP_USER", "SMTP_USERNAME", "SMTP_USER")
    if smtp_user:
        settings["smtp_username"] = smtp_user

    smtp_pass = _str_env("ECHOCHAT_SMTP_PASSWORD", "ECHOCHAT_SMTP_PASS", "SMTP_PASSWORD", "SMTP_PASS")
    if smtp_pass:
        settings["smtp_password"] = smtp_pass

    smtp_from = _str_env("ECHOCHAT_SMTP_FROM", "SMTP_FROM")
    if smtp_from:
        settings["smtp_from"] = smtp_from

    smtp_starttls = _bool_env("ECHOCHAT_SMTP_STARTTLS", "SMTP_STARTTLS")
    if smtp_starttls is not None:
        settings["smtp_use_starttls"] = smtp_starttls

    smtp_ssl = _bool_env("ECHOCHAT_SMTP_SSL", "SMTP_SSL")
    if smtp_ssl is not None:
        settings["smtp_use_ssl"] = smtp_ssl

    # Twilio/SMS 2FA (prefer env so provider credentials stay out of server_config.json)
    twilio_beta_enabled = _bool_env("ECHOCHAT_ENABLE_TWO_FACTOR_BETA", "ENABLE_TWO_FACTOR_BETA")
    if twilio_beta_enabled is not None:
        settings["enable_two_factor_beta"] = twilio_beta_enabled

    twilio_sms_enabled = _bool_env("ECHOCHAT_ENABLE_SMS_2FA", "ECHOCHAT_ENABLE_SMS_TWO_FACTOR", "ENABLE_SMS_2FA", "ENABLE_SMS_TWO_FACTOR")
    if twilio_sms_enabled is not None:
        settings["enable_sms_two_factor"] = twilio_sms_enabled
        if twilio_sms_enabled:
            settings["enable_two_factor_beta"] = True

    twilio_channel = _str_env("ECHOCHAT_TWILIO_VERIFY_CHANNEL", "ECHOCHAT_TWO_FACTOR_SMS_CHANNEL", "TWILIO_VERIFY_CHANNEL")
    if twilio_channel:
        settings["two_factor_sms_channel"] = twilio_channel.strip().lower()

    twilio_timeout = _int_env("ECHOCHAT_TWO_FACTOR_LOGIN_TIMEOUT_SECONDS", "TWO_FACTOR_LOGIN_TIMEOUT_SECONDS")
    if twilio_timeout:
        settings["two_factor_login_timeout_seconds"] = twilio_timeout

    twilio_account_sid = _str_env("ECHOCHAT_TWILIO_ACCOUNT_SID", "TWILIO_ACCOUNT_SID")
    if twilio_account_sid:
        settings["twilio_account_sid"] = twilio_account_sid

    twilio_auth_token = _str_env("ECHOCHAT_TWILIO_AUTH_TOKEN", "TWILIO_AUTH_TOKEN")
    if twilio_auth_token:
        settings["twilio_auth_token"] = twilio_auth_token

    twilio_verify_service_sid = _str_env("ECHOCHAT_TWILIO_VERIFY_SERVICE_SID", "TWILIO_VERIFY_SERVICE_SID")
    if twilio_verify_service_sid:
        settings["twilio_verify_service_sid"] = twilio_verify_service_sid

    # Dynamic DNS helper. Keep provider passwords/tokens in env for production.
    ddns_enabled = _bool_env("ECHOCHAT_DYNAMIC_DNS_ENABLED", "ECHOCHAT_DDNS_ENABLED", "DDNS_ENABLED")
    if ddns_enabled is not None:
        settings["dynamic_dns_enabled"] = ddns_enabled

    ddns_provider = _str_env("ECHOCHAT_DYNAMIC_DNS_PROVIDER", "ECHOCHAT_DDNS_PROVIDER", "DDNS_PROVIDER")
    if ddns_provider:
        settings["dynamic_dns_provider"] = ddns_provider

    ddns_username = _str_env("ECHOCHAT_DYNAMIC_DNS_USERNAME", "ECHOCHAT_DDNS_USERNAME", "DDNS_USERNAME")
    if ddns_username:
        settings["dynamic_dns_username"] = ddns_username

    ddns_password = _str_env("ECHOCHAT_DYNAMIC_DNS_PASSWORD", "ECHOCHAT_DDNS_PASSWORD", "DDNS_PASSWORD")
    if ddns_password:
        settings["dynamic_dns_password"] = ddns_password

    ddns_domain = _str_env("ECHOCHAT_DYNAMIC_DNS_DOMAIN", "ECHOCHAT_DDNS_DOMAIN", "DDNS_DOMAIN")
    if ddns_domain:
        settings["dynamic_dns_domain"] = ddns_domain

    ddns_update_url = _str_env("ECHOCHAT_DYNAMIC_DNS_UPDATE_URL", "ECHOCHAT_DDNS_UPDATE_URL", "DDNS_UPDATE_URL")
    if ddns_update_url:
        settings["dynamic_dns_update_url"] = ddns_update_url

    public_base_url = _str_env("ECHOCHAT_PUBLIC_BASE_URL", "ECHOCHAT_PUBLIC_URL", "PUBLIC_BASE_URL", "PUBLIC_URL")
    if public_base_url:
        settings["public_base_url"] = public_base_url

    cookie_secure = _bool_env("ECHOCHAT_COOKIE_SECURE", "COOKIE_SECURE")
    if cookie_secure is not None:
        settings["cookie_secure"] = cookie_secure

    cookie_samesite = _str_env("ECHOCHAT_COOKIE_SAMESITE", "COOKIE_SAMESITE")
    if cookie_samesite:
        settings["cookie_samesite"] = cookie_samesite

    trust_proxy_headers = _bool_env("ECHOCHAT_TRUST_PROXY_HEADERS", "TRUST_PROXY_HEADERS")
    if trust_proxy_headers is not None:
        settings["trust_proxy_headers"] = trust_proxy_headers

    proxy_fix_hops = _int_env("ECHOCHAT_PROXY_FIX_HOPS", "PROXY_FIX_HOPS")
    if proxy_fix_hops is not None and proxy_fix_hops >= 0:
        settings["proxy_fix_hops"] = proxy_fix_hops
    for _env_name, _setting_key in (
        ("ECHOCHAT_PROXY_FIX_X_FOR", "proxy_fix_x_for"),
        ("ECHOCHAT_PROXY_FIX_X_PROTO", "proxy_fix_x_proto"),
        ("ECHOCHAT_PROXY_FIX_X_HOST", "proxy_fix_x_host"),
        ("ECHOCHAT_PROXY_FIX_X_PORT", "proxy_fix_x_port"),
        ("ECHOCHAT_PROXY_FIX_X_PREFIX", "proxy_fix_x_prefix"),
    ):
        _proxy_val = _int_env(_env_name)
        if _proxy_val is not None and _proxy_val >= 0:
            settings[_setting_key] = _proxy_val

    run_mode = _str_env("ECHOCHAT_RUN_MODE", "ECHOCHAT_SERVER_MODE", "ECHOCHAT_DEPLOYMENT_MODE")
    if run_mode:
        settings["run_mode"] = run_mode.strip().lower()

    production_mode = _bool_env("ECHOCHAT_PRODUCTION_MODE", "PRODUCTION_MODE")
    if production_mode is not None:
        settings["production_mode"] = production_mode
        settings["run_mode"] = "production" if production_mode else "development"

    production_bind = _str_env("ECHOCHAT_PRODUCTION_BIND", "PRODUCTION_BIND")
    if production_bind:
        settings["production_bind"] = production_bind

    production_workers = _int_env("ECHOCHAT_WORKERS", "ECHOCHAT_PRODUCTION_WORKERS", "PRODUCTION_WORKERS", "WEB_CONCURRENCY")
    if production_workers is not None and production_workers > 0:
        settings["production_workers"] = production_workers

    production_instances = _int_env("ECHOCHAT_PRODUCTION_INSTANCES", "ECHOCHAT_INSTANCE_COUNT", "PRODUCTION_INSTANCES")
    if production_instances is not None and production_instances > 0:
        settings["production_instance_count"] = max(1, min(10, production_instances))

    instance_base_port = _int_env("ECHOCHAT_INSTANCE_BASE_PORT", "PRODUCTION_INSTANCE_BASE_PORT")
    if instance_base_port is not None and instance_base_port > 0:
        settings["production_instance_base_port"] = instance_base_port

    enable_health_endpoint = _bool_env("ECHOCHAT_ENABLE_HEALTH_ENDPOINT", "ENABLE_HEALTH_CHECK_ENDPOINT")
    if enable_health_endpoint is not None:
        settings["enable_health_check_endpoint"] = enable_health_endpoint

    health_endpoint = _str_env("ECHOCHAT_HEALTH_ENDPOINT", "HEALTH_CHECK_ENDPOINT")
    if health_endpoint:
        # Accept both "health" and "/health" in env files; Flask routes need a leading slash.
        settings["health_check_endpoint"] = health_endpoint if health_endpoint.startswith("/") else f"/{health_endpoint}"

    socketio_message_queue = _str_env("ECHOCHAT_SOCKETIO_MESSAGE_QUEUE", "SOCKETIO_MESSAGE_QUEUE")
    if socketio_message_queue:
        settings["socketio_message_queue"] = socketio_message_queue

    rate_limit_storage_uri = _str_env("ECHOCHAT_RATE_LIMIT_STORAGE_URI", "RATELIMIT_STORAGE_URI")
    if rate_limit_storage_uri:
        settings["rate_limit_storage_uri"] = rate_limit_storage_uri
        settings["rate_limit_storage"] = rate_limit_storage_uri

    simple_rate_limit_storage_uri = _str_env("ECHOCHAT_SIMPLE_RATE_LIMIT_STORAGE_URI", "SIMPLE_RATE_LIMIT_STORAGE_URI")
    if simple_rate_limit_storage_uri:
        settings["simple_rate_limit_storage_uri"] = simple_rate_limit_storage_uri

    socketio_transports = _str_env("ECHOCHAT_SOCKETIO_TRANSPORTS", "SOCKETIO_TRANSPORTS")
    if socketio_transports:
        settings["socketio_transports"] = [
            item.strip()
            for item in socketio_transports.split(",")
            if item.strip()
        ]

    auto_allow_lan_origins = _bool_env("ECHOCHAT_AUTO_ALLOW_LAN_ORIGINS", "AUTO_ALLOW_LAN_ORIGINS")
    if auto_allow_lan_origins is not None:
        settings["auto_allow_lan_origins"] = auto_allow_lan_origins

    shared_state_redis_url = _str_env("ECHOCHAT_SHARED_STATE_REDIS_URL", "SHARED_STATE_REDIS_URL")
    if shared_state_redis_url:
        settings["shared_state_redis_url"] = shared_state_redis_url

    cors_allowed_origins = _str_env("ECHOCHAT_CORS_ALLOWED_ORIGINS", "CORS_ALLOWED_ORIGINS")
    if cors_allowed_origins:
        settings["cors_allowed_origins"] = [
            item.strip()
            for item in cors_allowed_origins.split(",")
            if item.strip()
        ]

    allowed_origins = _str_env("ECHOCHAT_ALLOWED_ORIGINS", "ALLOWED_ORIGINS")
    if allowed_origins:
        settings["allowed_origins"] = [
            item.strip()
            for item in allowed_origins.split(",")
            if item.strip()
        ]

    hosting_mode = _str_env("ECHOCHAT_HOSTING_MODE", "HOSTING_MODE")
    if hosting_mode:
        settings["hosting_mode"] = hosting_mode.strip().lower().replace("-", "_").replace(" ", "_")

    reverse_proxy_lan_port = _int_env("ECHOCHAT_REVERSE_PROXY_LAN_PORT", "REVERSE_PROXY_LAN_PORT")
    if reverse_proxy_lan_port is not None and reverse_proxy_lan_port > 0:
        settings["reverse_proxy_lan_port"] = reverse_proxy_lan_port

    deployment_kit_output_dir = _str_env("ECHOCHAT_DEPLOYMENT_KIT_OUTPUT_DIR", "DEPLOYMENT_KIT_OUTPUT_DIR")
    if deployment_kit_output_dir:
        settings["deployment_kit_output_dir"] = deployment_kit_output_dir

    systemd_service_user = _str_env("ECHOCHAT_SYSTEMD_SERVICE_USER", "SYSTEMD_SERVICE_USER")
    if systemd_service_user:
        settings["systemd_service_user"] = systemd_service_user

    systemd_working_directory = _str_env("ECHOCHAT_SYSTEMD_WORKING_DIRECTORY", "SYSTEMD_WORKING_DIRECTORY")
    if systemd_working_directory:
        settings["systemd_working_directory"] = systemd_working_directory

    systemd_env_file = _str_env("ECHOCHAT_SYSTEMD_ENV_FILE", "SYSTEMD_ENV_FILE")
    if systemd_env_file:
        settings["systemd_env_file"] = systemd_env_file

    apply_scaled_runtime_safety_defaults(settings)


def _default_local_postgres_parts(db_name: str = "echochat") -> dict:
    return {
        "scheme": "postgresql",
        "user": getpass.getuser(),
        "password": "",
        "host": "localhost",
        "port": 5432,
        "db": db_name,
        "query": "",
        "fragment": "",
    }


def seed_first_run_local_config(path: Path, settings: dict) -> dict:
    """Auto-seed a usable local config on first run.

    When server_config.json is missing, setup should not force the user to hand-create
    the file or trip over placeholder DSNs like USER/PASSWORD/echo_db. Seed a local
    PostgreSQL DSN using the current OS user and standard localhost defaults instead.
    """
    if path.exists():
        return settings
    seeded = dict(settings or {})
    changed = False
    if not str(seeded.get("database_url") or "").strip():
        seeded["database_url"] = _build_postgres_dsn(_default_local_postgres_parts("echochat"))
        changed = True
    if not str(seeded.get("database_bootstrap_url") or "").strip():
        seeded["database_bootstrap_url"] = _build_postgres_dsn(_default_local_postgres_parts("postgres"))
        changed = True
    if changed:
        save_settings(path, seeded)
        print(f"ℹ️  Seeded {path} with local PostgreSQL defaults for user {getpass.getuser()}.")
    return seeded



_PRODUCTION_RUN_MODE_VALUES = {"production", "prod", "public", "public-beta", "public_beta"}
_DEVELOPMENT_RUN_MODE_VALUES = {"development", "dev", "local", "lan", "test", "testing"}


def _truthy_setting(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "production", "prod"}


def _normalized_run_mode(settings: dict) -> str:
    raw = (
        settings.get("run_mode")
        or settings.get("server_mode")
        or settings.get("deployment_mode")
        or ("production" if _truthy_setting(settings.get("production_mode")) else "")
        or "development"
    )
    val = str(raw).strip().lower().replace("_", "-").replace(" ", "-")
    if val in _PRODUCTION_RUN_MODE_VALUES:
        return "production"
    if val in _DEVELOPMENT_RUN_MODE_VALUES:
        return "development"
    return "development"


def _sync_run_mode_settings(settings: dict, *, mode: str | None = None) -> dict:
    """Keep run_mode and legacy production_mode in agreement.

    Older builds and hand-edited configs may disagree, for example
    ``run_mode=production`` with ``production_mode=false``. Runtime code, admin
    diagnostics, secret persistence, and deployment checks should all see one
    canonical binary mode. Unknown values fail safe to development.
    """
    canonical = str(mode or _normalized_run_mode(settings)).strip().lower().replace("_", "-").replace(" ", "-")
    if canonical in _PRODUCTION_RUN_MODE_VALUES:
        canonical = "production"
    elif canonical in _DEVELOPMENT_RUN_MODE_VALUES:
        canonical = "development"
    else:
        canonical = "development"
    settings["run_mode"] = canonical
    settings["production_mode"] = canonical == "production"
    return settings


def _effective_settings_for_cli_mode(settings: dict, args: argparse.Namespace) -> dict:
    """Return runtime settings after one-time --production/--development overrides.

    The saved JSON file is not changed here. This only ensures the current
    process, readiness checks, secret policy notices, and Flask startup guards all
    interpret the selected mode the same way.
    """
    effective = dict(settings or {})
    if getattr(args, "production", False):
        return _sync_run_mode_settings(effective, mode="production")
    if getattr(args, "development", False):
        _sync_run_mode_settings(effective, mode="development")
        # A one-time development launch is explicitly a local/LAN runner. Keep
        # the saved public-beta profile intact on disk, but do not let it make
        # this temporary dev process fail closed as an internet-facing beta.
        if str(effective.get("hosting_mode") or "").strip().lower().replace("-", "_").replace(" ", "_") == "public_beta":
            effective["hosting_mode"] = "lan"
        effective["cookie_secure"] = False
        effective["allow_insecure_lan_cookie_fallback"] = True
        effective["auto_allow_lan_origins"] = True
        return effective
    return _sync_run_mode_settings(effective)


def _should_run_production(settings: dict, args: argparse.Namespace) -> bool:
    """Return True when normal server start should exec Gunicorn.

    ``--production`` is kept as an explicit one-time override. The saved
    ``run_mode=production`` setting is what lets an admin simply run
    ``python main.py`` after setup and get the production runner automatically.
    """
    if getattr(args, "development", False):
        return False
    if getattr(args, "production", False):
        return True
    return _normalized_run_mode(settings) == "production"


def _production_bind_from_settings(settings: dict) -> str:
    explicit = str(settings.get("production_bind") or "").strip()
    if explicit:
        return explicit
    host = str(settings.get("host") or settings.get("server_host") or "0.0.0.0").strip() or "0.0.0.0"
    port = int(settings.get("port") or settings.get("server_port") or 5000)
    return f"{host}:{port}"


def _production_workers_from_settings(settings: dict) -> int:
    for key in ("production_workers", "worker_count", "web_workers"):
        try:
            value = int(settings.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    return 1


def _production_instance_count_from_settings(settings: dict) -> int:
    for key in ("production_instance_count", "production_instances", "instance_count"):
        try:
            value = int(settings.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return max(1, min(10, value))
    return 1


def _production_instance_base_port_from_settings(settings: dict) -> int:
    for key in ("production_instance_base_port", "instance_base_port", "server_port", "port"):
        try:
            value = int(settings.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    return 5000


def _find_gunicorn_executable() -> str | None:
    local = Path(__file__).resolve().parent / ".venv" / "bin" / "gunicorn"
    if local.exists() and os.access(local, os.X_OK):
        return str(local)
    return shutil.which("gunicorn")


def _production_worker_class_from_settings(settings: dict, env: dict | None = None) -> str:
    env = env or os.environ
    explicit = str(
        env.get("ECHOCHAT_GUNICORN_WORKER_CLASS")
        or settings.get("production_worker_class")
        or settings.get("gunicorn_worker_class")
        or ""
    ).strip().lower()
    if explicit:
        return "gthread" if explicit == "threading" else explicit

    async_mode = str(
        env.get("ECHOCHAT_SOCKETIO_ASYNC")
        or settings.get("production_async_mode")
        or "threading"
    ).strip().lower()
    if async_mode == "eventlet":
        return "eventlet"
    if async_mode in {"threading", "threads", "gthread"}:
        return "gthread"
    return async_mode or "gthread"


def _production_dependency_install_hint() -> str:
    return (
        "source .venv/bin/activate  # if your venv is not already active\n"
        "python -m pip install --upgrade pip\n"
        "python -m pip install -r requirements.txt"
    )


def _validate_production_dependencies(gunicorn: str, worker_class: str) -> list[str]:
    """Return human-readable production dependency errors before exec'ing Gunicorn.

    Gunicorn otherwise reports missing async workers as a raw "class uri invalid"
    traceback. Catching it here gives admins a clear fix command and avoids a
    confusing production startup failure.
    """
    errors: list[str] = []
    worker_class = (worker_class or "gthread").strip().lower()

    required_modules = ["gunicorn"]
    if worker_class == "eventlet":
        required_modules.append("eventlet")
    elif worker_class == "gthread":
        required_modules.append("simple_websocket")

    check = (
        "import importlib.util, sys\n"
        "mods = " + repr(required_modules) + "\n"
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]\n"
        "if missing:\n"
        "    raise SystemExit('missing:' + ','.join(missing))\n"
        "from gunicorn.util import load_class\n"
        f"load_class({worker_class!r})\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", check],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if "missing:gunicorn" in detail:
            errors.append("gunicorn is not importable in the active Python environment.")
        elif "missing:simple_websocket" in detail:
            errors.append("simple-websocket is not installed; it is required for the default threaded production WebSocket runner.")
        elif "missing:eventlet" in detail:
            errors.append("eventlet is not installed, but the eventlet production worker was selected.")
        else:
            errors.append(
                "Gunicorn could not load the selected production worker "
                f"({worker_class}). Install production dependencies or switch to gthread. "
                f"Details: {detail.splitlines()[-1] if detail else 'unknown worker-load failure'}"
            )

    return errors


def _blocking_public_beta_readiness_errors(settings: dict, settings_path: Path) -> list[str]:
    """Return public-beta readiness failures that should block production start.

    LAN/development production starts are allowed to proceed, but if the admin
    explicitly chose public_beta hosting then starting with bad HTTPS, cookie,
    origin, Redis, secret, or database settings would create a broken or unsafe
    tester experience.
    """
    try:
        from public_beta_readiness import build_public_beta_readiness, infer_hosting_mode

        if infer_hosting_mode(settings) != "public_beta":
            return []
        report = build_public_beta_readiness(
            settings,
            settings_file=settings_path,
            repo_root=Path(__file__).resolve().parent,
        )
        failures = [
            str(item.get("title") or item.get("code") or "readiness failure")
            for item in (report.get("items") or [])
            if str(item.get("level") or "").lower() == "fail"
        ]
        return failures
    except Exception as exc:
        return [f"could not complete public beta readiness check: {exc}"]


def _exec_production_server(settings: dict, settings_path: Path) -> None:
    """Replace the current process with Gunicorn using saved production settings."""
    root = Path(__file__).resolve().parent
    gunicorn = _find_gunicorn_executable()
    if not gunicorn:
        print("❌ Production mode is enabled, but gunicorn is not installed in this environment.")
        print("   Install dependencies first: pip install -r requirements.txt")
        print("   Or run one time in development mode: python main.py --development")
        raise SystemExit(2)

    env = os.environ.copy()
    env.setdefault("ECHOCHAT_CONFIG", str(settings_path))
    env.setdefault("ECHOCHAT_SOCKETIO_ASYNC", str(settings.get("production_async_mode") or "threading"))
    env.setdefault("ECHOCHAT_BIND", _production_bind_from_settings(settings))
    env.setdefault("ECHOCHAT_WORKERS", str(_production_workers_from_settings(settings)))
    env.setdefault("ECHOCHAT_PRODUCTION_WORKERS", env["ECHOCHAT_WORKERS"])
    env.setdefault("ECHOCHAT_PRODUCTION_INSTANCES", str(_production_instance_count_from_settings(settings)))
    env.setdefault("ECHOCHAT_INSTANCE_BASE_PORT", str(_production_instance_base_port_from_settings(settings)))
    if str(settings.get("socketio_message_queue") or "").strip():
        env.setdefault("ECHOCHAT_SOCKETIO_MESSAGE_QUEUE", str(settings.get("socketio_message_queue")).strip())
    if str(settings.get("shared_state_redis_url") or "").strip():
        env.setdefault("ECHOCHAT_SHARED_STATE_REDIS_URL", str(settings.get("shared_state_redis_url")).strip())
    env.setdefault("ECHOCHAT_FORWARDED_ALLOW_IPS", str(settings.get("forwarded_allow_ips") or "127.0.0.1"))
    env.setdefault("ECHOCHAT_GUNICORN_LOGLEVEL", str(settings.get("production_loglevel") or "info"))
    env.setdefault("ECHOCHAT_GUNICORN_WORKER_CLASS", _production_worker_class_from_settings(settings, env))

    worker_class = env["ECHOCHAT_GUNICORN_WORKER_CLASS"]
    readiness_errors = _blocking_public_beta_readiness_errors(settings, settings_path)
    if readiness_errors:
        print("❌ Public beta production readiness failed. Echo-Chat will not start as an internet-facing beta yet.")
        for err in readiness_errors[:12]:
            print(f"   - {err}")
        if len(readiness_errors) > 12:
            print(f"   - ...and {len(readiness_errors) - 12} more failure(s)")
        print("\nRun this for the full deployment report:")
        print("python main.py --public-beta-check")
        print("\nFor LAN-only testing instead:")
        print("python main.py --development")
        raise SystemExit(2)

    dependency_errors = _validate_production_dependencies(gunicorn, worker_class)
    if dependency_errors:
        print("❌ Production mode is enabled, but this environment is missing a required production runtime dependency.")
        for err in dependency_errors:
            print(f"   - {err}")
        print("\nFix it from your Echo-Chat project folder:")
        print(_production_dependency_install_hint())
        print("\nThen start again with:")
        print("python main.py --production")
        print("\nTemporary local fallback:")
        print("python main.py --development")
        raise SystemExit(2)

    server_name = _server_display_name(settings)
    print(f"🚀  Starting {server_name} in production mode with Gunicorn.")
    print(f"   config:  {env['ECHOCHAT_CONFIG']}")
    print(f"   bind:    {env['ECHOCHAT_BIND']}")
    print(f"   workers: {env['ECHOCHAT_WORKERS']} per instance")
    planned_instances = _production_instance_count_from_settings(settings)
    if planned_instances > 1:
        base_port = _production_instance_base_port_from_settings(settings)
        end_port = base_port + planned_instances - 1
        print(f"   scale:   {planned_instances} planned one-worker instance(s), ports {base_port}-{end_port}")
        print("   note:    this command starts one instance; use the deployment kit/systemd template for all planned instances.")
    print(f"   async:   {env['ECHOCHAT_SOCKETIO_ASYNC']}")
    print(f"   worker:  {worker_class}")
    print(f"   queue:   {'configured' if str(env.get('ECHOCHAT_SOCKETIO_MESSAGE_QUEUE') or '').strip() else 'not configured'}")
    print(f"   shared:  {'configured' if str(env.get('ECHOCHAT_SHARED_STATE_REDIS_URL') or '').strip() else 'not configured'}")
    if planned_instances > 1 and bool(settings.get("auto_configure_scaled_redis", True)):
        print("   Redis:   auto-config enabled for scaled deployments")
    print(f"   fwd IPs: {env.get('ECHOCHAT_FORWARDED_ALLOW_IPS')}")
    from redis_socketio_readiness import blocking_topology_errors, build_redis_socketio_report, format_redis_socketio_report

    topology_errors = blocking_topology_errors(settings)
    if topology_errors:
        print("❌ Production Socket.IO topology is not safe to start.")
        for err in topology_errors:
            print(f"   - {err}")
        print("\nRun this for the full beginner-friendly report:")
        print("python main.py --redis-socketio-check")
        raise SystemExit(2)

    topology_report = build_redis_socketio_report(settings, live_check=False)
    if topology_report.get("overall") == "warn":
        print("⚠️  Redis/Socket.IO topology warnings detected. Production can start, but review with:")
        print("   python main.py --redis-socketio-check")

    cmd = [gunicorn, "-c", str(root / "gunicorn_conf.py"), "wsgi:app"]
    os.execvpe(gunicorn, cmd, env)


def _run_setup_tui_doctor() -> None:
    """Print terminal/curses facts needed by the blue setup UI."""
    print("=== Echo-Chat setup TUI doctor ===")
    print(f"stdin is TTY:  {os.isatty(0)}")
    print(f"stdout is TTY: {os.isatty(1)}")
    print(f"TERM:          {os.environ.get('TERM') or '(empty)'}")
    print(f"COLORTERM:     {os.environ.get('COLORTERM') or '(empty)'}")
    print(f"Konsole vars:  KONSOLE_VERSION={os.environ.get('KONSOLE_VERSION') or '(empty)'}")
    print(f"setup legacy:  ECHOCHAT_SETUP_LEGACY={os.environ.get('ECHOCHAT_SETUP_LEGACY') or '(empty)'}")
    print(f"setup force:   ECHOCHAT_SETUP_TUI={os.environ.get('ECHOCHAT_SETUP_TUI') or '(empty)'}")
    if os.environ.get("ECHOCHAT_DOTENV_FILE"):
        print(f"dotenv file:   {os.environ.get('ECHOCHAT_DOTENV_FILE')}")
        keys = os.environ.get("ECHOCHAT_DOTENV_KEYS", "")
        if "ECHOCHAT_SETUP_LEGACY" in {x.strip() for x in keys.split(',') if x.strip()}:
            print("dotenv note:   ECHOCHAT_SETUP_LEGACY came from .env and will be ignored by setup.")
    try:
        size = shutil.get_terminal_size(fallback=(0, 0))
        print(f"terminal size: {size.columns}x{size.lines}")
    except Exception as exc:
        print(f"terminal size: error: {exc}")
    try:
        import curses  # type: ignore
        print("python curses: import OK")
    except Exception as exc:
        print(f"python curses: import FAILED: {exc}")
        return
    try:
        curses.setupterm(fd=1)
        colors = curses.tigetnum("colors")
        clear = curses.tigetstr("clear") is not None
        cup = curses.tigetstr("cup") is not None
        print(f"terminfo:      colors={colors} clear={clear} cursor_address={cup}")
    except Exception as exc:
        print(f"terminfo:      FAILED: {exc}")
    print("Recommendation:")
    print("  TERM=xterm-256color ECHOCHAT_SETUP_TUI=1 python main.py --setup")

def _setup_bypassing_cli_command(args: argparse.Namespace) -> bool:
    """Return True for explicit CLI actions that must not auto-launch setup.

    A missing server_config.json should open setup only for normal server start
    or explicit --setup. Diagnostic/deployment/migration commands should do the
    thing the user asked for instead of dropping into the interactive wizard.
    If the admin explicitly passes --setup, setup wins instead of being silently
    skipped by a combined diagnostic flag.
    """
    if getattr(args, "setup", False):
        return False
    return bool(
        getattr(args, "setup_doctor", False)
        or args.public_beta_check
        or args.redis_socketio_check
        or args.hosting_help
        or getattr(args, "dynamic_dns_check", False)
        or getattr(args, "dynamic_dns_update", False)
        or args.deployment_plan
        or args.write_deployment_kit
        or args.generate_proxy_config
        or args.preflight
        or args.migrate
        or args.list_migrations
        or args.schema_version
        or getattr(args, "generate_secrets", False)
    )


def _should_launch_setup(args: argparse.Namespace, *, config_missing_at_start: bool, setup_bypassing_command: bool) -> bool:
    """Return True when this process should run the interactive setup wizard."""
    return (not setup_bypassing_command) and (bool(getattr(args, "setup", False)) or bool(config_missing_at_start))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="configurable chat server")
    p.add_argument("--setup", action="store_true", help="run the interactive setup wizard")
    p.add_argument("--setup-doctor", action="store_true", help="diagnose terminal/curses support for the blue setup UI and exit")
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument("--production", action="store_true", help="start with the production Gunicorn runner for this launch")
    mode_group.add_argument("--development", action="store_true", help="force the built-in development/LAN runner even when run_mode=production")
    p.add_argument("--config", default=CONFIG_FILE, help="path to server config JSON")
    p.add_argument("--migrate", action="store_true", help="apply pending DB migrations and exit")
    p.add_argument("--list-migrations", action="store_true", help="list available DB migrations and exit")
    p.add_argument("--schema-version", action="store_true", help="print DB schema version and exit")
    p.add_argument("--preflight", action="store_true", help="run startup preflight checks and exit")
    p.add_argument("--public-beta-check", action="store_true", help="check public beta hosting readiness and exit")
    p.add_argument("--redis-socketio-check", action="store_true", help="check Redis, Socket.IO, Gunicorn worker, and rate-limit production topology and exit")
    p.add_argument("--redis-live-check", action="store_true", help="when used with --redis-socketio-check, also ping configured Redis URLs")
    p.add_argument("--hosting-help", action="store_true", help="print plain-English LAN/no-domain/public-beta hosting guidance and exit")
    p.add_argument("--dynamic-dns-check", action="store_true", help="validate Dynamic DNS helper settings and exit")
    p.add_argument("--dynamic-dns-update", action="store_true", help="send one Dynamic DNS update request and exit")
    p.add_argument("--deployment-plan", action="store_true", help="print a step-by-step production deployment plan and exit")
    p.add_argument("--write-deployment-kit", action="store_true", help="write reviewable systemd, env, proxy, and checklist deployment files and exit")
    p.add_argument("--deployment-kit-output-dir", default="deploy/generated-deployment", help="output directory for --write-deployment-kit")
    p.add_argument("--generate-proxy-config", choices=["all", "caddy", "nginx"], help="generate Caddy/Nginx reverse proxy config files and exit")
    p.add_argument("--proxy-output-dir", default="deploy/generated-proxy", help="output directory for --generate-proxy-config")
    p.add_argument("--generate-secrets", action="store_true", help="print strong Echo-Chat .env secrets and exit")
    p.add_argument("--write-env-secrets", action="store_true", help="with --generate-secrets, write/update .env with chmod 600")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings_path = Path(args.config)
    config_missing_at_start = not settings_path.exists()

    settings = load_settings(settings_path)
    apply_env_overrides(settings)
    settings = _sync_run_mode_settings(settings)

    setup_bypassing_command = _setup_bypassing_cli_command(args)
    should_launch_setup = _should_launch_setup(
        args,
        config_missing_at_start=config_missing_at_start,
        setup_bypassing_command=setup_bypassing_command,
    )

    if getattr(args, "setup_doctor", False):
        _run_setup_tui_doctor()
        return

    if should_launch_setup:
        settings = seed_first_run_local_config(settings_path, settings)

    if should_launch_setup:
        print(f"\n=== {_server_display_name(settings)} Setup Wizard ===\n")
        _, interactive_setup, _ = _load_setup_helpers()
        settings = interactive_setup(settings)
        settings = _sync_run_mode_settings(settings)
        scaled_changed = apply_scaled_runtime_safety_defaults(settings)
        if scaled_realtime_requested(settings) and any(scaled_changed.values()):
            print("ℹ️  Setup auto-filled Redis URLs for the selected multi-instance deployment:")
            for line in scaled_redis_summary_lines(settings, scaled_changed):
                print(f"   {line}")
            print()
        secret_result = ensure_core_runtime_secrets(settings, settings_file=settings_path)
        # Generate stable at-rest/privacy keys automatically when the admin enables
        # those features. Values are written to protected .env when JSON secret
        # persistence is disabled, so restarts keep decrypting old data.
        for _canonical in (
            "profile_field_encryption_key",
            "email_field_encryption_key",
            "email_hash_key",
            "security_backup_encryption_key",
            "privacy_retention_hash_key",
        ):
            _value, _generated, _env_path = ensure_secret(settings, _canonical, settings_file=settings_path)
            if _generated:
                secret_result.setdefault("generated", []).append(_canonical)
                if _env_path:
                    secret_result["env_file"] = str(_env_path)
        missing_runtime_env = _missing_runtime_env_after_secret_scrub(settings)
        save_settings(settings_path, settings)
        print(f"✅ Saved settings to {settings_path}\n")
        if not persist_secrets_enabled(settings):
            print("🔐 Secret persistence is disabled for this mode; keep DB/API/SMTP/Twilio/TURN secrets in environment variables or .env.\n")
        if secret_result.get("generated"):
            print(f"🔐 Generated stable core secrets: {', '.join(secret_result.get("generated", []))}")
            if secret_result.get("env_file"):
                print(f"   Saved to protected env file: {secret_result.get("env_file")}")
            print()
        if missing_runtime_env:
            print("⚠️  Setup saved successfully, but Echo-Chat will not auto-start yet because required runtime secrets were kept out of server_config.json:")
            for item in missing_runtime_env:
                print(f"   - {item}")
            print("\nSet those environment variables, or rerun setup with ECHOCHAT_PERSIST_SECRETS=1 if you intentionally want legacy config-file secret storage.")
            return
        settings = _reload_saved_settings_for_runtime(settings_path)

    settings = _effective_settings_for_cli_mode(settings, args)

    if settings.get("admin_pass"):
        print("ℹ️  Config-file admin_pass is no longer used by /login.")
        print(f"   {_server_display_name(settings)} now requires DB-backed user login; run --setup if you want to sync the admin account/password.")


    if args.generate_secrets:
        bundle = generate_secret_bundle(include_crypto=True)
        if args.write_env_secrets:
            env_path = write_env_secrets(bundle, path=Path(".env"))
            print(f"✅ Wrote strong Echo-Chat secrets to {env_path} (chmod 600).")
            print("Restart Echo-Chat so the new .env values are loaded.")
        else:
            print(format_env_bundle(bundle), end="")
        return

    if args.public_beta_check:
        from public_beta_readiness import build_public_beta_readiness, format_public_beta_readiness_report
        report = build_public_beta_readiness(settings, settings_file=settings_path, repo_root=Path(__file__).resolve().parent)
        print(format_public_beta_readiness_report(report))
        if report.get("overall") == "fail":
            raise SystemExit(2)
        if report.get("overall") == "warn":
            raise SystemExit(1)
        return

    if args.redis_socketio_check:
        from redis_socketio_readiness import build_redis_socketio_report, format_redis_socketio_report
        report = build_redis_socketio_report(settings, live_check=bool(args.redis_live_check))
        print(format_redis_socketio_report(report))
        if report.get("overall") == "fail":
            raise SystemExit(2)
        if report.get("overall") == "warn":
            raise SystemExit(1)
        return

    if args.hosting_help:
        from hosting_help import format_hosting_help
        print(format_hosting_help(settings))
        return

    if args.dynamic_dns_check:
        from dynamic_dns import build_dynamic_dns_report, format_dynamic_dns_report
        report = build_dynamic_dns_report(settings, live_check=False)
        print(format_dynamic_dns_report(report))
        if report.get("overall") == "fail":
            raise SystemExit(2)
        if report.get("overall") == "warn":
            raise SystemExit(1)
        return

    if args.dynamic_dns_update:
        from dynamic_dns import format_dynamic_dns_update_result, update_dynamic_dns
        result = update_dynamic_dns(settings)
        print(format_dynamic_dns_update_result(result))
        if result.get("overall") == "fail" or not result.get("updated"):
            raise SystemExit(2)
        return

    if args.deployment_plan:
        from deployment_wizard import build_deployment_plan, format_deployment_plan
        plan = build_deployment_plan(settings, settings_file=settings_path, repo_root=Path(__file__).resolve().parent)
        print(format_deployment_plan(plan))
        if not plan.get("safe_to_invite"):
            raise SystemExit(1)
        return

    if args.write_deployment_kit:
        from deployment_wizard import format_deployment_kit_report, write_deployment_kit
        output_dir = Path(args.deployment_kit_output_dir or settings.get("deployment_kit_output_dir") or "deploy/generated-deployment")
        written = write_deployment_kit(
            settings,
            output_dir,
            proxy="all",
            settings_file=settings_path,
            repo_root=Path(__file__).resolve().parent,
        )
        print(format_deployment_kit_report(settings, written))
        return

    if args.generate_proxy_config:
        from reverse_proxy_generator import format_proxy_generation_report, write_proxy_configs
        written = write_proxy_configs(
            settings,
            Path(args.proxy_output_dir),
            proxy=str(args.generate_proxy_config),
            repo_root=Path(__file__).resolve().parent,
        )
        print(format_proxy_generation_report(settings, written))
        return

    if args.list_migrations and not (args.migrate or args.schema_version or args.preflight):
        # Dependency-light listing: do not import psycopg2/database stack just to inspect files.
        import ast
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        items = []
        for path in sorted(migrations_dir.glob("m*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
                vals = {"VERSION": path.stem, "NAME": path.stem, "KIND": "python"}
                for node in tree.body:
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id in vals and isinstance(node.value, ast.Constant):
                                vals[target.id] = str(node.value.value)
                items.append(vals | {"path": str(path)})
            except Exception:
                items.append({"VERSION": path.stem, "NAME": path.stem, "KIND": "python", "path": str(path)})
        if not items:
            print("No migrations found.")
        else:
            for item in sorted(items, key=lambda x: x.get("VERSION", "")):
                print(f"{item.get('VERSION')}  {item.get('NAME')}  [{item.get('KIND')}]  {item.get('path')}")
        return

    from database import init_db_pool, apply_migrations, list_available_migrations, get_schema_version
    from db.core import prepare_runtime_database
    from preflight import run_preflight, format_preflight_report

    try:
        prepare_runtime_database(settings)
    except Exception as exc:
        print("❌ Echo-Chat could not start because the PostgreSQL database setting is not usable.")
        print(f"   {exc}")
        print("\nFix options:")
        print("   1. Run setup again: python main.py --setup")
        print("   2. Or set a local PostgreSQL DSN, for example:")
        print("      export DATABASE_URL=postgresql://$USER@localhost:5432/echochat")
        print("   3. If setup needs to create/repair the DB, also set:")
        print("      export ECHOCHAT_DB_BOOTSTRAP_URL=postgresql://$USER@localhost:5432/postgres")
        raise SystemExit(2)
    configure_logging(settings)

    def _init_db_runtime() -> None:
        def _safe_int(value, default, minimum=1, maximum=100):
            try:
                out = int(value)
            except Exception:
                out = int(default)
            return max(minimum, min(maximum, out))
        try:
            instances = int(settings.get("production_instance_count") or 1)
        except Exception:
            instances = 1
        cfg_min = _safe_int(settings.get("db_pool_min", 1), 1, minimum=1, maximum=25)
        raw_max = settings.get("db_pool_max")
        if raw_max is None or str(raw_max).strip() == "":
            cfg_max = 50 if instances <= 1 else (25 if instances <= 2 else 15 if instances <= 5 else 10)
        else:
            cfg_max = _safe_int(raw_max, 50 if instances <= 1 else 10, minimum=1, maximum=100)
        if instances <= 1 and cfg_max < 50:
            cfg_max = 50
        if cfg_min > cfg_max:
            cfg_min = cfg_max
        init_db_pool(
            minconn=cfg_min,
            maxconn=cfg_max,
            dsn=str(settings.get("database_url")) if settings.get("database_url") else None,
        )

    if args.list_migrations and not (args.migrate or args.schema_version):
        items = list_available_migrations()
        if not items:
            print("No migrations found.")
        else:
            for item in items:
                print(f"{item['version']}  {item['name']}  [{item['kind']}]  {item['path']}")
        return

    if args.migrate or args.schema_version or args.list_migrations or args.preflight:
        from flask import Flask

        app = Flask(__name__)
        with app.app_context():
            if args.migrate or args.schema_version:
                _init_db_runtime()
            if args.list_migrations:
                items = list_available_migrations()
                if not items:
                    print("No migrations found.")
                else:
                    for item in items:
                        print(f"{item['version']}  {item['name']}  [{item['kind']}]  {item['path']}")
            if args.migrate:
                result = apply_migrations()
                print("Applied:", ", ".join(result.get("applied") or []) or "none")
                print("Skipped:", ", ".join(result.get("skipped") or []) or "none")
            if args.schema_version:
                print(get_schema_version())
            if args.preflight:
                result = run_preflight(
                    settings,
                    settings_file=settings_path,
                    init_db_pool_if_needed=not (args.migrate or args.schema_version),
                )
                print(format_preflight_report(result))
                if result.get("overall") == "fail":
                    raise SystemExit(2)
        return

    if _should_run_production(settings, args):
        _exec_production_server(settings, settings_path)
        return

    # Ensure document root exists (used by some templates/static expectations)
    www_folder = settings.get("document_root", "www")
    os.makedirs(www_folder, exist_ok=True)

    from server_init import run_web_server

    run_web_server(settings, limiter=None, settings_file=settings_path)


if __name__ == "__main__":
    main()
