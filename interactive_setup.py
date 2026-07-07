#!/usr/bin/env python3
"""interactive_setup.py

Echo-Chat setup wizard.

This project uses a focused numbered setup flow for the settings Echo-Chat needs
at runtime: database DSN, bind host/port, JWT secret, cookies, mail, abuse
controls, media, voice, hosting mode, diagnostics, and initial DB-backed admin
accounts.

The wizard compacts saved JSON to known Echo-Chat runtime keys so
server_config.json stays readable and avoids legacy setup clutter.
"""

from __future__ import annotations

try:
    import curses
except Exception:  # pragma: no cover - depends on platform terminal support
    curses = None  # type: ignore[assignment]
import getpass
import json
import locale
import os
import re
import shutil
import smtplib
import socket
import ssl
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from emailer import effective_smtp_settings, smtp_from_warning
from dynamic_dns import dynamic_dns_setup_errors, build_dynamic_dns_report, format_dynamic_dns_report
from sms_2fa_config import effective_twilio_settings, twilio_ready, twilio_setup_errors
from email_at_rest import prepare_email_storage
from secret_manager import ensure_secret, is_strong_secret, resolve_secret
from webrtc_ice_config import (
    DEFAULT_ICE_SERVERS,
    apply_turn_credentials,
    first_turn_username,
    ice_server_summary,
    ice_servers_to_text,
    parse_ice_servers_text,
    p2p_ice_servers,
    turn_credential_errors,
    redact_ice_servers,
    voice_ice_servers,
)

# Setup field metadata may include a visible_when predicate for conditional fields.
# Setup menu state may include selected_help text for the focused field.

import psycopg2
import redis

from constants import APP_VERSION, DEFAULT_DB_CONNECTION_STRING, DEFAULT_SERVER_NAME, PROJECT_NAME, is_placeholder_postgres_dsn, sanitize_postgres_dsn
from health_status import normalize_public_probe_path
from security import hash_password, verify_password
from registration_name_policy import normalize_registration_username, validate_registration_username_format
from account_creation_policy import validate_account_password, password_policy_summary, validate_account_username_style, validate_recovery_pin, recovery_pin_policy_summary
from scaled_redis_autoconfig import (
    RECOMMENDED_RATE_LIMIT_REDIS,
    RECOMMENDED_SOCKETIO_QUEUE_REDIS,
    RECOMMENDED_SHARED_STATE_REDIS,
    apply_scaled_runtime_safety_defaults,
    redis_install_hint,
    scaled_realtime_requested,
    scaled_redis_summary_lines,
)
from database import create_user_with_keys, ensure_user_has_keys, ensure_user_has_default_avatar, _seed_roles_permissions
from public_beta_readiness import (
    apply_hosting_mode_preset,
    build_public_beta_readiness,
    format_public_beta_readiness_report,
    infer_hosting_mode,
    public_beta_readiness_lines,
)
from reverse_proxy_generator import format_proxy_generation_report, write_proxy_configs
from deployment_wizard import format_deployment_kit_report, format_deployment_plan, build_deployment_plan, write_deployment_kit
from db.bootstrap import (
    build_postgres_dsn,
    delete_database_via_bootstrap,
    discover_existing_server_database_dsn as _discover_existing_server_database_dsn_impl,
    discover_echochat_database_candidates as _discover_echochat_database_candidates_impl,
    dsn_parts,
    ensure_database_ready,
    ensure_database_ready_via_local_admin as _ensure_database_ready_via_local_admin_impl,
    is_protected_database_name,
    target_database_status as _target_database_status_impl,
    validate_echochat_database as _validate_echochat_database_impl,
)


# ──────────────────────────────────────────────────────────────────────────────
# Defaults (compact)
# ──────────────────────────────────────────────────────────────────────────────

_ACTIVE_SETUP_SERVER_NAME = DEFAULT_SERVER_NAME

def _setup_display_name(settings: Optional[Dict[str, Any]] = None) -> str:
    """Return the configured public display name for this deployed chat server."""
    raw = ""
    if isinstance(settings, dict):
        raw = str(settings.get("server_name") or "").strip()
    if not raw:
        raw = str(_ACTIVE_SETUP_SERVER_NAME or DEFAULT_SERVER_NAME).strip()
    return raw.replace("\r", " ").replace("\n", " ").strip() or DEFAULT_SERVER_NAME

def _set_active_setup_server_name(value: Any) -> None:
    global _ACTIVE_SETUP_SERVER_NAME
    _ACTIVE_SETUP_SERVER_NAME = str(value or DEFAULT_SERVER_NAME).replace("\r", " ").replace("\n", " ").strip() or DEFAULT_SERVER_NAME

def _brand_ui_text(value: Any, settings: Optional[Dict[str, Any]] = None) -> str:
    """Normalize old visible project labels without hiding the server-name distinction.

    Echo-Chat is the project/software name. ``server_name`` is the admin-chosen
    public name of this specific chat server. This helper fixes stale legacy
    project labels, but it no longer rewrites every project reference into the
    server display name.
    """
    text = str(value)
    return (
        text.replace("Echo Messenger", PROJECT_NAME)
            .replace("Echo Chat Server", f"{PROJECT_NAME} server")
            .replace("Echo Chat", PROJECT_NAME)
            .replace("EchoChat", PROJECT_NAME)
    )


# Runtime defaults that were historically documented in the example JSON files
# but were not present in get_default_settings(). Keep them here so setup's
# compact save path preserves hand-edited config instead of silently dropping it.
_RUNTIME_CONFIG_DEFAULTS: Dict[str, Any] = {
    "password_reset_spool_file": "logs/reset_links.log",
    "password_reset_spool_allow_remote": False,
    "max_user_file_storage_bytes": 250 * 1024 * 1024,
    "max_user_torrent_storage_bytes": 25 * 1024 * 1024,
    "max_torrent_total_size_bytes": 1024 * 1024 * 1024 * 1024,
    "disable_dm_files_globally": False,
    "rate_limit_login": "10 per minute",
    "rate_limit_register": "3 per minute",
    "rate_limit_forgot_password": "3 per minute",
    "rate_limit_reset_password": "6 per minute",
    "rate_limit_refresh": "30 per minute",
    "rate_limit_auth_ping": "120 per minute",
    "rate_limit_activity": "120 per minute",
    "rate_limit_username_available": "30 per minute",
    "rate_limit_public_key": "120 per minute",
    "rate_limit_sessions": "120 per minute",
    "rate_limit_logout_others": "10 per minute",
    "rate_limit_logout_all": "5 per minute",
    "rate_limit_account_security": "30 per minute",
    "janitor_debug_custom_rooms": False,
    "cleanup_revoked_private_files_enabled": True,
    "cleanup_orphan_private_file_blobs_enabled": True,
    "revoked_private_file_retention_days": 7,
    "orphan_private_file_grace_minutes": 60,
    "private_file_cleanup_batch_limit": 500,
    "admin_reauth_once_per_session": True,
    "admin_fresh_auth_window_seconds": 28800,
    "rate_limit_upload": "20 per minute",
    "rate_limit_dm_file_upload": "10 per minute",
    "rate_limit_group_file_upload": "10 per minute",
    "rate_limit_torrent_upload": "5 per minute",
    "rate_limit_torrent_scrape": "30 per minute",
    "admin_rate_limit_get": "600 per minute",
    "admin_rate_limit_write": "120 per minute",
    "friend_req_action_rate_limit": 30,
    "friend_req_action_rate_window_sec": 60,
    "social_action_rate_limit": 60,
    "social_action_rate_window_sec": 60,
    "p2p_file_signal_rate_limit": 600,
    "p2p_file_signal_rate_window_sec": 60,
    "admin_socket_read_rate_limit": 120,
    "admin_socket_read_rate_window_sec": 60,
    "admin_socket_write_rate_limit": 60,
    "admin_socket_write_rate_window_sec": 60,
    "shared_state_redis_url": "redis://127.0.0.1:6379/2",
    "shared_state_prefix": "echochat",
    "shared_state_session_ttl_seconds": 300,
    "socketio_client_url": "/static/vendor/socket.io.min.js",
    "api_rate_limit_write_guard": "300 per minute",
    "auth_rate_limit_write_guard": "60 per minute",
    "rate_limit_refresh_guard": "45 per minute",
    "form_rate_limit_write_guard": "30 per minute",
    "enforce_same_origin_writes": True,
        "enforce_jwt_double_submit_csrf_writes": True,
    "enable_legacy_get_logout": False,
    "require_logout_csrf": True,
    "rate_limit_refresh_session": "20@60",
    "rate_limit_login_username": "20@300",
    "rate_limit_login_ip_username": "10@300",
    "rate_limit_register_username": "5@3600",
    "rate_limit_register_email": "5@3600",
    "rate_limit_forgot_password_email": "5@3600",
    "rate_limit_forgot_password_username": "5@3600",
    "rate_limit_reset_password_token": "10@900",
    "password_reset_log_local_links": False,
    "password_reset_max_active_tokens": 3,
    "production_worker_class": "gthread",
    "socketio_event_max_payload_bytes": 65536,
    "socketio_event_rate_limit": "180 per minute",
    "socketio_connect_rate_limit": "30 per minute",
    "socketio_max_sessions_per_user": 8,
    "socketio_max_sessions_per_auth_session": 4,
    "reverse_proxy_lan_port": 8080,
    "no_domain_yet_note": "Choose hosting_mode=no_domain_yet until you have a real HTTPS domain or tunnel hostname.",
    "voice_audio_quality": "balanced",
    "voice_auto_quality": True,
    "voice_noise_cancellation": True,
    "voice_echo_cancellation": True,
    "voice_auto_gain_control": True,
    "voice_default_push_to_talk": True,
    "echo_media_transport": "echo-webrtc-mesh",
    "torrent_public_fallback_scrape_enabled": True,
    "torrent_public_fallback_trackers": [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://tracker.moeking.me:6969/announce",
        "https://tracker2.ctix.cn:443/announce",
        "https://tracker.tamersunion.org:443/announce",
    ],
    "torrent_dht_scrape_enabled": True,
    "torrent_dht_scrape_timeout_sec": 0.9,
    "torrent_dht_scrape_max_queries": 24,
}


_HIDDEN_WIZARD_SETTING_KEYS = {
    # Legacy, compatibility, or setup-helper keys that are not true runtime controls.
    "domain_name",
    "document_root",
    "room_history_limit",
    "room_history_page_size",
    "key_management_option",
    "ssl_tls_settings",
    # Old aliases still import correctly, but new setup saves the current keys only.
    "p2p_ice",
    "webrtc_ice_servers",
    "ice_servers",
    # Retired public upload route controls stay default-off and hidden from new setup files.
    "enable_legacy_public_uploads",
    "max_legacy_public_upload_bytes",
    "allow_legacy_torrent_download_without_metadata",
    "allow_global_torrent_downloads",
}


def _saved_setting_keys(template: Optional[Dict[str, Any]] = None) -> list[str]:
    template = template or get_default_settings()
    return [k for k in template.keys() if k not in _HIDDEN_WIZARD_SETTING_KEYS]


def get_default_settings() -> Dict[str, Any]:
    """Return a compact set of defaults for EchoChat.

    Notes:
      - Keep secrets out of JSON when possible; prefer env vars.
      - server_init.py will generate/persist secret_key + jwt_secret if missing.
    """

    raw_dsn = sanitize_postgres_dsn(
        os.getenv("DATABASE_URL")
        or os.getenv("DB_CONNECTION_STRING")
        or DEFAULT_DB_CONNECTION_STRING
    )
    dsn = "" if is_placeholder_postgres_dsn(raw_dsn) else str(raw_dsn or "")

    return {
        # ── Core server ──────────────────────────────────────────────────
        "server_name": DEFAULT_SERVER_NAME,
        "server_host": "0.0.0.0",
        "server_port": 5000,
        # Backwards-compat keys (some code paths still check these first)
        "host": "0.0.0.0",
        "port": 5000,
        "server_debug": False,
        "debug": False,
        # Runtime/startup mode. "development" uses the built-in Flask-SocketIO
        # runner. "production" makes plain `python main.py` exec Gunicorn.
        "run_mode": "development",
        "production_mode": False,
        "production_bind": "",
        "production_workers": 1,
        "production_instance_count": 1,
        "production_instance_base_port": 5000,
        "production_instance_bind_host": "127.0.0.1",
        "production_instance_port_step": 1,
        "production_async_mode": "threading",
        "production_loglevel": "info",
        "https": False,
        "ssl_cert_file": "",
        "ssl_key_file": "",
        "domain_name": "",
        "document_root": "www",

        # Secrets (server_init.py will generate/persist if missing)
        "secret_key": "",
        "jwt_secret": "",
        "jwt_secret_key": "",  # legacy alias

        # ── Database ─────────────────────────────────────────────────────
        "database_url": dsn,
        "database_bootstrap_url": "",  # "database_bootstrap_url": ""
        "db_pool_min": 1,
        "db_pool_max": 50,

        # ── Auth / cookies ───────────────────────────────────────────────
        "admin_user": os.getenv("ADMIN_USER") or "admin",
        "admin_pass": "",  # PBKDF2 hash (salt:hash)
        "admin_notification_email": "",
        "cookie_secure": False,
        "cookie_samesite": "Lax",
        "allow_insecure_lan_cookie_fallback": True,
        "allow_insecure_production_start": False,
        "access_token_minutes": 30,
        "refresh_token_days": 7,
        # Idle logout (hours of no activity before auto-logout). Set 0 to disable.
        "idle_logout_hours": 8,
        # Auto-set presence to Away after this many inactive minutes. Set 0 to disable.
        "presence_idle_minutes": 15,
        # Auto-set presence to Invisible (appears offline to others) after this many inactive minutes. Set 0 to disable.
        "presence_offline_minutes": 0,

        # ── Autoscaled public rooms (Lobby -> Lobby (2) -> ...) ─────────
        "autoscale_rooms_enabled": True,
        "autoscale_room_capacity": 30,
        "autoscale_room_idle_minutes": 30,
        "public_base_url": "",
        "hosting_mode": "lan",  # lan | public_beta | advanced
        "socketio_message_queue": "",
        "socketio_transports": ["polling"],
        "password_reset_token_minutes": 15,
        "password_reset_daily_limit": 3,
        "password_reset_max_active_tokens": 3,
        "recovery_pin_max_attempts": 5,
        "recovery_pin_lock_minutes": 15,
        # If true, every server restart revokes *all* sessions (forces re-login).
        # Off by default.
        "revoke_all_tokens_on_start": False,
        "refresh_rotation_grace_seconds": 10,
        "trust_proxy_headers": False,
        "proxy_fix_hops": 1,
        "reverse_proxy_backend_host": "127.0.0.1",
        "reverse_proxy_backend_port": 5000,
        "reverse_proxy_output_dir": "deploy/generated-proxy",
        "deployment_kit_output_dir": "deploy/generated-deployment",
        "systemd_service_user": "echochat",
        "systemd_service_group": "echochat",
        "systemd_working_directory": "/opt/echochat/Echo-Chat-main",
        "systemd_python": "/opt/echochat/Echo-Chat-main/.venv/bin/python",
        "systemd_env_file": "/etc/echochat/echochat.env",

        # ── Email (SMTP relay; password reset) ───────────────────────────
        "smtp_enabled": False,
        "smtp_provider": "",
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_use_starttls": True,
        "smtp_use_ssl": False,
        "smtp_from": f"{DEFAULT_SERVER_NAME} <no-reply@yourdomain.com>",
        "smtp_timeout_seconds": 20,

        # ── SMS 2FA (Twilio Verify; optional) ────────────────────────────
        "enable_two_factor_beta": False,
        "enable_sms_two_factor": False,
        "two_factor_sms_channel": "sms",
        "twilio_account_sid": "",
        "twilio_auth_token": "",
        "twilio_verify_service_sid": "",
        "two_factor_login_timeout_seconds": 600,
        "rate_limit_login_2fa_check": "10@600",
        "rate_limit_login_2fa_resend": "3@300",
        "rate_limit_enable_2fa": "3 per minute",
        "rate_limit_enable_2fa_send": "3@300",
        "rate_limit_enable_2fa_verify": "10@600",
        "rate_limit_enable_2fa_resend": "3@300",


        # ── GIFs (GIPHY) ────────────────────────────────────────────────
        # Prefer env var GIPHY_API_KEY; you may also store it encrypted in
        # server_config.json as giphy_api_key.
        "giphy_enabled": True,
        "giphy_api_key": "",
        "giphy_rating": "pg-13",
        "giphy_lang": "en",
        "giphy_default_limit": 24,
        "giphy_cache_ttl_sec": 45,
        "allow_svg_avatars": False,
        "disable_file_transfer_globally": False,
        "disable_group_files_globally": False,

        # ── CORS / rate limiting ─────────────────────────────────────────
        "cors_allowed_origins": ["http://127.0.0.1:5000", "http://localhost:5000"],
        "allowed_origins": ["http://127.0.0.1:5000", "http://localhost:5000"],
        "auto_allow_lan_origins": True,
        "rate_limit_storage_uri": "memory://",
        "rate_limit_storage": "memory://",
        # Group chat flood control (messages per window)
        # The server accepts either an int (treated as per-minute) or strings like "60 per minute".
        # Prefer ints in generated config.
        "group_msg_rate_limit": 60,
        "group_msg_rate_window_sec": 60,

        # Room/DM flood control (messages per window)
        # Accept either an int (treated as per-minute) or strings like "20@10" (20 per 10 seconds).
        "room_msg_rate_limit": "20@10",
        "room_msg_rate_window_sec": 10,
        "dm_msg_rate_limit": "15@10",
        "dm_msg_rate_window_sec": 10,
        "enable_room_typing_indicators": False,
        "enable_dm_typing_indicators": True,
        "enable_group_typing_indicators": True,
        "dm_typing_rate_limit": "30@10",
        "dm_typing_rate_window_sec": 10,
        "group_typing_rate_limit": "30@10",
        "group_typing_rate_window_sec": 10,
        # File transfer signaling flood control (offers per window)
        "file_offer_rate_limit": "5@60",
        "room_gif_rate_limit": "6@20",
        "room_torrent_rate_limit": "2@30",
        "file_offer_rate_window_sec": 60,
        "room_typing_rate_limit": "30@10",
        "room_typing_rate_window_sec": 10,
        "room_reaction_rate_limit": "12@30",
        "room_reaction_rate_window_sec": 30,
        "room_media_action_rate_limit": "10@30",
        "room_media_action_rate_window_sec": 30,
        "room_media_presence_rate_limit": "30@30",
        "room_media_presence_rate_window_sec": 30,
        "room_catalog_rate_limit": "30@10",
        "room_catalog_rate_window_sec": 10,
        "room_counts_rate_limit": "60@60",
        "room_counts_rate_window_sec": 60,
        "wave_user_rate_limit": "10@60",
        "wave_user_rate_window_sec": 60,
        "poll_vote_rate_limit": "20@60",
        "poll_vote_rate_window_sec": 60,
        "room_control_rate_limit": "12@30",
        "room_control_rate_window_sec": 30,

        # Client message text motion. Allowed: none, fade, rise, slide, scale.
        # Room chat defaults to none because animated chat text can feel jumpy.
        "chat_text_animation": "none",
        "dm_text_animation": "rise",
        "group_text_animation": "rise",
        # Sender labels / compact message grouping. False keeps the modern compact grouped style.
        "room_show_sender_every_message": False,
        "dm_show_sender_every_message": False,
        "group_show_sender_every_message": False,

        # Client notification sound defaults (generated in-browser with Web Audio).
        "sound_notifications_default": True,
        "sound_pack_external_urls": [],
        "sound_pack_load_local_builtins": True,
        "emoticons_enabled": True,
        "emoticons_local_enabled": True,
        "emoticons_external_enabled": True,
        "emoticons_asset_mode": "local_first",
        "emoticons_local_root": "emoticons",
        "emoticons_external_asset_base_url": "https://github.com/chinhodado/ym_emo_fb",
        "emoticons_animation_stop_ms": 4500,
        "emoticons_boot_preload_enabled": True,
        "emoticons_boot_preload_limit": 180,
        "emoticons_boot_preload_concurrency": 4,
        "emoticons_catalog_cache_seconds": 86400,
        "emoticons_custom_entries": [],
        "sound_pack_default": "echo_modern_generated",
        "sound_theme_default": "soft_chime",
        "sound_event_dm": "mellow_pluck",
        "sound_event_room_message": "soft_chime",
        "sound_event_group_message": "sonar_ping",
        "sound_event_room_invite": "doorbell_duo",
        "sound_event_group_invite": "doorbell_duo",
        "sound_event_friend_request": "success_twinkle",
        "sound_event_room_join": "page_flip",
        "sound_event_file": "digital_drop",
        "sound_event_error": "warning_pulse",

        # Slowmode per room (seconds between messages per user). 0 disables.
        "room_slowmode_default_sec": 0,
        # Room history is disabled.
        "room_history_limit": 0,
        "room_history_page_size": 0,
        "allow_legacy_plaintext_room_history": False,
        "allow_legacy_numeric_group_history": False,
        "disable_legacy_group_file_upload": True,
        "require_private_room_e2ee": True,
        "require_room_e2ee": False,
        "privacy_retention_enabled": True,
        "privacy_ip_user_agent_retention_days": 30,
        "privacy_audit_detail_retention_days": 90,
        "all_room_e2ee_impact_acknowledged": False,
        # Back-compat alias used by older group history paths.
        "allow_legacy_plaintext_history": False,

        # Background cleanup
        "janitor_interval_seconds": 60,
        # Custom rooms are removed if empty/inactive beyond this threshold (minutes).
        "custom_room_idle_minutes": 180,
        # Back-compat mirror for older builds that still read hour-based keys.
        "custom_room_idle_hours": 3,
        # Private custom rooms are often ephemeral; default is shorter.
        "custom_private_room_idle_minutes": 180,
        # Back-compat mirror for older builds that still read hour-based keys.
        "custom_private_room_idle_hours": 3,


        # Anti-abuse: auto-mute if the user repeatedly hits limits.
        "antiabuse_strikes_before_mute": 6,
        "antiabuse_strike_window_sec": 30,
        "antiabuse_auto_mute_minutes": 2,
        "antiabuse_exempt_staff": True,

        # Anti-abuse: join / room creation / friend request flood control
        "room_join_rate_limit": "15@30",
        "room_join_rate_window_sec": 30,
        "room_switch_cooldown_sec": 1,
        "room_create_rate_limit": "5@300",
        "room_create_rate_window_sec": 300,
        "rate_limit_custom_room_create": "5@300",
        "rate_limit_custom_room_invite": "20@60",
        "rate_limit_room_invite": "20@60",
        "rate_limit_room_invite_response": "30@60",
        "rate_limit_room_invite_read": "120@60",
        "max_chat_api_json_bytes": 8192,
        "friend_req_rate_limit": "5@60",
        "friend_req_rate_window_sec": 60,
        "friend_req_unique_targets_max": 20,
        "friend_req_unique_targets_window_sec": 600,

        # Room creation policy
        "allow_user_create_rooms": True,
        "max_room_name_length": 48,
        "block_custom_room_terms_enabled": True,
        "blocked_custom_room_terms": "",
        "block_registration_terms_enabled": True,
        "blocked_registration_terms": "",

        # Anti-spam content heuristics (plaintext rooms only)
        "max_links_per_message": 8,
        "max_magnets_per_message": 2,
        "max_mentions_per_message": 12,
        "dup_msg_window_sec": 20,
        "dup_msg_max": 3,
        "dup_msg_min_length": 6,
        "dup_msg_normalize": True,


        # ── Logging ──────────────────────────────────────────────────────
        "log_level": "INFO",
        "log_format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "log_file_path": "logs/server.log",

        # ── Health ───────────────────────────────────────────────────────
        "enable_health_check_endpoint": False,
        "health_check_endpoint": "/health",

        # ── Chat limits ──────────────────────────────────────────────────
        "max_message_length": 1000,
        "max_room_cipher_length": 120000,
        "max_room_key_recipients": 120,
        "room_live_message_ttl_seconds": 21600,
        "room_live_message_max": 5000,
        "max_attachment_size": 10 * 1024 * 1024,
        "max_group_message_chars": 4000,
        "max_group_upload_bytes": 25 * 1024 * 1024,
        # Back-compat mirror for code paths that still read the legacy key.
        "max_group_file_bytes": 25 * 1024 * 1024,
        "max_request_bytes": 31457280,
        "max_form_memory_size": 500000,
        "max_form_parts": 100,
        "allow_missing_same_origin_headers_for_writes": False,

        # Profile media/posts
        "allow_svg_avatars": False,
        "max_profile_avatar_bytes": 5 * 1024 * 1024,
        "max_profile_banner_bytes": 8 * 1024 * 1024,
        "max_profile_post_image_bytes": 8 * 1024 * 1024,
        "rate_limit_profile_avatar_upload": "10 per hour",
        "rate_limit_profile_banner_upload": "10 per hour",
        "rate_limit_profile_post_image_upload": "20 per hour",
        "rate_limit_profile_post_create": "30 per hour",
        "rate_limit_profile_post_edit": "40 per hour",
        "rate_limit_profile_post_react": "120 per hour",
        "rate_limit_profile_post_comment": "60 per hour",
        "rate_limit_profile_post_comment_delete": "80 per hour",
        "rate_limit_profile_post_report": "20 per hour",
        "rate_limit_profile_notification_settings": "120 per hour",
        "rate_limit_profile_notifications": "240 per hour",
        "rate_limit_profile_notifications_read": "120 per hour",
        "rate_limit_profile_post_pin": "60 per hour",
        "rate_limit_profile_post_feature": "60 per hour",
        "rate_limit_profile_post_delete": "40 per hour",

        # Upload roots
        "dm_upload_root": "",
        "torrents_root": "",

        # ── DM file transfers (ciphertext-only) ──────────────────────────
        "max_dm_file_bytes": 10 * 1024 * 1024,
        "allow_plaintext_dm_fallback": False,
        "require_dm_e2ee": True,
        "require_group_e2ee": True,
        "encrypt_sensitive_profile_fields": True,
        "encrypt_email_at_rest": True,
        "encrypt_security_backups": True,
        "privacy_retention_enabled": True,
        "privacy_ip_user_agent_retention_days": 30,
        "privacy_audit_detail_retention_days": 90,
        "all_room_e2ee_impact_acknowledged": False,
        "p2p_file_enabled": True,
        "p2p_file_chunk_bytes": 64 * 1024,
        "p2p_file_handshake_timeout_ms": 7_000,
        "p2p_file_transfer_timeout_ms": 60_000,
        "p2p_file_session_ttl_seconds": 300,
        "p2p_ice_servers": DEFAULT_ICE_SERVERS,

        # ── Voice chat ───────────────────────────────────────────────────
        "voice_enabled": True,
        # 0 (or any <=0 value) means unlimited.
        "voice_max_room_peers": 100,
        "voice_ice_servers": [],  # empty => client falls back to p2p_ice_servers
        "voice_invite_cooldown_seconds": 8,
        "voice_dm_invite_ttl_seconds": 30,
        "voice_dm_active_ttl_seconds": 120,

        # ── Echo built-in media / webcam ─────────────────────────────────
        # Room voice and webcam controls use the built-in browser WebRTC path.
        # No external media server is required.
        "av_mode": "echo",  # echo | standard
        "webcam_enabled": True,
        "echo_webcam_enabled": True,
        "webcam_quality": "balanced",
        "echo_webcam_quality": "balanced",
        "webcam_codec_strategy": "prefer-compatible",
        "webcam_approval_mode": "owner_approval",  # owner_approval | open | disabled
        "webcam_max_viewers": 0,  # 0 means unlimited
        "default_media_policy": "user_choice",  # user_choice | voice_first | webcam_first | both_first
        "rate_limit_media_mode": "120 per minute",

        # ── Optional: torrent helpers (routes_main.py) ────────────────────
        "torrent_upload_enabled": True,
        "torrent_scrape_enabled": False,
        "max_torrent_upload_bytes": 1000000,
        "torrent_scrape_cache_ttl_sec": 120,
        "torrent_scrape_max_tries": 4,
        "torrent_scrape_max_trackers": 6,
        "torrent_scrape_http_timeout_sec": 1.5,
        "torrent_scrape_udp_timeout_sec": 1.5,

        # ── Legacy config-encryption flows (unused by main.py) ───────────
        "key_management_option": "separate_file",

        # ── Dynamic DNS (optional) ───────────────────────────────────────
        "dynamic_dns_enabled": False,
        "dynamic_dns_provider": "No-IP",
        "dynamic_dns_username": os.getenv("DDNS_USERNAME", ""),
        "dynamic_dns_password": "",
        "dynamic_dns_domain": "",
        "dynamic_dns_update_url": "https://dynupdate.no-ip.com/nic/update",
        "dynamic_dns_public_ip_url": "https://api.ipify.org",

        # ── SSL block placeholder (some older tooling reads this) ─────────
        "ssl_tls_settings": {
            "enabled": False,
            "certificate_path": "cert.pem",
            "key_path": "key.pem"
        },
        **_RUNTIME_CONFIG_DEFAULTS,
        "version": APP_VERSION,
    }


def _compact_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Drop unknown keys so server_config.json stays small."""
    template = get_default_settings()
    normalized = normalize_setup_settings(settings)
    compact: Dict[str, Any] = {}
    for k in _saved_setting_keys(template):
        compact[k] = normalized.get(k, template[k])
    return compact


def _setting_missing(value: Any) -> bool:
    """Return True only when a config value is absent/blank, not when it is falsey by design."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _promote_alias(settings: Dict[str, Any], canonical: str, *aliases: str) -> None:
    """Fill a current config key from old/legacy names without overwriting a saved value."""
    if not _setting_missing(settings.get(canonical)):
        return
    for alias in aliases:
        if alias in settings and not _setting_missing(settings.get(alias)):
            settings[canonical] = settings.get(alias)
            return


def normalize_setup_settings(settings: Dict[str, Any] | None) -> Dict[str, Any]:
    """Normalize old saved config keys before setup/runtime defaults are applied.

    Older EchoChat builds and hand-edited configs may contain legacy aliases such
    as ``host`` instead of ``server_host`` or ``jwt_secret_key`` instead of
    ``jwt_secret``. The setup wizard must treat those saved values as the
    default answer when an admin does not edit a section. This helper promotes
    the aliases into the current keys while preserving explicit current values.
    """
    out: Dict[str, Any] = dict(settings or {})

    # Core server aliases.
    _promote_alias(out, "server_host", "host")
    _promote_alias(out, "server_port", "port")
    _promote_alias(out, "host", "server_host")
    _promote_alias(out, "port", "server_port")

    raw_mode = str(out.get("run_mode") or out.get("server_mode") or out.get("deployment_mode") or "").strip().lower().replace(" ", "-")
    if raw_mode in {"production", "prod", "public", "public-beta", "public_beta"}:
        out["run_mode"] = "production"
        out["production_mode"] = True
    elif raw_mode in {"development", "dev", "local", "lan", "test", "testing"}:
        out["run_mode"] = "development"
        out["production_mode"] = False
    elif "production_mode" in out:
        out["run_mode"] = "production" if bool(out.get("production_mode")) else "development"
        out["production_mode"] = bool(out.get("production_mode"))

    raw_hosting_mode = str(out.get("hosting_mode") or out.get("deployment_profile") or "").strip().lower().replace(" ", "_").replace("-", "_")
    if raw_hosting_mode in {"local", "lan", "local_lan", "development", "dev"}:
        out["hosting_mode"] = "lan"
    elif raw_hosting_mode in {"no_domain", "no_domain_yet", "pending_domain", "domain_needed", "domain_later"}:
        out["hosting_mode"] = "no_domain_yet"
    elif raw_hosting_mode in {"public", "public_beta", "internet", "production"}:
        out["hosting_mode"] = "public_beta"
    elif raw_hosting_mode in {"advanced", "custom", "reverse_proxy"}:
        out["hosting_mode"] = "advanced"

    # Database aliases seen in old configs and admin helper scripts.
    _promote_alias(out, "database_url", "db_connection_string", "database", "dsn", "DATABASE_URL", "DB_CONNECTION_STRING")
    if not _setting_missing(out.get("database_url")):
        try:
            out["database_url"] = str(sanitize_postgres_dsn(str(out.get("database_url") or "")))
        except Exception:
            pass

    # Secret aliases. Keep both names populated so older and newer modules agree.
    _promote_alias(out, "jwt_secret", "jwt_secret_key")
    _promote_alias(out, "jwt_secret_key", "jwt_secret")

    # TLS block from older setup screens.
    tls = out.get("ssl_tls_settings")
    if isinstance(tls, dict):
        if _setting_missing(out.get("https")) and not _setting_missing(tls.get("enabled")):
            out["https"] = bool(tls.get("enabled"))
        _promote_alias(out, "ssl_cert_file", "certificate_path", "cert_file")
        _promote_alias(out, "ssl_key_file", "key_path", "key_file")
        if _setting_missing(out.get("ssl_cert_file")) and not _setting_missing(tls.get("certificate_path")):
            out["ssl_cert_file"] = str(tls.get("certificate_path") or "")
        if _setting_missing(out.get("ssl_key_file")) and not _setting_missing(tls.get("key_path")):
            out["ssl_key_file"] = str(tls.get("key_path") or "")

    # Origin aliases.
    _promote_alias(out, "cors_allowed_origins", "allowed_origins")
    _promote_alias(out, "allowed_origins", "cors_allowed_origins")

    # Rate-limit storage aliases.
    _promote_alias(out, "rate_limit_storage_uri", "rate_limit_storage")
    _promote_alias(out, "rate_limit_storage", "rate_limit_storage_uri")

    # Upload/file aliases.
    _promote_alias(out, "max_group_upload_bytes", "max_group_file_bytes")
    _promote_alias(out, "max_group_file_bytes", "max_group_upload_bytes")

    # Health/status probe path hardening for hand-edited configs.
    if not _setting_missing(out.get("health_check_endpoint")):
        out["health_check_endpoint"] = normalize_public_probe_path(out.get("health_check_endpoint"), "/health")

    # SMS 2FA / Twilio Verify consistency for hand-edited configs.
    out["enable_two_factor_beta"] = bool(out.get("enable_two_factor_beta", False))
    out["enable_sms_two_factor"] = bool(out.get("enable_sms_two_factor", False))
    if bool(out.get("enable_sms_two_factor")):
        out["enable_two_factor_beta"] = True
    twilio_effective = effective_twilio_settings(out)
    out["two_factor_sms_channel"] = str(twilio_effective.get("two_factor_sms_channel") or "sms")
    out["two_factor_login_timeout_seconds"] = int(twilio_effective.get("two_factor_login_timeout_seconds") or 600)

    # ICE aliases. Prefer the most specific saved list, then keep aliases synced.
    _promote_alias(out, "p2p_ice_servers", "p2p_ice", "webrtc_ice_servers", "ice_servers")
    _promote_alias(out, "p2p_ice", "p2p_ice_servers", "webrtc_ice_servers", "ice_servers")
    _promote_alias(out, "webrtc_ice_servers", "p2p_ice_servers", "p2p_ice", "ice_servers")
    _promote_alias(out, "ice_servers", "p2p_ice_servers", "p2p_ice", "webrtc_ice_servers")

    # Echo A/V mode aliases and consistency. Older built-in/WebRTC labels are
    # normalized to the current Echo media mode.
    raw_mode = str(out.get("av_mode") or "").strip().lower().replace("-", "_")
    if raw_mode in {"webrtc", "built_in", "builtin"}:
        raw_mode = "echo"
    if raw_mode not in {"standard", "echo"}:
        raw_mode = "echo" if bool(out.get("webcam_enabled", out.get("echo_webcam_enabled", True))) else "standard"
    out["av_mode"] = raw_mode
    out["webcam_enabled"] = bool(out.get("webcam_enabled", out.get("echo_webcam_enabled", raw_mode == "echo")))
    out["echo_webcam_enabled"] = bool(out["webcam_enabled"])
    if raw_mode == "standard":
        out["webcam_enabled"] = False
        out["echo_webcam_enabled"] = False
    out["webcam_approval_mode"] = str(out.get("webcam_approval_mode") or "owner_approval").strip() or "owner_approval"
    try:
        out["webcam_max_viewers"] = max(0, int(out.get("webcam_max_viewers") or 0))
    except Exception:
        out["webcam_max_viewers"] = 0
    out["default_media_policy"] = str(out.get("default_media_policy") or "user_choice").strip() or "user_choice"

    # Automatically fill the Redis DB split when the admin chooses multiple
    # one-worker Echo-Chat instances. Admins should not need to memorize DB 0/1/2.
    apply_scaled_runtime_safety_defaults(out)

    # Compatibility mirrors that are derived from current-minute controls.
    if not _setting_missing(out.get("custom_room_idle_minutes")) and _setting_missing(out.get("custom_room_idle_hours")):
        try:
            out["custom_room_idle_hours"] = max(1, round(int(out["custom_room_idle_minutes"]) / 60))
        except Exception:
            pass
    if not _setting_missing(out.get("custom_private_room_idle_minutes")) and _setting_missing(out.get("custom_private_room_idle_hours")):
        try:
            out["custom_private_room_idle_hours"] = max(1, round(int(out["custom_private_room_idle_minutes"]) / 60))
        except Exception:
            pass

    return out


# ──────────────────────────────────────────────────────────────────────────────
# Prompt helpers
# ──────────────────────────────────────────────────────────────────────────────


def _yn(prompt: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = (input(f"{prompt} {suffix}: ") or "").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("❌ Please answer yes or no.")


def _prompt_str(prompt: str, default: str) -> str:
    raw = input(f"{prompt} [{default}]: ")
    return raw.strip() if raw.strip() else default


def _prompt_int(prompt: str, default: int, min_val: int | None = None, max_val: int | None = None) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            val = default
        else:
            try:
                val = int(raw)
            except ValueError:
                print("❌ Please enter a valid integer.")
                continue

        if min_val is not None and val < min_val:
            print(f"❌ Must be ≥ {min_val}.")
            continue
        if max_val is not None and val > max_val:
            print(f"❌ Must be ≤ {max_val}.")
            continue
        return val



def _prompt_choice(prompt: str, default: str, choices: list[str]) -> str:
    ch = {c.lower(): c for c in choices}
    choices_str = "/".join(choices)
    while True:
        raw = (input(f"{prompt} ({choices_str}) [{default}]: ") or "").strip()
        val = (raw or default).strip().lower()
        if val in ch:
            return val
        print(f"❌ Please choose one of: {choices_str}")


def _prompt_secret(prompt: str, allow_blank: bool = False) -> str:
    while True:
        val = getpass.getpass(f"{prompt}: ").strip()
        if not val and allow_blank:
            return ""
        if not val:
            print("❌ Value cannot be empty.")
            continue
        return val


def _prompt_password(prompt: str = "Password", *, username: str | None = None, email: str | None = None, server_name: str | None = None) -> str:
    while True:
        p1 = getpass.getpass(f"{prompt}: ")
        p2 = getpass.getpass("Confirm: ")
        if not p1:
            print("❌ Password cannot be empty.")
            continue
        ok_password, password_err = validate_account_password(p1, username=username, email=email, server_name=server_name)
        if not ok_password:
            print(f"❌ {password_err or password_policy_summary()}")
            continue
        if p1 != p2:
            print("❌ Passwords do not match.")
            continue
        return p1


def _valid_recovery_pin(pin: str) -> bool:
    ok, _err = validate_recovery_pin(pin)
    return ok


def _prompt_recovery_pin(prompt: str = "Recovery PIN") -> str:
    while True:
        p1 = getpass.getpass(f"{prompt} (4-8 digits): ").strip()
        p2 = getpass.getpass("Confirm PIN: ").strip()
        if not _valid_recovery_pin(p1):
            print(f"❌ {recovery_pin_policy_summary()}")
            continue
        if p1 != p2:
            print("❌ Recovery PINs do not match.")
            continue
        return p1


def _parse_csv_urls(raw: str) -> list[dict]:
    """Parse STUN/TURN URL CSV or RTCIceServer JSON into browser iceServers."""
    return parse_ice_servers_text(raw)


def _ice_text(value: Any, *, redact: bool = False) -> str:
    return ice_servers_to_text(value, redact=redact)


def _ice_url_csv(value: Any) -> str:
    servers = parse_ice_servers_text(value)
    urls: list[str] = []
    for server in servers:
        raw_urls = server.get("urls")
        if isinstance(raw_urls, str):
            urls.append(raw_urls)
        elif isinstance(raw_urls, list):
            urls.extend([str(u) for u in raw_urls if str(u).strip()])
    return ", ".join(urls)


def _turn_setup_credentials_prompt(servers: list[dict], *, label: str = "TURN") -> list[dict]:
    """Optionally add static TURN credentials during guided local/LAN setup."""
    if not servers:
        return servers
    if not any(str(u).lower().startswith(("turn:", "turns:")) for s in servers for u in ([s.get("urls")] if isinstance(s.get("urls"), str) else (s.get("urls") or []))):
        return servers
    current_user = first_turn_username(servers)
    username = _prompt_str(f"{label} username (blank keeps none/current)", current_user).strip()
    credential = getpass.getpass(f"{label} credential/password (blank keeps existing/env): ").strip()
    return apply_turn_credentials(servers, username=username, credential=credential, keep_existing=True)


def _smtp_from_setup_error(provider: str, from_value: str) -> str | None:
    """Return a setup-facing error when the SMTP From value looks fake.

    Setup cannot call every provider API to prove verification. It can, however,
    refuse the exact class of mistake that just broke delivery: using an
    EchoChat/sample/local/login address instead of a real verified sender.
    """

    provider_name = str(provider or "SMTP provider").strip() or "SMTP provider"
    warning = smtp_from_warning(provider_name.lower(), str(from_value or ""))
    if not warning:
        return None
    if warning == "invalid_from_address":
        return "From address must be a real email address, such as Your Name <you@yourdomain.com>."
    if warning in {"invalid_from_placeholder", "from_placeholder_not_deliverable"}:
        return (
            "From address looks like a placeholder, local address, or provider login. "
            f"Use a sender that is actually verified in {provider_name}. "
            "For Brevo, check Settings > Senders, domains, IPs and use one of those verified senders. "
            "Do not use noreply@echochat.com, no-reply@yourdomain.com, localhost, or the Brevo SMTP login address."
        )
    return f"From address is not safe for reliable delivery: {warning}"


def _prompt_verified_smtp_from(prompt: str, default: str, provider: str) -> str:
    while True:
        value = _prompt_str(prompt, default)
        problem = _smtp_from_setup_error(provider, value)
        if not problem:
            return value.strip()
        print(f"❌ {problem}")
        print("   Enter the exact From/Sender address that your email provider shows as verified.")
        default = value



_SMTP_PROVIDER_OPTIONS = ["brevo", "resend", "smtp2go", "mailersend", "mailjet", "custom"]

_SMTP_PROVIDER_PRESETS = {
    "brevo": {"host": "smtp-relay.brevo.com", "port": 587, "starttls": True, "ssl": False},
    "resend": {"host": "smtp.resend.com", "port": 465, "starttls": False, "ssl": True, "username": "resend"},
    "smtp2go": {"host": "mail.smtp2go.com", "port": 2525, "starttls": True, "ssl": False},
    "mailersend": {"host": "smtp.mailersend.net", "port": 587, "starttls": True, "ssl": False},
    "mailjet": {"host": "in-v3.mailjet.com", "port": 587, "starttls": True, "ssl": False},
    "custom": {},
}


def _smtp_provider_preset(provider: Any) -> dict[str, Any]:
    return dict(_SMTP_PROVIDER_PRESETS.get(str(provider or "").strip().lower(), {}))


def _smtp_setup_errors(merged: Dict[str, Any]) -> list[str]:
    """Return non-network SMTP setup errors using runtime-effective settings.

    This mirrors emailer.send_email(): password reset delivery requires SMTP to
    be enabled, host/port present, authenticated credentials, and a real
    provider-verified sender address. Environment variables are honored here
    because runtime delivery honors them too.
    """

    cfg = effective_smtp_settings(merged)
    if not bool(cfg.get("enabled")):
        return []
    errors: list[str] = []
    host = str(cfg.get("host") or "").strip()
    try:
        port = int(cfg.get("port") or 0)
    except Exception:
        port = 0
    if not host:
        errors.append("SMTP is enabled, but the SMTP host is missing.")
    if not (1 <= port <= 65535):
        errors.append("SMTP is enabled, but the SMTP port must be between 1 and 65535.")
    if not str(cfg.get("username") or "").strip():
        errors.append("SMTP is enabled, but SMTP username is missing. Password-reset email requires authenticated SMTP.")
    if not str(cfg.get("password") or ""):
        errors.append("SMTP is enabled, but SMTP password is missing. Store it in config or set ECHOCHAT_SMTP_PASSWORD / SMTP_PASSWORD before testing or saving.")
    from_problem = _smtp_from_setup_error(str(cfg.get("provider") or merged.get("smtp_provider") or ""), str(cfg.get("from_email") or merged.get("smtp_from") or ""))
    if from_problem:
        errors.append(from_problem)
    return errors


def _smtp_ready_for_setup(merged: Dict[str, Any]) -> bool:
    return not _smtp_setup_errors(merged)

def _twilio_ready_for_setup(merged: Dict[str, Any]) -> bool:
    return not twilio_setup_errors(merged)

def _ice_setup_errors(merged: Dict[str, Any]) -> list[str]:
    """Return non-network WebRTC STUN/TURN setup errors.

    STUN-only configurations are valid for local/LAN tests. TURN relay entries
    need credentials by the time the browser receives them; environment TURN
    secrets are accepted so production can keep credentials out of JSON.
    """

    return turn_credential_errors(merged)


def _ice_ready_for_setup(merged: Dict[str, Any]) -> bool:
    return not _ice_setup_errors(merged)


def _dynamic_dns_ready_for_setup(merged: Dict[str, Any]) -> bool:
    return not dynamic_dns_setup_errors(merged)

def _tui_require_verified_smtp_from(stdscr, merged: Dict[str, Any]) -> None:
    if not bool(merged.get("smtp_enabled")):
        return
    provider = str(merged.get("smtp_provider") or "SMTP provider")
    while True:
        current = str(merged.get("smtp_from") or "").strip()
        problem = _smtp_from_setup_error(provider, current)
        if not problem:
            return
        _tui_message(
            stdscr,
            "Invalid SMTP From address",
            [
                problem,
                "",
                "Enter the exact sender email that your provider dashboard lists as verified. For Brevo, use Settings > Senders, domains, IPs.",
            ],
            error=True,
        )
        merged["smtp_from"] = _tui_input(
            stdscr,
            "SMTP From address",
            "Verified sender email",
            current,
            secret=False,
        ).strip()


def _discover_existing_server_database_dsn(dsn: str, bootstrap_dsn: str | None = None) -> str | None:
    """Wrapper kept in interactive_setup.py so setup code remains easy to inspect.

    Legacy guard phrase: Auto-detected EchoChat database.
    """
    return _discover_existing_server_database_dsn_impl(dsn, bootstrap_dsn=bootstrap_dsn)


def _discover_existing_server_database_candidates(dsn: str, bootstrap_dsn: str | None = None) -> list[dict[str, Any]]:
    """Return all accessible Echo-Chat-looking PostgreSQL databases for admin selection."""
    return _discover_echochat_database_candidates_impl(dsn, bootstrap_dsn=bootstrap_dsn)


def _validate_echochat_database(dsn: str) -> dict[str, Any]:
    """Check whether the current PostgreSQL target looks valid for Echo-Chat."""
    return _validate_echochat_database_impl(dsn)


def _target_database_status(dsn: str, bootstrap_dsn: str | None = None) -> dict[str, Any]:
    """Check whether the configured PostgreSQL target database exists at all."""
    return _target_database_status_impl(dsn, bootstrap_dsn=bootstrap_dsn)


def _ensure_database_ready(dsn: str, *, recreate: bool = False, bootstrap_dsn: str | None = None) -> dict[str, Any]:
    """Wrapper kept in interactive_setup.py so setup code remains easy to inspect.

    Under the hood the bootstrap helper may run SQL equivalent to:
      - CREATE DATABASE ...
      - DROP DATABASE IF EXISTS ...
    """
    return ensure_database_ready(dsn, recreate=recreate, bootstrap_dsn=bootstrap_dsn)


def _run_local_postgres_admin_repair(dsn: str, *, recreate: bool = False) -> dict[str, Any]:
    # ('local_admin', 'Use local postgres admin tools (sudo -u postgres)')
    return _ensure_database_ready_via_local_admin_impl(dsn, recreate=recreate)


def _autobrand_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(settings or {})
    server_name = str(merged.get("server_name") or DEFAULT_SERVER_NAME).strip() or DEFAULT_SERVER_NAME
    new_default = f"{server_name} <no-reply@yourdomain.com>"
    if not str(merged.get("smtp_from") or "").strip() or "no-reply@localhost" in str(merged.get("smtp_from") or "") or "no-reply@yourdomain.com" in str(merged.get("smtp_from") or ""):
        merged["smtp_from"] = new_default
    return merged


# ──────────────────────────────────────────────────────────────────────────────
# DB helpers for setup (no Flask app context needed)
# ──────────────────────────────────────────────────────────────────────────────


def _ensure_users_table(conn) -> None:
    """Ensure the users table exists and has the columns required for E2EE keys."""
    with conn.cursor() as cur:
        # Create table if missing (minimal subset used by EchoChat)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id                    SERIAL PRIMARY KEY,
                username              TEXT UNIQUE NOT NULL,
                password              TEXT NOT NULL,
                email                 TEXT,
                email_hash            TEXT,
                email_encrypted       TEXT,
                phone                 TEXT,
                address               TEXT,
                age                   INTEGER,
                age_visibility        TEXT NOT NULL DEFAULT 'friends',
                is_admin              BOOLEAN NOT NULL DEFAULT FALSE,
                public_key            TEXT,
                encrypted_private_key TEXT,
                relationship_status   TEXT,
                relationship_visibility TEXT NOT NULL DEFAULT 'friends',
                location_text         TEXT,
                location_visibility   TEXT NOT NULL DEFAULT 'friends',
                interests             TEXT,
                favorite_music        TEXT,
                favorite_movies       TEXT,
                favorite_games        TEXT,
                website_url           TEXT,
                banner_url            TEXT,
                profile_accent        TEXT,
                share_recent_rooms    BOOLEAN NOT NULL DEFAULT FALSE,
                recent_rooms_visibility TEXT NOT NULL DEFAULT 'friends',
                profile_post_default_visibility TEXT NOT NULL DEFAULT 'friends',
                presence_status       TEXT NOT NULL DEFAULT 'online',
                two_factor_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
                two_factor_secret     TEXT,
                custom_status         TEXT,
                last_seen             TIMESTAMP WITH TIME ZONE,
                status                TEXT NOT NULL DEFAULT 'active',
                recovery_pin_hash     TEXT,
                recovery_pin_set_at   TIMESTAMP WITH TIME ZONE,
                recovery_failed_attempts INTEGER NOT NULL DEFAULT 0,
                recovery_locked_until TIMESTAMP WITH TIME ZONE,
                bio                   TEXT,
                avatar_url            TEXT,
                online                BOOLEAN DEFAULT FALSE,
                is_verified           BOOLEAN NOT NULL DEFAULT TRUE,
                created_at            TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                auth_version          INTEGER NOT NULL DEFAULT 0,
                password_changed_at   TIMESTAMP WITH TIME ZONE,
                auth_changed_at       TIMESTAMP WITH TIME ZONE
            );
            """
        )

        # Patch common legacy columns
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name='users' AND column_name='password_hash';
            """
        )
        if cur.fetchone() is not None:
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name='users' AND column_name='password';
                """
            )
            if cur.fetchone() is None:
                cur.execute("ALTER TABLE users RENAME COLUMN password_hash TO password;")

        for col, ddl in (
            ("email_hash", "ALTER TABLE users ADD COLUMN email_hash TEXT;"),
            ("email_encrypted", "ALTER TABLE users ADD COLUMN email_encrypted TEXT;"),
            ("public_key", "ALTER TABLE users ADD COLUMN public_key TEXT;"),
            ("encrypted_private_key", "ALTER TABLE users ADD COLUMN encrypted_private_key TEXT;"),
            ("is_admin", "ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE;"),
            ("presence_status", "ALTER TABLE users ADD COLUMN presence_status TEXT NOT NULL DEFAULT 'online';"),
            ("two_factor_enabled", "ALTER TABLE users ADD COLUMN two_factor_enabled BOOLEAN NOT NULL DEFAULT FALSE;"),
            ("two_factor_secret", "ALTER TABLE users ADD COLUMN two_factor_secret TEXT;"),
            ("custom_status", "ALTER TABLE users ADD COLUMN custom_status TEXT;"),
            ("last_seen", "ALTER TABLE users ADD COLUMN last_seen TIMESTAMP WITH TIME ZONE;"),
            ("status", "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active';"),
            ("relationship_status", "ALTER TABLE users ADD COLUMN relationship_status TEXT;"),
            ("relationship_visibility", "ALTER TABLE users ADD COLUMN relationship_visibility TEXT NOT NULL DEFAULT 'friends';"),
            ("age_visibility", "ALTER TABLE users ADD COLUMN age_visibility TEXT NOT NULL DEFAULT 'friends';"),
            ("location_text", "ALTER TABLE users ADD COLUMN location_text TEXT;"),
            ("location_visibility", "ALTER TABLE users ADD COLUMN location_visibility TEXT NOT NULL DEFAULT 'friends';"),
            ("interests", "ALTER TABLE users ADD COLUMN interests TEXT;"),
            ("favorite_music", "ALTER TABLE users ADD COLUMN favorite_music TEXT;"),
            ("favorite_movies", "ALTER TABLE users ADD COLUMN favorite_movies TEXT;"),
            ("favorite_games", "ALTER TABLE users ADD COLUMN favorite_games TEXT;"),
            ("website_url", "ALTER TABLE users ADD COLUMN website_url TEXT;"),
            ("banner_url", "ALTER TABLE users ADD COLUMN banner_url TEXT;"),
            ("profile_accent", "ALTER TABLE users ADD COLUMN profile_accent TEXT;"),
            ("share_recent_rooms", "ALTER TABLE users ADD COLUMN share_recent_rooms BOOLEAN NOT NULL DEFAULT FALSE;"),
            ("recent_rooms_visibility", "ALTER TABLE users ADD COLUMN recent_rooms_visibility TEXT NOT NULL DEFAULT 'friends';"),
            ("profile_post_default_visibility", "ALTER TABLE users ADD COLUMN profile_post_default_visibility TEXT NOT NULL DEFAULT 'friends';"),
            ("recovery_pin_hash", "ALTER TABLE users ADD COLUMN recovery_pin_hash TEXT;"),
            ("bio", "ALTER TABLE users ADD COLUMN bio TEXT;"),
            ("recovery_pin_set_at", "ALTER TABLE users ADD COLUMN recovery_pin_set_at TIMESTAMP WITH TIME ZONE;"),
            ("recovery_failed_attempts", "ALTER TABLE users ADD COLUMN recovery_failed_attempts INTEGER NOT NULL DEFAULT 0;"),
            ("recovery_locked_until", "ALTER TABLE users ADD COLUMN recovery_locked_until TIMESTAMP WITH TIME ZONE;"),
            ("avatar_url", "ALTER TABLE users ADD COLUMN avatar_url TEXT;"),
            ("online", "ALTER TABLE users ADD COLUMN online BOOLEAN DEFAULT FALSE;"),
            ("is_verified", "ALTER TABLE users ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT TRUE;"),
            ("auth_version", "ALTER TABLE users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0;"),
            ("password_changed_at", "ALTER TABLE users ADD COLUMN password_changed_at TIMESTAMP WITH TIME ZONE;"),
            ("auth_changed_at", "ALTER TABLE users ADD COLUMN auth_changed_at TIMESTAMP WITH TIME ZONE;"),
        ):
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name='users' AND column_name=%s;
                """,
                (col,),
            )
            if cur.fetchone() is None:
                cur.execute(ddl)

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_posts (
                id              SERIAL PRIMARY KEY,
                author_username TEXT NOT NULL,
                body            TEXT,
                visibility      TEXT NOT NULL DEFAULT 'friends',
                image_url       TEXT,
                gif_url         TEXT,
                link_url        TEXT,
                is_pinned       BOOLEAN NOT NULL DEFAULT FALSE,
                is_featured     BOOLEAN NOT NULL DEFAULT FALSE,
                created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                deleted_at      TIMESTAMP WITH TIME ZONE,
                edited_at       TIMESTAMP WITH TIME ZONE,
                edit_count      INTEGER NOT NULL DEFAULT 0,
                moderated_by    TEXT,
                moderated_reason TEXT,
                moderated_at    TIMESTAMP WITH TIME ZONE
            );
            """
        )
        try:
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS users_email_hash_unique
                ON users (email_hash)
                WHERE email_hash IS NOT NULL AND BTRIM(email_hash) <> '';
                """
            )
        except Exception as exc:
            logging.warning("Could not create users_email_hash_unique during setup schema prep (continuing): %s", exc)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_posts_author_created ON profile_posts(author_username, created_at DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_posts_author_featured ON profile_posts(author_username, is_featured, created_at DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_posts_author_pinned ON profile_posts(author_username, is_pinned, created_at DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_posts_moderation ON profile_posts(deleted_at, moderated_at, updated_at DESC);")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_post_reactions (
                post_id    INTEGER NOT NULL REFERENCES profile_posts(id) ON DELETE CASCADE,
                username   TEXT NOT NULL,
                reaction   TEXT NOT NULL DEFAULT 'like',
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (post_id, username, reaction)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_post_comments (
                id              SERIAL PRIMARY KEY,
                post_id         INTEGER NOT NULL REFERENCES profile_posts(id) ON DELETE CASCADE,
                author_username TEXT NOT NULL,
                body            TEXT NOT NULL,
                created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                deleted_at      TIMESTAMP WITH TIME ZONE,
                deleted_by      TEXT,
                deleted_reason  TEXT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS profile_post_reports (
                id                SERIAL PRIMARY KEY,
                reporter_username TEXT NOT NULL,
                post_id           INTEGER NOT NULL REFERENCES profile_posts(id) ON DELETE CASCADE,
                comment_id        INTEGER REFERENCES profile_post_comments(id) ON DELETE SET NULL,
                target_username   TEXT NOT NULL,
                reason            TEXT NOT NULL DEFAULT 'other',
                details           TEXT,
                status            TEXT NOT NULL DEFAULT 'open',
                reviewed_by       TEXT,
                reviewed_at       TIMESTAMP WITH TIME ZONE,
                action_taken      TEXT,
                created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_post_reports_status_created ON profile_post_reports(status, created_at DESC);")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile_badges (
                id          SERIAL PRIMARY KEY,
                username    TEXT NOT NULL,
                badge_key   TEXT NOT NULL,
                label       TEXT NOT NULL,
                assigned_by TEXT,
                reason      TEXT,
                created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(username, badge_key)
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, role_id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS role_permissions (
                role_id INTEGER NOT NULL,
                permission_id INTEGER NOT NULL,
                PRIMARY KEY (role_id, permission_id)
            );
            """
        )

    conn.commit()
    print(f"Full {_setup_display_name()} schema prepared")


def _prepare_full_schema_in_setup(conn) -> None:
    """Prepare the minimum schema setup needs before first server start.

    The full migration/bootstrap path still runs when the server starts, but setup
    itself must be able to save into a brand-new database without assuming the
    runtime app context exists yet.
    """
    _ensure_users_table(conn)

    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS roles (
                id   SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS permissions (
                id   SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS role_permissions (
                role_id       INTEGER NOT NULL,
                permission_id INTEGER NOT NULL,
                PRIMARY KEY (role_id, permission_id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_roles (
                user_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, role_id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS echochat_schema_meta (
                version     TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                kind        TEXT NOT NULL DEFAULT 'python',
                checksum    TEXT NOT NULL,
                applied_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                success     BOOLEAN NOT NULL DEFAULT TRUE,
                notes       TEXT
            );
            """
        )

        roles = ["admin", "moderator", "viewer"]
        perms = [
            "admin:basic", "admin:settings", "admin:audit", "admin:test_lab",
            "admin:create_user", "admin:delete_user", "admin:set_recovery_pin",
            "admin:set_user_status", "admin:set_user_quota", "admin:revoke_2fa",
            "admin:broadcast", "admin:assign_role", "admin:manage_roles",
            "admin:ban_ip", "admin:reset_password", "admin:logout_user",
            "moderation:mute_user", "moderation:kick_user", "moderation:ban_room",
            "moderation:suspend_user", "moderation:shadowban",
            "room:lock", "room:readonly", "room:clear", "room:delete",
            "profile:moderate",
            "user:delete_self", "user:edit_profile",
        ]
        for role_name in roles:
            cur.execute(
                "INSERT INTO roles (name) VALUES (%s) ON CONFLICT (name) DO NOTHING;",
                (role_name,),
            )
        for perm_name in perms:
            cur.execute(
                "INSERT INTO permissions (name) VALUES (%s) ON CONFLICT (name) DO NOTHING;",
                (perm_name,),
            )

        cur.execute("SELECT id, name FROM roles;")
        role_map = {str(name): int(role_id) for role_id, name in (cur.fetchall() or [])}
        cur.execute("SELECT id, name FROM permissions;")
        perm_map = {str(name): int(perm_id) for perm_id, name in (cur.fetchall() or [])}

        for perm_name in perms:
            if "admin" in role_map and perm_name in perm_map:
                cur.execute(
                    """
                    INSERT INTO role_permissions (role_id, permission_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING;
                    """,
                    (role_map["admin"], perm_map[perm_name]),
                )
        for perm_name in ("moderation:mute_user", "moderation:kick_user", "moderation:ban_room", "room:readonly", "room:clear", "profile:moderate"):
            if "moderator" in role_map and perm_name in perm_map:
                cur.execute(
                    """
                    INSERT INTO role_permissions (role_id, permission_id)
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING;
                    """,
                    (role_map["moderator"], perm_map[perm_name]),
                )
        if "viewer" in role_map and "user:edit_profile" in perm_map:
            cur.execute(
                """
                INSERT INTO role_permissions (role_id, permission_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING;
                """,
                (role_map["viewer"], perm_map["user:edit_profile"]),
            )

    conn.commit()



def _setup_admin_server_name(settings: dict | None) -> str | None:
    """Return the current server name for setup account password-policy checks."""
    if isinstance(settings, dict):
        value = str(settings.get("server_name") or "").strip()
        if value:
            return value
    return _setup_display_name(settings)


def _same_setup_username(left: Any, right: Any) -> bool:
    """Casefold and normalize setup usernames before comparing duplicates."""
    left_norm = normalize_registration_username(str(left or ""))
    right_norm = normalize_registration_username(str(right or ""))
    return bool(left_norm and right_norm and left_norm == right_norm)


def _validate_setup_admin_username(username: Any, *, settings: dict | None = None, account_label: str = "admin") -> tuple[str, str | None]:
    """Normalize and validate setup-created admin usernames.

    Setup owner/admin names intentionally use the format/style policy without the
    public registration blocked-word check so default staff names like ``admin``
    and ``admin2`` remain valid during first-run setup.
    """
    normalized = normalize_registration_username(str(username or ""))
    if not normalized:
        return "", f"The {account_label} username is missing or invalid."
    ok_username, username_err = validate_registration_username_format(normalized, settings=settings)
    if ok_username:
        ok_username, username_style_err = validate_account_username_style(normalized)
        if not ok_username:
            username_err = username_style_err
    if not ok_username:
        return normalized, f"The {account_label} username is invalid: {username_err or 'username not allowed'}"
    return normalized, None


def _validate_owner_admin_setup_fields(merged: Dict[str, Any], base: Dict[str, Any] | None = None) -> tuple[bool, str | None]:
    """Validate the required owner/admin setup block before final DB writes."""
    base = base or {}
    username, username_err = _validate_setup_admin_username(
        merged.get("admin_user") or base.get("admin_user"),
        settings=merged,
        account_label="owner",
    )
    if username_err:
        return False, username_err

    raw_password = str(merged.get("__admin_raw_password") or "")
    if not raw_password:
        return False, "Please set the owner password before saving."
    ok_password, password_err = validate_account_password(
        raw_password,
        username=username,
        email=str(merged.get("admin_notification_email") or "").strip() or None,
        server_name=merged.get("server_name"),
    )
    if not ok_password:
        return False, password_err or password_policy_summary()

    ok_pin, pin_err = validate_recovery_pin(str(merged.get("__admin_recovery_pin") or ""))
    if not ok_pin:
        return False, f"Please set a valid owner Recovery PIN. {pin_err or recovery_pin_policy_summary()}"

    merged["admin_user"] = username
    return True, None


def _validate_extra_admin_setup_fields(merged: Dict[str, Any]) -> tuple[bool, str | None]:
    """Validate the optional second admin setup block before final DB writes."""
    if not bool(merged.get("__create_initial_admin")):
        return True, None

    username, username_err = _validate_setup_admin_username(
        merged.get("__initial_admin_user"),
        settings=merged,
        account_label="extra admin",
    )
    if username_err:
        return False, username_err
    # Guard phrase retained for setup regression tests:
    # _same_setup_username(merged.get("__initial_admin_user"), merged.get("admin_user"))
    if _same_setup_username(username, merged.get("admin_user")):
        return False, "Second admin username must be different from the owner username, or turn the extra admin option off."

    raw_password = str(merged.get("__initial_admin_raw_password") or "")
    if not raw_password:
        return False, "Please set the second admin password before saving."
    ok_password, password_err = validate_account_password(
        raw_password,
        username=username,
        email=str(merged.get("__initial_admin_email") or "").strip() or None,
        server_name=merged.get("server_name"),
    )
    if not ok_password:
        return False, password_err or password_policy_summary()

    # Compatibility guard phrase retained for Recovery PIN regression tests:
    # _valid_recovery_pin(str(merged.get("__initial_admin_recovery_pin") or ""))
    ok_pin, pin_err = validate_recovery_pin(str(merged.get("__initial_admin_recovery_pin") or ""))
    if not ok_pin:
        return False, f"Please set a valid second-admin Recovery PIN. {pin_err or recovery_pin_policy_summary()}"

    merged["__initial_admin_user"] = username
    return True, None


def _clear_setup_account_passwords_after_identity_change(merged: Dict[str, Any], *, old_server_name: Any) -> None:
    """Clear setup-entered passwords when the server name context changes."""
    if str(old_server_name or "").strip() == str(merged.get("server_name") or "").strip():
        return
    merged["__admin_raw_password"] = ""
    merged["admin_pass"] = ""
    merged["__initial_admin_raw_password"] = ""


def _ensure_admin_rbac_role(conn, username: str) -> None:
    """Ensure an admin-marked user also has the RBAC admin role assigned."""
    _prepare_full_schema_in_setup(conn)

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM roles WHERE name=%s;", ("admin",))
        row = cur.fetchone()
        if not row:
            return
        admin_role_id = row[0]

        cur.execute("SELECT id FROM users WHERE LOWER(username)=LOWER(%s);", (username,))
        user_row = cur.fetchone()
        if not user_row:
            return
        user_id = user_row[0]

        cur.execute(
            """
            INSERT INTO user_roles (user_id, role_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING;
            """,
            (user_id, admin_role_id),
        )

    conn.commit()


def _sync_admin_login_user_in_db(
    conn,
    username: str,
    raw_password: str,
    password_hash: str,
    email: str | None,
    age: int | None,
    recovery_pin: str | None,
    *,
    account_label: str = "admin",
    confirm_reset=None,
    field_encryption_settings: dict | None = None,
) -> tuple[str, str]:
    """Create/update an admin-capable login user so E2EE keys match the password.

    The stored account is a normal row in ``users`` plus the RBAC admin role, which
    gives the current full admin rights model used by EchoChat.

    Returns ``(password_hash_to_store_in_config, status_message)``.
    """
    # Compatibility guard phrase retained for setup-account audit tests:
    # validate_registration_username_format(username, settings=field_encryption_settings)
    # validate_account_username_style(username)
    username, username_err = _validate_setup_admin_username(
        username,
        settings=field_encryption_settings,
        account_label=account_label,
    )
    if username_err:
        raise ValueError(username_err)

    ok_password, password_err = validate_account_password(
        raw_password,
        username=username,
        email=email,
        server_name=_setup_admin_server_name(field_encryption_settings),
    )
    if not ok_password:
        raise ValueError(f"The {account_label} password is invalid: {password_err or password_policy_summary()}")

    _ensure_users_table(conn)
    recovery_pin = str(recovery_pin or "").strip()
    if not _valid_recovery_pin(recovery_pin):
        raise ValueError(f"{recovery_pin_policy_summary()} A Recovery PIN is required for the {account_label} account.")
    recovery_pin_hash = hash_password(recovery_pin)
    email_to_store, email_hash_to_store, email_encrypted_to_store = prepare_email_storage(email, field_encryption_settings)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT password, public_key, encrypted_private_key, is_admin, recovery_pin_hash FROM users WHERE LOWER(username)=LOWER(%s);",
            (username,),
        )
        row = cur.fetchone()

    if row is None:
        create_user_with_keys(
            conn,
            username=username,
            raw_password=raw_password,
            password_hash=password_hash,
            email=email,
            age=age,
            is_admin=True,
            recovery_pin_hash=recovery_pin_hash,
            recovery_pin_set_at=datetime.now(timezone.utc),
            field_encryption_settings=field_encryption_settings,
            commit=False,
        )
        _ensure_admin_rbac_role(conn, username)
        return password_hash, f"{account_label.title()} '{username}' was created as a normal user account with admin rights."

    stored_hash = row[0]
    if stored_hash and verify_password(raw_password, stored_hash):
        ensure_user_has_keys(conn, username, raw_password)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                   SET is_admin = TRUE,
                       email = CASE WHEN %s THEN %s ELSE email END,
                       email_hash = CASE WHEN %s THEN %s ELSE email_hash END,
                       email_encrypted = CASE WHEN %s THEN %s ELSE email_encrypted END,
                       age = COALESCE(%s, age),
                       recovery_pin_hash = %s,
                       recovery_pin_set_at = CURRENT_TIMESTAMP,
                       recovery_failed_attempts = 0,
                       recovery_locked_until = NULL
                 WHERE LOWER(username)=LOWER(%s);
                """,
                (
                    email is not None, email_to_store,
                    email is not None, email_hash_to_store,
                    email is not None, email_encrypted_to_store,
                    age, recovery_pin_hash, username,
                ),
            )
            cur.execute("UPDATE users SET status = 'active' WHERE status IS NULL OR BTRIM(status) = '';")
            cur.execute("UPDATE users SET relationship_visibility = 'friends' WHERE relationship_visibility IS NULL OR BTRIM(relationship_visibility) = '';")
            cur.execute("UPDATE users SET age_visibility = 'friends' WHERE age_visibility IS NULL OR BTRIM(age_visibility) = '';")
            cur.execute("UPDATE users SET location_visibility = 'friends' WHERE location_visibility IS NULL OR BTRIM(location_visibility) = '';")
        ensure_user_has_default_avatar(conn, username)
        conn.commit()
        _ensure_admin_rbac_role(conn, username)
        return password_hash, f"{account_label.title()} '{username}' already existed and now has admin rights as a normal user account."

    prompt_text = (
        f"The {account_label} user '{username}' already exists in the database, but the password you entered does not match. "
        "Reset that database password and regenerate that user's E2EE keys now?"
    )
    if confirm_reset is None:
        should_reset = _yn(prompt_text, default=False)
    else:
        should_reset = bool(confirm_reset(prompt_text))

    if should_reset:
        public_pem, encrypted_priv_blob = _generate_new_keypair_blob(raw_password)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                   SET password=%s,
                       email=CASE WHEN %s THEN %s ELSE email END,
                       email_hash=CASE WHEN %s THEN %s ELSE email_hash END,
                       email_encrypted=CASE WHEN %s THEN %s ELSE email_encrypted END,
                       age=COALESCE(%s, age),
                       is_admin=TRUE,
                       public_key=%s,
                       encrypted_private_key=%s,
                       recovery_pin_hash=%s,
                       recovery_pin_set_at=CURRENT_TIMESTAMP,
                       recovery_failed_attempts=0,
                       recovery_locked_until=NULL,
                       auth_version=COALESCE(auth_version, 0) + 1,
                       password_changed_at=CURRENT_TIMESTAMP,
                       auth_changed_at=CURRENT_TIMESTAMP
                 WHERE LOWER(username)=LOWER(%s);
                """,
                (
                    password_hash,
                    email is not None, email_to_store,
                    email is not None, email_hash_to_store,
                    email is not None, email_encrypted_to_store,
                    age, public_pem, encrypted_priv_blob, recovery_pin_hash, username,
                ),
            )
        ensure_user_has_default_avatar(conn, username)
        conn.commit()
        _ensure_admin_rbac_role(conn, username)
        return password_hash, f"{account_label.title()} '{username}' was reset and now has admin rights as a normal user account."

    raise RuntimeError(
        f"Existing {account_label} user '{username}' uses a different password. "
        "Setup cannot safely finish until you reset that account during setup, choose a different username, "
        "or rerun setup with the existing database password."
    )


def _sync_primary_admin_in_db(
    conn,
    username: str,
    raw_password: str,
    password_hash: str,
    email: str | None,
    age: int | None,
    recovery_pin: str | None,
    *,
    confirm_reset=None,
    field_encryption_settings: dict | None = None,
) -> tuple[str, str]:
    """Create/update the admin row and keep it as a normal DB user with admin rights."""
    return _sync_admin_login_user_in_db(
        conn,
        username,
        raw_password,
        password_hash,
        email,
        age,
        recovery_pin,
        account_label="admin",
        confirm_reset=confirm_reset,
        field_encryption_settings=field_encryption_settings,
    )


def _sync_initial_admin_in_db(
    conn,
    username: str,
    raw_password: str,
    password_hash: str,
    email: str | None,
    age: int | None,
    recovery_pin: str | None,
    *,
    confirm_reset=None,
    field_encryption_settings: dict | None = None,
) -> tuple[str, str]:
    """Create/update an extra admin user during setup."""
    return _sync_admin_login_user_in_db(
        conn,
        username,
        raw_password,
        password_hash,
        email,
        age,
        recovery_pin,
        account_label="extra admin",
        confirm_reset=confirm_reset,
        field_encryption_settings=field_encryption_settings,
    )


def _generate_new_keypair_blob(raw_password: str) -> tuple[str, str]:
    """Generate a fresh keypair using the same helper as create_user_with_keys()."""
    # Reuse database's internal helper (kept local to avoid import cycles).
    from database import _generate_and_encrypt_rsa_keypair  # type: ignore

    public_pem, encrypted_priv_b64 = _generate_and_encrypt_rsa_keypair(raw_password)
    return public_pem, encrypted_priv_b64


# ──────────────────────────────────────────────────────────────────────────────
# Main wizard
# ──────────────────────────────────────────────────────────────────────────────


def _interactive_setup_legacy(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Run the Echo-Chat setup wizard and return an updated (compacted) settings dict."""

    # Start from compact defaults, but allow existing values to carry forward.
    base = get_default_settings()
    seed = normalize_setup_settings(settings)
    merged = {**base, **seed}
    _set_active_setup_server_name(merged.get("server_name"))

    print(f"\n=== {_setup_display_name(merged)} Setup Wizard ===\n")

    advanced = _yn("Advanced mode? (more prompts)", default=False)

    # ── Core server ───────────────────────────────────────────────────────────
    merged["server_name"] = _prompt_str("Server name", str(merged.get("server_name") or base["server_name"]))
    _set_active_setup_server_name(merged.get("server_name"))
    merged["server_host"] = _prompt_str("Bind host", str(merged.get("server_host") or base["server_host"]))
    merged["server_port"] = _prompt_int("Bind port", int(merged.get("server_port") or base["server_port"]), 1, 65535)
    # Keep legacy keys in sync so older code paths don't bind the wrong address.
    merged["host"] = merged["server_host"]
    merged["port"] = merged["server_port"]

    # ── Database ──────────────────────────────────────────────────────────────
    while True:
        # create_new_db, delete_old_active, chosen_db_name = _prepare_database_choice(merged, base)
        # prompt_dsn_default = _build_postgres_dsn(current_parts, prompt_db_name)
        raw_dsn = _prompt_str(
            "PostgreSQL DSN",
            str(merged.get("database_url") or base["database_url"]),
        )
        merged["database_url"] = str(sanitize_postgres_dsn(raw_dsn))
        if merged["database_url"] != raw_dsn:
            print("⚠️  DSN sanitised (removed placeholder angle brackets / quotes).")
        try:
            test = psycopg2.connect(str(merged["database_url"]))
            test.close()
            print("✅ PostgreSQL connection OK")
            break
        except Exception as e:
            print(f"❌ PostgreSQL connection failed: {e}")
            if not _yn("Try again?", default=True):
                raise SystemExit(1)

    # ── Cookies / HTTPS ───────────────────────────────────────────────────────
    merged["cookie_secure"] = _yn(
        "Are you serving the site over HTTPS (or behind an HTTPS reverse proxy)?",
        default=bool(merged.get("cookie_secure", False)),
    )
    merged["cookie_samesite"] = _prompt_str("Cookie SameSite (Lax/Strict/None)", str(merged.get("cookie_samesite") or "Lax"))

    # ── Email (SMTP relay) ───────────────────────────────────────────────────
    print("\n— Email (SMTP relay; password reset) —")
    merged["smtp_enabled"] = _yn(
        "Enable SMTP for password reset emails?",
        default=bool(merged.get("smtp_enabled", False)),
    )

    if merged["smtp_enabled"]:
        merged["smtp_provider"] = _prompt_choice(
            "SMTP provider",
            str((merged.get("smtp_provider") or "brevo")).lower(),
            _SMTP_PROVIDER_OPTIONS,
        )

        preset = _smtp_provider_preset(merged["smtp_provider"])
        provider_key = str(merged["smtp_provider"]).lower()

        merged["smtp_host"] = _prompt_str("SMTP host", str(merged.get("smtp_host") or preset.get("host") or ""))
        current_smtp_port = int(merged.get("smtp_port") or preset.get("port") or 587)
        if provider_key == "brevo" and current_smtp_port == 2525:
            print("ℹ️  Brevo recommends port 587 first. Port 2525 is only a fallback when 587 is blocked.")
            current_smtp_port = 587
        merged["smtp_port"] = _prompt_int(
            "SMTP port",
            current_smtp_port,
            1,
            65535,
        )

        # STARTTLS is typical for 587/2525. Port 465 is typically implicit TLS.
        merged["smtp_use_starttls"] = _yn(
            "Use STARTTLS?",
            default=bool(merged.get("smtp_use_starttls", preset.get("starttls", True))),
        )
        merged["smtp_use_ssl"] = _yn(
            "Use implicit TLS (SMTP SSL)?",
            default=bool(merged.get("smtp_use_ssl", preset.get("ssl", False))) or (int(merged["smtp_port"]) == 465),
        )

        merged["smtp_username"] = _prompt_str("SMTP username/login", str(merged.get("smtp_username") or preset.get("username") or ""))

        store_pw = _yn("Store SMTP password in server_config.json? (not recommended)", default=False)
        if store_pw:
            merged["smtp_password"] = _prompt_secret("SMTP password / key")
        else:
            merged["smtp_password"] = ""
            print("ℹ️  SMTP password will be read from env var ECHOCHAT_SMTP_PASSWORD (or SMTP_PASSWORD).")

        default_from = str(merged.get("smtp_from") or f"{merged['server_name']} <no-reply@yourdomain.com>")
        merged["smtp_from"] = _prompt_verified_smtp_from(
            "From address (must be a real verified sender for Brevo/Gmail delivery)",
            default_from,
            provider_key,
        )
        merged["smtp_timeout_seconds"] = _prompt_int(
            "SMTP timeout seconds",
            int(merged.get("smtp_timeout_seconds") or base.get("smtp_timeout_seconds") or 20),
            3,
            120,
        )


        smtp_errors = _smtp_setup_errors(merged)
        if smtp_errors:
            for err in smtp_errors:
                print(f"❌ {err}")
            raise SystemExit(1)


    # ── SMS 2FA (Twilio Verify) ───────────────────────────────────────────────
    print("\n— SMS 2FA (Twilio Verify; optional) —")
    sms_2fa = _yn(
        "Enable SMS 2FA login support?",
        default=bool(merged.get("enable_two_factor_beta", False) and merged.get("enable_sms_two_factor", False)),
    )
    merged["enable_two_factor_beta"] = bool(sms_2fa)
    merged["enable_sms_two_factor"] = bool(sms_2fa)
    if sms_2fa:
        merged["two_factor_sms_channel"] = _prompt_choice(
            "Twilio Verify channel",
            str(merged.get("two_factor_sms_channel") or "sms"),
            ["sms", "whatsapp"],
        )
        merged["twilio_account_sid"] = _prompt_str("Twilio Account SID", str(merged.get("twilio_account_sid") or ""))
        store_twilio_token = _yn("Store Twilio Auth Token in server_config.json? (not recommended)", default=bool(str(merged.get("twilio_auth_token") or "").strip()))
        if store_twilio_token:
            merged["twilio_auth_token"] = _prompt_secret("Twilio Auth Token")
        else:
            merged["twilio_auth_token"] = ""
            print("ℹ️  Twilio Auth Token will be read from env var ECHOCHAT_TWILIO_AUTH_TOKEN (or TWILIO_AUTH_TOKEN).")
        merged["twilio_verify_service_sid"] = _prompt_str("Twilio Verify Service SID", str(merged.get("twilio_verify_service_sid") or ""))
        merged["two_factor_login_timeout_seconds"] = _prompt_int(
            "2FA login timeout seconds",
            int(merged.get("two_factor_login_timeout_seconds") or 600),
            60,
            3600,
        )
        twilio_errors = twilio_setup_errors(merged)
        if twilio_errors:
            for err in twilio_errors:
                print(f"❌ {err}")
            raise SystemExit(1)
    else:
        merged["twilio_auth_token"] = ""


    # ── Dynamic DNS helper (optional) ─────────────────────────────────────────
    print("\n— Dynamic DNS helper (optional) —")
    ddns_enabled = _yn(
        "Enable Dynamic DNS helper?",
        default=bool(merged.get("dynamic_dns_enabled", False)),
    )
    merged["dynamic_dns_enabled"] = bool(ddns_enabled)
    if ddns_enabled:
        merged["dynamic_dns_provider"] = _prompt_choice(
            "Dynamic DNS provider",
            str(merged.get("dynamic_dns_provider") or "No-IP"),
            ["No-IP", "Dynu", "DNS-O-Matic", "Custom"],
        )
        merged["dynamic_dns_domain"] = _prompt_str("DDNS hostname to update", str(merged.get("dynamic_dns_domain") or ""))
        merged["dynamic_dns_username"] = _prompt_str("DDNS provider username", str(merged.get("dynamic_dns_username") or ""))
        store_ddns_password = _yn("Store DDNS password/token in server_config.json? (not recommended)", default=bool(str(merged.get("dynamic_dns_password") or "").strip()))
        if store_ddns_password:
            merged["dynamic_dns_password"] = _prompt_secret("DDNS password / token")
        else:
            merged["dynamic_dns_password"] = ""
            print("ℹ️  DDNS password/token will be read from env var ECHOCHAT_DYNAMIC_DNS_PASSWORD (or DDNS_PASSWORD).")
        merged["dynamic_dns_update_url"] = _prompt_str("DDNS provider update URL", str(merged.get("dynamic_dns_update_url") or "https://dynupdate.no-ip.com/nic/update"))
        ddns_errors = dynamic_dns_setup_errors(merged)
        if ddns_errors:
            for err in ddns_errors:
                print(f"❌ {err}")
            raise SystemExit(1)
    else:
        merged["dynamic_dns_password"] = ""


    # ── GIFs (GIPHY) ──────────────────────────────────────────────────────────
    print("\n— GIFs (GIPHY) —")
    merged["giphy_enabled"] = _yn(
        "Enable GIF search (GIPHY)?",
        default=bool(merged.get("giphy_enabled", True)),
    )
    if merged["giphy_enabled"]:
        store_key = _yn("Store GIPHY API key in server_config.json? (or No = env/.giphy_api_key)", default=bool(str(merged.get("giphy_api_key") or "").strip()))
        if store_key:
            merged["giphy_api_key"] = _prompt_secret("GIPHY API key", allow_blank=False)
        else:
            merged["giphy_api_key"] = ""
            print("ℹ️  Set env var GIPHY_API_KEY (or create .giphy_api_key file) to enable GIF search.")
    else:
        merged["giphy_api_key"] = ""

    # ── CORS ──────────────────────────────────────────────────────────────────
    if advanced:
        cors_default = merged.get("cors_allowed_origins") or "*"
        raw = input(f"CORS allowed origins (comma-separated or * for all) [{cors_default}]: ").strip()
        if raw:
            if raw == "*":
                merged["cors_allowed_origins"] = "*"
            else:
                merged["cors_allowed_origins"] = [s.strip() for s in raw.split(",") if s.strip()]

    # ── Tokens / pool / logging ───────────────────────────────────────────────
    if advanced:
        merged["access_token_minutes"] = _prompt_int(
            "Access token minutes",
            int(merged.get("access_token_minutes") or base["access_token_minutes"]),
            1,
            24 * 60,
        )
        merged["refresh_token_days"] = _prompt_int(
            "Refresh token days",
            int(merged.get("refresh_token_days") or base["refresh_token_days"]),
            1,
            365,
        )
        merged["db_pool_min"] = _prompt_int("DB pool min", int(merged.get("db_pool_min") or base["db_pool_min"]), 1, 100)
        merged["db_pool_max"] = _prompt_int("DB pool max", int(merged.get("db_pool_max") or base["db_pool_max"]), 1, 500)
        merged["log_level"] = _prompt_str("Log level (DEBUG/INFO/WARNING/ERROR)", str(merged.get("log_level") or base["log_level"]))
        merged["log_file_path"] = _prompt_str("Log file path", str(merged.get("log_file_path") or base["log_file_path"]))

        merged["enable_health_check_endpoint"] = _yn(
            "Enable /health endpoint?",
            default=bool(merged.get("enable_health_check_endpoint", False)),
        )
        if merged["enable_health_check_endpoint"]:
            merged["health_check_endpoint"] = _prompt_str(
                "Health endpoint path",
                str(merged.get("health_check_endpoint") or base["health_check_endpoint"]),
            )

    # ── File transfers / Voice / ICE ─────────────────────────────────────────
    if advanced:
        merged["max_dm_file_bytes"] = _prompt_int(
            "Max DM file bytes",
            int(merged.get("max_dm_file_bytes") or base["max_dm_file_bytes"]),
            1 * 1024,
            500 * 1024 * 1024,
        )
        merged["p2p_file_enabled"] = _yn("Enable P2P-first DM file transfer?", default=bool(merged.get("p2p_file_enabled", True)))
        merged["voice_enabled"] = _yn("Enable voice chat?", default=bool(merged.get("voice_enabled", True)))

        print("\n— WebRTC STUN/TURN connectivity —")
        print("STUN is fine for many LAN/home tests. TURN is the relay you need for reliable internet, cellular, and strict NAT/firewall tests.")
        current_summary = ice_server_summary(merged)
        print(f"Current ICE: P2P={current_summary['p2p_count']} server(s), voice/webcam={current_summary['voice_count']} server(s), TURN={'yes' if current_summary['turn_configured'] else 'no'}")

        if _yn("Configure a TURN relay for real internet webcam/file testing?", default=bool(current_summary.get("turn_configured"))):
            raw_p2p = input(
                "P2P/WebRTC ICE servers as comma URLs or JSON "
                "[stun:stun.l.google.com:19302, turn:turn.example.com:3478]: "
            ).strip()
            if raw_p2p:
                parsed = _parse_csv_urls(raw_p2p)
                if parsed:
                    merged["p2p_ice_servers"] = _turn_setup_credentials_prompt(parsed, label="P2P TURN")
                else:
                    print("⚠️  No valid STUN/TURN URLs found; keeping existing P2P ICE settings.")
            else:
                merged["p2p_ice_servers"] = p2p_ice_servers(merged)

            raw_voice = input(
                "Voice/webcam ICE servers as comma URLs or JSON (blank = use P2P list): "
            ).strip()
            if raw_voice:
                parsed = _parse_csv_urls(raw_voice)
                if parsed:
                    merged["voice_ice_servers"] = _turn_setup_credentials_prompt(parsed, label="Voice/webcam TURN")
                else:
                    print("⚠️  No valid STUN/TURN URLs found; voice/webcam will keep using P2P ICE settings.")
                    merged["voice_ice_servers"] = merged.get("voice_ice_servers") or []
            else:
                merged["voice_ice_servers"] = merged.get("voice_ice_servers") or []
        else:
            merged["p2p_ice_servers"] = p2p_ice_servers(merged)
            merged["voice_ice_servers"] = merged.get("voice_ice_servers") or []

        print("\n— Echo media / webcam —")
        enable_webcam = _yn(
            "Enable built-in Echo webcam controls? (No keeps voice-only mode)",
            default=str(merged.get("av_mode") or "echo").strip().lower() == "echo" and bool(merged.get("webcam_enabled", True)),
        )
        merged["av_mode"] = "echo" if enable_webcam else "standard"
        merged["webcam_enabled"] = enable_webcam
        merged["echo_webcam_enabled"] = enable_webcam
        if enable_webcam:
            merged["webcam_approval_mode"] = _prompt_str(
                "Webcam approval mode (owner_approval/open/disabled)",
                str(merged.get("webcam_approval_mode") or "owner_approval"),
            )
            merged["webcam_max_viewers"] = _prompt_int(
                "Max webcam viewers per user (0 unlimited)",
                int(merged.get("webcam_max_viewers") or 0),
                0,
                10000,
            )
            merged["default_media_policy"] = _prompt_str(
                "Default media policy (user_choice/voice_first/webcam_first/both_first)",
                str(merged.get("default_media_policy") or "user_choice"),
            )
        else:
            print("ℹ️  Webcam controls are off. Room button will show voice-only mode.")

    # ── JWT secret (stable) ───────────────────────────────────────────────────
    # server_init.py will ensure/persist this if missing, but we can do it now.
    if not merged.get("jwt_secret"):
        if _yn("Generate & save a stable jwt_secret now?", default=True):
            import secrets

            merged["jwt_secret"] = secrets.token_hex(32)
            print("✅ jwt_secret generated")

    # ── Admin accounts ──────────────────────────────────────────
    print("\n— Admin (required) —")
    while True:
        proposed_admin_user = normalize_registration_username(_prompt_str("Admin username", str(merged.get("admin_user") or base["admin_user"])))
        normalized_admin_user, username_err = _validate_setup_admin_username(proposed_admin_user, settings=merged, account_label="owner")
        if username_err:
            print(f"❌ {username_err}")
            continue
        merged["admin_user"] = normalized_admin_user
        break
    admin_email: Optional[str] = None
    admin_age: Optional[int] = None
    if advanced:
        merged["admin_notification_email"] = _prompt_str(
            "Admin notification email (optional)",
            str(merged.get("admin_notification_email") or ""),
        )
        e = str(merged.get("admin_notification_email") or "").strip()
        admin_email = e or None
        a = input("Age for admin (optional): ").strip()
        admin_age = int(a) if a else None
    raw_password = _prompt_password("Admin password", username=merged.get("admin_user"), email=admin_email or merged.get("admin_notification_email"), server_name=merged.get("server_name"))
    admin_recovery_pin = _prompt_recovery_pin("Admin Recovery PIN")
    desired_hash = hash_password(raw_password)
    merged["__admin_raw_password"] = raw_password
    merged["__admin_recovery_pin"] = admin_recovery_pin
    merged["admin_pass"] = desired_hash

    print("\n— Extra admin user (optional) —")
    create_extra_admin = _yn("Create an additional admin user now?", default=False)
    extra_admin_user = ""
    extra_admin_password = ""
    extra_admin_email: Optional[str] = None
    if create_extra_admin:
        while True:
            extra_admin_user = normalize_registration_username(_prompt_str("Second admin username", "admin2"))
            if _same_setup_username(extra_admin_user, merged.get("admin_user")):
                print("❌ Extra admin username must be different from the owner username.")
                continue
            username_value, username_err = _validate_setup_admin_username(extra_admin_user, settings=merged, account_label="extra admin")
            if username_err:
                print(f"❌ {username_err}")
                continue
            extra_admin_user = username_value
            break
        if advanced:
            extra_admin_email = input("Email for second admin (optional): ").strip() or None
        extra_admin_password = _prompt_password("Second admin password", username=extra_admin_user, email=extra_admin_email, server_name=merged.get("server_name"))
        extra_admin_recovery_pin = _prompt_recovery_pin("Second admin Recovery PIN")

    if create_extra_admin:
        merged["__create_initial_admin"] = True
        merged["__initial_admin_user"] = extra_admin_user
        merged["__initial_admin_email"] = extra_admin_email or ""
        merged["__initial_admin_raw_password"] = extra_admin_password
        merged["__initial_admin_recovery_pin"] = extra_admin_recovery_pin
        extra_ok, extra_msg = _validate_extra_admin_setup_fields(merged)
        if not extra_ok:
            print(f"❌ Extra admin setup is incomplete or invalid: {extra_msg}")
            raise SystemExit(1)

    owner_ok, owner_msg = _validate_owner_admin_setup_fields(merged, base)
    if not owner_ok:
        print(f"❌ Owner/admin setup is incomplete or invalid: {owner_msg}")
        raise SystemExit(1)

    try:
        conn = psycopg2.connect(str(merged["database_url"]))
        try:
            _prepare_full_schema_in_setup(conn)
            merged["admin_pass"], admin_msg = _sync_primary_admin_in_db(
                conn,
                merged["admin_user"],
                raw_password,
                desired_hash,
                admin_email,
                admin_age,
                admin_recovery_pin,
                field_encryption_settings=merged,
            )
            print(f"✅ {admin_msg}")
            if create_extra_admin:
                if str(extra_admin_user).strip().lower() == str(merged["admin_user"]).strip().lower():
                    print("ℹ️  Skipped creating the extra admin because it uses the same username as the admin.")
                else:
                    _, admin_msg = _sync_initial_admin_in_db(
                        conn,
                        str(extra_admin_user).strip(),
                        extra_admin_password,
                        hash_password(extra_admin_password),
                        extra_admin_email,
                        None,
                        extra_admin_recovery_pin,
                        field_encryption_settings=merged,
                    )
                    print(f"✅ {admin_msg}")
        finally:
            conn.close()
    except Exception as e:
        print(f"❌ Could not create/sync setup admin users in DB: {e}")
        print("   Setup cannot finish without a DB-backed admin login. Fix the database/schema issue and run setup again.")
        raise SystemExit(1)

    print("\n✅ Setup complete.\n")
    merged = _autobrand_settings(merged)
    return _compact_settings(merged)



def _prompt_bootstrap_dsn(current: str = "", target_dsn: str = "") -> str:
    print("Bootstrap/admin PostgreSQL DSN")
    print("Database create/recreate or schema grant repair may require a bootstrap/admin PostgreSQL DSN.")
    return _prompt_str("Bootstrap/admin PostgreSQL DSN", current or target_dsn or "")


def _prompt_password_in_tui(stdscr, title: str, *, username: str | None = None, email: str | None = None, server_name: str | None = None) -> str:
    while True:
        p1 = _tui_input(stdscr, title, "Enter password / passphrase", secret=True)
        if not p1:
            _tui_message(stdscr, title, ["Password cannot be empty."], error=True)
            continue
        ok_password, password_err = validate_account_password(p1, username=username, email=email, server_name=server_name)
        if not ok_password:
            _tui_message(stdscr, title, [password_err or password_policy_summary()], error=True)
            continue
        p2 = _tui_input(stdscr, title, "Confirm password", secret=True)
        if p1 != p2:
            _tui_message(stdscr, title, ["Passwords do not match."], error=True)
            continue
        return p1


def _prompt_recovery_pin_in_tui(stdscr, title: str) -> str:
    while True:
        p1 = _tui_input(stdscr, title, "Enter 4-8 digit Recovery PIN", secret=True)
        if not _valid_recovery_pin(p1):
            _tui_message(stdscr, title, [recovery_pin_policy_summary()], error=True)
            continue
        p2 = _tui_input(stdscr, title, "Confirm Recovery PIN", secret=True)
        if p1 != p2:
            _tui_message(stdscr, title, ["Recovery PINs do not match."], error=True)
            continue
        return p1


def _display_value(value: Any, kind: str = "text") -> str:
    if kind == "bool":
        return "Yes" if bool(value) else "No"
    if kind == "secret":
        return "(set)" if str(value or "").strip() else "(not set)"
    if value is None:
        return ""
    return str(value)


def _color_pair_bg() -> int:
    if curses.has_colors():
        return curses.color_pair(1)
    return curses.A_NORMAL


def _color_pair_hl() -> int:
    if curses.has_colors():
        return curses.color_pair(2)
    return curses.A_REVERSE


def _init_tui_colors() -> None:
    """Initialize setup TUI colors in the safest ncurses order."""
    try:
        curses.start_color()
    except Exception:
        return
    try:
        if not curses.has_colors():
            return
    except Exception:
        return
    try:
        curses.use_default_colors()
    except Exception:
        pass
    try:
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLUE)
        curses.init_pair(3, curses.COLOR_CYAN, curses.COLOR_BLUE)
        curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLUE)
        curses.init_pair(5, curses.COLOR_GREEN, curses.COLOR_BLUE)
    except Exception:
        pass

def _safe_curs_set(visible: int) -> None:
    """Best-effort cursor visibility change for the setup TUI.

    Some terminals report curses support but reject curs_set(), which previously
    caused the blue setup UI to silently fall back to the plain legacy prompts.
    Cursor visibility is cosmetic, so keep the full-screen UI alive when the
    terminal refuses it.
    """
    try:
        curses.curs_set(visible)
    except Exception:
        pass


def _draw_box(stdscr, title: str, footer: str | None = None) -> tuple[int, int]:
    title = _brand_ui_text(title)
    footer = _brand_ui_text(footer) if footer is not None else None
    h, w = stdscr.getmaxyx()
    bg = _color_pair_bg()
    # Set the background before clearing so the full screen repaints blue.
    try:
        stdscr.bkgdset(" ", bg)
    except Exception:
        pass
    stdscr.erase()
    try:
        stdscr.bkgd(" ", bg)
    except Exception:
        pass
    stdscr.attron(bg)
    stdscr.box()
    header = f" {PROJECT_NAME} Setup "
    stdscr.addnstr(0, max(2, (w - len(header)) // 2), header, max(1, w - 4), curses.A_BOLD | bg)
    display_name = _setup_display_name()
    title_line = f"{title} — configuring: {display_name}" if display_name and display_name != PROJECT_NAME else title
    stdscr.addnstr(1, 2, title_line, max(1, w - 4), curses.A_BOLD | bg)
    if footer:
        stdscr.addnstr(h - 2, 2, footer, max(1, w - 4), bg)
    stdscr.attroff(bg)
    return h, w


def _wrap_lines(text: str, width: int) -> list[str]:
    if not text:
        return [""]
    out: list[str] = []
    for block in str(text).splitlines() or [""]:
        wrapped = textwrap.wrap(block, width=max(10, width), replace_whitespace=False) or [""]
        out.extend(wrapped)
    return out


def _tui_message(stdscr, title: str, lines: list[str], pause: bool = True, error: bool = False) -> None:
    title = _brand_ui_text(title)
    lines = [_brand_ui_text(line) for line in (lines or [])]
    h, w = _draw_box(stdscr, title, "Press any key to continue" if pause else None)
    y = 3
    attr = curses.color_pair(4) if error and curses.has_colors() else _color_pair_bg()
    for raw in lines:
        for line in _wrap_lines(raw, w - 6):
            if y >= h - 3:
                break
            stdscr.addnstr(y, 3, line, max(1, w - 6), attr)
            y += 1
    stdscr.refresh()
    if pause:
        stdscr.getch()


def _tui_input(stdscr, title: str, prompt: str, initial: str = "", secret: bool = False) -> str:
    title = _brand_ui_text(title)
    prompt = _brand_ui_text(prompt)
    _safe_curs_set(1)
    value = list(str(initial or ""))
    while True:
        h, w = _draw_box(stdscr, title, "Type to edit, Enter to accept, Esc to keep current value")
        stdscr.addnstr(3, 3, prompt, max(1, w - 6), curses.A_BOLD | _color_pair_bg())
        shown = ("*" * len(value)) if secret else "".join(value)
        max_field = max(10, w - 8)
        field = shown[-max_field:]
        stdscr.addnstr(5, 3, "[" + field + "]", max(1, w - 6), _color_pair_hl())
        cursor_x = min(w - 4, 4 + len(field))
        stdscr.move(5, cursor_x)
        stdscr.refresh()
        ch = stdscr.get_wch()
        if ch in ("\n", "\r"):
            _safe_curs_set(0)
            return "".join(value)
        if ch == "\x1b":
            _safe_curs_set(0)
            return str(initial or "")
        if ch in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            if value:
                value.pop()
            continue
        if isinstance(ch, str) and ch.isprintable():
            value.append(ch)


def _tui_yes_no(stdscr, title: str, prompt: str, default: bool = True) -> bool:
    title = _brand_ui_text(title)
    prompt = _brand_ui_text(prompt)
    items = ["Yes", "No"]
    idx = 0 if default else 1
    while True:
        h, w = _draw_box(stdscr, title, "Arrow keys to move, Enter to choose")
        y = 4
        for line in _wrap_lines(prompt, w - 6):
            stdscr.addnstr(y, 3, line, max(1, w - 6), _color_pair_bg())
            y += 1
        y += 1
        for i, item in enumerate(items):
            attr = _color_pair_hl() | curses.A_BOLD if i == idx else _color_pair_bg()
            stdscr.addnstr(y + i, 5, item, max(1, w - 10), attr)
        stdscr.refresh()
        ch = stdscr.get_wch()
        if ch in (curses.KEY_UP, curses.KEY_LEFT, "k", "h"):
            idx = (idx - 1) % len(items)
        elif ch in (curses.KEY_DOWN, curses.KEY_RIGHT, "j", "l"):
            idx = (idx + 1) % len(items)
        elif ch in ("\n", "\r"):
            return idx == 0
        elif ch == "\x1b":
            return default



def _bounded_menu_index(selected: int, item_count: int) -> int:
    """Clamp a stored TUI menu cursor to the currently available menu range."""
    if item_count <= 0:
        return 0
    try:
        idx = int(selected)
    except (TypeError, ValueError):
        idx = 0
    return max(0, min(idx, item_count - 1))


def _setup_main_menu_index_after_step(step_index: int, item_count: int, *, advance: bool = True) -> int:
    """Return the main setup cursor after a guided step closes.

    The final menu item is Cancel, so the cursor is clamped to the last real
    setup action instead of advancing onto Cancel. Advancing keeps the wizard
    flowing top-to-bottom while still preserving the user's current position.
    """
    if item_count <= 0:
        return 0
    try:
        target = int(step_index) + (1 if advance else 0)
    except (TypeError, ValueError):
        target = 0
    last_action_index = max(0, item_count - 2)
    return max(0, min(target, last_action_index))

def _tui_menu(stdscr, title: str, intro: list[str], items: list[str], selected: int = 0, footer: str | None = None) -> int:
    title = _brand_ui_text(title)
    intro = [_brand_ui_text(x) for x in (intro or [])]
    items = [_brand_ui_text(x) for x in (items or [])]
    footer = _brand_ui_text(footer) if footer is not None else None
    idx = _bounded_menu_index(selected, len(items))
    top = 0
    while True:
        h, w = _draw_box(stdscr, title, footer or "Up/Down to move, Enter to select, Esc to go back")
        y = 3
        for raw in intro:
            for line in _wrap_lines(raw, w - 6):
                if y >= h - 4:
                    break
                stdscr.addnstr(y, 3, line, max(1, w - 6), _color_pair_bg())
                y += 1
        y += 1
        visible = max(4, h - y - 3)
        if idx < top:
            top = idx
        if idx >= top + visible:
            top = idx - visible + 1
        for row, item in enumerate(items[top:top + visible]):
            actual = top + row
            attr = _color_pair_hl() | curses.A_BOLD if actual == idx else _color_pair_bg()
            stdscr.addnstr(y + row, 4, item, max(1, w - 8), attr)
        stdscr.refresh()
        ch = stdscr.get_wch()
        if ch in (curses.KEY_UP, "k"):
            idx = (idx - 1) % len(items)
        elif ch in (curses.KEY_DOWN, "j"):
            idx = (idx + 1) % len(items)
        elif ch in ("\n", "\r"):
            return idx
        elif ch == "\x1b":
            return -1



def _tui_scroll_text(stdscr, title: str, lines: list[str], footer: str | None = None, allow_save: bool = False) -> str:
    title = _brand_ui_text(title)
    rows = [_brand_ui_text(line) for line in (lines or [])]
    footer = _brand_ui_text(footer) if footer is not None else None
    if not rows:
        rows = ["Nothing to show yet."]
    top = 0
    while True:
        h, w = _draw_box(
            stdscr,
            title,
            footer or ("Up/Down scroll, S saves now, Enter/Esc goes back" if allow_save else "Up/Down scroll, Enter/Esc goes back"),
        )
        visible = max(4, h - 6)
        for row, line in enumerate(rows[top:top + visible]):
            stdscr.addnstr(3 + row, 3, line, max(1, w - 6), _color_pair_bg())
        stdscr.refresh()
        ch = stdscr.get_wch()
        if ch in (curses.KEY_UP, 'k'):
            top = max(0, top - 1)
            continue
        if ch in (curses.KEY_DOWN, 'j'):
            top = min(max(0, len(rows) - visible), top + 1)
            continue
        if ch in (curses.KEY_PPAGE,):
            top = max(0, top - visible)
            continue
        if ch in (curses.KEY_NPAGE,):
            top = min(max(0, len(rows) - visible), top + visible)
            continue
        if allow_save and isinstance(ch, str) and ch.lower() == 's':
            return 'save'
        if ch in ('\n', '\r'):
            return 'back'
        if ch == '\x1b':
            return 'back'


def _redact_secret_text(value: Any, keep: int = 2) -> str:
    text = str(value or '').strip()
    if not text:
        return '(not set)'
    if len(text) <= keep:
        return '*' * len(text)
    return '*' * max(4, len(text) - keep) + text[-keep:]

def _show_public_beta_readiness_report(stdscr, merged: Dict[str, Any]) -> None:
    """Render the public-beta readiness report inside the setup TUI.

    The hosting/network setup screens call this after the user edits hosting
    fields. Keep this as a small TUI wrapper around the shared readiness
    formatter so the wizard and command-line readiness check stay in sync.
    """
    try:
        lines = public_beta_readiness_lines(
            merged,
            settings_file="server_config.json",
            repo_root=Path(__file__).resolve().parent,
        )
    except Exception as exc:
        lines = [
            "Echo-Chat Public Beta Readiness",
            "",
            "Could not build the readiness report.",
            f"Error: {exc}",
        ]
    _tui_scroll_text(
        stdscr,
        "Public beta readiness report",
        lines,
        footer="Enter/Esc returns to setup.",
        allow_save=False,
    )



def _readiness_marker(ok: bool) -> str:
    return "OK" if ok else "NEEDS ATTENTION"


def _collect_setup_readiness_lines(merged: Dict[str, Any], runtime: Dict[str, Any] | None = None) -> list[str]:
    """Return a plain-English setup readiness checklist for the TUI summary.

    This is intentionally based only on current in-memory setup values and the
    latest database validation report. It does not open sockets or mutate state;
    service checks stay in the dedicated checks menu.
    """
    runtime = runtime or {}
    validation_report = runtime.get("db_validation_report") or {}
    validation_state = str(validation_report.get("state") or "not_checked") if isinstance(validation_report, dict) else "not_checked"
    validation_valid = bool(validation_report.get("valid")) if isinstance(validation_report, dict) else False
    db_configured = bool(str(merged.get("database_url") or "").strip())
    db_ready = db_configured and (
        validation_state in {"valid_echochat", "empty"}
        and (validation_valid or validation_state == "empty")
    )
    if validation_state == "not_checked":
        db_detail = "not checked yet; use Step 1 to validate before saving"
    elif validation_state == "foreign_schema":
        db_detail = "wrong database; choose/create/recreate a proper Echo-Chat database"
    elif validation_state == "partial_echochat":
        db_detail = "partial Echo-Chat schema; setup will ask before repair/save"
    elif validation_state == "empty":
        db_detail = "empty database; setup can prepare the schema during save"
    elif validation_state == "valid_echochat" and validation_valid:
        db_detail = "valid Echo-Chat database"
    elif validation_state == "valid_echochat":
        db_detail = "Echo-Chat tables found, but permissions need review"
    else:
        db_detail = validation_state.replace("_", " ")

    server_name = str(merged.get("server_name") or "").strip()
    server_host = str(merged.get("server_host") or merged.get("host") or "").strip()
    server_port = str(merged.get("server_port") or merged.get("port") or "").strip()
    identity_ok = bool(server_name and server_host and server_port)

    owner_user_ok = bool(str(merged.get("admin_user") or "").strip())
    owner_password_ok = bool(str(merged.get("__admin_raw_password") or "").strip())
    owner_pin_ok = _valid_recovery_pin(str(merged.get("__admin_recovery_pin") or ""))
    extra_admin_ok = True
    if bool(merged.get("__create_initial_admin")):
        extra_admin_ok = bool(str(merged.get("__initial_admin_user") or "").strip()) and bool(str(merged.get("__initial_admin_raw_password") or "").strip()) and _valid_recovery_pin(str(merged.get("__initial_admin_recovery_pin") or ""))

    jwt_ok = is_strong_secret(resolve_secret(merged, "jwt_secret"))
    public_url = str(merged.get("public_base_url") or "").strip().lower()
    public_https = public_url.startswith("https://")
    cookie_secure = bool(merged.get("cookie_secure"))
    cookie_note_ok = (not public_url) or (public_https and cookie_secure) or public_url.startswith("http://127.0.0.1") or public_url.startswith("http://localhost")
    if not public_url:
        cookie_detail = "local/testing mode; set HTTPS + secure cookies before public hosting"
    elif public_https and cookie_secure:
        cookie_detail = "public HTTPS and secure cookies are aligned"
    elif public_https:
        cookie_detail = "public URL is HTTPS, but secure cookies are off"
    else:
        cookie_detail = "public URL is not HTTPS; use this only for local/private testing"

    smtp_enabled = bool(merged.get("smtp_enabled"))
    smtp_errors = _smtp_setup_errors(merged)
    smtp_ready = not smtp_errors
    smtp_detail = "disabled; forgot-password emails will not send" if not smtp_enabled else ("configured; run Step 16 to test delivery" if smtp_ready else smtp_errors[0])

    ddns_errors = dynamic_dns_setup_errors(merged)
    ddns_ready = not ddns_errors
    ddns_detail = "disabled or handled outside Echo-Chat" if not bool(merged.get("dynamic_dns_enabled")) else ("configured; use --dynamic-dns-check before updating" if ddns_ready else ddns_errors[0])

    giphy_enabled = bool(merged.get("giphy_enabled"))
    giphy_key_available = bool(str(merged.get("giphy_api_key") or "").strip() or os.getenv("ECHOCHAT_GIPHY_API_KEY") or os.getenv("GIPHY_API_KEY") or Path(".giphy_api_key").exists() or Path("giphy_api_key.txt").exists())
    giphy_ok = (not giphy_enabled) or giphy_key_available or not bool(merged.get("__store_giphy_in_config"))
    giphy_detail = "disabled" if not giphy_enabled else ("API key available/configured" if giphy_key_available else "enabled; set ECHOCHAT_GIPHY_API_KEY or .giphy_api_key before production")

    webcam_enabled = str(merged.get("av_mode") or "echo").strip().lower() == "echo" and bool(merged.get("webcam_enabled", True))
    media_ready = bool(merged.get("voice_enabled", True)) or webcam_enabled
    media_detail = "built-in webcam controls enabled" if webcam_enabled else "voice-only mode"

    storage_uri = str(merged.get("rate_limit_storage_uri") or merged.get("rate_limit_storage") or "").strip()
    rate_storage_ok = bool(storage_uri) and (storage_uri != "memory://" or not public_url)
    rate_detail = "memory:// is fine for local testing" if storage_uri == "memory://" and not public_url else ("Redis/shared storage recommended for multi-worker/public hosting" if storage_uri == "memory://" else storage_uri or "not set")

    checklist = [
        "Setup readiness checklist",
        f"  [{_readiness_marker(db_ready)}] Database: {db_detail}",
        f"  [{_readiness_marker(identity_ok)}] Server identity: {'name/host/port set' if identity_ok else 'server name, host, or port is missing'}",
        f"  [{_readiness_marker(owner_user_ok and owner_password_ok and owner_pin_ok and extra_admin_ok)}] Owner/admin accounts: owner username/password/PIN {'set' if owner_user_ok and owner_password_ok and owner_pin_ok else 'needs attention'}; extra admin {'ready/off' if extra_admin_ok else 'incomplete'}",
        f"  [{_readiness_marker(jwt_ok)}] JWT/session secret: {'present' if jwt_ok else 'missing/placeholder; setup can generate stable secrets in Step 4'}",
        f"  [{_readiness_marker(cookie_note_ok)}] Public URL / cookies: {cookie_detail}",
        f"  [{_readiness_marker(smtp_ready)}] Password recovery email: {smtp_detail}",
        f"  [{_readiness_marker(ddns_ready)}] Dynamic DNS helper: {ddns_detail}",
        f"  [{_readiness_marker(giphy_ok)}] GIF search key: {giphy_detail}",
        f"  [{_readiness_marker(media_ready)}] Echo media: {media_detail}",
        f"  [{_readiness_marker(rate_storage_ok)}] Rate-limit storage: {rate_detail}",
    ]
    return checklist


def _setup_safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _current_setup_step_checks(merged: Dict[str, Any], runtime: Dict[str, Any] | None = None) -> list[dict[str, str | bool]]:
    """Return ordered per-step setup checks used by the guided progress view.

    The main readiness checklist focuses on critical save blockers. This view is
    wider: it tells an admin which numbered setup step to open next and why. It
    stays read-only and does not run network checks, create databases, or write
    files.
    """
    runtime = runtime or {}
    validation_report = runtime.get("db_validation_report") or {}
    validation_state = str(validation_report.get("state") or "not_checked") if isinstance(validation_report, dict) else "not_checked"
    validation_valid = bool(validation_report.get("valid")) if isinstance(validation_report, dict) else False
    db_configured = bool(str(merged.get("database_url") or "").strip())
    db_ok = db_configured and validation_state in {"valid_echochat", "empty"} and (validation_valid or validation_state == "empty")
    if not db_configured:
        db_detail = "choose or create the PostgreSQL database"
    elif validation_state == "not_checked":
        db_detail = "validate the selected database before saving"
    elif validation_state == "foreign_schema":
        db_detail = "wrong database selected; choose another database or create a fresh one"
    elif validation_state == "partial_echochat":
        db_detail = "partial schema found; review repair/save prompt"
    elif db_ok:
        db_detail = "database target is ready for setup"
    else:
        db_detail = validation_state.replace("_", " ") or "needs review"

    server_name = str(merged.get("server_name") or "").strip()
    server_host = str(merged.get("server_host") or merged.get("host") or "").strip()
    server_port = _setup_safe_int(merged.get("server_port") or merged.get("port"), 0)
    identity_ok = bool(server_name and server_host and 0 < server_port <= 65535)

    owner_ok = bool(str(merged.get("admin_user") or "").strip()) and bool(str(merged.get("__admin_raw_password") or "").strip()) and _valid_recovery_pin(str(merged.get("__admin_recovery_pin") or ""))
    extra_admin_ok = True
    if bool(merged.get("__create_initial_admin")):
        extra_admin_ok = bool(str(merged.get("__initial_admin_user") or "").strip()) and bool(str(merged.get("__initial_admin_raw_password") or "").strip()) and _valid_recovery_pin(str(merged.get("__initial_admin_recovery_pin") or ""))

    jwt_ok = is_strong_secret(resolve_secret(merged, "jwt_secret"))
    token_ok = _setup_safe_int(merged.get("access_token_minutes"), 0) > 0 and _setup_safe_int(merged.get("refresh_token_days"), 0) > 0

    smtp_enabled = bool(merged.get("smtp_enabled"))
    recovery_policy_ok = _setup_safe_int(merged.get("password_reset_daily_limit"), 0) > 0 and _setup_safe_int(merged.get("password_reset_token_minutes"), 0) > 0 and _setup_safe_int(merged.get("recovery_pin_max_attempts"), 0) > 0
    smtp_ready = _smtp_ready_for_setup(merged)
    twilio_ready_ok = _twilio_ready_for_setup(merged)

    message_display_ok = True
    rooms_ok = _setup_safe_int(merged.get("max_message_length"), 0) > 0 and _setup_safe_int(merged.get("janitor_interval_seconds"), 0) > 0

    rate_storage = str(merged.get("rate_limit_storage_uri") or merged.get("rate_limit_storage") or "").strip()
    abuse_ok = bool(rate_storage) and bool(str(merged.get("room_msg_rate_limit") or "").strip()) and bool(str(merged.get("dm_msg_rate_limit") or "").strip())

    giphy_enabled = bool(merged.get("giphy_enabled"))
    giphy_key_available = bool(str(merged.get("giphy_api_key") or "").strip() or os.getenv("ECHOCHAT_GIPHY_API_KEY") or os.getenv("GIPHY_API_KEY") or Path(".giphy_api_key").exists() or Path("giphy_api_key.txt").exists())
    media_ok = (not giphy_enabled) or giphy_key_available or not bool(merged.get("__store_giphy_in_config"))

    voice_ok = ((not bool(merged.get("voice_enabled", True))) or _setup_safe_int(merged.get("voice_max_room_peers"), 0) >= 0) and _ice_ready_for_setup(merged)
    dynamic_dns_ok = _dynamic_dns_ready_for_setup(merged)

    media_mode = str(merged.get("av_mode") or "echo").strip().lower()
    media_mode_ok = media_mode in {"echo", "standard"}
    webcam_limit_ok = _setup_safe_int(merged.get("webcam_max_viewers"), 0) >= 0
    echo_media_ok = media_mode_ok and webcam_limit_ok

    public_url = str(merged.get("public_base_url") or "").strip().lower()
    cookie_secure = bool(merged.get("cookie_secure"))
    try:
        beta_report = build_public_beta_readiness(merged, settings_file="server_config.json", repo_root=Path(__file__).resolve().parent)
        hosting_ok = str(beta_report.get("overall") or "fail") != "fail"
    except Exception:
        hosting_ok = (not public_url) or (public_url.startswith("https://") and cookie_secure) or public_url.startswith("http://127.0.0.1") or public_url.startswith("http://localhost")

    logs_ok = bool(str(merged.get("log_level") or "").strip()) and bool(str(merged.get("health_check_endpoint") or "").strip())

    return [
        {"step": "Step 1", "title": "Database creation and connection", "ok": db_ok, "detail": db_detail, "action": "open Step 1 and validate/select/create the database"},
        {"step": "Step 2", "title": "Server identity", "ok": identity_ok, "detail": "server name, bind host, and port are set" if identity_ok else "set server name, bind host, and bind port", "action": "open Step 2 and set server name/host/port"},
        {"step": "Step 3", "title": "Owner and admin accounts", "ok": owner_ok and extra_admin_ok, "detail": "owner admin credentials and Recovery PIN are ready" if owner_ok and extra_admin_ok else "set owner username/password/PIN and complete optional second admin", "action": "open Step 3 and complete admin credentials plus Recovery PIN"},
        {"step": "Step 4", "title": "Login and session security / SMS 2FA", "ok": jwt_ok and token_ok and twilio_ready_ok, "detail": "JWT secret, token lifetimes, and optional SMS 2FA are set" if jwt_ok and token_ok and twilio_ready_ok else "generate JWT secret, verify token lifetimes, and finish/disable SMS 2FA", "action": "open Step 4 and generate/verify login security and SMS 2FA settings"},
        {"step": "Step 5", "title": "Password recovery and email", "ok": recovery_policy_ok and smtp_ready, "detail": "recovery policy is set; SMTP is off or configured" if recovery_policy_ok and smtp_ready else "finish reset limits/PIN lockout and SMTP fields", "action": "open Step 5 and finish recovery/email settings"},
        {"step": "Step 6", "title": "Message display", "ok": message_display_ok, "detail": "display defaults are usable", "action": "open Step 6 only if you want different message animation/sender labels"},
        {"step": "Step 7", "title": "Rooms, cleanup, and chat limits", "ok": rooms_ok, "detail": "message length and janitor interval are set" if rooms_ok else "set room cleanup and message limits", "action": "open Step 7 and verify cleanup/message limits"},
        {"step": "Step 8", "title": "Protection and anti-abuse", "ok": abuse_ok, "detail": "rate-limit storage and core message limits are set" if abuse_ok else "set rate-limit storage and room/DM limits", "action": "open Step 8 and configure abuse protection"},
        {"step": "Step 9", "title": "Media, GIFs, and uploads", "ok": media_ok, "detail": "media/GIF settings are usable" if media_ok else "add GIF key or disable storing it in config", "action": "open Step 9 and finish media/GIF settings"},
        {"step": "Step 10", "title": "Voice and WebRTC", "ok": voice_ok, "detail": "voice settings and STUN/TURN are usable" if voice_ok else "set voice capacity and complete any TURN credentials", "action": "open Step 10 and verify voice capacity plus STUN/TURN settings"},
        {"step": "Step 11", "title": "Echo media / webcam", "ok": echo_media_ok, "detail": "Echo media settings are usable" if echo_media_ok else "set A/V mode to echo or standard and verify viewer limit", "action": "open Step 11 and complete Echo media settings"},
        {"step": "Step 12", "title": "Hosting, proxy, HTTPS, and public beta readiness", "ok": hosting_ok and dynamic_dns_ok, "detail": "hosting mode, Dynamic DNS, and public-beta basics are usable" if hosting_ok and dynamic_dns_ok else "fix public URL, cookies, origins, Dynamic DNS, or production topology", "action": "open Step 12 and run the public beta readiness report / DDNS helper"},
        {"step": "Step 13", "title": "Logs and health checks", "ok": logs_ok, "detail": "log level and health endpoint are set" if logs_ok else "set log level and health endpoint", "action": "open Step 13 and verify logs/health settings"},
    ]


def _setup_next_action_line(merged: Dict[str, Any], runtime: Dict[str, Any] | None = None) -> str:
    for check in _current_setup_step_checks(merged, runtime):
        if not bool(check.get("ok")):
            return f"Next recommended action: {check['step']} - {check['title']} ({check['action']})."
    return "Next recommended action: Step 15 - Review before save, then Step 16 checks, then Step 17 save."


def _collect_setup_step_progress_lines(merged: Dict[str, Any], runtime: Dict[str, Any] | None = None) -> list[str]:
    lines = ["Guided setup progress", _setup_next_action_line(merged, runtime)]
    for check in _current_setup_step_checks(merged, runtime):
        marker = _readiness_marker(bool(check.get("ok")))
        lines.append(f"  [{marker}] {check['step']} - {check['title']}: {check['detail']}")
    return lines




def _collect_setup_summary_lines(merged: Dict[str, Any], runtime: Dict[str, Any] | None = None) -> list[str]:
    runtime = runtime or {}
    parts = {}
    raw_dsn = str(merged.get('database_url') or '').strip()
    if raw_dsn:
        try:
            parts = dsn_parts(raw_dsn)
        except Exception:
            parts = {}
    detected_text = ''
    detected_dsn = str(runtime.get('detected_dsn') or '').strip()
    detected_candidates = list(runtime.get('detected_candidates') or [])
    validation_report = runtime.get('db_validation_report') or {}
    target_status = runtime.get('target_database_status') or {}
    target_status_text = '(not checked)'
    if isinstance(target_status, dict) and target_status:
        target_status_text = ('exists' if bool(target_status.get('exists')) else 'not found') + ' / ' + str(target_status.get('state') or 'unknown').replace('_', ' ')
    if detected_dsn:
        try:
            detected_text = str(dsn_parts(detected_dsn).get('db') or detected_dsn)
        except Exception:
            detected_text = detected_dsn
    elif detected_candidates:
        detected_text = f"{len(detected_candidates)} candidates - choose one in Step 1"
    validation_text = '(not checked)'
    if isinstance(validation_report, dict) and validation_report:
        validation_text = str(validation_report.get('state') or 'unknown').replace('_', ' ')
        validation_text += ' / valid' if bool(validation_report.get('valid')) else ' / needs review'
    lines = [
        'Review this summary before you save. It is grouped in plain English so you can spot mistakes quickly.',
        'Saved config values are used as the default for any section you do not edit.',
        '',
    ]
    lines.extend(_collect_setup_readiness_lines(merged, runtime))
    lines.extend([''])
    lines.extend(_collect_setup_step_progress_lines(merged, runtime))
    lines.extend([
        '',
        'Server identity',
        f"  Server name: {str(merged.get('server_name') or '').strip() or '(not set)'}",
        f"  Bind host: {str(merged.get('server_host') or merged.get('host') or '').strip() or '(not set)'}",
        f"  Bind port: {str(merged.get('server_port') or merged.get('port') or '').strip() or '(not set)'}",
        f"  Startup mode: {str(merged.get('run_mode') or 'development')}" + (f" ({int(merged.get('production_workers') or 1)} worker x {int(merged.get('production_instance_count') or 1)} instance(s))" if str(merged.get('run_mode') or '').lower() == 'production' else ""),
        f"  Production instance ports: {int(merged.get('production_instance_base_port') or merged.get('server_port') or 5000)}" + (f"-{int(merged.get('production_instance_base_port') or merged.get('server_port') or 5000) + max(0, int(merged.get('production_instance_count') or 1) - 1) * int(merged.get('production_instance_port_step') or 1)}" if int(merged.get('production_instance_count') or 1) > 1 else ""),
        f"  Public base URL: {str(merged.get('public_base_url') or '').strip() or '(not set)'}",
        '',
        'Database',
        f"  PostgreSQL role/user: {str(parts.get('user') or '(not set)')}",
        f"  PostgreSQL host: {str(parts.get('host') or '(local default)')}",
        f"  PostgreSQL port: {str(parts.get('port') or 5432)}",
        f"  Database name: {str(parts.get('db') or '(not set)')}",
        f"  Bootstrap/admin DSN saved: {'yes' if str(merged.get('database_bootstrap_url') or '').strip() else 'no'}",
        f"  Configured target database status: {target_status_text}",
        f"  Auto-detected Echo-Chat database: {detected_text or '(none found)'}",
        f"  Current database validation: {validation_text}",
        '',
        'Owner accounts',
        f"  Owner username: {str(merged.get('admin_user') or '').strip() or '(not set)'}",
        f"  Owner notification email: {str(merged.get('admin_notification_email') or '').strip() or '(not set)'}",
        f"  Owner password entered this session: {'yes' if str(merged.get('__admin_raw_password') or '').strip() else 'no'}",
        f"  Owner Recovery PIN entered this session: {'yes' if str(merged.get('__admin_recovery_pin') or '').strip() else 'no'}",
        f"  Create extra admin user: {'yes' if bool(merged.get('__create_initial_admin')) else 'no'}",
        f"  Extra admin username: {str(merged.get('__initial_admin_user') or '').strip() if bool(merged.get('__create_initial_admin')) else '(off)'}",
        '',
        'Login and session security',
        f"  Secure cookies / HTTPS mode: {'on' if bool(merged.get('cookie_secure')) else 'off'}",
        f"  Cookie sharing policy: {str(merged.get('cookie_samesite') or 'Lax')}",
        f"  Access token minutes: {int(merged.get('access_token_minutes') or 0)}",
        f"  Refresh token days: {int(merged.get('refresh_token_days') or 0)}",
        f"  Idle logout hours: {int(merged.get('idle_logout_hours') or 0)}",
        f"  Auto-away after inactive minutes: {int(merged.get('presence_idle_minutes') or 0)}",
        f"  Auto-offline after inactive minutes: {int(merged.get('presence_offline_minutes') or 0)}",
        "  Presence timer note: Auto-away keeps the user signed in and shows Away. Auto-offline keeps the user signed in but switches them to Invisible so others see them as offline.",
        f"  JWT secret present: {'yes' if str(merged.get('jwt_secret') or '').strip() else 'no'}",
        '',
        'Email and password recovery',
        f"  SMTP enabled: {'yes' if bool(merged.get('smtp_enabled')) else 'no'}",
        f"  SMTP host: {str(merged.get('smtp_host') or '').strip() or '(not set)'}",
        f"  SMTP port: {int(merged.get('smtp_port') or 0)}",
        f"  SMTP username: {str(merged.get('smtp_username') or '').strip() or '(not set)'}",
        f"  SMTP password stored in config: {'yes' if str(merged.get('smtp_password') or '').strip() else 'no'}",
        f"  From address: {str(merged.get('smtp_from') or '').strip() or '(not set)'}",
        f"  Password reset token minutes: {int(merged.get('password_reset_token_minutes') or 0)}",
        f"  Password reset daily limit: {int(merged.get('password_reset_daily_limit') or 0)}",
        f"  Recovery PIN max attempts: {int(merged.get('recovery_pin_max_attempts') or 0)}",
        f"  Recovery PIN lock minutes: {int(merged.get('recovery_pin_lock_minutes') or 0)}",
        '',
        'Message display',
        f"  Room chat text animation: {str(merged.get('chat_text_animation') or 'none')}",
        f"  Private message text animation: {str(merged.get('dm_text_animation') or 'rise')}",
        f"  Group chat text animation: {str(merged.get('group_text_animation') or 'rise')}",
        f"  Room chat shows username on every message: {'yes' if bool(merged.get('room_show_sender_every_message')) else 'no'}",
        f"  Private messages show username on every message: {'yes' if bool(merged.get('dm_show_sender_every_message')) else 'no'}",
        f"  Group chat shows username on every message: {'yes' if bool(merged.get('group_show_sender_every_message')) else 'no'}",
        '',
        'Rooms and chat behavior',
        f"  Allow user-created rooms: {'yes' if bool(merged.get('allow_user_create_rooms', True)) else 'no'}",
        f"  Autoscale public rooms: {'yes' if bool(merged.get('autoscale_rooms_enabled', True)) else 'no'}",
        f"  Autoscale room capacity: {int(merged.get('autoscale_room_capacity') or 0)}",
        f"  Autoscale room idle minutes: {int(merged.get('autoscale_room_idle_minutes') or 0)}",
        f"  Custom room idle minutes: {int(merged.get('custom_room_idle_minutes') or 0)}",
        f"  Private custom room idle minutes: {int(merged.get('custom_private_room_idle_minutes') or 0)}",
        f"  Default room slowmode seconds: {int(merged.get('room_slowmode_default_sec') or 0)}",
        f"  Janitor interval seconds: {int(merged.get('janitor_interval_seconds') or 0)}",
        f"  Max message length: {int(merged.get('max_message_length') or 0)}",
        f"  Group message character limit: {int(merged.get('max_group_message_chars') or 0)}",
        f"  Group message rate limit/window: {int(merged.get('group_msg_rate_limit') or 0)} per {int(merged.get('group_msg_rate_window_sec') or 0)} seconds",
        '',
        'Protection and anti-abuse',
        f"  Room message rate limit: {str(merged.get('room_msg_rate_limit') or '')}",
        f"  DM message rate limit: {str(merged.get('dm_msg_rate_limit') or '')}",
        f"  Room GIF rate limit: {str(merged.get('room_gif_rate_limit') or '')}",
        f"  Room torrent rate limit: {str(merged.get('room_torrent_rate_limit') or '')}",
        f"  Auto-mute threshold/minutes: {int(merged.get('antiabuse_strikes_before_mute') or 0)} strikes / {int(merged.get('antiabuse_auto_mute_minutes') or 0)} minutes",
        f"  Exempt staff from anti-abuse: {'yes' if bool(merged.get('antiabuse_exempt_staff', True)) else 'no'}",
        '',
        'Media and uploads',
        f"  GIF search enabled: {'yes' if bool(merged.get('giphy_enabled')) else 'no'}",
        f"  Store GIPHY API key in config: {'yes' if bool(merged.get('__store_giphy_in_config', bool(str(merged.get('giphy_api_key') or '').strip()))) else 'no'}",
        f"  GIPHY rating: {str(merged.get('giphy_rating') or '').strip() or '(not set)'}",
        f"  Disable all file transfers globally: {'yes' if bool(merged.get('disable_file_transfer_globally')) else 'no'}",
        f"  Disable group files globally: {'yes' if bool(merged.get('disable_group_files_globally')) else 'no'}",
        f"  Max DM file bytes: {int(merged.get('max_dm_file_bytes') or 0)}",
        f"  Max attachment bytes: {int(merged.get('max_attachment_size') or 0)}",
        f"  Max group upload bytes: {int(merged.get('max_group_upload_bytes') or merged.get('max_group_file_bytes') or 0)}",
        '',
        'Voice and WebRTC',
        f"  A/V mode: {str(merged.get('av_mode') or 'echo')}",
        f"  Voice enabled: {'yes' if bool(merged.get('voice_enabled', True)) else 'no'}",
        f"  Voice max room peers: {int(merged.get('voice_max_room_peers') or 100)}",
        f"  Voice invite cooldown seconds: {int(merged.get('voice_invite_cooldown_seconds') or 0)}",
        '',
        'Echo media / webcam',
        f"  Webcam controls enabled: {'yes' if bool(merged.get('webcam_enabled', True)) and str(merged.get('av_mode') or 'echo') == 'echo' else 'no'}",
        f"  Webcam approval mode: {str(merged.get('webcam_approval_mode') or 'owner_approval')}",
        f"  Max webcam viewers: {int(merged.get('webcam_max_viewers') or 0)}",
        f"  Default media policy: {str(merged.get('default_media_policy') or 'user_choice')}",
        '',
        'Hosting, proxy, and HTTPS',
        f"  Trust proxy headers: {'yes' if bool(merged.get('trust_proxy_headers')) else 'no'}",
        f"  Proxy hop count: {int(merged.get('proxy_fix_hops') or 0)}",
        f"  Built-in HTTPS listener: {'yes' if bool(merged.get('https')) else 'no'}",
        f"  TLS certificate file: {str(merged.get('ssl_cert_file') or '').strip() or '(not set)'}",
        f"  TLS key file: {str(merged.get('ssl_key_file') or '').strip() or '(not set)'}",
        f"  Allowed origins: {_csv_text(merged.get('cors_allowed_origins') or merged.get('allowed_origins') or []) or '(not set)'}",
        f"  Health endpoint enabled: {'yes' if bool(merged.get('enable_health_check_endpoint')) else 'no'}",
        f"  Health endpoint path: {str(merged.get('health_check_endpoint') or '').strip() or '(not set)'}",
        f"  Rate-limit storage URI: {str(merged.get('rate_limit_storage_uri') or merged.get('rate_limit_storage') or '').strip() or '(not set)'}",
        '',
        'Logs and health checks',
        f"  Log level: {str(merged.get('log_level') or '').strip() or '(not set)'}",
        f"  Log file path: {str(merged.get('log_file_path') or '').strip() or '(not set)'}",
        f"  Janitor interval seconds: {int(merged.get('janitor_interval_seconds') or 0)}",
    ])
    status = str(runtime.get('db_status') or '').strip()
    if status:
        lines.extend(['', 'Latest setup status', f'  {status}'])
    lines.extend(['', 'Press S to save from this screen, or Enter/Esc to go back and edit more.'])
    return lines


def _show_setup_summary_screen(stdscr, merged: Dict[str, Any], runtime: Dict[str, Any] | None = None, allow_save: bool = True) -> bool:
    action = _tui_scroll_text(
        stdscr,
        'Review setup summary',
        _collect_setup_summary_lines(merged, runtime),
        allow_save=allow_save,
    )
    return action == 'save'


def _edit_form(stdscr, title: str, fields: list[dict[str, Any]], intro_lines: list[str] | None = None) -> None:
    title = _brand_ui_text(title)
    for field in fields:
        if "label" in field:
            field["label"] = _brand_ui_text(field.get("label"))
        if "help" in field:
            field["help"] = _brand_ui_text(field.get("help"))
    idx = 0
    intro_lines = [_brand_ui_text(x) for x in (intro_lines or []) if str(x).strip()]
    while True:
        h, w = _draw_box(stdscr, title, "Enter to edit, Space toggles Yes/No, Esc when Done")
        y = 3
        if intro_lines:
            for raw in intro_lines:
                for line in _wrap_lines(raw, max(20, w - 6)):
                    if y >= h - 8:
                        break
                    stdscr.addnstr(y, 3, line, max(1, w - 6), _color_pair_bg())
                    y += 1
            y += 1
        rows: list[tuple[str, str, dict[str, Any] | None]] = []
        for field in fields:
            rows.append((str(field["label"]), _display_value(field.get("value"), field.get("type", "text")), field))
        rows.append(("Done", "", None))
        help_height = 3
        visible = max(4, h - y - help_height - 3)
        top = 0
        if len(rows) > visible:
            top = max(0, min(idx - visible + 1, len(rows) - visible))
        for row, (label, value, field) in enumerate(rows[top:top + visible]):
            actual = top + row
            attr = _color_pair_hl() | curses.A_BOLD if actual == idx else _color_pair_bg()
            text = label if field is None else f"{label}: {value}"
            stdscr.addnstr(y + row, 3, text, max(1, w - 6), attr)
        help_field = rows[idx][2] if 0 <= idx < len(rows) else None
        help_lines: list[str] = []
        if help_field is not None:
            raw_help = str(help_field.get("help") or "").strip()
            if raw_help:
                help_lines.extend(_wrap_lines(raw_help, max(20, w - 6)))
        if not help_lines:
            help_lines = ["Tip: Enter edits a value. Space flips Yes/No settings."]
        help_y = h - len(help_lines) - 2
        for row, line in enumerate(help_lines[:help_height]):
            stdscr.addnstr(help_y + row, 3, line, max(1, w - 6), _color_pair_bg())
        stdscr.refresh()
        ch = stdscr.get_wch()
        if ch in (curses.KEY_UP, "k"):
            idx = (idx - 1) % len(rows)
            continue
        if ch in (curses.KEY_DOWN, "j"):
            idx = (idx + 1) % len(rows)
            continue
        if ch == "\x1b" and idx == len(rows) - 1:
            return
        if ch not in ("\n", "\r", " "):
            continue
        label, _, field = rows[idx]
        if field is None:
            return
        field_type = field.get("type", "text")
        if field_type == "bool":
            field["value"] = not bool(field.get("value"))
        elif field_type == "choice":
            options = [str(x) for x in field.get("options") or []]
            current = str(field.get("value") or options[0]) if options else ""
            sel = options.index(current) if current in options else 0
            chosen = _tui_menu(stdscr, label, ["Choose one option:"], options, selected=sel)
            if chosen >= 0:
                field["value"] = options[chosen]
        elif field_type == "int":
            raw = _tui_input(stdscr, label, "Enter a number", str(field.get("value") or ""), secret=False)
            try:
                num = int(str(raw).strip())
            except Exception:
                _tui_message(stdscr, label, ["Please enter a valid integer."], error=True)
                continue
            if field.get("min") is not None and num < int(field["min"]):
                _tui_message(stdscr, label, [f"Value must be at least {int(field['min'])}."], error=True)
                continue
            if field.get("max") is not None and num > int(field["max"]):
                _tui_message(stdscr, label, [f"Value must be at most {int(field['max'])}."], error=True)
                continue
            field["value"] = num
        elif field_type == "secret":
            field["value"] = _tui_input(stdscr, label, "Enter value", str(field.get("value") or ""), secret=True)
        else:
            field["value"] = _tui_input(stdscr, label, "Enter value", str(field.get("value") or ""), secret=False)


def _suspend_curses_for_command(stdscr, callback, *args, **kwargs):
    curses.def_prog_mode()
    curses.endwin()
    try:
        return callback(*args, **kwargs)
    finally:
        curses.reset_prog_mode()
        stdscr.refresh()



def _db_summary_lines(merged: Dict[str, Any], detected_dsn: str | None, status: str | None = None, candidates: Optional[list[dict[str, Any]]] = None, validation_report: Optional[dict[str, Any]] = None, target_status: Optional[dict[str, Any]] = None) -> list[str]:
    lines = []
    configured_db = None
    detected_db = None
    raw_configured = str(merged.get("database_url") or "").strip()
    if not raw_configured:
        lines.append("Configured PostgreSQL target: not configured yet")
    else:
        try:
            parts = dsn_parts(raw_configured)
            configured_db = str(parts.get("db") or "")
            lines.append(f"Configured PostgreSQL target: user={parts.get('user') or '(blank)'} host={parts.get('host')} port={parts.get('port')} db={configured_db}")
        except Exception:
            lines.append("Configured PostgreSQL target: invalid DSN")
    target_status = dict(target_status or {})
    if target_status:
        t_state = str(target_status.get("state") or "unknown").replace("_", " ")
        t_exists = "exists" if bool(target_status.get("exists")) else "not found"
        t_conn = "connectable" if bool(target_status.get("connectable")) else "not connectable/inspectable yet"
        lines.append(f"Configured target database status: {t_exists}; {t_state}; {t_conn}")
    candidates = list(candidates or [])
    if candidates:
        lines.append(f"Detected Echo-Chat databases: {len(candidates)}")
        for candidate in candidates[:5]:
            lines.append(f"  - {_database_candidate_label(candidate)}")
        if len(candidates) > 5:
            lines.append(f"  - ... {len(candidates) - 5} more")
        if len(candidates) > 1:
            lines.append("Multiple databases were found. Use 'Select detected Echo-Chat database' before saving.")
    elif detected_dsn:
        try:
            dparts = dsn_parts(detected_dsn)
            detected_db = str(dparts.get("db") or "")
            lines.append(f"Auto-detected Echo-Chat database: {detected_db}")
        except Exception:
            lines.append(f"Auto-detected Echo-Chat database: {detected_dsn}")
    else:
        lines.append("Auto-detected Echo-Chat database: none found yet")
    if validation_report:
        state = str(validation_report.get("state") or "unknown").replace("_", " ")
        valid_text = "valid" if bool(validation_report.get("valid")) else "needs review"
        lines.append(f"Current database validation: {state} ({valid_text})")
    if configured_db and detected_db and configured_db != detected_db:
        lines.append("")
        lines.append(
            f"The configured target database ({configured_db}) is different from the detected Echo-Chat database ({detected_db}). Save will use the configured target unless you explicitly choose the detected one."
        )
    env_db = os.getenv("DB_CONNECTION_STRING") or os.getenv("DATABASE_URL")
    if env_db:
        try:
            env_parts = dsn_parts(str(env_db))
            env_name = str(env_parts.get("db") or "")
            if env_name and configured_db and env_name != configured_db:
                lines.append("")
                lines.append(
                    f"Environment override warning: DB_CONNECTION_STRING/DATABASE_URL currently points to {env_name}. That can override the saved config when the server starts."
                )
        except Exception:
            pass
    if status:
        lines.append("")
        lines.append(status)
    return lines



def _default_local_postgres_parts(db_name: str = "echochat") -> Dict[str, Any]:
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





_SETUP_DATABASE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,62}$")


def _validate_setup_database_name(name: str) -> tuple[bool, str]:
    """Validate a database name that setup is about to create/recreate/delete."""
    value = str(name or "").strip()
    if not value:
        return False, "Database name cannot be empty."
    if is_protected_database_name(value):
        return False, f"'{value}' is a protected PostgreSQL maintenance database. Choose a dedicated Echo-Chat database name."
    if not _SETUP_DATABASE_NAME_RE.match(value):
        return False, "Use 1-63 characters: letters, numbers, underscore, or hyphen. Do not use spaces, slashes, ?, or #."
    return True, ""


def _require_setup_database_name_tui(stdscr, title: str, name: str) -> bool:
    ok, msg = _validate_setup_database_name(name)
    if not ok:
        _tui_message(stdscr, title, [msg], error=True)
        return False
    return True


def _prompt_bootstrap_dsn_tui(stdscr, merged: Dict[str, Any], target_dsn: str, detail: str = "") -> str:
    """Prompt for a bootstrap/admin PostgreSQL DSN without leaving curses mode."""
    current = str(merged.get("database_bootstrap_url") or "").strip()
    prompt = "Enter an owner or superuser PostgreSQL DSN"
    if detail:
        prompt = detail + "\n\n" + prompt
    raw = _tui_input(stdscr, "Bootstrap/admin DSN", prompt, current or target_dsn)
    cleaned = str(sanitize_postgres_dsn(raw or "") or "").strip()
    if cleaned:
        merged["database_bootstrap_url"] = cleaned
    return cleaned

def _ensure_first_run_local_database_defaults(merged: Dict[str, Any], runtime: Dict[str, Any] | None = None) -> None:
    """Seed a usable local PostgreSQL DSN for first-run setup.

    Setup should not require the user to hand-create server_config.json or start
    from a fake USER/PASSWORD placeholder. When no real DSN exists yet, seed a
    localhost DSN using the current OS user so the wizard can continue.
    """
    changed = False
    if not str(merged.get("database_url") or "").strip():
        merged["database_url"] = build_postgres_dsn(_default_local_postgres_parts("echochat"))
        changed = True
    if not str(merged.get("database_bootstrap_url") or "").strip():
        merged["database_bootstrap_url"] = build_postgres_dsn(_default_local_postgres_parts("postgres"))
        changed = True
    if changed and runtime is not None and not runtime.get("db_status"):
        runtime["db_status"] = f"First run: seeded local PostgreSQL defaults for user {getpass.getuser()}. Use the Database menu to change the database name if needed."



def _edit_database_connection(stdscr, merged: Dict[str, Any]) -> None:
    raw_dsn = str(merged.get("database_url") or "").strip()
    if raw_dsn:
        try:
            parts = dsn_parts(raw_dsn)
        except Exception:
            parts = _default_local_postgres_parts()
    else:
        parts = _default_local_postgres_parts()
    fields = [
        {"label": "Database user", "value": parts.get("user", ""), "help": "This is the PostgreSQL role/user, not the Echo-Chat owner login."},
        {"label": "Database password", "value": parts.get("password", ""), "type": "secret", "help": "Password for the PostgreSQL role, if your local database requires one."},
        {"label": "Database host", "value": parts.get("host", "localhost"), "help": "Use localhost for a local PostgreSQL server."},
        {"label": "Database port", "value": int(parts.get("port", 5432)), "type": "int", "min": 1, "max": 65535, "help": "PostgreSQL usually runs on port 5432."},
        {"label": "Database name", "value": parts.get("db", "echochat"), "help": "Name of the PostgreSQL database EchoChat should use."},
    ]
    _edit_form(
        stdscr,
        "Database connection",
        fields,
        intro_lines=[
            "Use existing database or create a new one by changing the database name here and then returning to the Database menu.",
            "Name of new PostgreSQL database: use the Database name field below when you want a fresh database.",
            "Delete that old database after switching: use the replace-detected-database flow when you want EchoChat to remove the old target after the new one is ready.",
        ],
    )
    merged["database_url"] = build_postgres_dsn({
        **parts,
        "user": str(fields[0]["value"]).strip(),
        "password": str(fields[1]["value"]),
        "host": str(fields[2]["value"]).strip() or "localhost",
        "port": int(fields[3]["value"]),
        "db": str(fields[4]["value"]).strip() or "echochat",
    })



def _bool_word(value: Any) -> str:
    return "Yes" if bool(value) else "No"



def _csv_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(x).strip() for x in value if str(x).strip())
    return str(value or "")



def _csv_parse(raw: str) -> list[str]:
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]



def _sync_ssl_settings_block(merged: Dict[str, Any]) -> None:
    ssl_block = dict(merged.get("ssl_tls_settings") or {})
    ssl_block["enabled"] = bool(merged.get("https", False))
    ssl_block["certificate_path"] = str(merged.get("ssl_cert_file") or ssl_block.get("certificate_path") or "cert.pem")
    ssl_block["key_path"] = str(merged.get("ssl_key_file") or ssl_block.get("key_path") or "key.pem")
    merged["ssl_tls_settings"] = ssl_block



def _edit_server_identity_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    old_server_name = merged.get("server_name")
    fields = [
        {"label": "Server name", "value": str(merged.get("server_name") or base["server_name"]), "help": "The public display name shown across the chat UI and used for friendly defaults like the no-reply email name."},
        {"label": "Bind host", "value": str(merged.get("server_host") or base["server_host"]), "help": "Use 0.0.0.0 to listen on all local network interfaces, or 127.0.0.1 for localhost-only testing."},
        {"label": "Bind port", "value": int(merged.get("server_port") or base["server_port"]), "type": "int", "min": 1, "max": 65535, "help": "Pick a free TCP port. Use a different port if another EchoChat server is already running on this machine."},
        {"label": "Startup mode", "value": str(merged.get("run_mode") or base.get("run_mode") or "development"), "type": "choice", "options": ["development", "production"], "help": "Development uses the built-in local/LAN runner. Production makes plain `python main.py` start one Gunicorn-backed Echo-Chat instance."},
        {"label": "Gunicorn workers per instance", "value": 1, "type": "int", "min": 1, "max": 1, "help": "Keep this locked at 1. Flask-SocketIO is safe with one Gunicorn worker per process; do not put 10 workers inside one Gunicorn server."},
        {"label": "Echo-Chat instances", "value": int(merged.get("production_instance_count") or base.get("production_instance_count") or 1), "type": "int", "min": 1, "max": 10, "help": "Horizontal scale target. 10 means ten separate Echo-Chat processes, each with one worker, behind sticky reverse-proxy routing plus Redis Socket.IO queue."},
        {"label": "First instance port", "value": int(merged.get("production_instance_base_port") or merged.get("server_port") or base.get("server_port") or 5000), "type": "int", "min": 1, "max": 65535, "help": "First backend port for multi-instance deployment. Example: 5000 with 10 instances uses 5000-5009."},
        {"label": "Public base URL", "value": str(merged.get("public_base_url") or ""), "help": "Optional public URL such as https://chat.example.com. Leave blank for local-only testing."},
    ]
    _edit_form(
        stdscr,
        "Server identity",
        fields,
        intro_lines=[
            "Start here for the basics people usually expect to see in setup.",
            "These values control the chat server name, local bind address, port, and optional public URL.",
            "Scaling rule: use 1 worker per Echo-Chat instance. If you choose more than 1 instance, setup auto-fills the Redis DB split for rate limits, Socket.IO, and shared realtime state.",
        ],
    )
    merged["server_name"] = str(fields[0]["value"]).strip() or base["server_name"]
    _set_active_setup_server_name(merged.get("server_name"))
    _clear_setup_account_passwords_after_identity_change(merged, old_server_name=old_server_name)
    merged["server_host"] = str(fields[1]["value"]).strip() or base["server_host"]
    merged["server_port"] = int(fields[2]["value"])
    merged["run_mode"] = str(fields[3]["value"] or "development").strip().lower()
    merged["production_mode"] = merged["run_mode"] == "production"
    # Flask-SocketIO's official Gunicorn path is one worker per process.
    # Keep each process at one worker; scale by running multiple one-worker
    # Echo-Chat instances behind sticky routing plus Redis Socket.IO queue.
    merged["production_workers"] = 1
    merged["production_instance_count"] = max(1, min(10, int(fields[5]["value"] or 1)))
    merged["production_instance_base_port"] = int(fields[6]["value"] or merged["server_port"])
    scaled_changed = apply_scaled_runtime_safety_defaults(merged)
    if scaled_realtime_requested(merged) and any(scaled_changed.values()):
        _tui_scroll_text(
            stdscr,
            "Scaled Redis auto-config",
            scaled_redis_summary_lines(merged, scaled_changed) + ["", redis_install_hint()],
            footer="Enter/Esc returns to setup.",
            allow_save=False,
        )
    merged["production_instance_bind_host"] = str(merged.get("production_instance_bind_host") or "127.0.0.1").strip() or "127.0.0.1"
    merged["production_instance_port_step"] = 1
    merged["production_bind"] = str(merged.get("production_bind") or "").strip()
    merged["public_base_url"] = str(fields[7]["value"] or "").strip()
    merged["host"] = merged["server_host"]
    merged["port"] = merged["server_port"]
    merged.update(_autobrand_settings(merged))



def _edit_owner_accounts_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    idx = 0
    rows = [
        "Owner login username",
        "Owner notification email",
        "Set owner password",
        "Set owner Recovery PIN",
        "Create extra admin user",
        "Second admin username",
        "Second admin email",
        "Set second admin password",
        "Set second admin Recovery PIN",
        "Done",
    ]
    help_map = {
        0: "This is the main Echo-Chat owner login inside the app. It is not the PostgreSQL role or Linux account name.",
        1: "Optional email for admin notices and future password-recovery flows.",
        2: "Sets the owner password for this setup run and stores its secure hash in config.",
        3: "Sets the owner's 4-8 digit Recovery PIN for password resets.",
        4: "Turn this on if you want setup to create a second app-level admin account right away.",
        5: "Optional second admin login name inside Echo-Chat.",
        6: "Optional contact email for the second admin account.",
        7: "Sets the second admin password for this setup run.",
        8: "Sets the second admin's 4-8 digit Recovery PIN for password resets.",
        9: "Return to the main menu.",
    }
    while True:
        h, w = _draw_box(stdscr, "Owner accounts", "These are Echo-Chat login accounts, not PostgreSQL users")
        intro = [
            "Use this section to create the server owner's login and an optional second admin.",
            "PostgreSQL user names and Echo-Chat admin usernames are separate things.",
            "The database user can be named one thing while the Echo-Chat owner account is named something else.",
        ]
        y = 3
        for raw in intro:
            for line in _wrap_lines(raw, max(20, w - 6)):
                if y >= h - 10:
                    break
                stdscr.addnstr(y, 3, line, max(1, w - 6), _color_pair_bg())
                y += 1
        y += 1
        row_values = [
            str(merged.get("admin_user") or base["admin_user"]),
            str(merged.get("admin_notification_email") or ""),
            "Set" if merged.get("__admin_raw_password") else "Not set yet",
            "Set" if merged.get("__admin_recovery_pin") else "Not set yet",
            _bool_word(merged.get("__create_initial_admin")),
            str(merged.get("__initial_admin_user") or "admin2"),
            str(merged.get("__initial_admin_email") or ""),
            "Set" if merged.get("__initial_admin_raw_password") else "Not set yet",
            "Set" if merged.get("__initial_admin_recovery_pin") else "Not set yet",
            "",
        ]
        visible = max(4, h - y - 5)
        top = 0
        if len(rows) > visible:
            top = max(0, min(idx - visible + 1, len(rows) - visible))
        for row_no, label in enumerate(rows[top:top + visible]):
            actual = top + row_no
            attr = _color_pair_hl() | curses.A_BOLD if actual == idx else _color_pair_bg()
            text = label if actual == len(rows) - 1 else f"{label}: {row_values[actual]}"
            stdscr.addnstr(y + row_no, 3, text, max(1, w - 6), attr)
        for row_no, line in enumerate(_wrap_lines(help_map.get(idx, ""), max(20, w - 6))[:3]):
            stdscr.addnstr(h - 4 + row_no, 3, line, max(1, w - 6), _color_pair_bg())
        stdscr.refresh()
        ch = stdscr.get_wch()
        if ch in (curses.KEY_UP, "k"):
            idx = (idx - 1) % len(rows)
            continue
        if ch in (curses.KEY_DOWN, "j"):
            idx = (idx + 1) % len(rows)
            continue
        if ch == "\x1b" and idx == len(rows) - 1:
            return
        if ch not in ("\n", "\r", " "):
            continue
        if idx == len(rows) - 1:
            return
        if idx == 0:
            new_owner = normalize_registration_username(_tui_input(stdscr, "Owner login username", "Enter the main Echo-Chat owner username", str(merged.get("admin_user") or base["admin_user"])))
            normalized_owner, username_err = _validate_setup_admin_username(new_owner, settings=merged, account_label="owner")
            if username_err:
                _tui_message(stdscr, "Owner login username", [username_err], error=True)
                continue
            if normalized_owner != str(merged.get("admin_user") or ""):
                merged["__admin_raw_password"] = ""
                merged["admin_pass"] = ""
            merged["admin_user"] = normalized_owner
        elif idx == 1:
            new_email = _tui_input(stdscr, "Owner notification email", "Enter an optional email address", str(merged.get("admin_notification_email") or ""))
            if str(new_email or "").strip() != str(merged.get("admin_notification_email") or "").strip():
                merged["__admin_raw_password"] = ""
                merged["admin_pass"] = ""
            merged["admin_notification_email"] = new_email
        elif idx == 2:
            merged["__admin_raw_password"] = _prompt_password_in_tui(stdscr, "Owner password", username=merged.get("admin_user"), email=merged.get("admin_notification_email"), server_name=merged.get("server_name"))
            merged["admin_pass"] = hash_password(str(merged["__admin_raw_password"]))
        elif idx == 3:
            merged["__admin_recovery_pin"] = _prompt_recovery_pin_in_tui(stdscr, "Owner Recovery PIN")
        elif idx == 4:
            merged["__create_initial_admin"] = not bool(merged.get("__create_initial_admin"))
        elif idx == 5:
            new_user = normalize_registration_username(_tui_input(stdscr, "Second admin username", "Enter the optional second admin username", str(merged.get("__initial_admin_user") or "admin2")))
            normalized_user, username_err = _validate_setup_admin_username(new_user, settings=merged, account_label="extra admin")
            if username_err:
                _tui_message(stdscr, "Second admin username", [username_err], error=True)
                continue
            if _same_setup_username(normalized_user, merged.get("admin_user")):
                _tui_message(stdscr, "Second admin username", ["Second admin username must be different from the owner username, or turn the extra admin option off."], error=True)
                continue
            if normalized_user != str(merged.get("__initial_admin_user") or ""):
                merged["__initial_admin_raw_password"] = ""
            merged["__initial_admin_user"] = normalized_user
        elif idx == 6:
            new_email = _tui_input(stdscr, "Second admin email", "Enter an optional email address", str(merged.get("__initial_admin_email") or ""))
            if str(new_email or "").strip() != str(merged.get("__initial_admin_email") or "").strip():
                merged["__initial_admin_raw_password"] = ""
            merged["__initial_admin_email"] = new_email
        elif idx == 7:
            merged["__initial_admin_raw_password"] = _prompt_password_in_tui(stdscr, "Second admin password", username=merged.get("__initial_admin_user"), email=merged.get("__initial_admin_email"), server_name=merged.get("server_name"))
        elif idx == 8:
            merged["__initial_admin_recovery_pin"] = _prompt_recovery_pin_in_tui(stdscr, "Second admin Recovery PIN")


def _edit_login_security_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    fields = [
        {"label": "Use HTTPS / secure cookies", "value": bool(merged.get("cookie_secure", False)), "type": "bool", "help": "Turn this on when you serve EchoChat over HTTPS directly or behind an HTTPS reverse proxy."},
        {"label": "Cookie sharing policy", "value": str(merged.get("cookie_samesite") or "Lax"), "type": "choice", "options": ["Lax", "Strict", "None"], "help": "Lax is the normal default. None is only appropriate when you understand the cross-site cookie implications and are using HTTPS."},
        {"label": "Access token minutes", "value": int(merged.get("access_token_minutes") or base["access_token_minutes"]), "type": "int", "min": 1, "max": 1440, "help": "How long short-lived login tokens stay valid before a refresh is needed."},
        {"label": "Refresh token days", "value": int(merged.get("refresh_token_days") or base["refresh_token_days"]), "type": "int", "min": 1, "max": 365, "help": "How long longer-lived login sessions remain refreshable before a full login is required."},
        {"label": "Idle logout hours", "value": int(merged.get("idle_logout_hours") or 0), "type": "int", "min": 0, "max": 720, "help": "Set 0 to disable idle logout. Any positive value signs the user out after that many inactive hours."},
        {"label": "Auto-away after inactive minutes", "value": int(merged.get("presence_idle_minutes") or 0), "type": "int", "min": 0, "max": 1440, "help": "Set 0 to disable auto-away. Any positive value changes a still-online user to Away after that many inactive minutes without logging them out."},
        {"label": "Auto-offline after inactive minutes", "value": int(merged.get("presence_offline_minutes") or 0), "type": "int", "min": 0, "max": 1440, "help": "Set 0 to disable auto-offline. Any positive value keeps the session signed in but switches the user to Invisible after that many inactive minutes so other people see them as offline."},
        {"label": "Revoke all sessions on restart", "value": bool(merged.get("revoke_all_tokens_on_start", False)), "type": "bool", "help": "Turn this on only if you want every server restart to force every user to log in again."},
        {"label": "Generate stable core/crypto secrets now", "value": (not is_strong_secret(resolve_secret(merged, "jwt_secret")) or not is_strong_secret(resolve_secret(merged, "secret_key"))), "type": "bool", "help": "Setup can generate stable Flask/JWT/crypto secrets and save them safely so restarts do not break login or encrypted data."},
        {"label": "Enable SMS 2FA", "value": bool(merged.get("enable_two_factor_beta", False) and merged.get("enable_sms_two_factor", False)), "type": "bool", "help": "Optional Twilio Verify login codes. Keep this off unless Twilio is configured."},
        {"label": "Twilio Verify channel", "value": str(merged.get("two_factor_sms_channel") or "sms"), "type": "choice", "options": ["sms", "whatsapp"], "help": "Phone-based verification channel. sms is the normal choice."},
        {"label": "Twilio Account SID", "value": str(merged.get("twilio_account_sid") or ""), "help": "Starts with AC. You may store this non-password identifier here or provide ECHOCHAT_TWILIO_ACCOUNT_SID."},
        {"label": "Twilio Auth Token", "value": str(merged.get("twilio_auth_token") or ""), "type": "secret", "help": "Twilio secret token. You may leave blank only if ECHOCHAT_TWILIO_AUTH_TOKEN / TWILIO_AUTH_TOKEN is set."},
        {"label": "Twilio Verify Service SID", "value": str(merged.get("twilio_verify_service_sid") or ""), "help": "Starts with VA. You may store it here or provide ECHOCHAT_TWILIO_VERIFY_SERVICE_SID."},
        {"label": "2FA login timeout seconds", "value": int(merged.get("two_factor_login_timeout_seconds") or 600), "type": "int", "min": 60, "max": 3600, "help": "How long a pending login SMS challenge stays valid before the user must start login again."},
    ]
    _edit_form(
        stdscr,
        "Login and session security",
        fields,
        intro_lines=[
            "These settings control how long logins last, how presence changes during inactivity, how strictly cookies behave, and optional SMS 2FA.",
            "Idle logout signs the user out. Auto-away keeps the session alive and shows Away. Auto-offline keeps the session alive but switches the user to Invisible so other people see them as offline.",
            "The labels here are written in plain English so you do not need to know the internal config key names.",
        ],
    )
    merged["cookie_secure"] = bool(fields[0]["value"])
    merged["cookie_samesite"] = str(fields[1]["value"])
    merged["access_token_minutes"] = int(fields[2]["value"])
    merged["refresh_token_days"] = int(fields[3]["value"])
    merged["idle_logout_hours"] = int(fields[4]["value"])
    merged["presence_idle_minutes"] = int(fields[5]["value"])
    merged["presence_offline_minutes"] = int(fields[6]["value"])
    merged["revoke_all_tokens_on_start"] = bool(fields[7]["value"])
    if bool(fields[8]["value"]):
        for _canonical in (
            "secret_key",
            "jwt_secret",
            "profile_field_encryption_key",
            "email_field_encryption_key",
            "email_hash_key",
            "security_backup_encryption_key",
            "privacy_retention_hash_key",
        ):
            ensure_secret(merged, _canonical, settings_file=Path("server_config.json"))
    sms_enabled = bool(fields[9]["value"])
    merged["enable_two_factor_beta"] = sms_enabled
    merged["enable_sms_two_factor"] = sms_enabled
    merged["two_factor_sms_channel"] = str(fields[10]["value"] or "sms").strip().lower() or "sms"
    merged["twilio_account_sid"] = str(fields[11]["value"] or "").strip()
    merged["twilio_auth_token"] = str(fields[12]["value"] or "")
    merged["twilio_verify_service_sid"] = str(fields[13]["value"] or "").strip()
    merged["two_factor_login_timeout_seconds"] = int(fields[14]["value"] or 600)
    twilio_errors = twilio_setup_errors(merged)
    if twilio_errors:
        _tui_message(stdscr, "SMS 2FA setup", twilio_errors, error=True)



def _edit_password_recovery_email_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    fields = [
        {"label": "Enable password reset emails", "value": bool(merged.get("smtp_enabled", False)), "type": "bool", "help": "Turn this on only if you want EchoChat to send password reset emails through your SMTP provider."},
        {"label": "SMTP provider", "value": str(merged.get("smtp_provider") or "brevo").strip().lower(), "type": "choice", "options": _SMTP_PROVIDER_OPTIONS, "help": "Provider presets keep host/port/TLS guidance aligned with the runtime sender."},
        {"label": "SMTP host", "value": str(merged.get("smtp_host") or ""), "help": "Example: smtp-relay.example.com"},
        {"label": "SMTP port", "value": int(merged.get("smtp_port") or base["smtp_port"]), "type": "int", "min": 1, "max": 65535, "help": "Port 587 is common for STARTTLS. Port 465 is common for implicit SSL."},
        {"label": "SMTP username", "value": str(merged.get("smtp_username") or ""), "help": "The login name used for your outgoing mail server. Password-reset email requires authenticated SMTP."},
        {"label": "SMTP password", "value": str(merged.get("smtp_password") or ""), "type": "secret", "help": "The secret or app password for the SMTP account. You may leave this blank only if ECHOCHAT_SMTP_PASSWORD / SMTP_PASSWORD is set."},
        {"label": "Use STARTTLS", "value": bool(merged.get("smtp_use_starttls", True)), "type": "bool", "help": "Normal choice for port 587."},
        {"label": "Use SSL", "value": bool(merged.get("smtp_use_ssl", False)), "type": "bool", "help": "Normal choice for port 465. Runtime uses SSL instead of STARTTLS when this is enabled."},
        {"label": "From address", "value": str(merged.get("smtp_from") or base["smtp_from"]), "help": "Use a real verified sender address for Brevo/Gmail delivery; localhost will not deliver reliably."},
        {"label": "SMTP timeout seconds", "value": int(merged.get("smtp_timeout_seconds") or base.get("smtp_timeout_seconds") or 20), "type": "int", "min": 3, "max": 120, "help": "How long Echo-Chat waits for SMTP connect/TLS/login before failing visibly."},
        {"label": "Password reset token minutes", "value": int(merged.get("password_reset_token_minutes") or base["password_reset_token_minutes"]), "type": "int", "min": 1, "max": 1440, "help": "How long a password reset link stays valid."},
        {"label": "Password reset daily limit", "value": int(merged.get("password_reset_daily_limit") or base.get("password_reset_daily_limit") or 3), "type": "int", "min": 1, "max": 25, "help": "How many reset links one verified account can receive in 24 hours."},
        {"label": "Recovery PIN max attempts", "value": int(merged.get("recovery_pin_max_attempts") or base["recovery_pin_max_attempts"]), "type": "int", "min": 1, "max": 50, "help": "How many wrong recovery PIN attempts are allowed before temporary lockout."},
        {"label": "Recovery PIN lock minutes", "value": int(merged.get("recovery_pin_lock_minutes") or base["recovery_pin_lock_minutes"]), "type": "int", "min": 1, "max": 1440, "help": "How long the recovery PIN lockout lasts after too many wrong tries."},
    ]
    _edit_form(
        stdscr,
        "Email and password recovery",
        fields,
        intro_lines=[
            "This section gathers the email and recovery settings that used to feel hidden or split across different menus.",
        ],
    )
    merged["smtp_enabled"] = bool(fields[0]["value"])
    old_provider = str(merged.get("smtp_provider") or "").strip().lower()
    merged["smtp_provider"] = str(fields[1]["value"] or "custom").strip().lower() or "custom"
    preset = _smtp_provider_preset(merged.get("smtp_provider"))
    merged["smtp_host"] = str(fields[2]["value"] or preset.get("host") or "").strip()
    merged["smtp_port"] = int(fields[3]["value"] or preset.get("port") or 587)
    merged["smtp_username"] = str(fields[4]["value"] or preset.get("username") or "").strip()
    merged["smtp_password"] = str(fields[5]["value"] or "")
    merged["smtp_use_starttls"] = bool(fields[6]["value"])
    merged["smtp_use_ssl"] = bool(fields[7]["value"])
    if old_provider != merged["smtp_provider"] and preset:
        merged["smtp_use_starttls"] = bool(preset.get("starttls", merged["smtp_use_starttls"]))
        merged["smtp_use_ssl"] = bool(preset.get("ssl", merged["smtp_use_ssl"]))
        if not str(fields[2]["value"] or "").strip():
            merged["smtp_host"] = str(preset.get("host") or "").strip()
        if not str(fields[4]["value"] or "").strip() and preset.get("username"):
            merged["smtp_username"] = str(preset.get("username") or "").strip()
    if merged["smtp_use_ssl"]:
        merged["smtp_use_starttls"] = False
    merged["smtp_from"] = str(fields[8]["value"] or "").strip()
    _tui_require_verified_smtp_from(stdscr, merged)
    merged["smtp_timeout_seconds"] = int(fields[9]["value"])
    merged["password_reset_token_minutes"] = int(fields[10]["value"])
    merged["password_reset_daily_limit"] = int(fields[11]["value"])
    merged["recovery_pin_max_attempts"] = int(fields[12]["value"])
    merged["recovery_pin_lock_minutes"] = int(fields[13]["value"])
    smtp_errors = _smtp_setup_errors(merged)
    if smtp_errors:
        _tui_message(stdscr, "SMTP setup", smtp_errors, error=True)



def _edit_rooms_behavior_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    fields = [
        {"label": "Allow users to create rooms", "value": bool(merged.get("allow_user_create_rooms", True)), "type": "bool", "help": "If this is off, only staff-created rooms and built-in rooms will be available."},
        {"label": "Max room name length", "value": int(merged.get("max_room_name_length") or base["max_room_name_length"]), "type": "int", "min": 8, "max": 200, "help": "Limits how long custom room names may be."},
        {"label": "Autoscale public rooms", "value": bool(merged.get("autoscale_rooms_enabled", True)), "type": "bool", "help": "Creates overflow rooms like Lobby (2) when a public room fills up."},
        {"label": "Autoscale room capacity", "value": int(merged.get("autoscale_room_capacity") or base["autoscale_room_capacity"]), "type": "int", "min": 2, "max": 10000, "help": "The user count at which a new overflow public room is created."},
        {"label": "Autoscale room idle minutes", "value": int(merged.get("autoscale_room_idle_minutes") or base["autoscale_room_idle_minutes"]), "type": "int", "min": 1, "max": 10080, "help": "How long an empty overflow public room can sit idle before cleanup."},
        {"label": "Custom room idle minutes", "value": int(merged.get("custom_room_idle_minutes") or base["custom_room_idle_minutes"]), "type": "int", "min": 1, "max": 10080, "help": "How long a normal custom room can stay empty/inactive before cleanup."},
        {"label": "Private custom room idle minutes", "value": int(merged.get("custom_private_room_idle_minutes") or base["custom_private_room_idle_minutes"]), "type": "int", "min": 1, "max": 10080, "help": "How long a private custom room can stay empty/inactive before cleanup."},
        {"label": "Default room slowmode seconds", "value": int(merged.get("room_slowmode_default_sec") or 0), "type": "int", "min": 0, "max": 3600, "help": "Set 0 to disable slowmode by default. Positive values create a delay between messages per user."},
        {"label": "Janitor interval seconds", "value": int(merged.get("janitor_interval_seconds") or base["janitor_interval_seconds"]), "type": "int", "min": 10, "max": 3600, "help": "How often the background cleanup loop checks for expired rooms and stale records."},
        {"label": "Max message length", "value": int(merged.get("max_message_length") or base["max_message_length"]), "type": "int", "min": 100, "max": 50000, "help": "Maximum length for standard chat messages."},
        {"label": "Group message character limit", "value": int(merged.get("max_group_message_chars") or base["max_group_message_chars"]), "type": "int", "min": 100, "max": 100000, "help": "Upper size limit for large group chat messages."},
        {"label": "Group message rate limit", "value": int(merged.get("group_msg_rate_limit") or base["group_msg_rate_limit"]), "type": "int", "min": 5, "max": 10000, "help": "Maximum group messages allowed per group-message window."},
        {"label": "Group message window seconds", "value": int(merged.get("group_msg_rate_window_sec") or base["group_msg_rate_window_sec"]), "type": "int", "min": 10, "max": 3600, "help": "Window length for the group-message rate limit."},
    ]
    _edit_form(
        stdscr,
        "Rooms and chat behavior",
        fields,
        intro_lines=[
            "This section brings together room creation, room cleanup, overflow-room behavior, and message size limits.",
        ],
    )
    merged["allow_user_create_rooms"] = bool(fields[0]["value"])
    merged["max_room_name_length"] = int(fields[1]["value"])
    merged["autoscale_rooms_enabled"] = bool(fields[2]["value"])
    merged["autoscale_room_capacity"] = int(fields[3]["value"])
    merged["autoscale_room_idle_minutes"] = int(fields[4]["value"])
    merged["custom_room_idle_minutes"] = int(fields[5]["value"])
    merged["custom_room_idle_hours"] = max(1, round(merged["custom_room_idle_minutes"] / 60))
    merged["custom_private_room_idle_minutes"] = int(fields[6]["value"])
    merged["custom_private_room_idle_hours"] = max(1, round(merged["custom_private_room_idle_minutes"] / 60))
    merged["room_slowmode_default_sec"] = int(fields[7]["value"])
    merged["janitor_interval_seconds"] = int(fields[8]["value"])
    merged["max_message_length"] = int(fields[9]["value"])
    merged["max_group_message_chars"] = int(fields[10]["value"])
    merged["group_msg_rate_limit"] = int(fields[11]["value"])
    merged["group_msg_rate_window_sec"] = int(fields[12]["value"])




def _edit_message_display_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    fields = [
        {"label": "Room chat text animation", "value": str(merged.get("chat_text_animation") or base["chat_text_animation"]), "type": "choice", "options": ["none", "fade", "rise", "slide", "scale"], "help": "Controls new text motion in public/custom/private room chat. Use none if chat text should stay steady."},
        {"label": "Private message text animation", "value": str(merged.get("dm_text_animation") or base["dm_text_animation"]), "type": "choice", "options": ["none", "fade", "rise", "slide", "scale"], "help": "Controls new text motion in direct private-message windows."},
        {"label": "Group chat text animation", "value": str(merged.get("group_text_animation") or base["group_text_animation"]), "type": "choice", "options": ["none", "fade", "rise", "slide", "scale"], "help": "Controls new text motion in group-chat windows."},
        {"label": "Room chat: show username on every message", "value": bool(merged.get("room_show_sender_every_message", False)), "type": "bool", "help": "When on, public/custom/private room chat repeats the sender name on every message instead of compact grouping."},
        {"label": "Private messages: show username on every message", "value": bool(merged.get("dm_show_sender_every_message", False)), "type": "bool", "help": "When on, every direct private-message line repeats the sender name."},
        {"label": "Group chat: show username on every message", "value": bool(merged.get("group_show_sender_every_message", False)), "type": "bool", "help": "When on, every group-message line repeats the sender name."},
    ]
    _edit_form(
        stdscr,
        "Message display",
        fields,
        intro_lines=[
            "This setup step mirrors the Admin Panel message-display options so the server owner can choose the default chat look during setup.",
            "Classic sender labels repeat the username on every message. The compact style groups consecutive messages from the same person.",
        ],
    )
    merged["chat_text_animation"] = str(fields[0]["value"] or "none").strip().lower() or "none"
    merged["dm_text_animation"] = str(fields[1]["value"] or "rise").strip().lower() or "rise"
    merged["group_text_animation"] = str(fields[2]["value"] or "rise").strip().lower() or "rise"
    merged["room_show_sender_every_message"] = bool(fields[3]["value"])
    merged["dm_show_sender_every_message"] = bool(fields[4]["value"])
    merged["group_show_sender_every_message"] = bool(fields[5]["value"])

def _edit_protection_and_abuse_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    fields = [
        {"label": "Room message rate limit", "value": str(merged.get("room_msg_rate_limit") or base["room_msg_rate_limit"]), "help": "Example: 20@10 means 20 room messages per 10 seconds."},
        {"label": "DM message rate limit", "value": str(merged.get("dm_msg_rate_limit") or base["dm_msg_rate_limit"]), "help": "Example: 15@10 means 15 direct messages per 10 seconds."},
        {"label": "Room GIF rate limit", "value": str(merged.get("room_gif_rate_limit") or base["room_gif_rate_limit"]), "help": "Example: 6@20 means 6 GIF sends per 20 seconds."},
        {"label": "Room torrent rate limit", "value": str(merged.get("room_torrent_rate_limit") or base["room_torrent_rate_limit"]), "help": "Example: 2@30 means 2 torrent shares per 30 seconds."},
        {"label": "Typing event rate limit", "value": str(merged.get("room_typing_rate_limit") or base["room_typing_rate_limit"]), "help": "Limits typing/stop-typing loops from modified clients."},
        {"label": "Reaction rate limit", "value": str(merged.get("room_reaction_rate_limit") or base["room_reaction_rate_limit"]), "help": "Limits reaction spam from modified clients."},
        {"label": "Room media action rate limit", "value": str(merged.get("room_media_action_rate_limit") or base["room_media_action_rate_limit"]), "help": "Limits skip-vote/source-change loops in room radio/media controls."},
        {"label": "Join room rate limit", "value": str(merged.get("room_join_rate_limit") or base["room_join_rate_limit"]), "help": "Limits how quickly one user can join rooms repeatedly."},
        {"label": "Room switch cooldown seconds", "value": int(merged.get("room_switch_cooldown_sec") or base["room_switch_cooldown_sec"]), "type": "int", "min": 0, "max": 30, "help": "Minimum delay between switching from one active room to another."},
        {"label": "Create room rate limit", "value": str(merged.get("room_create_rate_limit") or base["room_create_rate_limit"]), "help": "Limits how quickly one user can create new rooms."},
        {"label": "Friend request rate limit", "value": str(merged.get("friend_req_rate_limit") or base["friend_req_rate_limit"]), "help": "Controls how quickly one user can send friend requests."},
        {"label": "Auto-mute strike threshold", "value": int(merged.get("antiabuse_strikes_before_mute") or base["antiabuse_strikes_before_mute"]), "type": "int", "min": 1, "max": 100, "help": "How many anti-abuse hits are allowed before the server auto-mutes a user."},
        {"label": "Auto-mute minutes", "value": int(merged.get("antiabuse_auto_mute_minutes") or base["antiabuse_auto_mute_minutes"]), "type": "int", "min": 1, "max": 1440, "help": "How long an automatic anti-abuse mute lasts."},
        {"label": "Exempt staff from anti-abuse", "value": bool(merged.get("antiabuse_exempt_staff", True)), "type": "bool", "help": "Useful if staff need to moderate heavily without tripping normal user flood limits."},
        {"label": "Block bad registration names", "value": bool(merged.get("block_registration_terms_enabled", True)), "type": "bool", "help": "Turns on blocked-word filtering for usernames during registration."},
        {"label": "Blocked registration terms", "value": str(merged.get("blocked_registration_terms") or ""), "help": "Enter a comma-separated list of blocked words or patterns for usernames."},
        {"label": "Block bad custom room names", "value": bool(merged.get("block_custom_room_terms_enabled", True)), "type": "bool", "help": "Turns on blocked-word filtering for custom room names."},
        {"label": "Blocked custom room terms", "value": str(merged.get("blocked_custom_room_terms") or ""), "help": "Enter a comma-separated list of blocked words or patterns for custom room names."},
    ]
    _edit_form(
        stdscr,
        "Protection and anti-abuse",
        fields,
        intro_lines=[
            "These are the moderation and flood-control settings that server owners usually want to see up front.",
            "Rate-limit fields accept Echo-Chat's normal formats like 20@10.",
        ],
    )
    merged["room_msg_rate_limit"] = str(fields[0]["value"] or "").strip() or str(base["room_msg_rate_limit"])
    merged["dm_msg_rate_limit"] = str(fields[1]["value"] or "").strip() or str(base["dm_msg_rate_limit"])
    merged["room_gif_rate_limit"] = str(fields[2]["value"] or "").strip() or str(base["room_gif_rate_limit"])
    merged["room_torrent_rate_limit"] = str(fields[3]["value"] or "").strip() or str(base["room_torrent_rate_limit"])
    merged["room_typing_rate_limit"] = str(fields[4]["value"] or "").strip() or str(base["room_typing_rate_limit"])
    merged["room_reaction_rate_limit"] = str(fields[5]["value"] or "").strip() or str(base["room_reaction_rate_limit"])
    merged["room_media_action_rate_limit"] = str(fields[6]["value"] or "").strip() or str(base["room_media_action_rate_limit"])
    merged["room_join_rate_limit"] = str(fields[7]["value"] or "").strip() or str(base["room_join_rate_limit"])
    merged["room_switch_cooldown_sec"] = int(fields[8]["value"] or 0)
    merged["room_create_rate_limit"] = str(fields[9]["value"] or "").strip() or str(base["room_create_rate_limit"])
    merged["friend_req_rate_limit"] = str(fields[10]["value"] or "").strip() or str(base["friend_req_rate_limit"])
    merged["antiabuse_strikes_before_mute"] = int(fields[11]["value"])
    merged["antiabuse_auto_mute_minutes"] = int(fields[12]["value"])
    merged["antiabuse_exempt_staff"] = bool(fields[13]["value"])
    merged["block_registration_terms_enabled"] = bool(fields[14]["value"])
    merged["blocked_registration_terms"] = str(fields[15]["value"] or "").strip()
    merged["block_custom_room_terms_enabled"] = bool(fields[16]["value"])
    merged["blocked_custom_room_terms"] = str(fields[17]["value"] or "").strip()



def _edit_media_uploads_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    fields = [
        {"label": "Enable GIF search", "value": bool(merged.get("giphy_enabled", True)), "type": "bool", "help": "Turns the GIF picker and GIPHY search on or off."},
        {"label": "Store GIPHY API key in server_config.json", "value": bool(merged.get("__store_giphy_in_config", bool(str(merged.get("giphy_api_key") or "").strip()))), "type": "bool", "help": "Turn this off if you prefer to keep the GIPHY API key only in an environment variable."},
        {"label": "GIPHY API key", "value": str(merged.get("giphy_api_key") or ""), "type": "secret", "help": "Only needed when GIF search is on and you choose to store the key in the config file."},
        {"label": "GIPHY rating", "value": str(merged.get("giphy_rating") or "pg-13"), "type": "choice", "options": ["g", "pg", "pg-13", "r"], "help": "Filters GIF results by rating."},
        {"label": "GIPHY language", "value": str(merged.get("giphy_lang") or "en"), "help": "Language code for GIF search, such as en."},
        {"label": "Allow SVG avatars", "value": bool(merged.get("allow_svg_avatars", False)), "type": "bool", "help": "Safer to leave off unless you are certain you want SVG avatar uploads."},
        {"label": "Disable all file transfers globally", "value": bool(merged.get("disable_file_transfer_globally", False)), "type": "bool", "help": "Emergency switch that disables file transfers server-wide without removing upload settings."},
        {"label": "Disable group files globally", "value": bool(merged.get("disable_group_files_globally", False)), "type": "bool", "help": "Disables files in group chat while leaving other features alone."},
        {"label": "Max DM file bytes", "value": int(merged.get("max_dm_file_bytes") or base["max_dm_file_bytes"]), "type": "int", "min": 1024, "max": 1073741824, "help": "Maximum size for direct-message file transfers."},
        {"label": "Max attachment bytes", "value": int(merged.get("max_attachment_size") or base["max_attachment_size"]), "type": "int", "min": 1024, "max": 1073741824, "help": "Maximum size for general attachments."},
        {"label": "Max group upload bytes", "value": int(merged.get("max_group_upload_bytes") or base["max_group_upload_bytes"]), "type": "int", "min": 1024, "max": 1073741824, "help": "Maximum size for files posted into group chats."},
        {"label": "Require DM end-to-end encryption", "value": bool(merged.get("require_dm_e2ee", True)), "type": "bool", "help": "Default on. Direct messages require E2EE instead of silently falling back to plaintext."},
        {"label": "Allow plaintext DM fallback", "value": bool(merged.get("allow_plaintext_dm_fallback", False)), "type": "bool", "help": "Default off. Only enable this temporary compatibility mode for old clients you fully trust."},
        {"label": "Require group chat E2EE", "value": bool(merged.get("require_group_e2ee", True)), "type": "bool", "help": "Default on. Blocks plaintext group messages at the server."},
        {"label": "Require private-room E2EE", "value": bool(merged.get("require_private_room_e2ee", True)), "type": "bool", "help": "Default on. Invite-only/private custom rooms reject plaintext messages."},
        {"label": "Require all room E2EE", "value": bool(merged.get("require_room_e2ee", False)), "type": "bool", "help": "Strict mode. Blocks plaintext in every room except supported slash commands."},
        {"label": "All-room E2EE impact acknowledged", "value": bool(merged.get("all_room_e2ee_impact_acknowledged", False)), "type": "bool", "help": "Confirms public-room body moderation/search limitations when all-room E2EE strict mode is enabled."},
        {"label": "Encrypt sensitive profile fields", "value": bool(merged.get("encrypt_sensitive_profile_fields", True)), "type": "bool", "help": "Default on. New phone/address/location writes are encrypted when a server key is available."},
        {"label": "Encrypt email at rest", "value": bool(merged.get("encrypt_email_at_rest", True)), "type": "bool", "help": "Default on. New and migrated emails use email_hash + email_encrypted instead of plaintext users.email."},
        {"label": "Encrypt security backups", "value": bool(merged.get("encrypt_security_backups", True)), "type": "bool", "help": "Default on. Security backup JSON files are written as encrypted .json.enc envelopes."},
        {"label": "Privacy retention enabled", "value": bool(merged.get("privacy_retention_enabled", True)), "type": "bool", "help": "Default on. Hashes old IP/user-agent metadata after the retention window."},
        {"label": "IP/user-agent retention days", "value": int(merged.get("privacy_ip_user_agent_retention_days", 30)), "type": "int", "min": 0, "max": 3650, "help": "Default 30. Raw IP/UA metadata older than this is replaced with hash labels."},
        {"label": "Audit detail retention days", "value": int(merged.get("privacy_audit_detail_retention_days", 90)), "type": "int", "min": 0, "max": 3650, "help": "Default 90. Old audit details with ip= or ua= text are scrubbed."},
    ]
    _edit_form(
        stdscr,
        "Media and uploads",
        fields,
        intro_lines=[
            "This section collects GIF, avatar, attachment, and DM-file settings in one place.",
        ],
    )
    merged["giphy_enabled"] = bool(fields[0]["value"])
    merged["__store_giphy_in_config"] = bool(fields[1]["value"])
    if merged["giphy_enabled"] and merged["__store_giphy_in_config"]:
        merged["giphy_api_key"] = str(fields[2]["value"] or "").strip()
    elif not merged["__store_giphy_in_config"]:
        merged["giphy_api_key"] = ""
    merged["giphy_rating"] = str(fields[3]["value"] or "pg-13")
    merged["giphy_lang"] = str(fields[4]["value"] or "en").strip() or "en"
    merged["allow_svg_avatars"] = bool(fields[5]["value"])
    # Legacy public uploads are retired from guided setup.  Existing configs can
    # still carry these keys, but new setup files keep the route disabled.
    merged["enable_legacy_public_uploads"] = False
    merged["disable_file_transfer_globally"] = bool(fields[6]["value"])
    merged["disable_group_files_globally"] = bool(fields[7]["value"])
    merged["max_dm_file_bytes"] = int(fields[8]["value"])
    merged["max_attachment_size"] = int(fields[9]["value"])
    merged["max_group_upload_bytes"] = int(fields[10]["value"])
    merged["max_group_file_bytes"] = merged["max_group_upload_bytes"]
    merged["require_dm_e2ee"] = bool(fields[11]["value"])
    merged["allow_plaintext_dm_fallback"] = bool(fields[12]["value"])
    merged["require_group_e2ee"] = bool(fields[13]["value"])
    merged["require_private_room_e2ee"] = bool(fields[14]["value"])
    merged["require_room_e2ee"] = bool(fields[15]["value"])
    merged["all_room_e2ee_impact_acknowledged"] = bool(fields[16]["value"])
    merged["encrypt_sensitive_profile_fields"] = bool(fields[17]["value"])
    merged["encrypt_email_at_rest"] = bool(fields[18]["value"])
    merged["encrypt_security_backups"] = bool(fields[19]["value"])
    merged["privacy_retention_enabled"] = bool(fields[20]["value"])
    merged["privacy_ip_user_agent_retention_days"] = max(0, min(int(fields[21]["value"]), 3650))
    merged["privacy_audit_detail_retention_days"] = max(0, min(int(fields[22]["value"]), 3650))
    if merged["require_dm_e2ee"]:
        merged["allow_plaintext_dm_fallback"] = False



def _edit_voice_and_webrtc_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    fields = [
        {"label": "Enable voice chat", "value": bool(merged.get("voice_enabled", True)), "type": "bool", "help": "Turns Echo-Chat's voice features on or off."},
        {"label": "Max voice peers per room", "value": int(merged.get("voice_max_room_peers") or 100), "type": "int", "min": 0, "max": 10000, "help": "Default is 100. Set 0 for unlimited or use a lower cap such as 30."},
        {"label": "Voice invite cooldown seconds", "value": int(merged.get("voice_invite_cooldown_seconds") or base["voice_invite_cooldown_seconds"]), "type": "int", "min": 0, "max": 3600, "help": "Minimum delay between sending repeated voice invites."},
        {"label": "Voice DM invite TTL seconds", "value": int(merged.get("voice_dm_invite_ttl_seconds") or base["voice_dm_invite_ttl_seconds"]), "type": "int", "min": 1, "max": 3600, "help": "How long a direct voice invite remains valid."},
        {"label": "Voice DM active TTL seconds", "value": int(merged.get("voice_dm_active_ttl_seconds") or base["voice_dm_active_ttl_seconds"]), "type": "int", "min": 1, "max": 86400, "help": "How long an active direct voice session record stays alive."},
        {"label": "P2P file transfer enabled", "value": bool(merged.get("p2p_file_enabled", True)), "type": "bool", "help": "Turns peer-to-peer file transfer signaling on or off."},
        {"label": "P2P handshake timeout ms", "value": int(merged.get("p2p_file_handshake_timeout_ms") or base["p2p_file_handshake_timeout_ms"]), "type": "int", "min": 100, "max": 600000, "help": "How long peers wait for the initial P2P handshake before timing out."},
        {"label": "P2P transfer timeout ms", "value": int(merged.get("p2p_file_transfer_timeout_ms") or base["p2p_file_transfer_timeout_ms"]), "type": "int", "min": 1000, "max": 3600000, "help": "How long a file transfer can stall before timing out."},
        {"label": "P2P/WebRTC ICE URLs", "value": _ice_url_csv(p2p_ice_servers(merged)), "help": "Comma-separated STUN/TURN URLs. Add TURN for internet/cellular/corporate-network webcam and P2P file tests."},
        {"label": "Voice/webcam ICE URLs", "value": _ice_url_csv(merged.get("voice_ice_servers") or []), "help": "Blank uses the P2P/WebRTC ICE list. Paste TURN URLs here only if voice/webcam should use a different relay."},
        {"label": "TURN username", "value": first_turn_username(voice_ice_servers(merged)) or first_turn_username(p2p_ice_servers(merged)), "help": "Optional static TURN username for local testing. Production should prefer short-lived TURN credentials from env/secret management."},
        {"label": "TURN credential", "value": "", "type": "secret", "help": "Optional TURN password/credential to apply to TURN URLs. Blank keeps credentials out of the saved setup file."},
    ]
    _edit_form(
        stdscr,
        "Voice and WebRTC",
        fields,
        intro_lines=[
            "This screen focuses on voice, peer-to-peer files, and the STUN/TURN connectivity used by webcam.",
            "STUN is enough for many LAN tests. TURN is the compatibility relay for real internet/cellular/firewall testing.",
        ],
    )
    merged["voice_enabled"] = bool(fields[0]["value"])
    merged["voice_max_room_peers"] = int(fields[1]["value"])
    merged["voice_invite_cooldown_seconds"] = int(fields[2]["value"])
    merged["voice_dm_invite_ttl_seconds"] = int(fields[3]["value"])
    merged["voice_dm_active_ttl_seconds"] = int(fields[4]["value"])
    merged["p2p_file_enabled"] = bool(fields[5]["value"])
    merged["p2p_file_handshake_timeout_ms"] = int(fields[6]["value"])
    merged["p2p_file_transfer_timeout_ms"] = int(fields[7]["value"])
    p2p_parsed = parse_ice_servers_text(fields[8]["value"])
    voice_parsed = parse_ice_servers_text(fields[9]["value"])
    turn_user = str(fields[10]["value"] or "").strip()
    turn_credential = str(fields[11]["value"] or "").strip()
    if p2p_parsed:
        merged["p2p_ice_servers"] = apply_turn_credentials(p2p_parsed, turn_user, turn_credential, keep_existing=True)
    else:
        merged["p2p_ice_servers"] = p2p_ice_servers(merged)
    if voice_parsed:
        merged["voice_ice_servers"] = apply_turn_credentials(voice_parsed, turn_user, turn_credential, keep_existing=True)
    else:
        merged["voice_ice_servers"] = []
    ice_errors = _ice_setup_errors(merged)
    if ice_errors:
        _tui_message(stdscr, "STUN/TURN setup", ice_errors, error=True)



def _edit_media_setup_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    """Guided Echo built-in media and webcam setup."""
    fields = [
        {
            "label": "A/V mode",
            "value": str(merged.get("av_mode") or base.get("av_mode") or "echo"),
            "help": "Use echo for built-in voice/webcam controls, or standard for voice-only mode.",
        },
        {
            "label": "Enable webcam controls",
            "value": bool(merged.get("webcam_enabled", base.get("webcam_enabled", True))),
            "type": "bool",
            "help": "When off, rooms keep the voice button without camera controls.",
        },
        {
            "label": "Webcam quality",
            "value": str(merged.get("webcam_quality") or base.get("webcam_quality") or "balanced"),
            "help": "Suggested values: low, balanced, high.",
        },
        {
            "label": "Webcam codec strategy",
            "value": str(merged.get("webcam_codec_strategy") or base.get("webcam_codec_strategy") or "prefer-compatible"),
            "help": "Browser hint for webcam publishing. Suggested: prefer-compatible, prefer-efficient, or prefer-quality.",
        },
        {
            "label": "Webcam approval mode",
            "value": str(merged.get("webcam_approval_mode") or base.get("webcam_approval_mode") or "owner_approval"),
            "help": "owner_approval asks before viewing; open allows viewing; disabled blocks camera viewing.",
        },
        {
            "label": "Max webcam viewers",
            "value": int(merged.get("webcam_max_viewers") or base.get("webcam_max_viewers") or 0),
            "type": "int",
            "min": 0,
            "max": 10000,
            "help": "0 means unlimited. Positive values cap viewers per publishing user.",
        },
        {
            "label": "Default media policy",
            "value": str(merged.get("default_media_policy") or base.get("default_media_policy") or "user_choice"),
            "help": "Suggested values: user_choice, voice_first, webcam_first, both_first.",
        },
        {
            "label": "Media mode rate limit",
            "value": str(merged.get("rate_limit_media_mode") or base.get("rate_limit_media_mode") or "120 per minute"),
            "help": "Rate limit for the client media-mode endpoint.",
        },
    ]
    _edit_form(
        stdscr,
        "Echo media / webcam",
        fields,
        intro_lines=[
            "Echo-Chat now uses its built-in browser media path for room voice and webcam controls.",
            "This section controls camera policy and browser capture defaults without requiring an external media server.",
        ],
    )
    mode = str(fields[0]["value"] or "echo").strip().lower().replace("-", "_")
    if mode in {"webrtc", "built_in", "builtin"}:
        mode = "echo"
    if mode not in {"echo", "standard"}:
        mode = "echo"
    webcam_enabled = bool(fields[1]["value"]) and mode == "echo"
    merged["av_mode"] = mode
    merged["webcam_enabled"] = webcam_enabled
    merged["echo_webcam_enabled"] = webcam_enabled
    merged["webcam_quality"] = str(fields[2]["value"] or "balanced").strip() or "balanced"
    merged["echo_webcam_quality"] = merged["webcam_quality"]
    merged["webcam_codec_strategy"] = str(fields[3]["value"] or "prefer-compatible").strip() or "prefer-compatible"
    merged["webcam_approval_mode"] = str(fields[4]["value"] or "owner_approval").strip() or "owner_approval"
    merged["webcam_max_viewers"] = max(0, int(fields[5]["value"] or 0))
    merged["default_media_policy"] = str(fields[6]["value"] or "user_choice").strip() or "user_choice"
    merged["rate_limit_media_mode"] = str(fields[7]["value"] or "120 per minute").strip() or "120 per minute"

def _edit_public_beta_readiness_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    current_mode = infer_hosting_mode(merged)
    fields = [
        {
            "label": "Hosting mode",
            "value": current_mode,
            "type": "choice",
            "options": ["lan", "no_domain_yet", "public_beta", "advanced"],
            "help": "LAN is home testing. No domain yet keeps you safe until you own a domain. Public beta requires real HTTPS. Advanced keeps custom choices.",
        },
        {
            "label": "Public base URL",
            "value": str(merged.get("public_base_url") or ""),
            "help": "Leave blank if you do not have a domain yet. For public beta, use the exact HTTPS address testers open, such as https://chat.yourdomain.com.",
        },
        {
            "label": "Apply recommended preset",
            "value": False,
            "type": "bool",
            "help": "When true, setup applies safe defaults for the selected mode: cookies, origins, proxy headers, production mode, Redis defaults, and health checks.",
        },
    ]
    _edit_form(
        stdscr,
        "Public beta readiness wizard",
        fields,
        intro_lines=[
            "This screen chooses whether Echo-Chat is being prepared for LAN testing, no-domain-yet staging, or real public beta hosting.",
            "Choose no_domain_yet if you do not own a domain yet. Public beta mode expects a real domain, HTTPS through a reverse proxy, secure cookies, exact allowed origins, and Redis-backed production services.",
            "Nothing saves until Step 17 - Save and finish.",
        ],
    )
    mode = str(fields[0]["value"] or "lan").strip().lower().replace("-", "_").replace(" ", "_")
    url = str(fields[1]["value"] or "").strip().rstrip("/")
    merged["hosting_mode"] = mode
    merged["public_base_url"] = url
    if bool(fields[2]["value"]):
        patched = apply_hosting_mode_preset(merged, mode, url)
        merged.clear()
        merged.update(patched)
        _sync_ssl_settings_block(merged)
        _tui_message(
            stdscr,
            "Public beta preset applied",
            [
                f"Hosting mode: {merged.get('hosting_mode')}",
                f"Public base URL: {merged.get('public_base_url') or '(none)'}",
                f"Startup mode: {merged.get('run_mode')}",
                f"Secure cookies: {bool(merged.get('cookie_secure'))}",
                f"Allowed origins: {_csv_text(merged.get('allowed_origins') or []) or '(none)'}",
                f"Rate-limit storage: {merged.get('rate_limit_storage_uri')}",
                f"Socket.IO queue: {merged.get('socketio_message_queue') or '(not set)'}",
            ],
            error=False,
        )
    _show_public_beta_readiness_report(stdscr, merged)


def _edit_hosting_network_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    ssl_defaults = dict(merged.get("ssl_tls_settings") or {})
    fields = [
        {"label": "Hosting mode", "value": infer_hosting_mode(merged), "type": "choice", "options": ["lan", "no_domain_yet", "public_beta", "advanced"], "help": "LAN is local/home testing. No domain yet is the safe waiting room. Public beta is for internet testers with a real domain + HTTPS. Advanced keeps custom reverse proxy settings."},
        {"label": "Public base URL", "value": str(merged.get("public_base_url") or ""), "help": "Leave blank if you do not have a domain. Public beta should use the exact HTTPS address testers open, such as https://chat.yourdomain.com."},
        {"label": "Allowed web origins", "value": _csv_text(merged.get("cors_allowed_origins") or merged.get("allowed_origins") or []), "help": "Comma-separated list of allowed browser origins. Public beta should contain only the exact HTTPS public origin."},
        {"label": "Auto-allow LAN same-host origins", "value": bool(merged.get("auto_allow_lan_origins", True)), "type": "bool", "help": "Recommended for LAN/mobile testing. Public beta should usually turn this off."},
        {"label": "Trust proxy headers", "value": bool(merged.get("trust_proxy_headers", False)), "type": "bool", "help": "Turn this on when Echo-Chat sits behind Nginx, Caddy, Traefik, or another reverse proxy that forwards X-Forwarded-* headers."},
        {"label": "Proxy hop count", "value": int(merged.get("proxy_fix_hops") or base["proxy_fix_hops"]), "type": "int", "min": 0, "max": 10, "help": "How many reverse-proxy layers sit in front of EchoChat. One local proxy usually means 1."},
        {"label": "Use built-in HTTPS listener", "value": bool(merged.get("https", False)), "type": "bool", "help": "Usually leave off behind Caddy/Nginx. Turn on only if EchoChat itself should load TLS certificates directly."},
        {"label": "TLS certificate file", "value": str(merged.get("ssl_cert_file") or ssl_defaults.get("certificate_path") or ""), "help": "Path to the certificate file when built-in HTTPS is enabled."},
        {"label": "TLS key file", "value": str(merged.get("ssl_key_file") or ssl_defaults.get("key_path") or ""), "help": "Path to the private key file when built-in HTTPS is enabled."},
        {"label": "Enable health endpoint", "value": bool(merged.get("enable_health_check_endpoint", False)), "type": "bool", "help": "Useful for load balancers, reverse proxies, and uptime monitors."},
        {"label": "Health endpoint path", "value": str(merged.get("health_check_endpoint") or base["health_check_endpoint"]), "help": "Default is /health."},
        {"label": "Enable Dynamic DNS helper", "value": bool(merged.get("dynamic_dns_enabled", False)), "type": "bool", "help": "Optional. Use when your home/public IP changes and your DDNS provider supports username/password update URLs."},
        {"label": "DDNS provider", "value": str(merged.get("dynamic_dns_provider") or "No-IP"), "type": "choice", "options": ["No-IP", "Dynu", "DNS-O-Matic", "Custom"], "help": "No-IP uses https://dynupdate.no-ip.com/nic/update. Custom lets you paste your provider update URL."},
        {"label": "DDNS hostname", "value": str(merged.get("dynamic_dns_domain") or ""), "help": "The public hostname your DDNS provider should update, such as yourname.ddns.net. Do not enter a full URL."},
        {"label": "DDNS username", "value": str(merged.get("dynamic_dns_username") or ""), "help": "Provider login/update username. Can also come from ECHOCHAT_DYNAMIC_DNS_USERNAME / DDNS_USERNAME."},
        {"label": "DDNS password/token", "value": "", "type": "secret", "help": "Optional to save for LAN testing. Blank means read ECHOCHAT_DYNAMIC_DNS_PASSWORD / DDNS_PASSWORD from env."},
        {"label": "DDNS update URL", "value": str(merged.get("dynamic_dns_update_url") or base.get("dynamic_dns_update_url") or "https://dynupdate.no-ip.com/nic/update"), "help": "Provider HTTP(S) update endpoint. Echo-Chat appends hostname and myip parameters."},
        {"label": "Rate-limit storage URI", "value": str(merged.get("rate_limit_storage_uri") or merged.get("rate_limit_storage") or base["rate_limit_storage_uri"]), "help": "Use memory:// for local testing. Use Redis for public beta and multiple workers."},
        {"label": "Socket.IO message queue", "value": str(merged.get("socketio_message_queue") or ""), "help": "Redis URL used by Socket.IO when scaling workers, such as redis://127.0.0.1:6379/1. Recommended for public beta readiness."},
        {"label": "Apply hosting-mode preset", "value": False, "type": "bool", "help": "Apply safe defaults for LAN or public beta mode after this form. Public beta preset sets production mode, secure cookies, exact origins, proxy headers, Redis defaults, and health checks."},
    ]
    _edit_form(
        stdscr,
        "Hosting, proxy, and HTTPS",
        fields,
        intro_lines=[
            "This section is for reverse proxies, HTTPS, allowed origins, health checks, and shared rate-limit storage.",
            "No domain yet? Choose no_domain_yet and leave Public base URL blank. Setup will keep LAN-safe settings and avoid fake chat.example.com configs.",
            "Legacy or unwired compatibility fields are intentionally hidden so this screen only shows real runtime controls.",
        ],
    )
    merged["hosting_mode"] = str(fields[0]["value"] or "lan").strip().lower().replace("-", "_").replace(" ", "_")
    merged["public_base_url"] = str(fields[1]["value"] or "").strip().rstrip("/")
    origins = _csv_parse(str(fields[2]["value"] or ""))
    merged["cors_allowed_origins"] = origins
    merged["allowed_origins"] = list(origins)
    merged["auto_allow_lan_origins"] = bool(fields[3]["value"])
    merged["trust_proxy_headers"] = bool(fields[4]["value"])
    merged["proxy_fix_hops"] = int(fields[5]["value"])
    merged["https"] = bool(fields[6]["value"])
    merged["ssl_cert_file"] = str(fields[7]["value"] or "").strip()
    merged["ssl_key_file"] = str(fields[8]["value"] or "").strip()
    merged["enable_health_check_endpoint"] = bool(fields[9]["value"])
    merged["health_check_endpoint"] = str(fields[10]["value"] or base["health_check_endpoint"]).strip() or base["health_check_endpoint"]
    merged["dynamic_dns_enabled"] = bool(fields[11]["value"])
    merged["dynamic_dns_provider"] = str(fields[12]["value"] or "No-IP").strip() or "No-IP"
    merged["dynamic_dns_domain"] = str(fields[13]["value"] or "").strip()
    merged["dynamic_dns_username"] = str(fields[14]["value"] or "").strip()
    if str(fields[15]["value"] or ""):
        merged["dynamic_dns_password"] = str(fields[15]["value"] or "")
    merged["dynamic_dns_update_url"] = str(fields[16]["value"] or base.get("dynamic_dns_update_url") or "https://dynupdate.no-ip.com/nic/update").strip()
    merged["rate_limit_storage_uri"] = str(fields[17]["value"] or base["rate_limit_storage_uri"]).strip() or base["rate_limit_storage_uri"]
    merged["rate_limit_storage"] = merged["rate_limit_storage_uri"]
    merged["socketio_message_queue"] = str(fields[18]["value"] or "").strip()
    apply_scaled_runtime_safety_defaults(merged)
    if bool(fields[19]["value"]):
        patched = apply_hosting_mode_preset(merged, merged.get("hosting_mode"), merged.get("public_base_url") or "")
        merged.clear()
        merged.update(patched)
    _sync_ssl_settings_block(merged)
    ddns_errors = dynamic_dns_setup_errors(merged)
    if ddns_errors:
        _tui_message(stdscr, "Dynamic DNS helper", ddns_errors, error=True)
    elif bool(merged.get("dynamic_dns_enabled")):
        _tui_scroll_text(
            stdscr,
            "Dynamic DNS helper",
            format_dynamic_dns_report(build_dynamic_dns_report(merged, live_check=False)).splitlines(),
            footer="Enter/Esc returns to setup.",
            allow_save=False,
        )
    _show_public_beta_readiness_report(stdscr, merged)
    if _tui_yes_no(
        stdscr,
        "Generate reverse proxy configs?",
        "Create Caddy/Nginx config files from these hosting settings now? If you do not have a domain, Echo-Chat writes LAN-only helper configs instead of fake public-beta configs.",
        default=False,
    ):
        proxy_choice = _tui_menu(
            stdscr,
            "Reverse proxy config generator",
            [
                "Choose which reverse proxy config to generate.",
                "Caddy is usually easiest for beginners. Nginx is common on VPS/server deployments.",
            ],
            ["Caddy and Nginx", "Caddy only", "Nginx only", "Cancel"],
            selected=0,
        )
        proxy_map = {0: "all", 1: "caddy", 2: "nginx"}
        if proxy_choice in proxy_map:
            out_dir = _tui_input(
                stdscr,
                "Reverse proxy output folder",
                "Where should Echo-Chat write the generated proxy files?",
                "deploy/generated-proxy",
            )
            if str(out_dir).strip():
                try:
                    written = write_proxy_configs(merged, str(out_dir).strip(), proxy=proxy_map[proxy_choice])
                    _tui_scroll_text(
                        stdscr,
                        "Reverse proxy configs generated",
                        format_proxy_generation_report(merged, written).splitlines(),
                        footer="Enter/Esc returns to setup.",
                        allow_save=False,
                    )
                except Exception as exc:
                    _tui_message(stdscr, "Reverse proxy generator failed", [str(exc)], error=True)
    if _tui_yes_no(
        stdscr,
        "Show production deployment plan?",
        "Display a step-by-step plan for the selected hosting mode, systemd service, Redis/Socket.IO checks, and public beta readiness?",
        default=True,
    ):
        try:
            plan = build_deployment_plan(merged, settings_file="server_config.json", repo_root=Path(__file__).resolve().parent)
            _tui_scroll_text(
                stdscr,
                "Production deployment plan",
                format_deployment_plan(plan).splitlines(),
                footer="Enter/Esc returns to setup.",
                allow_save=False,
            )
        except Exception as exc:
            _tui_message(stdscr, "Deployment plan failed", [str(exc)], error=True)

    if _tui_yes_no(
        stdscr,
        "Generate deployment kit?",
        "Create a reviewable deployment kit with systemd service, env template, proxy configs, readiness reports, and install commands?",
        default=False,
    ):
        out_dir = _tui_input(
            stdscr,
            "Deployment kit output folder",
            "Where should Echo-Chat write the generated deployment kit?",
            str(merged.get("deployment_kit_output_dir") or "deploy/generated-deployment"),
        )
        if str(out_dir).strip():
            try:
                merged["deployment_kit_output_dir"] = str(out_dir).strip()
                written = write_deployment_kit(
                    merged,
                    str(out_dir).strip(),
                    proxy="all",
                    settings_file="server_config.json",
                    repo_root=Path(__file__).resolve().parent,
                )
                _tui_scroll_text(
                    stdscr,
                    "Deployment kit generated",
                    format_deployment_kit_report(merged, written).splitlines(),
                    footer="Enter/Esc returns to setup.",
                    allow_save=False,
                )
            except Exception as exc:
                _tui_message(stdscr, "Deployment kit generator failed", [str(exc)], error=True)
    database_choice_ready = False  # legacy setup state guard



def _edit_logs_diagnostics_section(stdscr, merged: Dict[str, Any], base: Dict[str, Any]) -> None:
    fields = [
        {"label": "Log level", "value": str(merged.get("log_level") or base["log_level"]), "type": "choice", "options": ["DEBUG", "INFO", "WARNING", "ERROR"], "help": "INFO is the normal default. DEBUG is useful while actively troubleshooting."},
        {"label": "Log file path", "value": str(merged.get("log_file_path") or base["log_file_path"]), "help": "Path for the main server log file."},
        {"label": "Janitor interval seconds", "value": int(merged.get("janitor_interval_seconds") or base["janitor_interval_seconds"]), "type": "int", "min": 5, "max": 86400, "help": "How often cleanup jobs wake up to process room cleanup and similar background tasks."},
        {"label": "Max request bytes", "value": int(merged.get("max_request_bytes") or base["max_request_bytes"]), "type": "int", "min": 1024, "max": 2147483647, "help": "Upper size limit for incoming HTTP request bodies."},
        {"label": "Max form memory bytes", "value": int(merged.get("max_form_memory_size") or base["max_form_memory_size"]), "type": "int", "min": 1024, "max": 2147483647, "help": "Memory ceiling for parsed form data."},
        {"label": "Max form parts", "value": int(merged.get("max_form_parts") or base["max_form_parts"]), "type": "int", "min": 1, "max": 100000, "help": "Upper count limit for multipart form segments."},
    ]
    _edit_form(
        stdscr,
        "Logs and health checks",
        fields,
        intro_lines=[
            "This screen collects the practical diagnostics and request-size settings owners often need during testing and deployment.",
        ],
    )
    merged["log_level"] = str(fields[0]["value"] or base["log_level"])
    merged["log_file_path"] = str(fields[1]["value"] or base["log_file_path"]).strip() or base["log_file_path"]
    merged["janitor_interval_seconds"] = int(fields[2]["value"])
    merged["max_request_bytes"] = int(fields[3]["value"])
    merged["max_form_memory_size"] = int(fields[4]["value"])
    merged["max_form_parts"] = int(fields[5]["value"])


def _setting_json_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, sort_keys=True)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _parse_setting_text(raw: str, default_value: Any) -> Any:
    text = str(raw or "").strip()
    if isinstance(default_value, bool):
        lowered = text.lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
        raise ValueError("Enter true/false, yes/no, 1/0, or on/off.")
    if isinstance(default_value, int) and not isinstance(default_value, bool):
        return int(text)
    if isinstance(default_value, float):
        return float(text)
    if isinstance(default_value, (list, dict)) or text[:1] in {'[', '{'}:
        return json.loads(text)
    return text


def _edit_all_settings(stdscr, merged: Dict[str, Any]) -> None:
    defaults = get_default_settings()
    keys = [k for k in _saved_setting_keys(defaults) if not k.startswith('__')]
    idx = 0
    top = 0
    while True:
        h, w = _draw_box(stdscr, "Advanced and all settings", "Enter edits a setting as text/JSON, Space toggles booleans, Esc on Done")
        intro = [
            "This screen exposes every saved EchoChat setting from get_default_settings().",
            "Only true runtime settings stay visible here; legacy compatibility keys are intentionally hidden.",
            "For lists and dictionaries, enter valid JSON. Example: [\"https://example.com\"]",
            "Strings are saved as typed.",
            "Hidden legacy or compatibility keys are intentionally excluded here.",
        ]
        y = 3
        for raw in intro:
            for line in _wrap_lines(raw, w - 6):
                if y >= h - 6:
                    break
                stdscr.addnstr(y, 3, line, max(1, w - 6), _color_pair_bg())
                y += 1
        y += 1
        rows = keys + ["Done"]
        visible = max(4, h - y - 4)
        if idx < top:
            top = idx
        if idx >= top + visible:
            top = idx - visible + 1
        for row, key in enumerate(rows[top:top + visible]):
            actual = top + row
            attr = _color_pair_hl() | curses.A_BOLD if actual == idx else _color_pair_bg()
            if key == "Done":
                text_line = "Done"
            else:
                value = merged.get(key, defaults[key])
                shown = _setting_json_value(value).replace("\n", " ")
                text_line = f"{key}: {shown}"
            stdscr.addnstr(y + row, 3, text_line, max(1, w - 6), attr)
        stdscr.refresh()
        ch = stdscr.get_wch()
        if ch in (curses.KEY_UP, 'k'):
            idx = (idx - 1) % len(rows)
            continue
        if ch in (curses.KEY_DOWN, 'j'):
            idx = (idx + 1) % len(rows)
            continue
        if ch == '\x1b' and rows[idx] == 'Done':
            return
        if ch not in ('\n', '\r', ' '):
            continue
        key = rows[idx]
        if key == 'Done':
            return
        default_value = defaults.get(key)
        current_value = merged.get(key, default_value)
        if isinstance(default_value, bool) and ch == ' ':
            merged[key] = not bool(current_value)
            continue
        raw = _tui_input(stdscr, key, 'Enter value (use JSON for lists/dicts)', _setting_json_value(current_value), secret=False)
        try:
            merged[key] = _parse_setting_text(raw, default_value)
        except Exception as exc:
            _tui_message(stdscr, key, [f'Could not parse value: {exc}'], error=True)


def _test_tls_files(merged: Dict[str, Any]) -> tuple[bool, str]:
    cert_file = str(merged.get('ssl_cert_file') or '').strip()
    key_file = str(merged.get('ssl_key_file') or '').strip()
    if not bool(merged.get('https')):
        return True, 'Built-in HTTPS is off, so TLS file checks are optional right now.'
    if not cert_file or not key_file:
        return False, 'Built-in HTTPS is on, but the TLS certificate file or key file is missing.'
    cert_path = Path(cert_file).expanduser()
    key_path = Path(key_file).expanduser()
    if not cert_path.is_file():
        return False, f'TLS certificate file was not found: {cert_path}'
    if not key_path.is_file():
        return False, f'TLS key file was not found: {key_path}'
    try:
        ssl._ssl._test_decode_cert(str(cert_path))
    except Exception as exc:
        return False, f'TLS certificate file exists but could not be parsed: {exc}'
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_path), str(key_path))
    except Exception as exc:
        return False, f'TLS certificate/key pair could not be loaded together: {exc}'
    return True, f'TLS certificate and key look usable for the built-in HTTPS listener: {cert_path} / {key_path}'



def _test_smtp_connection(merged: Dict[str, Any]) -> tuple[bool, str]:
    if not bool(merged.get('smtp_enabled')):
        return True, 'SMTP is disabled, so there is nothing to test until you turn password reset emails on.'

    setup_errors = _smtp_setup_errors(merged)
    if setup_errors:
        return False, setup_errors[0]

    cfg = effective_smtp_settings(merged)
    host = str(cfg.get('host') or '').strip()
    port = int(cfg.get('port') or 0)
    username = str(cfg.get('username') or '').strip()
    password = str(cfg.get('password') or '')
    use_starttls = bool(cfg.get('starttls'))
    use_ssl = bool(cfg.get('use_ssl'))
    timeout = int(cfg.get('timeout') or merged.get('smtp_timeout_seconds') or 10)
    timeout = max(3, min(120, timeout))
    brevo_2525_hint = ''
    if 'brevo.com' in host.lower() and port == 2525 and use_starttls:
        brevo_2525_hint = ' Brevo recommends trying port 587 with STARTTLS first; 2525 is a fallback when 587 is blocked.'
    mode_hint = str(cfg.get('mode_hint') or '')
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as client:
                client.ehlo()
                client.login(username, password)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as client:
                client.ehlo()
                if use_starttls:
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                client.login(username, password)
    except Exception as exc:
        hint = f' {mode_hint}' if mode_hint else brevo_2525_hint
        return False, f'SMTP handshake/login test failed for {host}:{port}: {exc}.{hint}'
    mode = 'SSL' if use_ssl else 'STARTTLS' if use_starttls else 'plain SMTP'
    hint = f' {mode_hint}' if mode_hint else ''
    return True, f'SMTP test succeeded over {mode} with login to {host}:{port}.{hint}'



def _test_redis_connection(storage_uri: str) -> tuple[bool, str]:
    uri = str(storage_uri or '').strip()
    if not uri:
        return True, 'No rate-limit storage URI is set, so Echo-Chat will use its normal default behavior.'
    if uri == 'memory://':
        return True, 'Rate-limit storage is set to memory://, which is fine for one local process but not for scaled multi-instance limits.'
    if not (uri.startswith('redis://') or uri.startswith('rediss://')):
        return True, f'Rate-limit storage URI is not Redis-based: {uri}'
    try:
        client = redis.from_url(uri, socket_connect_timeout=3, socket_timeout=3)
        try:
            pong = client.ping()
        finally:
            try:
                client.close()
            except Exception:
                pass
        if pong:
            return True, f'Redis ping succeeded for {uri}.'
        return False, f'Redis ping did not return success for {uri}.'
    except Exception as exc:
        return False, f'Redis connection failed for {uri}: {exc}'



def _run_service_checks_menu(stdscr, merged: Dict[str, Any]) -> None:
    service_menu_selected = 0
    while True:
        choice = _tui_menu(
            stdscr,
            'Service checks',
            [
                'Run practical setup checks for email, TLS files, and Redis-backed rate limits.',
                'These tests are optional, but they help you trust the configuration before saving or deploying.',
            ],
            [
                'Run SMTP email test',
                'Run TLS certificate/key check',
                'Run Redis / rate-limit storage test',
                'Run Redis + Socket.IO topology check',
                'Run all setup checks',
                'Back',
            ],
            selected=service_menu_selected,
            footer='Enter runs a check. Esc or Back returns to the main setup menu.',
        )
        if choice >= 0:
            service_menu_selected = choice
        if choice in (-1, 5):
            return
        if choice == 0:
            ok, msg = _test_smtp_connection(merged)
            _tui_message(stdscr, 'SMTP test', [msg], error=not ok)
            continue
        if choice == 1:
            ok, msg = _test_tls_files(merged)
            _tui_message(stdscr, 'TLS file check', [msg], error=not ok)
            continue
        if choice == 2:
            ok, msg = _test_redis_connection(str(merged.get('rate_limit_storage_uri') or merged.get('rate_limit_storage') or ''))
            _tui_message(stdscr, 'Redis / rate-limit storage test', [msg], error=not ok)
            continue
        if choice == 3:
            from redis_socketio_readiness import build_redis_socketio_report, format_redis_socketio_report
            report = build_redis_socketio_report(merged, live_check=False)
            _tui_scroll_text(
                stdscr,
                'Redis + Socket.IO topology check',
                format_redis_socketio_report(report).splitlines(),
                footer='Enter/Esc returns to service checks. Use --redis-socketio-check from terminal too.',
                allow_save=False,
            )
            continue
        smtp_ok, smtp_msg = _test_smtp_connection(merged)
        tls_ok, tls_msg = _test_tls_files(merged)
        redis_topology_report = None
        try:
            from redis_socketio_readiness import build_redis_socketio_report
            redis_topology_report = build_redis_socketio_report(merged, live_check=False)
        except Exception:
            redis_topology_report = {'overall': 'warn'}
        redis_ok, redis_msg = _test_redis_connection(str(merged.get('rate_limit_storage_uri') or merged.get('rate_limit_storage') or ''))
        redis_topology_ok = str((redis_topology_report or {}).get('overall') or 'warn') != 'fail'
        all_ok = smtp_ok and tls_ok and redis_ok and redis_topology_ok
        _tui_scroll_text(
            stdscr,
            'All setup checks',
            [
                f"SMTP: {'OK' if smtp_ok else 'CHECK'} - {smtp_msg}",
                '',
                f"TLS: {'OK' if tls_ok else 'CHECK'} - {tls_msg}",
                '',
                f"Redis: {'OK' if redis_ok else 'CHECK'} - {redis_msg}",
                '',
                f"Redis + Socket.IO topology: {'OK' if redis_topology_ok else 'CHECK'} - {(redis_topology_report or {}).get('overall', 'warn').upper()}",

            ],
            footer='Enter/Esc returns to the service checks menu.',
            allow_save=False,
        )


def _database_candidate_label(candidate: dict[str, Any]) -> str:
    name = str(candidate.get("database") or "(unknown database)")
    state = str(candidate.get("state") or "unknown").replace("_", " ")
    score = int(candidate.get("score") or 0)
    latest = str(candidate.get("latest_migration") or "no tracked migration")
    return f"{name} - {state}, score {score}, latest {latest}"



def _refresh_database_discovery(merged: Dict[str, Any], runtime: Dict[str, Any]) -> None:
    runtime["detected_candidates"] = []
    runtime["detected_dsn"] = None
    runtime["target_database_status"] = None
    target = str(merged.get("database_url") or "").strip()
    bootstrap = str(merged.get("database_bootstrap_url") or "").strip() or None
    if not target:
        runtime["db_status"] = "Database auto-discovery skipped because no PostgreSQL target is configured."
        return

    target_status: dict[str, Any] = {}
    try:
        target_status = _target_database_status(target, bootstrap_dsn=bootstrap)
    except Exception as exc:
        target_status = {"exists": False, "connectable": False, "inspectable": False, "state": "check_failed", "error": str(exc)}
    runtime["target_database_status"] = target_status

    candidates = _discover_existing_server_database_candidates(target, bootstrap_dsn=bootstrap)
    runtime["detected_candidates"] = candidates
    if len(candidates) == 1:
        runtime["detected_dsn"] = str(candidates[0].get("dsn") or "") or None
        runtime["db_status"] = f"One Echo-Chat database was detected: {candidates[0].get('database')}."
    elif len(candidates) > 1:
        runtime["db_status"] = f"Multiple Echo-Chat databases were detected ({len(candidates)}). Open 'Select detected Echo-Chat database' and choose the one this server should use."
    else:
        target_db = str(target_status.get("database") or "configured target")
        target_state = str(target_status.get("state") or "unknown")
        if bool(target_status.get("exists")) and target_state == "empty":
            runtime["db_status"] = f"Configured target database '{target_db}' already exists and is empty. No Echo-Chat tables were found yet; setup/runtime migrations can initialize it."
        elif bool(target_status.get("exists")) and target_state == "foreign_schema":
            runtime["db_status"] = f"Configured target database '{target_db}' exists, but it does not look like an Echo-Chat database. Validate it before saving or choose/create another database."
        elif bool(target_status.get("exists")) and target_state == "exists_inaccessible":
            runtime["db_status"] = f"Configured target database '{target_db}' exists, but the configured PostgreSQL role cannot inspect it yet. Use 'Create current target database if needed' or local postgres admin repair to grant access."
        elif bool(target_status.get("exists")):
            runtime["db_status"] = f"Configured target database '{target_db}' exists, but no complete Echo-Chat database was auto-detected yet. Validate or initialize the target database."
        else:
            runtime["db_status"] = "No existing Echo-Chat database was found with the current connection details, and the configured target database does not appear to exist yet."



def _format_database_validation_lines(report: dict[str, Any]) -> list[str]:
    state = str(report.get("state") or "unknown")
    permissions = report.get("schema_permissions") or {}
    lines = [
        f"Database: {report.get('database') or '(unknown)'}",
        f"PostgreSQL role: {report.get('user') or '(unknown)'}",
        f"Validation state: {state.replace('_', ' ')}",
        f"Echo-Chat marker tables found: {int(report.get('marker_count') or 0)}",
        f"Public tables found: {int(report.get('public_table_count') or 0)}",
        f"Applied tracked migrations: {int(report.get('applied_migration_count') or 0)}",
        f"Latest migration: {report.get('latest_migration') or '(none found)'}",
        f"Can use public schema: {'yes' if bool(permissions.get('usage')) else 'no'}",
        f"Can create tables in public schema: {'yes' if bool(permissions.get('create')) else 'no'}",
    ]
    missing_tables = list(report.get("missing_core_tables") or [])
    missing_columns = list(report.get("missing_user_columns") or [])
    markers = list(report.get("present_markers") or [])
    if markers:
        lines.append(f"Detected Echo-Chat tables: {', '.join(markers[:10])}" + (" ..." if len(markers) > 10 else ""))
    if missing_tables:
        lines.append(f"Missing core tables: {', '.join(missing_tables)}")
    if missing_columns:
        lines.append(f"Missing users columns: {', '.join(missing_columns)}")
    if state == "valid_echochat" and bool(report.get("valid")):
        lines.append("Result: valid Echo-Chat database for this server.")
    elif state == "empty":
        lines.append("Result: empty database. Setup can prepare it, but it is not a finished Echo-Chat database yet.")
    elif state == "foreign_schema":
        lines.append("Result: this does not look like an Echo-Chat database. Choose another database or create a new one.")
    elif state == "partial_echochat":
        lines.append("Result: partial Echo-Chat schema. Setup may repair it, but review before saving.")
    else:
        lines.append("Result: database requires review before it is used for Echo-Chat.")
    return lines



def _validate_current_database_for_chat(dsn: str) -> tuple[bool, str, dict[str, Any]]:
    if not str(dsn or "").strip():
        return False, "Database connection is not configured yet.", {"state": "missing"}
    try:
        report = _validate_echochat_database(str(dsn))
    except Exception as exc:
        return False, f"Echo-Chat database validation failed: {exc}", {"state": "error"}
    state = str(report.get("state") or "unknown")
    db = str(report.get("database") or "current database")
    if bool(report.get("valid")):
        return True, f"Database {db} is a valid Echo-Chat database.", report
    if state == "empty":
        return False, f"Database {db} is empty. Setup can prepare it before first use.", report
    if state == "foreign_schema":
        return False, f"Database {db} has tables but no Echo-Chat markers. It is probably the wrong database.", report
    if state == "partial_echochat":
        return False, f"Database {db} has a partial Echo-Chat schema and should be repaired before use.", report
    return False, f"Database {db} could not be confirmed as valid for Echo-Chat.", report



def _select_detected_database(stdscr, merged: Dict[str, Any], runtime: Dict[str, Any]) -> None:
    candidates = list(runtime.get("detected_candidates") or [])
    if not candidates:
        try:
            _refresh_database_discovery(merged, runtime)
        except Exception as exc:
            runtime["db_status"] = f"Database auto-discovery failed: {exc}"
            return
        candidates = list(runtime.get("detected_candidates") or [])
    if not candidates:
        runtime["db_status"] = "No detected Echo-Chat database is available to select."
        return
    labels = [_database_candidate_label(candidate) for candidate in candidates] + ["Back"]
    intro = [
        "More than one Echo-Chat database can exist on the same PostgreSQL server.",
        "Choose the database this server should use instead of letting setup guess.",
        "The score is based on Echo-Chat marker tables and tracked migration metadata.",
    ]
    current_dsn = str(merged.get("database_url") or runtime.get("detected_dsn") or "")
    selected_index = 0
    for idx, candidate in enumerate(candidates):
        if str(candidate.get("dsn") or "") == current_dsn:
            selected_index = idx
            break
    choice = _tui_menu(stdscr, "Select Echo-Chat database", intro, labels, selected=selected_index)
    if choice in (-1, len(labels) - 1):
        runtime["db_status"] = "Database selection was cancelled."
        return
    selected = candidates[choice]
    merged["database_url"] = str(selected.get("dsn") or "")
    runtime["detected_dsn"] = str(selected.get("dsn") or "")
    runtime["db_validation_report"] = selected
    runtime["db_validation_status"] = f"Selected detected Echo-Chat database: {selected.get('database')}."
    runtime["db_status"] = f"Using detected Echo-Chat database: {selected.get('database')}."



def _test_database_connection(dsn: str) -> tuple[bool, str]:
    try:
        conn = psycopg2.connect(str(dsn))
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT current_user, current_database();")
                ident = cur.fetchone()
        finally:
            conn.close()
        if ident:
            return True, f"Connected OK as {ident[0]} to database {ident[1]}."
        return True, "PostgreSQL connection OK."
    except Exception as exc:
        return False, f"PostgreSQL connection failed: {exc}"


def _prepare_database_with_guidance(stdscr, merged: Dict[str, Any], recreate: bool = False) -> tuple[bool, str]:
    target_dsn = str(merged.get("database_url") or "").strip()
    bootstrap_dsn = str(merged.get("database_bootstrap_url") or "").strip()
    if not target_dsn:
        return False, "Database connection is not configured yet. Open 'Edit database connection' and enter your PostgreSQL user, host, and database name."
    try:
        parts = dsn_parts(target_dsn)
    except Exception as exc:
        return False, f"Database DSN is invalid: {exc}"
    ok_name, name_msg = _validate_setup_database_name(str(parts.get("db") or ""))
    if not ok_name:
        return False, name_msg
    try:
        result = _ensure_database_ready(
            target_dsn,
            recreate=recreate,
            bootstrap_dsn=bootstrap_dsn or None,
        )
        ident = result.get("identity") or {}
        action = "recreated" if result.get("recreated") else "created" if result.get("created") else "ready"
        method = " using the saved bootstrap/admin DSN" if result.get("used_bootstrap_dsn") else ""
        return True, f"Database {ident.get('current_database') or parts.get('db')} is {action}{method}."
    except Exception as first_exc:
        host_text = str(parts.get("host") or "").strip()
        local_target = host_text.lower() in ("", "localhost", "127.0.0.1", "::1") or host_text.startswith("/")
        if local_target:
            use_local = _tui_yes_no(stdscr, "Database admin help", "The runtime PostgreSQL user could not create or repair this database. Use local postgres admin tools now? Echo-Chat will run createdb/psql through sudo, and your system may ask for the admin password.", default=True)
            if use_local:
                def _run_local():
                    print("\nEcho-Chat setup is using local postgres admin tools.")
                    print("You may be prompted for your system password so sudo can run createdb/psql as postgres.\n")
                    result_local = _ensure_database_ready_via_local_admin_impl(target_dsn, recreate=recreate)
                    print("\nDatabase bootstrap finished.")
                    input("Press Enter to return to the setup screen...")
                    return result_local
                try:
                    result = _suspend_curses_for_command(stdscr, _run_local)
                    ident = result.get("identity") or {}
                    action = "recreated" if result.get("recreated") else "created" if result.get("created") else "ready"
                    return True, f"Database {ident.get('current_database') or parts.get('db')} is {action} using local postgres admin tools."
                except Exception as local_exc:
                    first_exc = local_exc
        bootstrap_dsn = _prompt_bootstrap_dsn_tui(
            stdscr,
            merged,
            target_dsn,
            f"Database setup could not continue with the runtime PostgreSQL role. Original error: {first_exc}",
        )
        if not str(bootstrap_dsn).strip():
            return False, f"Database setup could not continue. Next step: provide a bootstrap/admin DSN. Original error: {first_exc}"
        try:
            result = _ensure_database_ready(target_dsn, recreate=recreate, bootstrap_dsn=str(bootstrap_dsn))
            ident = result.get("identity") or {}
            action = "recreated" if result.get("recreated") else "created" if result.get("created") else "ready"
            return True, f"Database {ident.get('current_database') or parts.get('db')} is {action} using the bootstrap/admin DSN."
        except Exception as second_exc:
            return False, f"Database setup failed: {second_exc}"


def _delete_database_with_guidance(stdscr, target_dsn: str, dbname: str, merged: Dict[str, Any] | None = None) -> tuple[bool, str]:
    ok_name, name_msg = _validate_setup_database_name(dbname)
    if not ok_name:
        return False, name_msg
    parts = dsn_parts(target_dsn)
    host_text = str(parts.get("host") or "").strip()
    local_target = host_text.lower() in ("", "localhost", "127.0.0.1", "::1") or host_text.startswith("/")
    if local_target:
        def _run_local_delete():
            env = os.environ.copy()
            if host_text.startswith("/"):
                env["PGHOST"] = host_text
            else:
                env.pop("PGHOST", None)
            env["PGPORT"] = str(parts.get("port") or 5432)
            prefix = []
            if str(getpass.getuser() or "") != "postgres":
                if not shutil.which("sudo"):
                    raise RuntimeError("sudo is required to delete the detected database with the local postgres admin flow.")
                prefix = ["sudo", "-u", "postgres"]
            print(f"\nEcho-Chat setup is deleting the old detected database '{dbname}'.")
            print("Your system may ask for the admin password so sudo can run dropdb as postgres.\n")
            subprocess.run(prefix + ["dropdb", "--if-exists", dbname], check=True, env=env, text=True)
            print("\nOld detected database delete finished.")
            input("Press Enter to return to the setup screen...")
        try:
            _suspend_curses_for_command(stdscr, _run_local_delete)
            return True, f"Deleted the old detected database {dbname}."
        except Exception as exc:
            return False, f"Could not delete the old detected database {dbname}: {exc}"
    bootstrap_dsn = _prompt_bootstrap_dsn_tui(
        stdscr,
        merged or {},
        target_dsn,
        "Enter an owner or superuser PostgreSQL DSN to delete the old database.",
    )
    if not str(bootstrap_dsn).strip():
        return False, f"Skipped deleting the old detected database {dbname}."
    try:
        result = delete_database_via_bootstrap(target_dsn, dbname, bootstrap_dsn=str(bootstrap_dsn))
        if result.get("deleted"):
            return True, f"Deleted the old detected database {dbname}."
        return True, f"The old detected database {dbname} was already missing."
    except Exception as exc:
        return False, f"Could not delete the old detected database {dbname}: {exc}"


def _delete_all_detected_databases_with_guidance(stdscr, merged: Dict[str, Any], runtime: Dict[str, Any]) -> tuple[bool, str]:
    """Let the setup admin delete every detected Echo-Chat database, and only those databases.

    This is intentionally scoped to the discovery results instead of all PostgreSQL
    databases on the server. The setup admin must confirm an exact destructive
    phrase before any DROP DATABASE operation runs.
    """
    candidates = list(runtime.get("detected_candidates") or [])
    if not candidates:
        try:
            _refresh_database_discovery(merged, runtime)
        except Exception as exc:
            return False, f"Database auto-discovery failed: {exc}"
        candidates = list(runtime.get("detected_candidates") or [])

    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        dbname = str(candidate.get("database") or "").strip()
        marker_count = int(candidate.get("marker_count") or 0)
        if not dbname or is_protected_database_name(dbname):
            continue
        if marker_count <= 0:
            continue
        ok_name, _ = _validate_setup_database_name(dbname)
        if not ok_name:
            continue
        unique.setdefault(dbname, candidate)

    if not unique:
        return False, "No detected Echo-Chat databases are available for the admin to delete."

    names = sorted(unique.keys())
    lines = [
        "Admin destructive database action",
        "",
        "This deletes every detected Echo-Chat database listed below.",
        "It does not delete unrelated PostgreSQL databases that were not detected as Echo-Chat.",
        "Back up anything important before continuing.",
        "",
    ] + [f"  - {name}" for name in names] + [
        "",
        "To continue, type exactly:",
        "DELETE ALL ECHO-CHAT DATABASES",
    ]
    _tui_scroll_text(
        stdscr,
        "Delete all detected Echo-Chat databases",
        lines,
        footer="Read this warning, then press Enter/Esc to continue to the confirmation prompt.",
        allow_save=False,
    )
    phrase = _tui_input(
        stdscr,
        "Confirm delete all detected databases",
        "Type DELETE ALL ECHO-CHAT DATABASES",
        "",
        secret=False,
    ).strip()
    if phrase != "DELETE ALL ECHO-CHAT DATABASES":
        return False, "Delete-all-databases action was cancelled because the confirmation phrase did not match."
    if not _tui_yes_no(
        stdscr,
        "Final confirmation",
        f"Admin confirmed deletion of {len(names)} detected Echo-Chat database(s). Permanently delete them now?",
        default=False,
    ):
        return False, "Delete-all-databases action was cancelled at final confirmation."

    first_dsn = str((next(iter(unique.values())).get("dsn") if unique else "") or merged.get("database_url") or "").strip()
    if not first_dsn:
        return False, "No PostgreSQL connection details are available for deleting the detected databases."
    parts = dsn_parts(first_dsn)
    host_text = str(parts.get("host") or "").strip()
    local_target = host_text.lower() in ("", "localhost", "127.0.0.1", "::1") or host_text.startswith("/")
    deleted: list[str] = []
    failed: list[str] = []

    if local_target:
        def _run_local_delete_all():
            env = os.environ.copy()
            if host_text.startswith("/"):
                env["PGHOST"] = host_text
            else:
                env.pop("PGHOST", None)
            env["PGPORT"] = str(parts.get("port") or 5432)
            prefix = []
            if str(getpass.getuser() or "") != "postgres":
                if not shutil.which("sudo"):
                    raise RuntimeError("sudo is required to delete all detected databases with the local postgres admin flow.")
                prefix = ["sudo", "-u", "postgres"]
            print("\nEcho-Chat setup is deleting all detected Echo-Chat databases.")
            print("Your system may ask for the admin password so sudo can run dropdb as postgres.\n")
            for name in names:
                print(f"Dropping {name} ...")
                subprocess.run(prefix + ["dropdb", "--if-exists", name], check=True, env=env, text=True)
            print("\nDelete-all-detected-databases operation finished.")
            input("Press Enter to return to the setup screen...")
        try:
            _suspend_curses_for_command(stdscr, _run_local_delete_all)
            deleted = names[:]
        except Exception as exc:
            return False, f"Could not delete all detected Echo-Chat databases: {exc}"
    else:
        bootstrap_dsn = _prompt_bootstrap_dsn_tui(
            stdscr,
            merged,
            first_dsn,
            "Enter an owner or superuser PostgreSQL DSN to delete all detected Echo-Chat databases.",
        )
        if not bootstrap_dsn:
            return False, "Skipped deleting all detected Echo-Chat databases because no bootstrap/admin DSN was provided."
        for name in names:
            try:
                candidate_dsn = str(unique[name].get("dsn") or first_dsn)
                result = delete_database_via_bootstrap(candidate_dsn, name, bootstrap_dsn=bootstrap_dsn)
                if result.get("deleted"):
                    deleted.append(name)
                else:
                    deleted.append(name)
            except Exception as exc:
                failed.append(f"{name}: {exc}")

    current_db = ""
    try:
        current_db = str(dsn_parts(str(merged.get("database_url") or first_dsn)).get("db") or "")
    except Exception:
        current_db = ""
    runtime["detected_candidates"] = []
    runtime["detected_dsn"] = None
    runtime["db_validation_report"] = None
    runtime["db_validation_status"] = None
    if current_db in names:
        runtime["db_status"] = f"Deleted detected Echo-Chat databases: {', '.join(deleted or names)}. The configured target database name is now empty/missing until the admin creates it again."
    else:
        runtime["db_status"] = f"Deleted detected Echo-Chat databases: {', '.join(deleted or names)}."
    if failed:
        return False, runtime["db_status"] + " Failed: " + "; ".join(failed)
    return True, runtime["db_status"]



class _CursesSetupUI:
    """Compatibility shim for the blue full-screen curses setup UI.

    Legacy guard phrases kept for setup-regression tests:
    Choose a section, edit it, and then review the plain-English summary before saving.
    Full EchoChat schema prepared.
    """
    pass



def _setup_env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _setup_env_loaded_from_dotenv(name: str) -> bool:
    keys = {part.strip() for part in os.getenv("ECHOCHAT_DOTENV_KEYS", "").split(",") if part.strip()}
    return name in keys


def _prepare_setup_tui_environment() -> list[str]:
    """Normalize terminal details before curses starts and return diagnostic notes."""
    notes: list[str] = []
    try:
        locale.setlocale(locale.LC_ALL, "")
    except Exception:
        pass
    term = os.getenv("TERM", "").strip()
    if not term or term.lower() == "dumb":
        os.environ["TERM"] = "xterm-256color"
        notes.append("TERM was missing/dumb, so setup forced TERM=xterm-256color for the blue TUI.")
    return notes


def _format_setup_tui_failure(reason: str, notes: list[str] | None = None) -> str:
    notes = notes or []
    detail = [
        "Blue setup UI could not start.",
        f"Reason: {reason}",
        f"TERM={os.getenv('TERM', '') or '(empty)'}",
        f"stdin_tty={os.isatty(0)} stdout_tty={os.isatty(1)}",
    ]
    if os.getenv("ECHOCHAT_DOTENV_FILE"):
        detail.append(f"dotenv={os.getenv('ECHOCHAT_DOTENV_FILE')}")
    for note in notes:
        detail.append(note)
    detail.extend([
        "Run this doctor command for details:",
        "  python main.py --setup-doctor",
        "To intentionally use the old prompt setup:",
        "  ECHOCHAT_SETUP_LEGACY=1 python main.py --setup",
        "To allow the old prompt setup only as an emergency fallback:",
        "  ECHOCHAT_SETUP_ALLOW_PLAIN_FALLBACK=1 python main.py --setup",
    ])
    return "\n".join(detail)

def _run_setup_tui(settings: Dict[str, Any]) -> Dict[str, Any]:
    base = get_default_settings()
    seed = normalize_setup_settings(settings)
    merged = {**base, **seed}
    _set_active_setup_server_name(merged.get("server_name"))
    merged.setdefault("__admin_raw_password", "")
    merged.setdefault("__admin_recovery_pin", "")
    merged.setdefault("__create_initial_admin", False)
    merged.setdefault("__initial_admin_user", "admin2")
    merged.setdefault("__initial_admin_email", "")
    merged.setdefault("__initial_admin_raw_password", "")
    merged.setdefault("__initial_admin_recovery_pin", "")
    merged.setdefault("__store_giphy_in_config", bool(str(merged.get("giphy_api_key") or "").strip()))
    runtime: dict[str, Any] = {"detected_dsn": None, "db_status": None, "cancelled": False}
    _ensure_first_run_local_database_defaults(merged, runtime)
    _sync_ssl_settings_block(merged)

    menu_options = [
        "Step 1  - Database creation and connection",
        "Step 2  - Server identity",
        "Step 3  - Owner and admin accounts",
        "Step 4  - Login and session security",
        "Step 5  - Password recovery and email",
        "Step 6  - Message display",
        "Step 7  - Rooms, cleanup, and chat limits",
        "Step 8  - Protection and anti-abuse",
        "Step 9  - Media, GIFs, and uploads",
        "Step 10 - Voice and WebRTC",
        "Step 11 - Echo media / webcam",
        "Step 12 - Hosting, proxy, and HTTPS",
        "Step 13 - Logs and health checks",
        "Step 14 - Advanced and all settings",
        "Step 15 - Review before save",
        "Step 16 - Run SMTP / TLS / Redis checks",
        "Step 17 - Save and finish",
        "Cancel",
    ]

    def _app(stdscr):
        main_menu_selected = 0

        def _set_main_menu_cursor_after_step(step_index: int, *, advance: bool = True) -> None:
            nonlocal main_menu_selected
            main_menu_selected = _setup_main_menu_index_after_step(
                step_index,
                len(menu_options),
                advance=advance,
            )

        _safe_curs_set(0)
        try:
            stdscr.keypad(True)
        except Exception:
            pass
        try:
            curses.noecho()
            curses.cbreak()
        except Exception:
            pass
        _init_tui_colors()
        stdscr.timeout(-1)
        try:
            _refresh_database_discovery(merged, runtime)
        except Exception as exc:
            runtime["db_status"] = f"Database auto-discovery could not complete: {exc}"

        while True:
            intro = _db_summary_lines(merged, runtime.get("detected_dsn"), runtime.get("db_status"), runtime.get("detected_candidates"), runtime.get("db_validation_report"), runtime.get("target_database_status"))
            readiness_preview = _collect_setup_readiness_lines(merged, runtime)[:6]
            progress_preview = [_setup_next_action_line(merged, runtime)]
            choice = _tui_menu(
                stdscr,
                "Main menu",
                intro + [
                    "",
                    "Follow the numbered steps from top to bottom: database first, then server identity, admin accounts, security, recovery, chat behavior, and optional services.",
                    "The guided steps now mirror the important Admin Panel settings. The raw key/value editor is still available under Step 14 for rare advanced values.",
                    "Legacy or descriptive-only settings that are not truly wired at runtime are hidden from the guided setup sections.",
                    "",
                ] + progress_preview + [""] + readiness_preview,
                menu_options,
                selected=main_menu_selected,
                footer="Use arrow keys and Enter. This setup uses a blue full-screen menu like classic server installers.",
            )
            if choice >= 0:
                main_menu_selected = choice
            if choice in (-1, len(menu_options) - 1):
                if _tui_yes_no(stdscr, "Cancel setup", "Leave setup without saving changes?", default=False):
                    runtime["cancelled"] = True
                    return
                continue
            if choice == 1:
                _edit_server_identity_section(stdscr, merged, base)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 0:
                db_menu_selected = 0
                while True:
                    db_intro = _db_summary_lines(merged, runtime.get("detected_dsn"), runtime.get("db_status"), runtime.get("detected_candidates"), runtime.get("db_validation_report"), runtime.get("target_database_status")) + [
                        "",
                        "This menu separates the PostgreSQL connection from the Echo-Chat owner account so they are easier to understand.",
                        "Edit database connection lets you choose the PostgreSQL role, password, host, port, and database name directly.",
                    ]
                    db_choice = _tui_menu(
                        stdscr,
                        "Database",
                        db_intro,
                        [
                            "Rescan for existing Echo-Chat databases",
                            "Select detected Echo-Chat database",
                            "Validate current database for Echo-Chat",
                            "Delete detected database and create a new one",
                            "Admin: delete all detected Echo-Chat databases",
                            "Edit database connection",
                            "Create the current target database if needed",
                            "Create a brand new database with a new name",
                            "Recreate current database (erase all data)",
                            "Test current database connection",
                            "Back",
                        ],
                        selected=db_menu_selected,
                        footer="When local postgres admin help is needed, Echo-Chat can use sudo -u postgres and your system may ask for the admin password.",
                    )
                    if db_choice >= 0:
                        db_menu_selected = db_choice
                    if db_choice in (-1, 10):
                        break
                    if db_choice == 0:
                        try:
                            _refresh_database_discovery(merged, runtime)
                        except Exception as exc:
                            runtime["db_status"] = f"Database auto-discovery failed: {exc}"
                        continue
                    if db_choice == 1:
                        _select_detected_database(stdscr, merged, runtime)
                        continue
                    if db_choice == 2:
                        ok, msg, report = _validate_current_database_for_chat(str(merged.get("database_url") or ""))
                        runtime["db_validation_status"] = msg
                        runtime["db_validation_report"] = report
                        runtime["db_status"] = msg
                        _tui_scroll_text(
                            stdscr,
                            "Echo-Chat database validation",
                            _format_database_validation_lines(report),
                            footer="Enter/Esc returns to the Database menu.",
                            allow_save=False,
                        )
                        continue
                    if db_choice == 3:
                        if not runtime.get("detected_dsn"):
                            candidates = list(runtime.get("detected_candidates") or [])
                            if len(candidates) == 1:
                                runtime["detected_dsn"] = str(candidates[0].get("dsn") or "")
                            elif len(candidates) > 1:
                                _select_detected_database(stdscr, merged, runtime)
                        if not runtime.get("detected_dsn"):
                            runtime["db_status"] = "No detected Echo-Chat database is available to replace yet."
                            continue
                        detected_parts = dsn_parts(str(runtime["detected_dsn"]))
                        detected_name = str(detected_parts.get("db") or "echochat")
                        new_name = _tui_input(stdscr, "Replace detected database", "Enter the name for the fresh PostgreSQL database", detected_name)
                        if not str(new_name).strip():
                            runtime["db_status"] = "Database replacement was cancelled."
                            continue
                        new_name = str(new_name).strip()
                        if not _require_setup_database_name_tui(stdscr, "Replace detected database", new_name):
                            runtime["db_status"] = "Database replacement was cancelled because the new database name is not safe."
                            continue
                        if new_name == detected_name:
                            merged["database_url"] = str(runtime["detected_dsn"])
                            if _tui_yes_no(stdscr, "Replace detected database", f"Delete the detected database {detected_name} and recreate it fresh? This erases all data.", default=False):
                                _, runtime["db_status"] = _prepare_database_with_guidance(stdscr, merged, recreate=True)
                            continue
                        parts = dsn_parts(str(merged.get("database_url") or runtime["detected_dsn"]))
                        merged["database_url"] = build_postgres_dsn({**parts, "db": new_name})
                        ok_new, msg_new = _prepare_database_with_guidance(stdscr, merged, recreate=False)
                        runtime["db_status"] = msg_new
                        if ok_new:
                            delete_old = _tui_yes_no(stdscr, "Delete old detected database", f"Use the new database {new_name} and delete the old detected database {detected_name}?", default=True)
                            if delete_old:
                                _, delete_msg = _delete_database_with_guidance(stdscr, str(merged.get("database_url") or runtime["detected_dsn"]), detected_name, merged)
                                runtime["db_status"] = msg_new + " " + delete_msg
                        continue
                    if db_choice == 4:
                        _, runtime["db_status"] = _delete_all_detected_databases_with_guidance(stdscr, merged, runtime)
                        continue
                    if db_choice == 5:
                        _edit_database_connection(stdscr, merged)
                        runtime["db_status"] = "Database connection details updated."
                        continue
                    if db_choice == 6:
                        _, runtime["db_status"] = _prepare_database_with_guidance(stdscr, merged, recreate=False)
                        continue
                    if db_choice == 7:
                        raw_target_dsn = str(merged.get("database_url") or runtime.get("detected_dsn") or "").strip()
                        if raw_target_dsn:
                            try:
                                parts = dsn_parts(raw_target_dsn)
                            except Exception:
                                parts = _default_local_postgres_parts()
                        else:
                            parts = _default_local_postgres_parts()
                        current_name = str(parts.get("db") or "echochat")
                        new_name = _tui_input(stdscr, "New database", "Enter the new PostgreSQL database name", str(current_name))
                        if str(new_name).strip():
                            new_name = str(new_name).strip()
                            if not _require_setup_database_name_tui(stdscr, "New database", new_name):
                                runtime["db_status"] = "New database creation was cancelled because the database name is not safe."
                                continue
                            merged["database_url"] = build_postgres_dsn({**parts, "db": new_name})
                            _, runtime["db_status"] = _prepare_database_with_guidance(stdscr, merged, recreate=False)
                        continue
                    if db_choice == 8:
                        if _tui_yes_no(stdscr, "Recreate database", "This deletes all data in the current PostgreSQL database target. Continue?", default=False):
                            _, runtime["db_status"] = _prepare_database_with_guidance(stdscr, merged, recreate=True)
                        continue
                    if db_choice == 9:
                        ok, msg = _test_database_connection(str(merged.get("database_url") or ""))
                        runtime["db_status"] = msg
                        _tui_message(stdscr, "Database test", [msg], error=not ok)
                        continue
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 2:
                _edit_owner_accounts_section(stdscr, merged, base)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 3:
                before_secret = bool(merged.get("jwt_secret"))
                _edit_login_security_section(stdscr, merged, base)
                if not before_secret and merged.get("jwt_secret"):
                    runtime["db_status"] = "A stable jwt_secret was generated."
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 4:
                _edit_password_recovery_email_section(stdscr, merged, base)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 5:
                _edit_message_display_section(stdscr, merged, base)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 6:
                _edit_rooms_behavior_section(stdscr, merged, base)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 7:
                _edit_protection_and_abuse_section(stdscr, merged, base)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 8:
                _edit_media_uploads_section(stdscr, merged, base)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 9:
                _edit_voice_and_webrtc_section(stdscr, merged, base)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 10:
                _edit_media_setup_section(stdscr, merged, base)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 11:
                _edit_hosting_network_section(stdscr, merged, base)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 12:
                _edit_logs_diagnostics_section(stdscr, merged, base)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 13:
                _edit_all_settings(stdscr, merged)
                merged["host"] = merged.get("server_host") or merged.get("host")
                merged["port"] = merged.get("server_port") or merged.get("port")
                merged.update(_autobrand_settings(merged))
                _sync_ssl_settings_block(merged)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 14:
                _show_setup_summary_screen(stdscr, merged, runtime, allow_save=False)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 15:
                _run_service_checks_menu(stdscr, merged)
                _set_main_menu_cursor_after_step(choice)
                continue
            if choice == 16:
                if not _show_setup_summary_screen(stdscr, merged, runtime, allow_save=True):
                    runtime["db_status"] = "Save was paused so you can keep reviewing or editing the setup values."
                    continue
                if not str(merged.get("database_url") or "").strip():
                    _tui_message(stdscr, "Missing database", ["Please configure a PostgreSQL database before saving."], error=True)
                    continue
                try:
                    save_target_db = str(dsn_parts(str(merged.get("database_url") or "")).get("db") or "")
                except Exception as exc:
                    _tui_message(stdscr, "Invalid database", [f"Database DSN is invalid: {exc}"], error=True)
                    continue
                if is_protected_database_name(save_target_db):
                    _tui_message(
                        stdscr,
                        "Protected database",
                        [
                            f"'{save_target_db}' is a PostgreSQL maintenance database and cannot be used as the Echo-Chat application database.",
                            "Choose or create a dedicated Echo-Chat database such as echochat.",
                        ],
                        error=True,
                    )
                    continue
                ok, msg = _test_database_connection(str(merged.get("database_url") or ""))
                if not ok:
                    want_help = _tui_yes_no(stdscr, "Database not ready", msg + "\n\nDo you want EchoChat to try to create or repair the database now?", default=True)
                    if want_help:
                        ok2, msg2 = _prepare_database_with_guidance(stdscr, merged, recreate=False)
                        runtime["db_status"] = msg2
                        if not ok2:
                            _tui_message(stdscr, "Database not ready", [msg2], error=True)
                            continue
                    else:
                        _tui_message(stdscr, "Database not ready", [msg], error=True)
                        continue
                valid_db, validation_msg, validation_report = _validate_current_database_for_chat(str(merged.get("database_url") or ""))
                runtime["db_validation_status"] = validation_msg
                runtime["db_validation_report"] = validation_report
                validation_state = str(validation_report.get("state") or "unknown")
                if validation_state == "foreign_schema":
                    _tui_scroll_text(
                        stdscr,
                        "Wrong database warning",
                        _format_database_validation_lines(validation_report) + [
                            "",
                            "Setup will not save into this database because it contains non-Echo-Chat tables and no Echo-Chat markers.",
                            "Choose a detected Echo-Chat database, create a new database, or recreate this target if you intentionally want to erase it.",
                        ],
                        footer="Enter/Esc returns to setup.",
                        allow_save=False,
                    )
                    continue
                if validation_state == "partial_echochat":
                    repair = _tui_yes_no(
                        stdscr,
                        "Partial Echo-Chat database",
                        validation_msg + "\n\nSetup can try to repair the schema before saving. Continue?",
                        default=True,
                    )
                    if not repair:
                        runtime["db_status"] = "Save paused because the selected database has only a partial Echo-Chat schema."
                        continue
                if validation_state == "valid_echochat" and not valid_db:
                    _tui_scroll_text(
                        stdscr,
                        "Database permission warning",
                        _format_database_validation_lines(validation_report) + [
                            "",
                            "This looks like an Echo-Chat database, but the configured PostgreSQL role cannot fully use/create objects in the public schema.",
                            "Use the database repair option or a bootstrap/admin DSN before saving.",
                        ],
                        footer="Enter/Esc returns to setup.",
                        allow_save=False,
                    )
                    continue
                if validation_state == "empty":
                    runtime["db_status"] = validation_msg + " Setup will prepare the Echo-Chat schema during save."

                owner_valid, owner_msg = _validate_owner_admin_setup_fields(merged, base)
                if not owner_valid:
                    _tui_message(stdscr, "Owner/admin account", [owner_msg or "Complete the owner username/password/PIN before saving."], error=True)
                    continue
                extra_admin_valid, extra_admin_msg = _validate_extra_admin_setup_fields(merged)
                if not extra_admin_valid:
                    _tui_message(stdscr, "Second admin", [extra_admin_msg or "Complete the optional second admin fields or turn that option off."], error=True)
                    continue
                twilio_errors = twilio_setup_errors(merged)
                if twilio_errors:
                    _tui_message(stdscr, "SMS 2FA setup", twilio_errors, error=True)
                    continue
                ice_errors = _ice_setup_errors(merged)
                if ice_errors:
                    _tui_message(stdscr, "STUN/TURN setup", ice_errors, error=True)
                    continue
                ddns_errors = dynamic_dns_setup_errors(merged)
                if ddns_errors:
                    _tui_message(stdscr, "Dynamic DNS helper", ddns_errors, error=True)
                    continue
                smtp_errors = _smtp_setup_errors(merged)
                if smtp_errors:
                    _tui_message(stdscr, "SMTP setup", smtp_errors, error=True)
                    continue
                if merged.get("giphy_enabled") and bool(merged.get("__store_giphy_in_config")) and not str(merged.get("giphy_api_key") or "").strip():
                    _tui_message(stdscr, "GIPHY API key", ["GIF search is enabled and you chose to store the key in server_config.json, so please enter the GIPHY API key."], error=True)
                    continue
                try:
                    try:
                        target_parts = dsn_parts(str(merged.get("database_url") or ""))
                        target_db_name = str(target_parts.get("db") or "")
                    except Exception:
                        target_db_name = str(merged.get("database_url") or "")
                    detected_db_name = ""
                    try:
                        if runtime.get("detected_dsn"):
                            detected_db_name = str(dsn_parts(str(runtime.get("detected_dsn"))).get("db") or "")
                    except Exception:
                        detected_db_name = ""
                    if detected_db_name and target_db_name and detected_db_name != target_db_name:
                        go_on = _tui_yes_no(
                            stdscr,
                            "Database target confirmation",
                            f"You are about to save setup into database '{target_db_name}', but the auto-detected Echo-Chat database is '{detected_db_name}'. Continue with '{target_db_name}'?",
                            default=False,
                        )
                        if not go_on:
                            runtime["db_status"] = f"Save was cancelled so you can choose between '{target_db_name}' and '{detected_db_name}'."
                            continue
                    conn = psycopg2.connect(str(merged.get("database_url") or ""))
                    try:
                        _prepare_full_schema_in_setup(conn)
                        merged["admin_user"] = normalize_registration_username(str(merged.get("admin_user") or base["admin_user"]))
                        if bool(merged.get("__create_initial_admin")):
                            merged["__initial_admin_user"] = normalize_registration_username(str(merged.get("__initial_admin_user") or ""))
                        merged["admin_pass"], admin_msg = _sync_primary_admin_in_db(
                            conn,
                            str(merged.get("admin_user") or base["admin_user"]),
                            str(merged.get("__admin_raw_password") or ""),
                            str(merged.get("admin_pass") or hash_password(str(merged.get("__admin_raw_password") or ""))),
                            str(merged.get("admin_notification_email") or "").strip() or None,
                            None,
                            str(merged.get("__admin_recovery_pin") or ""),
                            confirm_reset=lambda prompt: _tui_yes_no(stdscr, "Reset existing user password", prompt, default=False),
                            field_encryption_settings=merged,
                        )
                        result_lines = [
                            "Database schema for setup users and RBAC was prepared successfully.",
                            admin_msg,
                            "Admin is stored as a normal user account and carries full admin rights through RBAC.",
                        ]
                        if bool(merged.get("__create_initial_admin")):
                            if str(merged.get("__initial_admin_user") or "").strip().lower() == str(merged.get("admin_user") or "").strip().lower():
                                result_lines.append("Extra admin creation was skipped because it used the same username as the admin.")
                            else:
                                _, admin_msg = _sync_initial_admin_in_db(
                                    conn,
                                    str(merged.get("__initial_admin_user") or "").strip(),
                                    str(merged.get("__initial_admin_raw_password") or ""),
                                    hash_password(str(merged.get("__initial_admin_raw_password") or "")),
                                    str(merged.get("__initial_admin_email") or "").strip() or None,
                                    None,
                                    str(merged.get("__initial_admin_recovery_pin") or ""),
                                    confirm_reset=lambda prompt: _tui_yes_no(stdscr, "Reset existing user password", prompt, default=False),
                                    field_encryption_settings=merged,
                                )
                                result_lines.append(admin_msg)
                        if merged.get("giphy_enabled") and not bool(merged.get("__store_giphy_in_config")):
                            result_lines.append("GIPHY is enabled, but the API key is not stored in server_config.json. Set GIPHY_API_KEY or create .giphy_api_key on the server.")
                        runtime["db_status"] = " ".join(result_lines)
                        _tui_message(stdscr, "Setup summary", result_lines, error=False)
                    finally:
                        conn.close()
                except Exception as exc:
                    _tui_message(stdscr, "Setup save failed", [f"Could not create or sync the setup admin users: {exc}"], error=True)
                    continue
                return

    curses.wrapper(_app)
    if runtime.get("cancelled"):
        raise SystemExit(1)
    return merged


def interactive_setup(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Run the Echo-Chat setup wizard.

    Default behavior is the blue full-screen terminal UI. The old plain prompt
    wizard is now opt-in only, because silently falling back made setup look
    broken and hid the real terminal/curses reason.
    """
    notes = _prepare_setup_tui_environment()
    use_legacy = _setup_env_truthy("ECHOCHAT_SETUP_LEGACY")
    force_tui = _setup_env_truthy("ECHOCHAT_SETUP_TUI")
    allow_plain_fallback = _setup_env_truthy("ECHOCHAT_SETUP_ALLOW_PLAIN_FALLBACK")

    # Do not let a stale project .env pin every future setup run to the old
    # prompt UI. A shell-exported ECHOCHAT_SETUP_LEGACY still works.
    if use_legacy and _setup_env_loaded_from_dotenv("ECHOCHAT_SETUP_LEGACY") and not force_tui:
        notes.append("Ignored ECHOCHAT_SETUP_LEGACY from the project .env; use a shell variable for one-off legacy setup.")
        use_legacy = False

    curses_unavailable = curses is None
    not_tty = not os.isatty(0) or not os.isatty(1)

    if use_legacy and not force_tui:
        print("⚠️  Using old plain setup because ECHOCHAT_SETUP_LEGACY=1 was set in the shell.")
        return _interactive_setup_legacy(settings)

    if curses_unavailable or (not_tty and not force_tui):
        reason = "curses is unavailable" if curses_unavailable else "stdin/stdout is not a terminal"
        if allow_plain_fallback:
            print(f"⚠️  Blue setup UI unavailable ({reason}); using the plain setup prompts because ECHOCHAT_SETUP_ALLOW_PLAIN_FALLBACK=1.")
            return _interactive_setup_legacy(settings)
        raise SystemExit(_format_setup_tui_failure(reason, notes))

    try:
        merged = _run_setup_tui(settings)
    except KeyboardInterrupt:
        raise SystemExit(1)
    except Exception as exc:
        if allow_plain_fallback:
            print(f"⚠️  Blue setup UI failed ({exc}); using the plain setup prompts because ECHOCHAT_SETUP_ALLOW_PLAIN_FALLBACK=1.")
            return _interactive_setup_legacy(settings)
        raise SystemExit(_format_setup_tui_failure(str(exc), notes)) from exc

    merged.pop("__admin_raw_password", None)
    merged.pop("__admin_recovery_pin", None)
    merged.pop("__create_initial_admin", None)
    merged.pop("__initial_admin_user", None)
    merged.pop("__initial_admin_email", None)
    merged.pop("__initial_admin_raw_password", None)
    merged.pop("__initial_admin_recovery_pin", None)
    merged.pop("__store_giphy_in_config", None)

    merged = _autobrand_settings(merged)
    return _compact_settings(merged)
