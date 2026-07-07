#!/usr/bin/env python3
"""
server_init.py
Initialises and runs the Echo-Chat Flask application.
Ensures init_database() is called within an application context
and registers teardown properly without causing context errors.
"""

from __future__ import annotations

from socketio_async_bootstrap import ECHOCHAT_SOCKETIO_ASYNC, EVENTLET_AVAILABLE
from echochat_wsgi_guard import EchoChatStartResponseGuard

import json
import os
import logging
import ipaddress
import socket
import re
from urllib.parse import urlparse
import secrets
import sys
from datetime import timedelta, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, request, g, session
from werkzeug.exceptions import RequestEntityTooLarge
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_socketio import SocketIO, emit, disconnect

# Engine.IO long-polling hardening
#
# In threading mode the client uses HTTP long-polling. During initial connect or
# reconnect the browser can legally batch many queued Socket.IO packets into a
# single POST. Python-EngineIO protects itself with a max_decode_packets limit,
# but EchoChat can briefly exceed that limit during bootstrap and trigger:
#   ValueError: Too many packets in payload
# Raise the ceiling defensively so one queued bootstrap burst does not wedge the
# session. Keep the known-good client missed-message bootstrap flow intact.
try:
    from engineio import payload as _engineio_payload  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency path
    _engineio_payload = None
else:
    try:
        _cur = int(getattr(_engineio_payload.Payload, "max_decode_packets", 0) or 0)
        if _cur < 200:
            _engineio_payload.Payload.max_decode_packets = 200
    except Exception:
        pass

# Socket.IO auth error hardening
from jwt import ExpiredSignatureError, InvalidTokenError
from flask_jwt_extended.exceptions import CSRFError, JWTExtendedException, NoAuthorizationError
from flask_wtf import CSRFProtect

from constants import APP_VERSION, DEFAULT_SERVER_NAME, sanitize_postgres_dsn, get_db_connection_string, redact_postgres_dsn, postgres_dsn_parts, sanitize_sound_pack_external_urls
from werkzeug.middleware.proxy_fix import ProxyFix
from secrets_policy import persist_secrets_enabled, scrub_secrets_for_persist
from secret_manager import ensure_secret, is_strong_secret, missing_core_or_crypto, resolve_secret
from scaled_redis_autoconfig import apply_scaled_runtime_safety_defaults, redis_install_hint
from preflight import run_preflight, log_preflight_summary
from account_creation_policy import password_policy_metadata
from db.core import prepare_runtime_database

from database import (
    init_database,
    close_db,
    init_db_pool,
    is_auth_token_revoked,
    is_auth_session_active,
    is_refresh_token_active,
    is_refresh_token_usable,
    revoke_all_tokens_global,
    get_db_identity,
    get_schema_version,
)

# Background cleanup
from janitor import start_janitor
from routes_auth import register_auth_routes
from routes_main import register_main_routes
from routes_chat import chat_bp
from routes_groups import register_group_routes
from routes_admin_tools import register_admin_tools
from moderation_routes import register_moderation_routes
from routes_media import register_media_routes
from media_mode import client_av_config, media_permissions_policy
from realtime.state import configure_shared_state, shared_state_summary

# CORS is optional
try:
    from flask_cors import CORS
except ImportError:
    CORS = None


def _wrap_wsgi_start_response_guard(app: Flask, settings: Dict[str, Any], *, layer: str) -> None:
    """Wrap the current WSGI stack with EchoChatStartResponseGuard when enabled.

    Flask-SocketIO installs an Engine.IO WSGI middleware around the Flask app.
    A guard applied before Socket.IO only protects Flask routes; Engine.IO
    polling/disconnect paths can still bypass it and trigger Werkzeug's raw
    ``write() before start_response`` assertion.  Calling this helper both
    before and after Socket.IO setup protects both layers while avoiding a
    duplicate wrap of the exact same object.
    """

    if not bool(settings.get("wsgi_start_response_guard", True)):
        app.config[f"ECHOCHAT_WSGI_START_RESPONSE_GUARD_{layer.upper()}"] = False
        return

    current = app.wsgi_app
    if isinstance(current, EchoChatStartResponseGuard):
        app.config[f"ECHOCHAT_WSGI_START_RESPONSE_GUARD_{layer.upper()}"] = "already_wrapped"
        return

    app.wsgi_app = EchoChatStartResponseGuard(current)
    app.config[f"ECHOCHAT_WSGI_START_RESPONSE_GUARD_{layer.upper()}"] = True


def _truthy_runtime_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _safe_positive_int(value: Any, default: int, *, name: str, minimum: int | None = None, maximum: int | None = None) -> int:
    """Parse a positive integer runtime setting without crashing startup.

    Socket.IO topology settings are often edited by hand or supplied through
    environment variables.  A bad value should fall back to a safe default and
    log a warning instead of preventing Echo-Chat from booting.
    """
    try:
        parsed = int(value)
    except Exception:
        logging.warning("Invalid %s=%r; using %s", name, value, default)
        parsed = int(default)
    if minimum is not None and parsed < minimum:
        logging.warning("%s=%s is below minimum %s; clamping", name, parsed, minimum)
        parsed = int(minimum)
    if maximum is not None and parsed > maximum:
        logging.warning("%s=%s is above maximum %s; clamping", name, parsed, maximum)
        parsed = int(maximum)
    return parsed


def _safe_int(value: Any, default: int, *, name: str, minimum: int | None = None, maximum: int | None = None) -> int:
    """Parse an integer runtime setting without crashing startup.

    Use this for app-factory settings that may be hand-edited in JSON or supplied
    by environment variables.  Invalid values fall back to a safe default and are
    logged instead of killing the app with a raw ValueError.
    """
    try:
        parsed = int(value)
    except Exception:
        logging.warning("Invalid %s=%r; using %s", name, value, default)
        parsed = int(default)
    if minimum is not None and parsed < minimum:
        logging.warning("%s=%s is below minimum %s; clamping", name, parsed, minimum)
        parsed = int(minimum)
    if maximum is not None and parsed > maximum:
        logging.warning("%s=%s is above maximum %s; clamping", name, parsed, maximum)
        parsed = int(maximum)
    return parsed


def _socketio_redis_queue_forced(settings: Dict[str, Any]) -> bool:
    return (
        _truthy_runtime_flag(os.environ.get("ECHOCHAT_FORCE_SOCKETIO_REDIS_QUEUE"))
        or _truthy_runtime_flag(os.environ.get("FORCE_SOCKETIO_REDIS_QUEUE"))
        or _truthy_runtime_flag(settings.get("force_socketio_redis_queue"))
        or _truthy_runtime_flag(settings.get("socketio_force_message_queue"))
    )


def _runtime_worker_count(settings: Dict[str, Any]) -> int:
    for name in ("ECHOCHAT_WORKERS", "ECHOCHAT_PRODUCTION_WORKERS", "WEB_CONCURRENCY", "PRODUCTION_WORKERS"):
        raw = os.environ.get(name)
        if raw:
            return _safe_positive_int(raw, 1, name=name, minimum=1)
    for key in ("production_workers", "worker_count", "web_workers"):
        raw = settings.get(key)
        if raw:
            return _safe_positive_int(raw, 1, name=key, minimum=1)
    return 1


def _production_instance_count(settings: Dict[str, Any]) -> int:
    for name in ("ECHOCHAT_PRODUCTION_INSTANCES", "ECHOCHAT_INSTANCE_COUNT", "PRODUCTION_INSTANCES"):
        raw = os.environ.get(name)
        if raw:
            return _safe_positive_int(raw, 1, name=name, minimum=1, maximum=10)
    for key in ("production_instance_count", "production_instances", "instance_count"):
        raw = settings.get(key)
        if raw:
            return _safe_positive_int(raw, 1, name=key, minimum=1, maximum=10)
    return 1


def _explicit_socketio_queue_from_settings(settings: Dict[str, Any]) -> str:
    socketio_profile = settings.get("socketio_profile") or {}
    if not isinstance(socketio_profile, dict):
        socketio_profile = {}
    return (
        str(socketio_profile.get("message_queue") or "").strip()
        or str(settings.get("socketio_message_queue") or "").strip()
    )


def _redact_redis_url(url: Any) -> str:
    """Return a Redis URL safe for logs by hiding passwords."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw
        host = parsed.hostname or ""
        netloc = host
        if parsed.port:
            netloc += f":{parsed.port}"
        if parsed.username or parsed.password:
            user = parsed.username or "redis"
            netloc = f"{user}:***@{netloc}"
        return urlunparse((parsed.scheme, netloc, parsed.path or "", "", "", ""))
    except Exception:
        return raw.replace("://", "://***@", 1) if "@" in raw else raw


def _get_socketio_message_queue(settings: Dict[str, Any], *, worker_count: int = 1) -> Optional[str]:
    """Resolve the Socket.IO message queue URL for this runtime.

    Flask-SocketIO only needs a Redis/RabbitMQ/Kafka message queue when
    broadcasts must cross process boundaries, such as multiple one-worker
    instances behind sticky routing, or an explicit external emitter.  A local
    single-worker Gunicorn run should not attach to a Redis pub/sub listener
    just because setup stored a future scaling URL; doing so causes noisy
    ``Cannot receive from redis... retrying`` loops when Redis is unavailable or
    pub/sub is unhealthy.

    Force queue attachment for a single worker with:
      ECHOCHAT_FORCE_SOCKETIO_REDIS_QUEUE=1

    Explicit env URLs still win for scaled/runtime deployments.
    """
    force_queue = _socketio_redis_queue_forced(settings)

    # Runtime env vars are treated as an intentional operator choice and attach
    # immediately even for a single process. They are used by external emitters,
    # containers, and scaled deployments.
    for key in ("ECHOCHAT_SOCKETIO_MESSAGE_QUEUE", "SOCKETIO_MESSAGE_QUEUE"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v

    candidate = _explicit_socketio_queue_from_settings(settings)
    if not candidate:
        if os.environ.get("REDIS_URL"):
            logging.info("[socketio] REDIS_URL is ignored for Socket.IO. Set ECHOCHAT_SOCKETIO_MESSAGE_QUEUE explicitly when scaling.")
        return None

    instances = _production_instance_count(settings)
    if int(worker_count or 1) <= 1 and instances <= 1 and not force_queue:
        logging.info(
            "[socketio] Single-worker/single-instance runtime: Socket.IO message queue is disabled for this process. "
            "Set ECHOCHAT_FORCE_SOCKETIO_REDIS_QUEUE=1 only if you intentionally use external Socket.IO emitters."
        )
        return None

    return candidate


def _require_redis_connectivity(redis_url: str) -> None:
    """Fail fast if a Redis message queue is configured but not reachable."""
    if not redis_url:
        return

    if not (redis_url.startswith("redis://") or redis_url.startswith("rediss://")):
        # Only validate redis:// style URLs here.
        return

    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
            health_check_interval=10,
        )
        client.ping()
        logging.info("[socketio] Redis message queue reachable")
    except ImportError:
        logging.critical(
            "[socketio] Redis message queue configured (%s) but python package 'redis' is not installed. "
            "Install with: pip install redis>=5.0 or python -m pip install -r requirements.txt",
            _redact_redis_url(redis_url),
        )
        raise SystemExit(2)
    except Exception as exc:
        logging.critical(
            "[socketio] Redis message queue configured (%s) but Redis is not reachable: %s. %s",
            _redact_redis_url(redis_url),
            exc,
            redis_install_hint(),
        )
        raise SystemExit(2)





def _parse_socketio_transport_setting(value: Any) -> list[str] | None:
    """Return a clean transport list from config/env, or None for automatic mode."""
    if value is None:
        return None
    if isinstance(value, str):
        raw_items = [item.strip().lower() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item).strip().lower() for item in value if str(item).strip()]
    else:
        return None
    out: list[str] = []
    for item in raw_items:
        if item in {"polling", "websocket"} and item not in out:
            out.append(item)
    return out or None


def _resolve_socketio_runtime_profile(settings: Dict[str, Any], async_mode: str | None = None) -> Dict[str, Any]:
    async_mode = str(async_mode or _determine_socketio_async_mode() or "threading").strip().lower()
    worker_count = _runtime_worker_count(settings)
    production_instances = _production_instance_count(settings)
    socketio_profile = settings.get("socketio_profile") or {}
    if not isinstance(socketio_profile, dict):
        socketio_profile = {}
    message_queue = _get_socketio_message_queue(settings, worker_count=worker_count)
    forced_websocket_only = bool(worker_count > 1)
    if worker_count > 1 and not message_queue:
        logging.warning('Multi-worker mode requires explicit ECHOCHAT_SOCKETIO_MESSAGE_QUEUE/socketio_message_queue')
    if production_instances > 1 and not message_queue:
        logging.warning('Multiple Echo-Chat instances require explicit ECHOCHAT_SOCKETIO_MESSAGE_QUEUE/socketio_message_queue')
    if worker_count > 1 and async_mode == 'threading':
        logging.warning('Multi-worker mode requires eventlet/WebSocket support; threading + polling is not supported')

    explicit_transports = _parse_socketio_transport_setting(
        os.getenv("ECHOCHAT_SOCKETIO_TRANSPORTS")
        or socketio_profile.get("transports")
        or settings.get("socketio_transports")
    )
    if explicit_transports:
        transports = explicit_transports
    elif forced_websocket_only:
        transports = ["websocket"]
    elif async_mode == "threading":
        # Stable default for Gunicorn gthread + Flask-SocketIO threading mode.
        # This avoids browser reconnect loops caused by a WebSocket-first client
        # when the deployment is only ready for Engine.IO long-polling.
        transports = ["polling"]
    else:
        transports = ["websocket", "polling"]

    websocket_only = transports == ["websocket"]
    event_payload_limit = _safe_positive_int(
        settings.get("socketio_event_max_payload_bytes") or 65536,
        65536,
        name="socketio_event_max_payload_bytes",
        minimum=1024,
        maximum=1048576,
    )
    requested_http_buffer = _safe_positive_int(
        settings.get("socketio_max_http_buffer_size")
        or socketio_profile.get("max_http_buffer_size")
        or max(262144, event_payload_limit + 65536),
        262144,
        name="socketio_max_http_buffer_size",
        minimum=16384,
        maximum=1048576,
    )
    # Engine.IO may wrap one Socket.IO event with polling metadata, so keep a
    # small allowance over the per-event ceiling but never let HTTP polling be a
    # multi-megabyte upload lane.  File transfer/upload endpoints have their own
    # bounded HTTP routes; Socket.IO is for control/signaling only.
    max_http_buffer_size = min(requested_http_buffer, max(16384, event_payload_limit + 65536))
    return {
        "worker_count": worker_count,
        "production_instance_count": production_instances,
        "message_queue": message_queue,
        "websocket_only": websocket_only,
        "transports": transports,
        "socketio_event_max_payload_bytes": event_payload_limit,
        "max_http_buffer_size": max_http_buffer_size,
    }



def _normalise_origin_entries(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if "," in raw:
            items = [item.strip() for item in raw.split(",") if item.strip()]
        else:
            items = [raw]
    elif isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        return None
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out or None


def _origin_is_loopback_default(origin: str) -> bool:
    try:
        parsed = urlparse(str(origin or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.strip().lower()
        if host == "localhost":
            return True
        try:
            ip = ipaddress.ip_address(host)
            return bool(ip.is_loopback)
        except ValueError:
            return False
    except Exception:
        return False


def _localhost_only_origin_list(origins: Any) -> bool:
    entries = _normalise_origin_entries(origins)
    if not entries:
        return False
    return all(_origin_is_loopback_default(origin) for origin in entries)


def _settings_enable_auto_lan_origins(settings: Dict[str, Any]) -> bool:
    # True by default for local/LAN beta testing. Public deployments should set
    # public_base_url and explicit allowed origins through setup.
    return bool(settings.get("auto_allow_lan_origins", True))


def _use_same_origin_lan_cors_mode(settings: Dict[str, Any], configured_origins: Any) -> bool:
    if not _settings_enable_auto_lan_origins(settings):
        return False
    if not _localhost_only_origin_list(configured_origins):
        return False
    if str(settings.get("public_base_url") or "").strip():
        return False
    if bool(settings.get("https", False)):
        return False
    return True


def _public_lan_http_warning(settings: Dict[str, Any]) -> str | None:
    if bool(settings.get("https", False)) or str(settings.get("public_base_url") or "").strip().lower().startswith("https://"):
        return None
    if bool(settings.get("cookie_secure", False)):
        return (
            "cookie_secure=true while this server is running over plain HTTP. "
            "LAN login may rely on the insecure-LAN cookie fallback; set cookie_secure=false "
            "for LAN testing or use HTTPS for public beta hosting."
        )
    return None


def _is_public_beta_startup(settings: Dict[str, Any]) -> bool:
    mode = str(settings.get("hosting_mode") or settings.get("deployment_profile") or "").strip().lower().replace("-", "_").replace(" ", "_")
    run_mode = str(settings.get("run_mode") or "").strip().lower()
    production_mode = bool(settings.get("production_mode", False)) or run_mode in {"production", "prod"}
    public_url = str(settings.get("public_base_url") or "").strip()

    # A saved/default hosting_mode=lan must not hide a real public production URL.
    # main.py --development rewrites run_mode/production_mode for the current process,
    # so the explicit development override still stays local even if a public URL is saved.
    if production_mode and public_url.lower().startswith("https://"):
        try:
            parsed_public = urlparse(public_url)
            if parsed_public.scheme == "https" and parsed_public.hostname:
                return True
        except Exception:
            pass

    if mode == "public_beta":
        return True
    if mode in {"lan", "local", "development", "dev", "no_domain_yet", "no_domain"}:
        return False
    if not production_mode:
        return False
    if public_url:
        return True
    return False


def _origin_entries_include_wildcard(origins: Any) -> bool:
    entries = _normalise_origin_entries(origins)
    return bool(entries and "*" in entries)


def _normalize_runtime_cookie_samesite(value: Any, *, cookie_secure: bool = False) -> str:
    """Return a browser-safe SameSite value for runtime cookies.

    Flask-JWT-Extended passes this value through to Set-Cookie. A typo in
    server_config.json should not make auth/session cookies malformed.
    SameSite=None also requires Secure in modern browsers, so downgrade to Lax
    for local HTTP testing. Public-beta readiness still blocks that mismatch.
    """
    raw = str(value or "Lax").strip().lower()
    if raw == "strict":
        return "Strict"
    if raw == "none":
        return "None" if cookie_secure else "Lax"
    if raw in {"", "lax"}:
        return "Lax"
    logging.warning("Invalid cookie_samesite=%r; using Lax", value)
    return "Lax"


def _validate_public_beta_startup_settings(settings: Dict[str, Any], settings_file: Optional[Path] | None = None) -> None:
    """Fail closed for internet-facing beta settings.

    LAN/development mode stays permissive. In public beta mode, the server should
    not boot with insecure cookies, wildcard credentialed origins, disabled same-
    origin write guards, weak secrets, missing database configuration, or in-memory
    shared-state settings. Admins can deliberately bypass with
    ``allow_insecure_production_start`` for emergency local diagnostics only.

    This guard lives in ``create_app()`` on purpose: it protects both
    ``python main.py --production`` and direct WSGI/Gunicorn imports of
    ``wsgi:app``. The main production launcher also runs the same readiness report
    before exec'ing Gunicorn, but direct WSGI starts must not bypass it.
    """
    if not _is_public_beta_startup(settings):
        return
    allow_unsafe = bool(settings.get("allow_insecure_production_start", False))
    if allow_unsafe and os.getenv("ECHOCHAT_FORCE_UNSAFE_PUBLIC_START") != "1":
        logging.critical("allow_insecure_production_start=true ignored without ECHOCHAT_FORCE_UNSAFE_PUBLIC_START=1")
        allow_unsafe = False

    errors: list[str] = []
    if allow_unsafe:
        # Still never allow public startup with missing/placeholder core secrets;
        # one-off Flask/JWT secrets break auth and can poison at-rest crypto.
        if not is_strong_secret(resolve_secret(settings, "secret_key")):
            errors.append("SECRET_KEY must be a stable strong secret even when unsafe bypass is requested")
        if not is_strong_secret(resolve_secret(settings, "jwt_secret")):
            errors.append("JWT_SECRET_KEY must be a stable strong secret even when unsafe bypass is requested")
        if errors:
            raise RuntimeError("Public beta startup blocked: " + "; ".join(errors))
        logging.critical("ECHOCHAT_FORCE_UNSAFE_PUBLIC_START=1; public beta readiness guards bypassed after core-secret check")
        return


    # Keep a tiny built-in safety net first so a broken readiness import never
    # turns an unsafe public-beta configuration into a successful boot.
    public_url = str(settings.get("public_base_url") or "").strip()
    parsed_public = urlparse(public_url) if public_url else None
    if not public_url or not parsed_public or parsed_public.scheme != "https" or not parsed_public.hostname:
        errors.append("public_base_url must be a real https:// URL in public beta mode")
    if bool(settings.get("debug") or settings.get("server_debug")):
        errors.append("debug/server_debug must be false")
    if not bool(settings.get("cookie_secure") or settings.get("https")):
        errors.append("cookie_secure must be true for auth cookies")
    if bool(settings.get("allow_insecure_lan_cookie_fallback", False)):
        errors.append("allow_insecure_lan_cookie_fallback must be false")
    if not bool(settings.get("enforce_same_origin_writes", True)):
        errors.append("enforce_same_origin_writes must be true")
    if _origin_entries_include_wildcard(settings.get("allowed_origins")) or _origin_entries_include_wildcard(settings.get("cors_allowed_origins")):
        errors.append("allowed_origins/cors_allowed_origins must not contain wildcard '*'")

    rate_storage = str(settings.get("rate_limit_storage_uri") or settings.get("rate_limit_storage") or "memory://").strip().lower()
    if not rate_storage or rate_storage == "memory://":
        errors.append("rate_limit_storage_uri must use Redis or another shared backend, not memory://")

    workers = _runtime_worker_count(settings)
    instances = _production_instance_count(settings)
    if (workers > 1 or instances > 1) and not _explicit_socketio_queue_from_settings(settings):
        errors.append("scaled Socket.IO topology requires explicit socketio_message_queue")
    if (workers > 1 or instances > 1) and not str(
        os.environ.get("ECHOCHAT_SHARED_STATE_REDIS_URL")
        or os.environ.get("SHARED_STATE_REDIS_URL")
        or settings.get("shared_state_redis_url")
        or ""
    ).strip():
        errors.append("scaled realtime topology requires explicit shared_state_redis_url")

    max_socket_payload = _safe_int(
        settings.get("socketio_event_max_payload_bytes") or 65536,
        65536,
        name="socketio_event_max_payload_bytes",
        minimum=1024,
        maximum=1048576,
    )
    if max_socket_payload > 131072:
        errors.append("socketio_event_max_payload_bytes must be 131072 or less for public beta")

    missing_crypto = missing_core_or_crypto(settings, include_crypto=True)
    if missing_crypto:
        errors.append("stable public secrets/crypto keys missing: " + ", ".join(missing_crypto))

    try:
        from public_beta_readiness import build_public_beta_readiness

        report = build_public_beta_readiness(
            settings,
            settings_file=settings_file or Path("server_config.json"),
            repo_root=Path(__file__).resolve().parent,
        )
        for item in report.get("items") or []:
            if str(item.get("level") or "").lower() == "fail":
                title = str(item.get("title") or item.get("code") or "public beta readiness failure")
                if title not in errors:
                    errors.append(title)
    except Exception as exc:
        errors.append(f"public beta readiness check could not complete: {exc}")

    if errors:
        raise RuntimeError("Public beta startup blocked: " + "; ".join(errors))



def _determine_socketio_async_mode() -> str:
    async_mode = "threading"
    if ECHOCHAT_SOCKETIO_ASYNC == "eventlet" and not EVENTLET_AVAILABLE:
        print("[socketio] ECHOCHAT_SOCKETIO_ASYNC=eventlet but eventlet is not installed; falling back to threading")
    if ECHOCHAT_SOCKETIO_ASYNC == "eventlet" and EVENTLET_AVAILABLE:
        async_mode = "eventlet"
    return async_mode





def _ensure_db_teardown_registered(app: Flask) -> None:
    """Register DB teardown once, before any app-context DB work runs.

    Startup uses ``app.app_context()`` to run migrations and read DB identity.
    That startup context can call ``get_db()`` just like a request.  If the
    teardown hook is registered only after database initialization, that first
    context can leave a pooled connection checked out until process exit.
    """
    funcs = getattr(app, "teardown_appcontext_funcs", None)
    if funcs is not None and close_db in funcs:
        return
    app.teardown_appcontext(close_db)


def _log_connected_database_identity(settings: Dict[str, Any], ident: Dict[str, Any]) -> None:
    """Log connected DB identity and call out obvious wrong-config mismatches."""
    configured_dsn = str(settings.get("database_url") or get_db_connection_string(settings) or "")
    configured = postgres_dsn_parts(configured_dsn)
    connected_user = str(ident.get("current_user") or "").strip()
    connected_db = str(ident.get("current_database") or "").strip()

    logging.info(
        "Connected DB: user=%s db=%s server=%s:%s",
        connected_user or None,
        connected_db or None,
        ident.get("server_addr"),
        ident.get("server_port"),
    )

    mismatches: list[str] = []
    expected_user = str(configured.get("user") or "").strip()
    expected_db = str(configured.get("db") or "").strip()
    if expected_user and connected_user and expected_user != connected_user:
        mismatches.append(f"configured user={expected_user!r} connected user={connected_user!r}")
    if expected_db and connected_db and expected_db != connected_db:
        mismatches.append(f"configured db={expected_db!r} connected db={connected_db!r}")

    if mismatches:
        logging.warning("Configured DB does not match connected DB: %s", "; ".join(mismatches))
    elif expected_user or expected_db:
        logging.info("Connected DB matches configured database/user")


def _db_pool_bounds_for_runtime(settings: Dict[str, Any]) -> tuple[int, int]:
    """Return DB pool min/max adjusted for the current instance topology.

    Single-process LAN/dev mode keeps the historical larger pool because the UI
    can burst through reconnects and admin polling.  Scaled production must not
    force every instance to 50 connections; ten one-worker instances at 50 each
    can exceed a normal local PostgreSQL max_connections setting before users
    even arrive.
    """
    instances = _production_instance_count(settings)
    cfg_min = _safe_int(settings.get("db_pool_min", 1), 1, name="db_pool_min", minimum=1, maximum=25)
    raw_max = settings.get("db_pool_max", None)
    if raw_max is None or str(raw_max).strip() == "":
        if instances <= 1:
            cfg_max = 50
        elif instances <= 2:
            cfg_max = 25
        elif instances <= 5:
            cfg_max = 15
        else:
            cfg_max = 10
    else:
        cfg_max = _safe_int(raw_max, 50 if instances <= 1 else 10, name="db_pool_max", minimum=1, maximum=100)

    if instances <= 1 and cfg_max < 50:
        logging.warning("db_pool_max=%s is low for single-instance UI bursts; forcing to 50", cfg_max)
        cfg_max = 50
    elif instances > 1 and cfg_max > 25:
        logging.warning(
            "db_pool_max=%s with %s instances can open up to %s database connections; consider 5-15 per instance or PgBouncer",
            cfg_max,
            instances,
            cfg_max * instances,
        )

    if cfg_min > cfg_max:
        logging.warning("db_pool_min=%s is above db_pool_max=%s; clamping min to max", cfg_min, cfg_max)
        cfg_min = cfg_max
    return cfg_min, cfg_max


def _configure_realtime_shared_state(app: Flask, settings: Dict[str, Any], runtime_context: Dict[str, Any]) -> dict:
    """Configure Redis-backed shared realtime state and expose status.

    Socket.IO Redis pub/sub carries cross-process emits, while shared-state Redis
    keeps presence and room rosters consistent across multiple app instances.
    For scaled mode, degraded process-local state is not safe enough to hide.
    """
    workers = _safe_positive_int(runtime_context.get("worker_count") or 1, 1, name="worker_count", minimum=1)
    instances = _safe_positive_int(runtime_context.get("production_instance_count") or 1, 1, name="production_instance_count", minimum=1, maximum=10)
    scaled = bool(workers > 1 or instances > 1)
    error = None
    enabled = False
    try:
        enabled = bool(configure_shared_state(settings))
    except Exception as exc:  # pragma: no cover - defensive runtime path
        error = str(exc)
        enabled = False
        logging.exception("Shared realtime state configuration failed")

    summary = {}
    try:
        summary = dict(shared_state_summary() or {})
    except Exception as exc:
        if not error:
            error = str(exc)
        summary = {}

    status = {
        "enabled": enabled,
        "scaled_required": scaled,
        "error": error,
        "summary": summary,
    }
    app.config["ECHOCHAT_SHARED_STATE_ENABLED"] = enabled
    app.config["ECHOCHAT_SHARED_STATE_STATUS"] = status
    runtime_context["shared_state_enabled"] = enabled

    if enabled:
        logging.info("Shared realtime state enabled prefix=%s", summary.get("prefix"))
        return status

    if scaled:
        msg = (
            "Scaled realtime mode requires explicit reachable shared-state Redis. "
            "Set ECHOCHAT_SHARED_STATE_REDIS_URL=redis://127.0.0.1:6379/2."
        )
        if error:
            msg += f" Last error: {error}"
        logging.critical(msg)
        raise RuntimeError(msg)

    logging.info("Shared realtime state disabled; single-instance process-local state will be used")
    return status


def _initialize_database_stack(app: Flask, settings: Dict[str, Any]) -> None:
    prepare_runtime_database(settings)
    with app.app_context():
        # Defensive DSN sanitisation (common: pasted placeholder angle brackets)
        if settings.get("database_url"):
            settings["database_url"] = str(sanitize_postgres_dsn(str(settings["database_url"])))
        # Optional Postgres connection pooling (defaults are safe for dev).
        # NOTE: In practice, the web UI can create short bursts of requests (page reloads,
        # multiple tabs, admin polling, socket reconnect recovery). If db_pool_max is too small,
        # Postgres pooling exhausts and anything DB-backed (missed PM delivery/ack, rooms list,
        # invites, etc.) becomes flaky.
        # We therefore enforce a sane *floor* for dev so the app remains stable even if an
        # older server_config.json has db_pool_max=10.
        cfg_min, cfg_max = _db_pool_bounds_for_runtime(settings)
        init_db_pool(
            minconn=cfg_min,
            maxconn=cfg_max,
            dsn=str(settings.get("database_url")) if settings.get("database_url") else None,
        )
        init_database()

        # Log live DB identity (detect wrong DB/role quickly)
        try:
            ident = get_db_identity()
            _log_connected_database_identity(settings, ident)
            logging.info("Schema state: %s", get_schema_version())
        except Exception as exc:
            logging.warning("Could not read DB identity/schema version: %s", exc)

        # Optional hard switch: revoke all sessions on boot.
        # This is OFF by default because it logs everyone out after restarts.
        if bool(settings.get("revoke_all_tokens_on_start", False)):
            try:
                revoke_all_tokens_global()
                print("🔒 revoke_all_tokens_on_start=true -> revoked all sessions")
            except Exception as e:
                print(f"⚠️  Failed to revoke tokens on start: {e}")



def _create_socketio_instance(app: Flask, settings: Dict[str, Any], cors_origins: Any) -> tuple[SocketIO, Dict[str, Any]]:
    async_mode = _determine_socketio_async_mode()
    app.config["ECHOCHAT_SOCKETIO_ASYNC_MODE"] = async_mode

    socketio_profile = _resolve_socketio_runtime_profile(settings, async_mode=async_mode)
    transports = list(socketio_profile.get("transports") or ["polling"])
    app.config["ECHOCHAT_SOCKETIO_TRANSPORTS"] = transports
    app.config["ECHOCHAT_SOCKETIO_WEBSOCKET_ONLY"] = bool(socketio_profile.get("websocket_only"))
    app.config["ECHOCHAT_WS_ENABLED"] = "websocket" in transports
    app.config["ECHOCHAT_START_JANITOR_INPROCESS"] = bool(
        socketio_profile.get("worker_count", 1) <= 1
        and socketio_profile.get("production_instance_count", 1) <= 1
        and not os.environ.get("GUNICORN_CMD_ARGS")
    )

    # Cross-process broadcast (required for scale): configure an explicit Redis
    # Socket.IO queue with ECHOCHAT_SOCKETIO_MESSAGE_QUEUE or socketio_message_queue.
    # Do not rely on generic REDIS_URL; Echo-Chat keeps Redis DBs separated.
    message_queue = socketio_profile.get("message_queue")
    if message_queue:
        _require_redis_connectivity(message_queue)

    # Keep this as a plain cookie name for python-engineio compatibility.
    # Some Engine.IO versions concatenate cookie attributes as strings and crash
    # when dict values such as httponly=True/secure=False are booleans.
    # JWT/auth cookies remain hardened separately by Flask/JWT settings.
    engineio_cookie = "echochat_io"

    socketio = SocketIO(
        app,
        async_mode=async_mode,
        cors_allowed_origins=cors_origins,
        cookie=engineio_cookie,
        always_connect=True,
        logger=False,
        engineio_logger=False,
        ping_interval=20,
        ping_timeout=15,
        transports=socketio_profile["transports"],
        max_http_buffer_size=socketio_profile["max_http_buffer_size"],
        message_queue=message_queue,
    )

    runtime_context = {
        "async_mode": async_mode,
        "ws_enabled": app.config.get("ECHOCHAT_WS_ENABLED"),
        "message_queue": message_queue,
        "worker_count": socketio_profile.get("worker_count"),
        "production_instance_count": socketio_profile.get("production_instance_count"),
        "websocket_only": socketio_profile.get("websocket_only"),
        "socketio_event_max_payload_bytes": socketio_profile.get("socketio_event_max_payload_bytes"),
        "max_http_buffer_size": socketio_profile.get("max_http_buffer_size"),
        "engineio_cookie_hardened": False,
        "engineio_cookie_mode": "compatibility-name-only",
    }
    _configure_realtime_shared_state(app, settings, runtime_context)
    app.config["ECHOCHAT_SOCKETIO_MESSAGE_QUEUE"] = message_queue
    app.config["ECHOCHAT_SOCKETIO_RUNTIME_PROFILE"] = dict(runtime_context)
    return socketio, runtime_context



def _record_startup_preflight(
    app: Flask,
    settings: Dict[str, Any],
    settings_file: Optional[Path] | None,
    runtime_context: Dict[str, Any],
) -> None:
    try:
        with app.app_context():
            startup_preflight = run_preflight(
                settings,
                settings_file=settings_file,
                init_db_pool_if_needed=False,
                runtime_context=runtime_context,
            )
        app.config["ECHOCHAT_STARTUP_PREFLIGHT"] = startup_preflight
        app.config["ECHOCHAT_LAST_PREFLIGHT"] = startup_preflight
        log_preflight_summary(startup_preflight, logger=logging.getLogger(__name__))
    except Exception as exc:
        logging.warning("Could not complete startup preflight: %s", exc)
        fallback = {
            "overall": "warn",
            "timestamp": datetime.now().isoformat(),
            "checks": [],
            "counts": {"ok": 0, "warn": 1, "fail": 0, "disabled": 0, "info": 0},
            "error": str(exc),
        }
        app.config["ECHOCHAT_STARTUP_PREFLIGHT"] = fallback
        app.config["ECHOCHAT_LAST_PREFLIGHT"] = dict(fallback)



def _register_application_routes(
    app: Flask,
    settings: Dict[str, Any],
    socketio: SocketIO,
    limiter: Optional[Limiter] | None,
) -> None:
    register_auth_routes(app, settings, limiter=limiter)
    register_main_routes(app, settings, socketio)
    # NOTE: Legacy HTTP DM routes performed server-side decryption.
    # EchoChat's active direct messaging path is Socket.IO ciphertext relay
    # (see socket_handlers.py). Keeping HTTP DM routes disabled avoids
    # accidental server-side plaintext handling.
    register_group_routes(app, settings, limiter=limiter)
    register_admin_tools(app, settings, socketio=socketio, limiter=limiter)
    register_moderation_routes(app, settings, limiter=limiter)
    register_media_routes(app, settings, limiter=limiter)
    app.register_blueprint(chat_bp)

    from socket_handlers import register_socketio_handlers
    register_socketio_handlers(socketio, settings)

def _runtime_server_name(settings: Dict[str, Any] | None) -> str:
    raw = str((settings or {}).get("server_name") or DEFAULT_SERVER_NAME).strip() or DEFAULT_SERVER_NAME
    return raw.replace("\r", " ").replace("\n", " ").strip() or DEFAULT_SERVER_NAME


def _log_startup_banner(settings: Dict[str, Any], settings_file: Optional[Path] | None) -> None:
    """Log a boot banner that makes 'wrong DB / wrong config' obvious."""
    try:
        cfg_path = Path(settings_file) if settings_file else None
        cfg_exists = bool(cfg_path and cfg_path.exists())
        cfg_mtime = None
        if cfg_exists:
            try:
                cfg_mtime = datetime.fromtimestamp(cfg_path.stat().st_mtime).isoformat(timespec="seconds")
            except Exception:
                cfg_mtime = None

        dsn = get_db_connection_string(settings)
        parts = postgres_dsn_parts(dsn)

        server_label = _runtime_server_name(settings)
        logging.info("==================== %s Boot ====================", server_label)
        logging.info("%s version: %s", server_label, APP_VERSION)
        logging.info("Schema mode: tracked migrations (echochat_schema_meta)")
        logging.info("Settings file: %s (exists=%s%s)", str(cfg_path) if cfg_path else "<none>", cfg_exists,
                     f", mtime={cfg_mtime}" if cfg_mtime else "")
        logging.info(
            "Configured DB: host=%s port=%s db=%s user=%s",
            parts.get("host"), parts.get("port"), parts.get("db"), parts.get("user"),
        )
        logging.info("Configured DSN: %s", redact_postgres_dsn(dsn))
        logging.info("========================================================")
    except Exception as exc:  # pragma: no cover
        # Never block boot on banner failures.
        try:
            logging.warning("Could not emit boot banner: %s", exc)
        except Exception:
            pass


def create_app(
    settings: Dict[str, Any],
    limiter: Optional[Limiter] | None = None,
    settings_file: Optional[Path] | None = None,
) -> tuple[Flask, SocketIO]:
    """Create and configure the Flask + Socket.IO application.

    This function does **not** start a server. It is safe to import from a
    Gunicorn `wsgi.py` module.
    """

    settings_file = Path(settings_file) if isinstance(settings_file, str) else settings_file
    apply_scaled_runtime_safety_defaults(settings)
    # Make secrets admin-friendly: if setup/config scrubbed secrets out of JSON,
    # generate stable values and store them in a protected .env before any public
    # readiness guard or at-rest crypto helper can fall back to one-off material.
    for _canonical in (
        "secret_key",
        "jwt_secret",
        "profile_field_encryption_key",
        "email_field_encryption_key",
        "email_hash_key",
        "security_backup_encryption_key",
        "privacy_retention_hash_key",
    ):
        ensure_secret(settings, _canonical, settings_file=settings_file)
    _validate_public_beta_startup_settings(settings, settings_file=settings_file)

    # ───── Flask App Core ─────
    app = Flask(__name__, static_folder="static", template_folder="templates")
    # Keep the runtime app version available to routes/templates that only
    # receive the Flask app/config object. Do not trust server_config.json for
    # package identity; VERSION.txt is the single source of truth.
    app.config["APP_VERSION"] = APP_VERSION
    # Expose the live settings file path to admin endpoints so they can
    # persist runtime settings updates without guessing filenames.
    app.config["ECHOCHAT_SETTINGS_FILE"] = str(settings_file) if settings_file else None
    # Expose the live runtime settings dict to blueprints that need it.
    app.config["ECHOCHAT_SETTINGS"] = settings

    trust_proxy_headers = bool(settings.get("trust_proxy_headers", False))
    proxy_fix_hops = _safe_int(settings.get("proxy_fix_hops", 1), 1, name="proxy_fix_hops", minimum=0, maximum=5)
    proxy_fix_x_for = _safe_int(settings.get("proxy_fix_x_for", proxy_fix_hops), proxy_fix_hops, name="proxy_fix_x_for", minimum=0, maximum=5)
    proxy_fix_x_proto = _safe_int(settings.get("proxy_fix_x_proto", proxy_fix_hops), proxy_fix_hops, name="proxy_fix_x_proto", minimum=0, maximum=5)
    proxy_fix_x_host = _safe_int(settings.get("proxy_fix_x_host", proxy_fix_hops), proxy_fix_hops, name="proxy_fix_x_host", minimum=0, maximum=5)
    proxy_fix_x_port = _safe_int(settings.get("proxy_fix_x_port", proxy_fix_hops), proxy_fix_hops, name="proxy_fix_x_port", minimum=0, maximum=5)
    proxy_fix_x_prefix = _safe_int(settings.get("proxy_fix_x_prefix", 0), 0, name="proxy_fix_x_prefix", minimum=0, maximum=5)
    app.config["ECHOCHAT_TRUST_PROXY_HEADERS"] = trust_proxy_headers
    app.config["ECHOCHAT_PROXY_FIX_HOPS"] = proxy_fix_hops
    app.config["ECHOCHAT_PROXY_FIX_COUNTS"] = {
        "x_for": proxy_fix_x_for,
        "x_proto": proxy_fix_x_proto,
        "x_host": proxy_fix_x_host,
        "x_port": proxy_fix_x_port,
        "x_prefix": proxy_fix_x_prefix,
    }
    if trust_proxy_headers:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_fix_x_for,
            x_proto=proxy_fix_x_proto,
            x_host=proxy_fix_x_host,
            x_port=proxy_fix_x_port,
            x_prefix=proxy_fix_x_prefix,
        )

    # Dev-server hardening: prevent Werkzeug's built-in server from printing
    # "write() before start_response" for invalid WSGI edge paths.
    # This first wrap protects normal Flask routes. Socket.IO installs its own
    # WSGI middleware later, so we apply the same guard again after Socket.IO
    # setup to protect Engine.IO polling / disconnect edges too.
    _wrap_wsgi_start_response_guard(app, settings, layer="flask")

    app.secret_key = _ensure_secret_key(settings, settings_file)
    max_request_bytes = _safe_int(settings.get("max_request_bytes") or 31457280, 31457280, name="max_request_bytes", minimum=1024, maximum=104857600)
    max_form_memory_size = _safe_int(settings.get("max_form_memory_size") or 500000, 500000, name="max_form_memory_size", minimum=1024, maximum=10485760)
    max_form_parts = _safe_int(settings.get("max_form_parts") or 100, 100, name="max_form_parts", minimum=1, maximum=10000)
    app.config.update(
        MAX_CONTENT_LENGTH=max_request_bytes,
        MAX_FORM_MEMORY_SIZE=max_form_memory_size,
        MAX_FORM_PARTS=max_form_parts,
    )


    def _get_csp_nonce() -> str:
        nonce = getattr(g, "echochat_csp_nonce", None)
        if not nonce:
            nonce = secrets.token_urlsafe(16)
            g.echochat_csp_nonce = nonce
        return nonce

    def _origin_for_csp(url: Any) -> str | None:
        try:
            raw = str(url or "").strip()
            if not raw:
                return None
            if raw.startswith("/"):
                return "'self'"
            parsed = urlparse(raw)
            if parsed.scheme in {"http", "https", "ws", "wss"} and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return None
        return None

    def _build_default_csp() -> str:
        nonce = _get_csp_nonce()
        socketio_client_url = str(settings.get("socketio_client_url") or "/static/vendor/socket.io.min.js").strip()
        script_src = ["'self'", f"'nonce-{nonce}'"]
        socketio_origin = _origin_for_csp(socketio_client_url)
        if socketio_origin and socketio_origin not in script_src:
            script_src.append(socketio_origin)
        # Allow the built-in local Socket.IO bootstrap loader to fall back to the
        # official Socket.IO browser bundle when static/vendor/socket.io.min.js
        # has not been replaced with a real local bundle yet.
        socketio_cdn_origin = "https://cdn.socket.io"
        if socketio_cdn_origin not in script_src:
            script_src.append(socketio_cdn_origin)
        for sound_pack_url in sanitize_sound_pack_external_urls(settings.get("sound_pack_external_urls")):
            sound_pack_origin = _origin_for_csp(sound_pack_url)
            if sound_pack_origin and sound_pack_origin not in script_src:
                script_src.append(sound_pack_origin)
        connect_src = ["'self'", "ws:", "wss:"]
        img_src = ["'self'", "data:", "blob:", "https://api.dicebear.com", "https://*.giphy.com", "https://i.ytimg.com", "https://*.ytimg.com"]
        media_src = ["'self'", "blob:", "https:", "https://*.giphy.com"]
        frame_src = [
            "'self'",
            "https://www.youtube.com",
            "https://youtube.com",
            "https://www.youtube-nocookie.com",
            "https://youtube-nocookie.com",
            "https://player.vimeo.com",
            "https://www.iheart.com",
            "https://iheart.com",
        ]

        directives = {
            "default-src": ["'self'"],
            "base-uri": ["'self'"],
            "form-action": ["'self'"],
            "object-src": ["'none'"],
            "frame-ancestors": ["'none'"],
            "frame-src": frame_src,
            "script-src": script_src,
            "script-src-attr": ["'none'"],
            "style-src": ["'self'", "'unsafe-inline'"],
            "img-src": img_src,
            "font-src": ["'self'", "data:"],
            "connect-src": connect_src,
            "media-src": media_src,
            "worker-src": ["'self'", "blob:"],
            "manifest-src": ["'self'"],
        }
        return "; ".join(f"{k} {' '.join(v)}" for k, v in directives.items())

    @app.context_processor
    def inject_echochat_template_globals():
        server_name = str(settings.get("server_name") or DEFAULT_SERVER_NAME).strip() or DEFAULT_SERVER_NAME
        password_policy = password_policy_metadata()
        return {
            "server_name": server_name,
            "server_name_admin": f"{server_name} Admin",
            "app_version": APP_VERSION,
            "csp_nonce": _get_csp_nonce(),
            "socketio_client_url": str(settings.get("socketio_client_url") or "/static/vendor/socket.io.min.js").strip(),
            "password_policy": password_policy.get("summary"),
            "password_min_length": password_policy.get("min_length"),
            "password_max_length": password_policy.get("max_length"),
            "password_recommended_length": password_policy.get("recommended_length"),
            "password_common_weak": password_policy.get("common_weak"),
        }

    # Cookie security: keep dev-friendly defaults, allow hardened prod settings.
    cookie_secure = bool(settings.get("cookie_secure", False) or settings.get("https", False))
    cookie_samesite = _normalize_runtime_cookie_samesite(settings.get("cookie_samesite"), cookie_secure=cookie_secure)

    app.config.update(
        SECRET_KEY=app.secret_key,
        SESSION_COOKIE_NAME="echochat_session",
        SESSION_COOKIE_SECURE=cookie_secure,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE=cookie_samesite,
        JWT_SECRET_KEY=_ensure_jwt_secret(settings, settings_file),
        JWT_TOKEN_LOCATION=["cookies"],
        JWT_ACCESS_COOKIE_NAME="echochat_access",
        JWT_REFRESH_COOKIE_NAME="echochat_refresh",
        JWT_ACCESS_COOKIE_PATH="/",
        JWT_REFRESH_COOKIE_PATH="/token/refresh",
        # Keep CSRF cookies readable from /chat while restricting refresh token cookie path.
        JWT_ACCESS_CSRF_COOKIE_PATH="/",
        JWT_REFRESH_CSRF_COOKIE_PATH="/",
        JWT_COOKIE_SECURE=cookie_secure,
        JWT_COOKIE_SAMESITE=cookie_samesite,
        JWT_COOKIE_CSRF_PROTECT=True,

        # Defaults in Flask-JWT-Extended are short (15 minutes). For dev UX we
        # use a longer access token and rely on refresh to keep sessions alive.
        JWT_ACCESS_TOKEN_EXPIRES=timedelta(minutes=_safe_int(settings.get("access_token_minutes", 30), 30, name="access_token_minutes", minimum=1, maximum=1440)),
        JWT_REFRESH_TOKEN_EXPIRES=timedelta(days=_safe_int(settings.get("refresh_token_days", 7), 7, name="refresh_token_days", minimum=1, maximum=90)),

        # Flask-WTF's global CSRF protection conflicts with our JSON APIs.
        # We validate CSRF manually on HTML forms, and rely on JWT's CSRF tokens
        # (csrf_access_token/csrf_refresh_token) for API calls.
        WTF_CSRF_CHECK_DEFAULT=False,
        WTF_CSRF_HEADERS=["X-CSRF-TOKEN", "X-CSRFToken"],
    )

    CSRFProtect(app)
    jwt = JWTManager(app)

    # ------------------------------------------------------------------
    # Baseline security headers
    # ------------------------------------------------------------------
    # Keep these defaults non-breaking for your current templates.
    # You can override via server_config.json:
    #   - content_security_policy
    #   - permissions_policy
    #   - x_frame_options
    #   - referrer_policy
    #   - hsts_max_age / hsts_include_subdomains / hsts_preload
    @app.after_request
    def _add_security_headers(resp):
        try:
            resp.headers.setdefault("X-Content-Type-Options", "nosniff")
            resp.headers.setdefault("Cross-Origin-Opener-Policy", str(settings.get("cross_origin_opener_policy") or "same-origin"))
            resp.headers.setdefault("Cross-Origin-Resource-Policy", str(settings.get("cross_origin_resource_policy") or "same-origin"))
            referrer_policy = str(settings.get("referrer_policy") or "strict-origin-when-cross-origin")
            if request.path.startswith(("/admin/test_lab/", "/admin/test-lab/")):
                # The randomized Test Lab token is intentionally in the URL path.
                # Never leak it through Referer headers to static/external assets.
                referrer_policy = "no-referrer"
            resp.headers.setdefault("Referrer-Policy", referrer_policy)
            resp.headers.setdefault(
                "X-Frame-Options",
                str(settings.get("x_frame_options") or "DENY"),
            )
            resp.headers.setdefault("Origin-Agent-Cluster", "?1")
            resp.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")

            # Permissions-Policy: do not block camera/microphone (EchoChat voice + webcam).
            # Default media Permissions-Policy fallback: camera=(self), microphone=(self).
            resp.headers.setdefault(
                "Permissions-Policy",
                media_permissions_policy(settings),
            )

            csp = settings.get("content_security_policy") or settings.get("csp_policy")
            if not csp:
                csp = _build_default_csp()
            resp.headers.setdefault("Content-Security-Policy", str(csp))

            sensitive_paths = {"/chat", "/login", "/register", "/forgot-password", "/logout", "/token/refresh"}
            if request.path.startswith("/admin") or request.path.startswith("/api/debug/config") or request.path in sensitive_paths or request.path.startswith("/reset-password"):
                resp.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0, private")
                resp.headers.setdefault("Pragma", "no-cache")
                resp.headers.setdefault("Expires", "0")

            # Versioned static assets are requested as /static/...?...v=<APP_VERSION>.
            # Treat those URLs as immutable so reloads do not generate dozens of
            # conditional requests for split chat runtime files, CSS, and lazy-loaded vendor files.
            static_asset_roots = ("/static/css/", "/static/js/", "/static/vendor/", "/static/emoticons/")
            if request.path.startswith(static_asset_roots) and request.args.get("v"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"

            if request.path.startswith("/static/uploads/profile_avatars/"):
                resp.headers.setdefault("Cache-Control", "public, max-age=604800, immutable")
                resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
                resp.headers.setdefault("Content-Security-Policy", "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline';")

            if request.path.startswith("/static/uploads/legacy_public/"):
                resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
                resp.headers.setdefault("Content-Security-Policy", "sandbox; default-src 'none';")
                resp.headers.setdefault("Content-Disposition", "attachment")

            # Only send HSTS when HTTPS is in use.
            if cookie_secure:
                max_age = _safe_int(settings.get("hsts_max_age") or 31536000, 31536000, name="hsts_max_age", minimum=0, maximum=63072000)
                inc_sub = bool(settings.get("hsts_include_subdomains", True))
                preload = bool(settings.get("hsts_preload", False))
                hsts = f"max-age={max_age}"
                if inc_sub:
                    hsts += "; includeSubDomains"
                if preload:
                    hsts += "; preload"
                resp.headers.setdefault("Strict-Transport-Security", hsts)
        except Exception:
            # Never break responses because of header injection.
            pass
        return resp

    # Idle logout window (hours). Access tokens are treated as invalid if the
    # session has no *client-side activity* for this long.
    idle_hours = settings.get("idle_logout_hours", 8)
    try:
        idle_hours = float(idle_hours) if idle_hours is not None else 8.0
    except Exception:
        idle_hours = 8.0
    max_idle_seconds = idle_hours * 3600.0 if idle_hours and idle_hours > 0 else None

    # ------------------------------------------------------------------
    # JWT revocation / refresh rotation enforcement
    # ------------------------------------------------------------------
    @jwt.token_in_blocklist_loader
    def _token_in_blocklist(jwt_header, jwt_payload):
        """Return True if the token should be rejected.

        - Access tokens: reject only if explicitly revoked.
        - Refresh tokens: reject if missing from DB, revoked, replaced, or expired.
          (The underlying JWT library already enforces expiration, but we
          re-check for safety and for allow_expired decode paths.)
        """
        try:
            jti = jwt_payload.get("jti")
            token_type = jwt_payload.get("type") or jwt_payload.get("token_type")
            username = jwt_payload.get("sub")

            sid = jwt_payload.get("sid")

            if token_type == "access":
                if is_auth_token_revoked(jti):
                    return True
                # Access tokens must be bound to a live auth-session row.  This
                # makes admin/user revocation take effect for every protected
                # HTTP endpoint even when idle logout is disabled.
                if not sid:
                    return True
                if not is_auth_session_active(sid, username=username, max_idle_seconds=max_idle_seconds):
                    return True
                return False

            if token_type == "refresh":
                if not is_refresh_token_usable(username, jti):
                    return True
                # Respect explicit session revocation (logout, admin revoke, etc.).
                if sid and not is_auth_session_active(sid, username=username, max_idle_seconds=None):
                    return True
                return False

            # Unknown token types are rejected.
            return True
        except Exception:
            return True

    # ------------------------------------------------------------------
    # CORS (hardened defaults)
    # ------------------------------------------------------------------
    # Default: CORS is OFF unless explicitly configured.
    # Why: EchoChat uses cookie-based auth; "*" + credentials is unsafe.
    cors_cfg = settings.get("cors_allowed_origins")
    if cors_cfg is None:
        cors_cfg = settings.get("allowed_origins")

    cors_origins = None
    cors_enabled = False

    def _normalize_cors_origins(val):
        if val is None:
            return None
        if isinstance(val, str):
            raw = val.strip()
            if not raw:
                return None
            # Support comma-separated strings
            if "," in raw:
                items = [x.strip() for x in raw.split(",") if x.strip()]
                return items or None
            return raw
        if isinstance(val, (list, tuple, set)):
            items = [str(x).strip() for x in val if str(x).strip()]
            return items or None
        return None

    cors_candidate = _normalize_cors_origins(cors_cfg)
    if cors_candidate is not None:
        # Disallow wildcard with credentials.
        if cors_candidate == "*" or (isinstance(cors_candidate, (list, tuple)) and "*" in cors_candidate):
            logging.warning("CORS origins includes '*'. Disabling CORS because EchoChat uses credentialed cookies.")
            cors_candidate = None

    if _use_same_origin_lan_cors_mode(settings, cors_candidate):
        logging.info(
            "Default localhost-only allowed origins detected; using same-origin LAN mode so browsers opened at "
            "http://<this-computer-ip>:%s can connect Socket.IO without reconnect loops.",
            _safe_int(settings.get("port") or settings.get("server_port") or 5000, 5000, name="port", minimum=1, maximum=65535),
        )
        cors_candidate = None

    if cors_candidate is not None:
        cors_origins = cors_candidate
        cors_enabled = True
        if CORS:
            CORS(
                app,
                supports_credentials=True,
                origins=cors_origins,
            )
        else:
            print("⚠️  flask-cors not installed; CORS settings ignored.")

    app.config["ECHOCHAT_EFFECTIVE_CORS_ORIGINS"] = cors_origins

    lan_warning = _public_lan_http_warning(settings)
    if lan_warning:
        logging.warning(lan_warning)

    storage_uri = settings.get("rate_limit_storage_uri") or settings.get("rate_limit_storage") or "memory://"
    simple_guard_storage_uri = str(settings.get("simple_rate_limit_storage_uri") or storage_uri or "").strip()
    if simple_guard_storage_uri.startswith(("redis://", "rediss://")):
        app.config["ECHOCHAT_SIMPLE_RATE_LIMIT_REDIS_URL"] = simple_guard_storage_uri
    else:
        app.config["ECHOCHAT_SIMPLE_RATE_LIMIT_REDIS_URL"] = ""
    if limiter is None:
        limiter = Limiter(
            key_func=lambda: get_request_ip(),
            storage_uri=storage_uri,
        )
    limiter.init_app(app)
    _ensure_db_teardown_registered(app)


    # ────────────────────────────────────────────────────────────
    # HTTP rate limiting (admin guardrail)
    # ────────────────────────────────────────────────────────────
    # For /admin/* (many endpoints), we apply a centralized per-IP guardrail
    # to avoid missing new endpoints accidentally.
    #
    # Configure via server_config.json:
    #   - admin_rate_limit_get:   "600 per minute"
    #   - admin_rate_limit_write: "120 per minute"
    #
    from security import parse_rate_limit_value, request_has_valid_double_submit_csrf, request_is_same_origin, simple_rate_limit, get_request_ip  # local import to avoid cycles

    def _rate_limited_response(message: str, retry_after: float = 0.0):
        status = 429
        headers = {'Retry-After': str(int(max(1, retry_after or 1.0)))}
        wants_json = False
        try:
            wants_json = (request.path.startswith('/api/') or request.path.startswith('/auth/') or request.path.startswith('/admin/'))
            if not wants_json:
                wants_json = request.accept_mimetypes.best == 'application/json'
        except Exception:
            wants_json = False
        if wants_json:
            return ({'ok': False, 'error': 'rate_limited', 'message': message}, status, headers)
        return (message, status, headers)

    def _csrf_rejected_response(path: str, reason: str = 'csrf_required'):
        status = 403
        wants_json = False
        try:
            wants_json = (
                path.startswith('/api/')
                or path.startswith('/auth/')
                or path.startswith('/admin/')
                or path == '/upload'
                or request.accept_mimetypes.best == 'application/json'
            )
        except Exception:
            wants_json = False
        if wants_json:
            return ({'ok': False, 'error': 'csrf_required', 'code': 'csrf_required', 'reason': reason}, status)
        return ('CSRF token missing or invalid', status)

    def _path_requires_jwt_double_submit_csrf(path: str, method: str) -> bool:
        """Central guard for cookie-authenticated HTTP write endpoints.

        Public auth forms keep their dedicated form/fallback CSRF handlers; this
        guard covers the protected JSON/API/admin/upload surfaces that rely on
        JWT cookies and therefore must send the readable JWT CSRF cookie back in
        X-CSRF-TOKEN or a same-origin form field.
        """
        if method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return False
        if path == '/token/refresh':
            return True
        if path == '/upload':
            return True
        if path.startswith('/api/'):
            return True
        if path.startswith('/admin/') or path == '/admin':
            return True
        if path.startswith('/auth/'):
            return True
        if path.startswith('/moderation'):
            return True
        return False

    @app.before_request
    def _request_security_guard():
        try:
            request.max_form_memory_size = int(app.config.get("MAX_FORM_MEMORY_SIZE") or max_form_memory_size)
            request.max_form_parts = int(app.config.get("MAX_FORM_PARTS") or max_form_parts)
            path = request.path or ''
            method = (request.method or 'GET').upper()
            is_write = method in ('POST', 'PUT', 'PATCH', 'DELETE')
            ip = get_request_ip()

            protected_write = False
            if is_write:
                protected_write = (
                    path.startswith('/api/')
                    or path.startswith('/admin')
                    or path.startswith('/auth/')
                    or path in {'/login', '/register', '/forgot-password', '/token/refresh', '/logout'}
                    or path.startswith('/reset-password/')
                    or path.startswith('/moderation')
                )

            upload_overhead = 256_000
            upload_caps = {
                '/upload': _safe_int(settings.get('max_legacy_public_upload_bytes') or max_request_bytes, max_request_bytes, name='max_legacy_public_upload_bytes', minimum=1024, maximum=104857600) + upload_overhead,
                '/api/dm_files/upload': _safe_int(settings.get('max_dm_file_bytes') or max_request_bytes, max_request_bytes, name='max_dm_file_bytes', minimum=1024, maximum=104857600) + upload_overhead,
                '/api/group_files/upload': _safe_int(settings.get('max_group_upload_bytes') or settings.get('max_group_file_bytes') or max_request_bytes, max_request_bytes, name='max_group_upload_bytes', minimum=1024, maximum=104857600) + upload_overhead,
            }
            request_content_limit = upload_caps.get(path, max_request_bytes)
            if path.startswith('/api/groups/') and path.endswith('/upload'):
                request_content_limit = _safe_int(settings.get('max_group_upload_bytes') or settings.get('max_group_file_bytes') or max_request_bytes, max_request_bytes, name='max_group_upload_bytes', minimum=1024, maximum=104857600) + upload_overhead
            request.max_content_length = _safe_int(request_content_limit, max_request_bytes, name='request_content_limit', minimum=1024, maximum=105113600)

            if protected_write and bool(settings.get('enforce_same_origin_writes', True)):
                allow_missing_same_origin_headers_for_writes = bool(settings.get('allow_missing_same_origin_headers_for_writes', False))
                ok_origin, origin_reason = request_is_same_origin(request, allow_missing=allow_missing_same_origin_headers_for_writes)
                if not ok_origin:
                    logging.warning('Blocked cross-site write request path=%s method=%s ip=%s reason=%s', path, method, ip, origin_reason)
                    return ({'ok': False, 'error': 'cross_site_blocked', 'reason': origin_reason}, 403)

            if _path_requires_jwt_double_submit_csrf(path, method):
                if bool(settings.get('enforce_jwt_double_submit_csrf_writes', True)) and not request_has_valid_double_submit_csrf(request):
                    logging.warning('Blocked protected write without valid JWT CSRF path=%s method=%s ip=%s', path, method, ip)
                    return _csrf_rejected_response(path)

            if path.startswith('/admin') or path.startswith('/api/debug/config'):
                get_val = settings.get('admin_rate_limit_get') or '600 per minute'
                write_val = settings.get('admin_rate_limit_write') or '120 per minute'
                if method in ('GET', 'HEAD', 'OPTIONS'):
                    lim, win = parse_rate_limit_value(get_val, default_limit=600, default_window=60)
                    ok, retry_after = simple_rate_limit(f'admin:{ip}:{method}', limit=lim, window_sec=win)
                else:
                    lim, win = parse_rate_limit_value(write_val, default_limit=120, default_window=60)
                    actor = str(session.get('username') or 'anonymous').strip() or 'anonymous'
                    # Broad IP guard plus actor/path guard catches both bot floods
                    # and one compromised admin tab hammering a specific write.
                    ok, retry_after = simple_rate_limit(f'admin:{ip}:{method}', limit=lim, window_sec=win)
                    if ok:
                        ok, retry_after = simple_rate_limit(f'adminw:{ip}:{actor}:{path}:{method}', limit=lim, window_sec=win)
                if not ok:
                    return _rate_limited_response('Rate limited', retry_after)

            if is_write and path.startswith('/api/') and not path.startswith('/api/debug/config'):
                lim, win = parse_rate_limit_value(settings.get('api_rate_limit_write_guard') or '300 per minute', default_limit=300, default_window=60)
                ok, retry_after = simple_rate_limit(f'apiw:{ip}:{method}', limit=lim, window_sec=win)
                if not ok:
                    return _rate_limited_response('API write rate limited', retry_after)

            if is_write and path.startswith('/auth/'):
                lim, win = parse_rate_limit_value(settings.get('auth_rate_limit_write_guard') or '60 per minute', default_limit=60, default_window=60)
                ok, retry_after = simple_rate_limit(f'authw:{ip}:{method}', limit=lim, window_sec=win)
                if not ok:
                    return _rate_limited_response('Auth action rate limited', retry_after)

            if path == '/token/refresh' and method == 'POST':
                lim, win = parse_rate_limit_value(settings.get('rate_limit_refresh_guard') or '45 per minute', default_limit=45, default_window=60)
                ok, retry_after = simple_rate_limit(f'refresh:{ip}', limit=lim, window_sec=win)
                if not ok:
                    return _rate_limited_response('Refresh rate limited', retry_after)

            if is_write and (path in {'/login', '/register', '/forgot-password', '/account/security', '/enable-2fa'} or path.startswith('/reset-password/')):
                lim, win = parse_rate_limit_value(settings.get('form_rate_limit_write_guard') or '30 per minute', default_limit=30, default_window=60)
                ok, retry_after = simple_rate_limit(f'formw:{ip}:{path}', limit=lim, window_sec=win)
                if not ok:
                    return _rate_limited_response('Form submission rate limited', retry_after)
        except Exception as exc:
            protected = False
            try:
                p = request.path or ''
                m = (request.method or 'GET').upper()
                protected = (m in {'POST', 'PUT', 'PATCH', 'DELETE'} and (
                    p.startswith('/api/')
                    or p.startswith('/admin')
                    or p.startswith('/auth/')
                    or p.startswith('/moderation')
                    or p in {'/login', '/register', '/forgot-password', '/token/refresh', '/logout', '/upload'}
                    or p.startswith('/reset-password/')
                ))
            except Exception:
                protected = True
            logging.exception('Central request security guard failed; protected=%s', protected)
            if protected:
                wants_json = False
                try:
                    wants_json = request.path.startswith(('/api/', '/auth/', '/admin/')) or request.accept_mimetypes.best == 'application/json'
                except Exception:
                    wants_json = True
                if wants_json:
                    return ({'ok': False, 'error': 'security_guard_failed'}, 500)
                return ('Security guard failed', 500)
            return None
        return None

    @app.errorhandler(RequestEntityTooLarge)
    def _handle_413(_exc):
        return ({"ok": False, "error": "request_too_large"}, 413)

    # Boot banner (helps catch wrong config / wrong DB early)
    _log_startup_banner(settings, settings_file)

    # DB teardown must be registered before startup app-context DB work.
    _ensure_db_teardown_registered(app)

    # ───── Initialize DB ─────
    _initialize_database_stack(app, settings)

    # ───── SocketIO Setup ─────
    # Use a dedicated Engine.IO cookie name so it never collides with JWT cookies.
    # The cookie is hardened with HttpOnly/Secure/SameSite attributes in
    # _create_socketio_instance().
    #
    # NOTE: long-polling generates a *ton* of HTTP requests (and log lines). If
    # eventlet is available, we prefer it to enable WebSockets and dramatically
    # cut request volume.
    socketio, socketio_runtime_context = _create_socketio_instance(app, settings, cors_origins)

    # Flask-SocketIO replaces/wraps app.wsgi_app with an Engine.IO middleware.
    # Re-apply the guard at the outer layer so Socket.IO long-polling aborts
    # cannot bypass the Flask-route guard and trigger Werkzeug's raw
    # "write() before start_response" assertion.
    _wrap_wsgi_start_response_guard(app, settings, layer="socketio")

    _record_startup_preflight(app, settings, settings_file, socketio_runtime_context)

    # Expose the SocketIO instance to blueprints that need to emit events from
    # normal HTTP routes (e.g., invite notifications).
    app.config["ECHOCHAT_SOCKETIO"] = socketio

    # ───── Global Socket.IO Error Handler (Fix A) ─────
    # Flask-SocketIO will otherwise log/propagate JWT errors raised inside
    # event handlers (e.g., @jwt_required()) and can leave clients in a bad
    # state. We convert auth errors into a client-visible signal and then
    # disconnect so the browser can refresh/re-auth.
    @socketio.on_error_default  # applies to all namespaces
    def _socketio_default_error_handler(e):
        try:
            sid = getattr(request, "sid", None)
        except Exception:
            sid = None

        # Auth/token problems (expired, missing, CSRF) -> notify + disconnect
        if isinstance(e, ExpiredSignatureError):
            try:
                if sid:
                    emit("auth_error", {"reason": "access_token_expired"}, to=sid)
            except Exception:
                pass
            try:
                disconnect(sid=sid)
            except Exception:
                pass
            return

        if isinstance(e, (InvalidTokenError, NoAuthorizationError, CSRFError, JWTExtendedException)):
            try:
                if sid:
                    emit("auth_error", {"reason": "auth_failed"}, to=sid)
            except Exception:
                pass
            try:
                disconnect(sid=sid)
            except Exception:
                pass
            return

        # Everything else: log it, but avoid crashing the server thread.
        try:
            app.logger.exception("Socket.IO handler error: %s", e)
        except Exception:
            pass
        return

    # ───── Routes ─────
    _register_application_routes(app, settings, socketio, limiter)

    return app, socketio


def run_web_server(
    settings: Dict[str, Any],
    limiter: Optional[Limiter] | None = None,
    settings_file: Optional[Path] | None = None,
) -> None:
    """Bootstrap the Flask-SocketIO app, attach blueprints & handlers, then run it."""

    app, socketio = create_app(settings, limiter=limiter, settings_file=settings_file)

    # ───── Run Server (dev / single-process) ─────
    host = settings.get("host") or settings.get("server_host") or "0.0.0.0"
    port = _safe_int(settings.get("port") or settings.get("server_port") or 5000, 5000, name="port", minimum=1, maximum=65535)
    debug = bool(settings.get("debug") or settings.get("server_debug") or False)

    # HTTPS support (required for WebCrypto/E2EE on non-localhost origins).
    https_enabled = bool(settings.get("https", False))
    ssl_cert = settings.get("ssl_cert_file") or settings.get("ssl_cert") or settings.get("cert_file")
    ssl_key = settings.get("ssl_key_file") or settings.get("ssl_key") or settings.get("key_file")
    ssl_context = None

    if https_enabled:
        if ssl_cert and ssl_key and os.path.exists(str(ssl_cert)) and os.path.exists(str(ssl_key)):
            ssl_context = (str(ssl_cert), str(ssl_key))
        else:
            print("⚠️  https=true but ssl_cert_file/ssl_key_file missing or not found. Falling back to HTTP.")
            https_enabled = False

    scheme = "https" if https_enabled else "http"
    print(f"🚀  Starting {_runtime_server_name(settings)} on {scheme}://{host}:{port} (debug={debug})")

    async_mode = app.config.get("ECHOCHAT_SOCKETIO_ASYNC_MODE") or "threading"
    use_reloader = bool(debug and async_mode == "threading")

    # Background janitor: cleanup inactive custom rooms + expired messages.
    # NOTE: When running under Gunicorn with multiple workers, run this as a
    # separate service (see janitor_runner.py) to avoid N janitors. In debug
    # reloader mode, only the serving child process should start the thread;
    # the parent watcher process must stay cleanup-free.
    if bool(app.config.get("ECHOCHAT_START_JANITOR_INPROCESS", True)):
        if use_reloader and os.environ.get("WERKZEUG_RUN_MAIN") not in {"true", "1"}:
            logging.info("Skipping in-process janitor startup in Werkzeug reloader parent process")
        else:
            start_janitor(settings)
    else:
        logging.info("Skipping in-process janitor startup because multi-worker mode was detected")

    # Reduce console spam from long-polling and redact randomized Test Lab tokens
    # from Werkzeug access logs. This does not disable Socket.IO itself; it only
    # suppresses noisy request log lines and removes bearer-like URL material.
    try:

        class _EchoChatSensitiveAccessFilter(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:  # type: ignore
                try:
                    msg = record.getMessage()
                except Exception:
                    return True
                if "/socket.io/" in msg:
                    return False
                redacted = re.sub(r"/admin/test_lab/[A-Za-z0-9_\-]{24,}(/?)", r"/admin/test_lab/<redacted>\1", msg)
                redacted = re.sub(r"/admin/test-lab/[A-Za-z0-9_\-]{24,}(/?)", r"/admin/test-lab/<redacted>\1", redacted)
                if redacted != msg:
                    record.msg = redacted
                    record.args = ()
                return True

        logging.getLogger("werkzeug").addFilter(_EchoChatSensitiveAccessFilter())
    except Exception:
        pass

    run_kwargs = {
        "host": host,
        "port": port,
        "debug": debug,
        "use_reloader": use_reloader,
        "log_output": False,
    }
    if ssl_context is not None:
        if async_mode == "threading":
            run_kwargs["ssl_context"] = ssl_context
        else:
            print(
                "⚠️  HTTPS via ssl_context is only supported by the built-in threading server. "
                "Starting without built-in TLS for async_mode=%s; use a reverse proxy or "
                "set ECHOCHAT_SOCKETIO_ASYNC=threading if you need direct dev HTTPS."
                % async_mode
            )

    socketio.run(app, **run_kwargs)


# ───── Helpers ─────
def _ensure_secret_key(
    settings: Dict[str, Any],
    settings_file: Optional[Path],
) -> str:
    key = resolve_secret(settings, "secret_key")
    if key:
        settings["secret_key"] = key
        return key

    key, generated, env_path = ensure_secret(settings, "secret_key", settings_file=settings_file)
    if generated:
        if env_path:
            print(f"✅ Stable secret_key generated and saved to {env_path}.")
        elif _persist_generated_key(settings, settings_file):
            print("✅ secret_key generated and saved to settings.")
        else:
            print("⚠️  secret_key generated for this process, but could not be persisted. Run: python main.py --generate-secrets --write-env-secrets")
    return key


def _ensure_jwt_secret(
    settings: Dict[str, Any],
    settings_file: Optional[Path],
) -> str:
    key = resolve_secret(settings, "jwt_secret")
    if key:
        settings["jwt_secret"] = key
        return key

    key, generated, env_path = ensure_secret(settings, "jwt_secret", settings_file=settings_file)
    if generated:
        if env_path:
            print(f"✅ Stable jwt_secret generated and saved to {env_path}.")
        elif _persist_generated_key(settings, settings_file):
            print("✅ jwt_secret generated and saved to settings.")
        else:
            print("⚠️  jwt_secret generated for this process, but could not be persisted. Run: python main.py --generate-secrets --write-env-secrets")
    return key


def _persist_generated_key(settings: Dict[str, Any], settings_file: Optional[Path]) -> bool:
    if not persist_secrets_enabled(settings):
        return False
    if not settings_file:
        print("⚠️  settings_file path not supplied; cannot persist generated secrets.")
        return False

    try:
        if settings_file.suffix.lower() == ".json":
            existing: dict | None = None
            if settings_file.exists():
                try:
                    with settings_file.open("r", encoding="utf-8") as fp:
                        existing = json.load(fp)
                except Exception:
                    existing = None
            if existing is None and settings_file.exists():
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                bad_path = settings_file.with_suffix(settings_file.suffix + f".bad-{ts}")
                try:
                    settings_file.rename(bad_path)
                    print(f"⚠️  Backed up invalid settings file to: {bad_path}")
                except Exception as exc:
                    print(f"⚠️  Could not back up invalid settings file: {exc}")
                    return False
                existing = {}
            merged = dict(existing or {})
            merged.update(settings)
            merged = scrub_secrets_for_persist(merged)
            with settings_file.open("w", encoding="utf-8") as fp:
                json.dump(merged, fp, indent=2)
                fp.write("\n")
        elif settings_file.suffix.lower() in {".yml", ".yaml"}:
            import yaml
            with settings_file.open("w", encoding="utf-8") as fp:
                yaml.safe_dump(scrub_secrets_for_persist(settings), fp, sort_keys=False)
        else:
            print(f"⚠️  Unsupported settings file format: {settings_file}")
            return False
    except Exception as exc:
        print(f"⚠️  Could not persist generated secret to {settings_file}: {exc}", file=sys.stderr)
        return False
    return True
