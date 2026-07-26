from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from constants import APP_VERSION, get_db_connection_string, postgres_dsn_parts, redact_postgres_dsn, sanitize_postgres_dsn
from media_mode import resolve_av_mode
from secrets_policy import persist_secrets_enabled
from secret_manager import is_strong_secret, resolve_secret
from runtime_timing import timing_float, timing_int


PROJECT_ROOT = Path(__file__).resolve().parent


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status(name: str, state: str, summary: str, **extra: Any) -> dict:
    item = {"name": name, "status": state, "summary": summary}
    item.update(extra)
    return item


_STATUS_RANK = {"fail": 3, "warn": 2, "ok": 1, "info": 0, "disabled": 0}


def _overall_status(checks: list[dict]) -> str:
    highest = "ok"
    for check in checks:
        state = str(check.get("status") or "ok")
        if _STATUS_RANK.get(state, 0) > _STATUS_RANK.get(highest, 0):
            highest = state
    return highest


def _summarise_counts(checks: list[dict]) -> dict:
    counts = {"ok": 0, "warn": 0, "fail": 0, "info": 0, "disabled": 0}
    for check in checks:
        state = str(check.get("status") or "ok")
        counts[state] = counts.get(state, 0) + 1
    return counts


def _redact_url_password(url: str | None) -> str | None:
    if not url:
        return url
    try:
        parsed = urlparse(str(url))
        if not parsed.scheme or "@" not in parsed.netloc:
            return str(url)
        creds, host = parsed.netloc.rsplit("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            creds = f"{user}:***"
        redacted = parsed._replace(netloc=f"{creds}@{host}")
        return urlunparse(redacted)
    except Exception:
        return str(url)




def _safe_positive_int(value: Any, default: int = 1, *, maximum: int | None = None) -> int:
    try:
        out = int(value)
    except Exception:
        out = default
    if out < 1:
        out = default
    if maximum is not None and out > maximum:
        out = maximum
    return out


def _safe_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        out = int(value)
    except Exception:
        out = default
    if minimum is not None and out < minimum:
        out = minimum
    if maximum is not None and out > maximum:
        out = maximum
    return out


def _resolve_worker_count(settings: dict) -> int:
    for name in ("HUI_WORKERS", "HUI_PRODUCTION_WORKERS", "WEB_CONCURRENCY", "PRODUCTION_WORKERS"):
        raw = os.environ.get(name)
        if raw:
            return _safe_positive_int(raw, 1)
    for key in ("production_workers", "worker_count", "web_workers"):
        raw = settings.get(key)
        if raw:
            return _safe_positive_int(raw, 1)
    return 1


def _resolve_instance_count(settings: dict) -> int:
    for name in ("HUI_PRODUCTION_INSTANCES", "HUI_INSTANCE_COUNT", "PRODUCTION_INSTANCES"):
        raw = os.environ.get(name)
        if raw:
            return _safe_positive_int(raw, 1, maximum=10)
    for key in ("production_instance_count", "production_instances", "instance_count"):
        raw = settings.get(key)
        if raw:
            return _safe_positive_int(raw, 1, maximum=10)
    return 1

def _resolve_runtime_paths(settings: dict) -> dict[str, Path]:
    def _resolve(raw: str | None, fallback: Path) -> Path:
        if raw is None or str(raw).strip() == "":
            return fallback
        p = Path(str(raw))
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    document_root = _resolve(settings.get("document_root"), PROJECT_ROOT / "www")
    static_upload_root = PROJECT_ROOT / "static" / "uploads"
    profile_avatar_root = static_upload_root / "profile_avatars"
    dm_upload_root = _resolve(settings.get("dm_upload_root"), PROJECT_ROOT / "uploads" / "dm_files")
    group_upload_root = _resolve(settings.get("group_upload_root"), PROJECT_ROOT / "uploads" / "group_files")
    torrents_root = _resolve(settings.get("torrents_root"), PROJECT_ROOT / "uploads" / "torrents")
    legacy_group_upload_root = PROJECT_ROOT / "instance" / "uploads" / "groups"

    return {
        "document_root": document_root,
        "static_upload_root": static_upload_root,
        "profile_avatar_root": profile_avatar_root,
        "dm_upload_root": dm_upload_root,
        "group_upload_root": group_upload_root,
        "torrents_root": torrents_root,
        "legacy_group_upload_root": legacy_group_upload_root,
    }


def _write_probe(path: Path) -> tuple[bool, str | None]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".hui-preflight-", dir=str(path), delete=False) as tmp:
            tmp.write(b"ok")
            probe = Path(tmp.name)
        try:
            probe.unlink(missing_ok=True)
        except TypeError:  # pragma: no cover - Python < 3.8 fallback
            if probe.exists():
                probe.unlink()
        return True, None
    except Exception as exc:
        return False, str(exc)


def _check_settings_file(settings_file: str | os.PathLike[str] | None) -> dict:
    if not settings_file:
        return _status("settings_file", "info", "No settings file path supplied", path=None, exists=False)
    path = Path(str(settings_file))
    return _status(
        "settings_file",
        "ok" if path.exists() else "warn",
        "Settings file found" if path.exists() else "Settings file path does not exist yet",
        path=str(path),
        exists=path.exists(),
    )


def _check_secret_and_cookie_coherence(settings: dict) -> dict:
    secret_key = resolve_secret(settings, "secret_key")
    jwt_secret = resolve_secret(settings, "jwt_secret")

    https_enabled = bool(settings.get("https", False))
    cookie_secure = bool(settings.get("cookie_secure", False) or https_enabled)
    cookie_samesite = str(settings.get("cookie_samesite") or "Lax").strip() or "Lax"
    persistence = bool(persist_secrets_enabled(settings))

    missing = []
    if not is_strong_secret(secret_key):
        missing.append("secret_key")
    if not is_strong_secret(jwt_secret):
        missing.append("jwt_secret")

    state = "ok"
    notes: list[str] = []
    if missing:
        if persistence:
            state = "warn"
            notes.append(f"missing {', '.join(missing)}; runtime can generate/persist them")
        else:
            state = "warn"
            notes.append(f"missing {', '.join(missing)} while secret persistence is disabled; restarts will rotate secrets")

    if cookie_samesite.lower() == "none" and not cookie_secure:
        state = "fail"
        notes.append("cookie_samesite=None requires cookie_secure=true/https=true")

    cors_cfg = settings.get("cors_allowed_origins")
    if cors_cfg is None:
        cors_cfg = settings.get("allowed_origins")
    cors_text = json.dumps(cors_cfg) if isinstance(cors_cfg, (list, dict)) else str(cors_cfg or "")
    if "*" in cors_text:
        if state != "fail":
            state = "warn"
        notes.append("wildcard CORS with cookie auth is unsafe; prefer explicit origins")

    if not notes:
        notes.append("secret + cookie settings look coherent")

    return _status(
        "security_config",
        state,
        "; ".join(notes),
        https_enabled=https_enabled,
        cookie_secure=cookie_secure,
        cookie_samesite=cookie_samesite,
        secret_persistence_enabled=persistence,
        missing_secrets=missing,
    )


def _check_upload_surface(settings: dict) -> dict:
    legacy_public = bool(settings.get("enable_legacy_public_uploads", False))
    allow_svg = bool(settings.get("allow_svg_avatars", False))
    notes: list[str] = []
    state = "ok"
    if legacy_public:
        state = "warn"
        notes.append("legacy /upload endpoint is enabled; prefer ciphertext-only DM/group uploads")
    if allow_svg:
        state = "warn"
        notes.append("user-uploaded SVG avatars are enabled")
    if not notes:
        notes.append("public upload surface is minimized")
    return _status("upload_surface", state, "; ".join(notes), legacy_public_uploads=legacy_public, allow_svg_avatars=allow_svg)


def _resolve_message_queue(settings: dict) -> str | None:
    for key in ("HUI_SOCKETIO_MESSAGE_QUEUE", "SOCKETIO_MESSAGE_QUEUE"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    profile = settings.get("socketio_profile") or {}
    if isinstance(profile, dict):
        v = str(profile.get("message_queue") or "").strip()
        if v:
            return v
    v = str(settings.get("socketio_message_queue") or "").strip()
    return v or None




def _check_socketio_topology(settings: dict, runtime_context: dict | None = None) -> dict:
    runtime_context = runtime_context or {}
    worker_count = _safe_int(runtime_context.get("worker_count") or _resolve_worker_count(settings), 1, minimum=1)
    instance_count = _safe_int(runtime_context.get("production_instance_count") or _resolve_instance_count(settings), 1, minimum=1, maximum=10)
    websocket_only = bool(runtime_context.get("websocket_only") or False)
    max_http_buffer_size = _safe_int(runtime_context.get("max_http_buffer_size") or settings.get("socketio_max_http_buffer_size") or settings.get("max_request_bytes") or 31457280, 31457280, minimum=16384, maximum=104857600)
    async_mode = runtime_context.get("async_mode")
    ws_enabled = runtime_context.get("ws_enabled")
    message_queue = runtime_context.get("message_queue") or _resolve_message_queue(settings)

    state = "ok"
    notes: list[str] = ["Socket.IO topology evaluated"]
    if worker_count > 1 and not message_queue:
        state = "fail"
        notes.append("multi-worker mode does not have an explicit Socket.IO message queue configured")
    if instance_count > 1 and not message_queue:
        state = "fail"
        notes.append("multiple one-worker instances need an explicit Socket.IO message queue")
    if worker_count > 1 and websocket_only and not ws_enabled:
        state = "fail"
        notes.append("multi-worker websocket-only topology requires WebSocket support; threading/polling is not supported")
    elif worker_count > 1 and async_mode == "threading":
        state = "warn" if state != "fail" else state
        notes.append("multi-worker runtime is using threading mode; prefer eventlet or another WebSocket-capable worker")

    return _status(
        "socketio_topology",
        state,
        "; ".join(notes),
        worker_count=worker_count,
        production_instance_count=instance_count,
        websocket_only=websocket_only,
        max_http_buffer_size=max_http_buffer_size,
        async_mode=async_mode,
        ws_enabled=ws_enabled,
        message_queue=_redact_url_password(message_queue),
    )

def _check_db(settings: dict, init_db_pool_if_needed: bool) -> dict:
    dsn = get_db_connection_string(settings)
    try:
        from database import get_db, get_db_identity, get_schema_version, init_db_pool

        dsn_override = None
        if settings.get("database_url"):
            dsn_override = str(sanitize_postgres_dsn(str(settings["database_url"])))
        if init_db_pool_if_needed:
            instances = _resolve_instance_count(settings)
            cfg_min = _safe_int(settings.get("db_pool_min", 1), 1, minimum=1, maximum=25)
            if settings.get("db_pool_max") is None:
                cfg_max = 50 if instances <= 1 else (25 if instances <= 2 else 15 if instances <= 5 else 10)
            else:
                cfg_max = _safe_int(settings.get("db_pool_max", 50), 50 if instances <= 1 else 10, minimum=1, maximum=100)
            if instances <= 1 and cfg_max < 50:
                cfg_max = 50
            if cfg_min > cfg_max:
                cfg_min = cfg_max
            init_db_pool(
                minconn=cfg_min,
                maxconn=cfg_max,
                dsn=dsn_override,
                wait_timeout_seconds=settings.get("db_pool_wait_seconds", 10),
            )
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            cur.fetchone()
        ident = get_db_identity()
        schema_state = get_schema_version()
        return _status(
            "database",
            "ok",
            "Database reachable",
            dsn=redact_postgres_dsn(dsn),
            dsn_parts=postgres_dsn_parts(dsn),
            identity=ident,
            schema_state=schema_state,
        )
    except Exception as exc:
        return _status(
            "database",
            "fail",
            f"Database check failed: {exc}",
            dsn=redact_postgres_dsn(dsn),
            dsn_parts=postgres_dsn_parts(dsn),
            error=str(exc),
        )


def _check_runtime_paths(settings: dict) -> dict:
    paths = _resolve_runtime_paths(settings)
    details: dict[str, dict] = {}
    worst = "ok"
    for name, path in paths.items():
        ok, error = _write_probe(path)
        details[name] = {
            "path": str(path),
            "writable": ok,
            "error": error,
        }
        if not ok:
            worst = "fail"
    summary = "Runtime paths are writable" if worst == "ok" else "One or more runtime paths are not writable"
    return _status("runtime_paths", worst, summary, paths=details)


def _resolve_shared_state_url(settings: dict) -> str | None:
    for key in ("HUI_SHARED_STATE_REDIS_URL", "SHARED_STATE_REDIS_URL"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    v = str(settings.get("shared_state_redis_url") or "").strip()
    return v or None


def _check_shared_state_topology(settings: dict, runtime_context: dict | None = None) -> dict:
    runtime_context = runtime_context or {}
    worker_count = _safe_int(runtime_context.get("worker_count") or _resolve_worker_count(settings), 1, minimum=1)
    instance_count = _safe_int(runtime_context.get("production_instance_count") or _resolve_instance_count(settings), 1, minimum=1, maximum=10)
    scaled = bool(worker_count > 1 or instance_count > 1)
    url = _resolve_shared_state_url(settings)
    runtime_enabled = runtime_context.get("shared_state_enabled")
    state = "ok"
    notes = []
    if scaled and not url:
        state = "fail"
        notes.append("scaled realtime mode requires explicit shared_state_redis_url")
    elif scaled and runtime_enabled is False:
        state = "fail"
        notes.append("shared-state Redis is configured but runtime did not enable it")
    elif scaled:
        notes.append("shared-state Redis configured for scaled realtime state")
    elif url:
        notes.append("shared-state Redis configured")
    else:
        state = "info"
        notes.append("single-instance mode can use process-local realtime state")
    return _status(
        "shared_state_topology",
        state,
        "; ".join(notes),
        production_instance_count=instance_count,
        worker_count=worker_count,
        shared_state_url=_redact_url_password(url),
        runtime_enabled=runtime_enabled,
    )


def _check_socket_runtime(settings: dict, runtime_context: dict | None = None) -> dict:
    runtime_context = runtime_context or {}
    queue_url = _resolve_message_queue(settings)
    async_mode = runtime_context.get("async_mode")
    ws_enabled = runtime_context.get("ws_enabled")

    if not queue_url:
        return _status(
            "socketio_runtime",
            "info",
            "No Socket.IO message queue configured",
            async_mode=async_mode,
            ws_enabled=ws_enabled,
            message_queue=None,
        )

    if not (str(queue_url).startswith("redis://") or str(queue_url).startswith("rediss://")):
        return _status(
            "socketio_runtime",
            "warn",
            "Socket.IO message queue is configured but is not a redis:// URL; connectivity probe skipped",
            async_mode=async_mode,
            ws_enabled=ws_enabled,
            message_queue=_redact_url_password(queue_url),
        )

    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(
            queue_url,
            socket_connect_timeout=timing_float(settings, "redis_connect_timeout_seconds"),
            socket_timeout=timing_float(settings, "redis_socket_timeout_seconds"),
            health_check_interval=timing_int(settings, "redis_health_check_interval_seconds"),
        )
        client.ping()
        return _status(
            "socketio_runtime",
            "ok",
            "Socket.IO message queue is reachable",
            async_mode=async_mode,
            ws_enabled=ws_enabled,
            message_queue=_redact_url_password(queue_url),
        )
    except ImportError:
        return _status(
            "socketio_runtime",
            "fail",
            "Redis message queue configured but python package 'redis' is not installed",
            async_mode=async_mode,
            ws_enabled=ws_enabled,
            message_queue=_redact_url_password(queue_url),
        )
    except Exception as exc:
        return _status(
            "socketio_runtime",
            "fail",
            f"Redis message queue is not reachable: {exc}",
            async_mode=async_mode,
            ws_enabled=ws_enabled,
            message_queue=_redact_url_password(queue_url),
            error=str(exc),
        )


def _check_media_mode(settings: dict) -> dict:
    decision = resolve_av_mode(settings)
    features = decision.get("features") or {}
    policy = decision.get("webcam_policy") or {}
    return _status(
        "media_mode",
        "ok" if bool(decision.get("voice_enabled")) else "disabled",
        str(decision.get("label") or "Hui media mode ready"),
        requested_av_mode=decision.get("requested_mode"),
        active_av_mode=decision.get("mode"),
        webcam_enabled=bool(features.get("webcam")),
        microphone_enabled=bool(features.get("microphone")),
        webcam_policy=policy,
        transport="hui-webrtc-mesh",
    )

def _check_health_endpoint(settings: dict) -> dict:
    enabled = bool(settings.get("enable_health_check_endpoint", False))
    endpoint = str(settings.get("health_check_endpoint") or "/health")
    return _status(
        "health_endpoint",
        "ok" if enabled else "disabled",
        f"Health endpoint {'enabled' if enabled else 'disabled'} at {endpoint}",
        enabled=enabled,
        endpoint=endpoint,
    )


def run_preflight(
    settings: dict,
    settings_file: str | os.PathLike[str] | None = None,
    *,
    init_db_pool_if_needed: bool = False,
    runtime_context: Optional[Dict[str, Any]] = None,
    include_database: bool = True,
) -> dict:
    # Preflight is observational: it must report the saved/effective topology,
    # not silently auto-fill Redis or resize database pools before checking it.
    settings = dict(settings or {})
    runtime_context = dict(runtime_context or {})
    checks = [
        _check_socketio_topology(settings, runtime_context),
        _check_shared_state_topology(settings, runtime_context),
        _check_settings_file(settings_file),
        _check_secret_and_cookie_coherence(settings),
        _check_upload_surface(settings),
    ]
    if include_database:
        checks.append(_check_db(settings, init_db_pool_if_needed=init_db_pool_if_needed))
    else:
        checks.append(_status(
            "database_skipped",
            "info",
            "Database check skipped; rerun config doctor with --include-db after PostgreSQL/env secrets are ready",
            include_database=False,
        ))
    checks.extend([
        _check_runtime_paths(settings),
        _check_socket_runtime(settings, runtime_context=runtime_context),
        _check_media_mode(settings),
        _check_health_endpoint(settings),
    ])
    overall = _overall_status(checks)
    counts = _summarise_counts(checks)
    return {
        "version": APP_VERSION,
        "timestamp": _utcnow_iso(),
        "overall": overall,
        "counts": counts,
        "settings_file": str(settings_file) if settings_file else None,
        "runtime": {
            "async_mode": runtime_context.get("async_mode"),
            "ws_enabled": runtime_context.get("ws_enabled"),
            "message_queue": _redact_url_password(runtime_context.get("message_queue") or _resolve_message_queue(settings)),
        },
        "checks": checks,
    }


def log_preflight_summary(result: dict, logger: Optional[logging.Logger] = None) -> None:
    logger = logger or logging.getLogger(__name__)
    counts = result.get("counts") or {}
    logger.info(
        "Preflight overall: %s (ok=%s warn=%s fail=%s disabled=%s)",
        result.get("overall"),
        counts.get("ok", 0),
        counts.get("warn", 0),
        counts.get("fail", 0),
        counts.get("disabled", 0),
    )
    for check in result.get("checks") or []:
        state = str(check.get("status") or "info")
        msg = "[%s] %s: %s"
        if state == "fail":
            logger.error(msg, state.upper(), check.get("name"), check.get("summary"))
        elif state == "warn":
            logger.warning(msg, state.upper(), check.get("name"), check.get("summary"))
        else:
            logger.info(msg, state.upper(), check.get("name"), check.get("summary"))


def format_preflight_report(result: dict) -> str:
    lines = []
    lines.append(f"Preflight overall: {result.get('overall')}")
    lines.append(f"Timestamp: {result.get('timestamp')}")
    runtime = result.get("runtime") or {}
    if runtime:
        lines.append(
            "Runtime: async_mode={async_mode} ws_enabled={ws_enabled} message_queue={message_queue}".format(
                async_mode=runtime.get("async_mode"),
                ws_enabled=runtime.get("ws_enabled"),
                message_queue=runtime.get("message_queue"),
            )
        )
    for check in result.get("checks") or []:
        lines.append(f"- [{str(check.get('status') or '').upper()}] {check.get('name')}: {check.get('summary')}")
    return "\n".join(lines)
