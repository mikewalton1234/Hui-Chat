"""Redis and Socket.IO production topology checks for Hui Chat.

This module is deliberately dependency-light. It can run before PostgreSQL is
configured and before the Flask app is imported. The optional live Redis ping is
only performed when the caller requests it.
"""

from __future__ import annotations

import importlib.util
import os
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

from scaled_redis_autoconfig import (
    RECOMMENDED_RATE_LIMIT_REDIS,
    RECOMMENDED_SOCKETIO_QUEUE_REDIS,
    RECOMMENDED_SHARED_STATE_REDIS,
    apply_scaled_runtime_safety_defaults,
    redis_install_hint,
)
from runtime_timing import timing_float


@dataclass(frozen=True)
class RedisSocketIOItem:
    level: str
    code: str
    title: str
    detail: str
    fix: str = ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "production", "prod"}


def _hosting_mode(settings: dict[str, Any]) -> str:
    raw = str(settings.get("hosting_mode") or settings.get("deployment_profile") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"public", "public_beta", "internet", "production"}:
        return "public_beta"
    if raw in {"no_domain", "no_domain_yet", "pending_domain", "domain_later"}:
        return "no_domain_yet"
    if raw in {"lan", "local", "development", "dev"}:
        return "lan"
    if raw in {"advanced", "custom", "reverse_proxy"}:
        return "advanced"
    public_url = str(settings.get("public_base_url") or "").strip().lower()
    if public_url.startswith("https://"):
        return "public_beta"
    return "lan"


def _run_mode(settings: dict[str, Any]) -> str:
    raw = str(settings.get("run_mode") or settings.get("server_mode") or settings.get("deployment_mode") or "").strip().lower().replace("-", "_")
    if raw in {"production", "prod", "public", "public_beta"} or _truthy(settings.get("production_mode")):
        return "production"
    if raw in {"development", "dev", "lan", "local", "test", "testing"}:
        return "development"
    return raw or "development"


def _int_setting(settings: dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        try:
            value = int(settings.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    return default


def _production_workers(settings: dict[str, Any]) -> int:
    for env_key in ("HUI_WORKERS", "HUI_PRODUCTION_WORKERS", "WEB_CONCURRENCY", "PRODUCTION_WORKERS"):
        raw = os.getenv(env_key)
        if raw:
            try:
                value = int(raw)
                if value > 0:
                    return value
            except Exception:
                pass
    return _int_setting(settings, "production_workers", "worker_count", "web_workers", default=1)


def _production_instances(settings: dict[str, Any]) -> int:
    for env_key in ("HUI_PRODUCTION_INSTANCES", "HUI_INSTANCE_COUNT", "PRODUCTION_INSTANCES"):
        raw = os.getenv(env_key)
        if raw:
            try:
                value = int(raw)
                if value > 0:
                    return max(1, min(10, value))
            except Exception:
                pass
    return max(1, min(10, _int_setting(settings, "production_instance_count", "production_instances", "instance_count", default=1)))


def _worker_class(settings: dict[str, Any]) -> str:
    raw = (
        os.getenv("HUI_GUNICORN_WORKER_CLASS")
        or settings.get("production_worker_class")
        or settings.get("gunicorn_worker_class")
        or "gthread"
    )
    value = str(raw or "gthread").strip().lower()
    return "gthread" if value in {"", "threading", "threads"} else value


def _async_mode(settings: dict[str, Any]) -> str:
    raw = os.getenv("HUI_SOCKETIO_ASYNC") or settings.get("production_async_mode") or settings.get("socketio_async_mode") or "threading"
    return str(raw or "threading").strip().lower()


def _as_transports(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = [part.strip().lower() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(part).strip().lower() for part in value if str(part).strip()]
    else:
        raw = []
    out: list[str] = []
    for item in raw:
        if item in {"polling", "websocket"} and item not in out:
            out.append(item)
    return out


def _socketio_transports(settings: dict[str, Any], workers: int, async_mode: str) -> list[str]:
    explicit = os.getenv("HUI_SOCKETIO_TRANSPORTS") or settings.get("socketio_transports")
    parsed = _as_transports(explicit)
    if parsed:
        return parsed
    if workers > 1:
        return ["websocket"]
    if async_mode == "threading":
        return ["polling"]
    return ["websocket", "polling"]


def _queue_url(settings: dict[str, Any]) -> str:
    socketio_profile = settings.get("socketio_profile") or {}
    if not isinstance(socketio_profile, dict):
        socketio_profile = {}
    return str(
        os.getenv("HUI_SOCKETIO_MESSAGE_QUEUE")
        or os.getenv("SOCKETIO_MESSAGE_QUEUE")
        or socketio_profile.get("message_queue")
        or settings.get("socketio_message_queue")
        or ""
    ).strip()


def _rate_limit_url(settings: dict[str, Any]) -> str:
    return str(
        os.getenv("HUI_RATE_LIMIT_STORAGE_URI")
        or os.getenv("RATELIMIT_STORAGE_URI")
        or settings.get("rate_limit_storage_uri")
        or settings.get("rate_limit_storage")
        or "memory://"
    ).strip()


def _shared_state_url(settings: dict[str, Any]) -> str:
    return str(
        os.getenv("HUI_SHARED_STATE_REDIS_URL")
        or os.getenv("SHARED_STATE_REDIS_URL")
        or settings.get("shared_state_redis_url")
        or ""
    ).strip()


def _is_redis_url(url: str) -> bool:
    return str(url or "").strip().lower().startswith(("redis://", "rediss://"))


def _redact_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc += f":{parsed.port}"
        if parsed.username or parsed.password:
            user = parsed.username or "redis"
            netloc = f"{user}:***@{netloc}"
        return urlunparse((parsed.scheme, netloc, parsed.path or "", "", "", ""))
    except Exception:
        return raw.replace("://", "://***@", 1) if "@" in raw else raw


def _redis_identity(url: str) -> tuple[str, str, int, str] | None:
    if not _is_redis_url(url):
        return None
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "localhost").lower()
        port = int(parsed.port or 6379)
        db = (parsed.path or "/0").lstrip("/") or "0"
        return scheme, host, port, db
    except Exception:
        return None


def _same_redis_db(left: str, right: str) -> bool:
    li = _redis_identity(left)
    ri = _redis_identity(right)
    return bool(li and ri and li == ri)


def _redis_package_installed() -> bool:
    return importlib.util.find_spec("redis") is not None


def _tcp_port_open(host: str, port: int, timeout: float = 1.25) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, f"TCP connection succeeded to {host}:{port}."
    except Exception as exc:
        return False, f"TCP connection failed to {host}:{port}: {exc}"


def _ping_redis_url(url: str, *, timeout: float = 1.25) -> tuple[bool, str]:
    if not _is_redis_url(url):
        return False, f"Not a Redis URL: {_redact_url(url) or '(blank)'}"
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = int(parsed.port or 6379)
    if not _redis_package_installed():
        ok, msg = _tcp_port_open(host, port, timeout=timeout)
        if ok:
            return True, f"redis Python package is missing, but Redis TCP port appears reachable. Install redis>=5.0 for real PING validation. {msg}"
        return False, "redis Python package is missing and Redis TCP fallback failed. " + msg
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(url, socket_connect_timeout=timeout, socket_timeout=timeout)
        try:
            pong = client.ping()
        finally:
            try:
                client.close()
            except Exception:
                pass
        if pong:
            return True, f"Redis PING succeeded for {_redact_url(url)}."
        return False, f"Redis PING did not return success for {_redact_url(url)}."
    except Exception as exc:
        return False, f"Redis PING failed for {_redact_url(url)}: {exc}"


def _unique_redis_urls(*urls: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        raw = str(url or "").strip()
        if not raw or not _is_redis_url(raw):
            continue
        ident = str(_redis_identity(raw) or raw)
        if ident not in seen:
            out.append(raw)
            seen.add(ident)
    return out


def build_redis_socketio_report(settings: dict[str, Any], *, live_check: bool = False) -> dict[str, Any]:
    """Build a Redis/Socket.IO topology report.

    The report is suitable for terminal output and for embedding inside the
    broader public beta readiness report.
    """
    settings = dict(settings or {})
    auto_changed = apply_scaled_runtime_safety_defaults(settings)
    mode = _hosting_mode(settings)
    public_mode = mode == "public_beta"
    run_mode = _run_mode(settings)
    production_mode = run_mode == "production"
    workers = _production_workers(settings)
    instances = _production_instances(settings)
    worker_class = _worker_class(settings)
    async_mode = _async_mode(settings)
    transports = _socketio_transports(settings, workers, async_mode)
    queue = _queue_url(settings)
    rate_url = _rate_limit_url(settings)
    shared_url = _shared_state_url(settings)
    queue_is_redis = _is_redis_url(queue)
    rate_is_redis = _is_redis_url(rate_url)
    shared_is_redis = _is_redis_url(shared_url)
    redis_needed = queue_is_redis or rate_is_redis or shared_is_redis or public_mode or workers > 1 or instances > 1
    redis_pkg = _redis_package_installed()

    items: list[RedisSocketIOItem] = []

    if any(auto_changed.values()):
        items.append(RedisSocketIOItem(
            "pass",
            "scaled-redis-auto-config",
            "Scaled Redis URLs were auto-filled",
            f"rate={_redact_url(settings.get('rate_limit_storage_uri'))}; socketio={_redact_url(settings.get('socketio_message_queue'))}; shared={_redact_url(settings.get('shared_state_redis_url'))}",
            redis_install_hint(),
        ))

    if redis_pkg:
        items.append(RedisSocketIOItem("pass", "redis-python-package", "redis Python package is installed", "The Python client required for Redis health checks is importable."))
    elif live_check and redis_needed:
        items.append(RedisSocketIOItem("fail", "redis-python-package", "redis Python package is missing", "Live Redis validation was requested, but import redis failed.", "Run: python -m pip install redis>=5.0 or python -m pip install -r requirements.txt."))
    elif live_check:
        items.append(RedisSocketIOItem("warn", "redis-python-package", "redis Python package is not installed", "No Redis URL was configured for live validation."))

    if production_mode:
        items.append(RedisSocketIOItem("pass", "production-mode", "Production mode selected", f"run_mode={run_mode}; worker_class={worker_class}; async_mode={async_mode}"))
    elif public_mode:
        items.append(RedisSocketIOItem("fail", "production-mode", "Public beta is not using production mode", f"run_mode={run_mode}", "Set run_mode=production or start with python main.py --production."))
    else:
        items.append(RedisSocketIOItem("warn", "production-mode", "Development/LAN mode", "Acceptable for local testing. Use production mode before inviting internet testers."))

    if workers <= 1:
        items.append(RedisSocketIOItem("pass", "production-workers", "Single Gunicorn worker per instance", "Safe default for Flask-SocketIO under Gunicorn."))
    else:
        # Gunicorn's internal worker balancer is not sticky. Even with Redis, the
        # safe beginner path is one worker per Gunicorn process; scale horizontally
        # later with sticky routing and Redis queue.
        items.append(RedisSocketIOItem(
            "fail",
            "production-workers",
            "Gunicorn multi-worker mode is not beginner-safe for Socket.IO",
            f"production_workers={workers}; worker_class={worker_class}; transports={','.join(transports)}",
            "Use production_workers=1. Later scale with multiple one-worker Hui Chat instances behind a sticky reverse proxy plus socketio_message_queue=redis://127.0.0.1:6379/1.",
        ))

    if instances <= 1:
        items.append(RedisSocketIOItem("pass", "production-instances", "Single Hui Chat instance planned", "One instance with one worker is the beginner-safe deployment."))
    elif queue_is_redis:
        items.append(RedisSocketIOItem("pass", "production-instances", "Multiple one-worker instances planned", f"production_instance_count={instances}. Use sticky proxy routing across the instance ports."))
    else:
        items.append(RedisSocketIOItem("fail", "scaled-instance-queue", "Multiple instances need Redis Socket.IO queue", f"production_instance_count={instances}; socketio_message_queue is blank or not Redis.", f"Set socketio_message_queue={RECOMMENDED_SOCKETIO_QUEUE_REDIS} before starting all instances."))

    if (workers > 1 or instances > 1) and not queue:
        items.append(RedisSocketIOItem("fail", "socketio-message-queue", "Socket.IO message queue is missing", "Multiple realtime workers or multiple one-worker instances require a shared message queue for broadcasts.", f"Set socketio_message_queue={RECOMMENDED_SOCKETIO_QUEUE_REDIS}, or keep production_workers=1 and production_instance_count=1."))
    elif queue_is_redis:
        items.append(RedisSocketIOItem("pass", "socketio-message-queue", "Socket.IO Redis message queue configured", _redact_url(queue)))
    elif queue:
        items.append(RedisSocketIOItem("warn", "socketio-message-queue", "Socket.IO message queue is not Redis", _redact_url(queue), "Redis is the documented/simple path for Hui Chat public beta."))
    elif public_mode:
        items.append(RedisSocketIOItem("warn", "socketio-message-queue", "Socket.IO message queue is not configured", "Single-instance public beta can start without it, but Redis queue is recommended before adding testers or scaling.", f"Set socketio_message_queue={RECOMMENDED_SOCKETIO_QUEUE_REDIS}."))
    elif workers <= 1 and instances <= 1:
        items.append(RedisSocketIOItem("pass", "socketio-message-queue", "Socket.IO queue optional for LAN single-instance testing", "No message queue configured."))
    else:
        items.append(RedisSocketIOItem("fail", "socketio-message-queue", "Socket.IO message queue is missing", "Scaled topology needs a Redis Socket.IO queue.", f"Set socketio_message_queue={RECOMMENDED_SOCKETIO_QUEUE_REDIS}."))

    generic_redis_url = str(os.getenv("REDIS_URL") or "").strip()
    if generic_redis_url and not queue:
        items.append(RedisSocketIOItem("warn", "generic-redis-url", "Generic REDIS_URL is not used as the Socket.IO queue", _redact_url(generic_redis_url), "Set HUI_SOCKETIO_MESSAGE_QUEUE explicitly; keep REDIS_URL only for tools that truly require a generic Redis setting."))

    if rate_is_redis:
        items.append(RedisSocketIOItem("pass", "rate-limit-storage", "Redis-backed rate limits configured", _redact_url(rate_url)))
    elif str(rate_url or "").strip() == "memory://":
        level = "fail" if public_mode else "warn"
        items.append(RedisSocketIOItem(level, "rate-limit-storage", "Rate limits use memory://", "memory:// is per-process and resets on restart.", f"For public beta set rate_limit_storage_uri={RECOMMENDED_RATE_LIMIT_REDIS}."))
    else:
        level = "warn" if public_mode else "pass"
        items.append(RedisSocketIOItem(level, "rate-limit-storage", "Rate-limit storage is not Redis", _redact_url(rate_url) or "(blank)", "Use Redis for public beta unless this backend is shared and production-safe."))

    if shared_url:
        if shared_is_redis:
            items.append(RedisSocketIOItem("pass", "shared-state-redis", "Shared-state Redis URL configured", _redact_url(shared_url)))
        else:
            items.append(RedisSocketIOItem("warn", "shared-state-redis", "Shared-state URL is not Redis", _redact_url(shared_url), "Use Redis for cross-process shared state when scaling."))
    elif instances > 1 or workers > 1:
        items.append(RedisSocketIOItem("fail", "shared-state-redis", "Shared-state Redis URL is missing for scaled realtime mode", "Presence and live room state would stay process-local unless shared_state_redis_url is explicit.", f"Set shared_state_redis_url={RECOMMENDED_SHARED_STATE_REDIS}."))
    elif public_mode:
        items.append(RedisSocketIOItem("pass", "shared-state-redis", "Shared-state Redis URL is optional for first beta", "Not required for a single-instance public beta; useful later for scaled shared state."))

    if queue_is_redis and rate_is_redis and _same_redis_db(queue, rate_url):
        items.append(RedisSocketIOItem("warn", "redis-db-separation", "Socket.IO queue and rate limits share the same Redis DB", f"Both use {_redact_url(queue)}", f"Use {RECOMMENDED_RATE_LIMIT_REDIS} for rate limits and {RECOMMENDED_SOCKETIO_QUEUE_REDIS} for Socket.IO."))
    elif queue_is_redis and rate_is_redis:
        items.append(RedisSocketIOItem("pass", "redis-db-separation", "Redis DB separation looks good", f"rate={_redact_url(rate_url)}; socketio={_redact_url(queue)}"))
    elif public_mode:
        items.append(RedisSocketIOItem("warn", "redis-db-separation", "Redis DB separation could not be verified", "Configure both rate-limit storage and Socket.IO queue as Redis URLs."))

    if queue_is_redis and shared_is_redis and _same_redis_db(queue, shared_url):
        items.append(RedisSocketIOItem("warn", "redis-shared-state-separation", "Socket.IO queue and shared state use the same Redis DB", f"Both use {_redact_url(queue)}", f"Use {RECOMMENDED_SOCKETIO_QUEUE_REDIS} for Socket.IO and {RECOMMENDED_SHARED_STATE_REDIS} for shared state."))
    elif queue_is_redis and shared_is_redis:
        items.append(RedisSocketIOItem("pass", "redis-shared-state-separation", "Socket.IO and shared-state Redis DBs are separate", f"socketio={_redact_url(queue)}; shared={_redact_url(shared_url)}"))

    db_pool_max = _int_setting(settings, "db_pool_max", default=50)
    db_pool_wait_seconds = _int_setting(settings, "db_pool_wait_seconds", default=10)
    production_threads = _int_setting(settings, "production_threads", default=_int_setting(settings, "gunicorn_threads", default=100))
    planned_db_connections = instances * max(1, db_pool_max)
    if instances > 1 and planned_db_connections > 80:
        items.append(RedisSocketIOItem("warn", "db-pool-scale", "Planned DB pool can exceed a typical local PostgreSQL limit", f"production_instance_count={instances}; db_pool_max={db_pool_max}; possible web connections={planned_db_connections}", "Lower db_pool_max per instance, raise PostgreSQL max_connections, or add PgBouncer before scaling high."))
    elif instances > 1:
        items.append(RedisSocketIOItem("pass", "db-pool-scale", "Planned DB pool scale looks bounded", f"production_instance_count={instances}; db_pool_max={db_pool_max}; possible web connections={planned_db_connections}"))

    if instances > 1 and production_threads > db_pool_max and db_pool_wait_seconds <= 0:
        items.append(RedisSocketIOItem("fail", "db-pool-burst-wait", "Scaled DB pool has no burst wait", f"production_threads={production_threads}; db_pool_max={db_pool_max}; db_pool_wait_seconds={db_pool_wait_seconds}", "Set db_pool_wait_seconds=10 or higher so short browser/Socket.IO bursts queue instead of failing immediately."))
    elif instances > 1 and production_threads > db_pool_max:
        items.append(RedisSocketIOItem("pass", "db-pool-burst-wait", "Scaled DB pool burst queue is enabled", f"production_threads={production_threads}; db_pool_max={db_pool_max}; db_pool_wait_seconds={db_pool_wait_seconds}"))

    if async_mode == "threading" and worker_class != "gthread":
        items.append(RedisSocketIOItem("warn", "async-worker-alignment", "Threading async mode does not match worker class", f"async_mode={async_mode}; worker_class={worker_class}", "Use production_async_mode=threading and production_worker_class=gthread for the default path."))
    elif async_mode == "eventlet" and worker_class != "eventlet":
        items.append(RedisSocketIOItem("warn", "async-worker-alignment", "Eventlet async mode does not match worker class", f"async_mode={async_mode}; worker_class={worker_class}", "Use worker_class=eventlet only after installing requirements-eventlet.txt."))
    else:
        items.append(RedisSocketIOItem("pass", "async-worker-alignment", "Socket.IO async mode and Gunicorn worker align", f"async_mode={async_mode}; worker_class={worker_class}"))

    if async_mode == "threading" and transports == ["polling"]:
        items.append(RedisSocketIOItem("pass", "socketio-transports", "Threaded production transport is stable polling", "This avoids WebSocket-first reconnect loops in the default gthread setup."))
    elif async_mode == "threading" and "websocket" in transports:
        items.append(RedisSocketIOItem("warn", "socketio-transports", "Threaded mode has WebSocket enabled", f"transports={transports}", "This can work with simple-websocket, but use polling during early public beta unless you have tested WebSocket through your reverse proxy."))
    else:
        items.append(RedisSocketIOItem("pass", "socketio-transports", "Socket.IO transports reviewed", f"transports={transports}"))

    if live_check:
        live_timeout = max(
            timing_float(settings, "redis_connect_timeout_seconds"),
            timing_float(settings, "redis_socket_timeout_seconds"),
        )
        for url in _unique_redis_urls(rate_url, queue, shared_url):
            ok, msg = _ping_redis_url(url, timeout=live_timeout)
            items.append(RedisSocketIOItem("pass" if ok else "fail", "redis-live-ping", "Redis live ping succeeded" if ok else "Redis live ping failed", msg, redis_install_hint() + "; also confirm redis>=5.0 is installed in the Python venv." if not ok else ""))
        if not _unique_redis_urls(rate_url, queue, shared_url):
            items.append(RedisSocketIOItem("warn", "redis-live-ping", "No Redis URL available for live ping", "Configure rate_limit_storage_uri or socketio_message_queue first."))

    fail_count = sum(1 for item in items if item.level == "fail")
    warn_count = sum(1 for item in items if item.level == "warn")
    pass_count = sum(1 for item in items if item.level == "pass")
    overall = "fail" if fail_count else "warn" if warn_count else "pass"
    return {
        "overall": overall,
        "mode": mode,
        "production_workers": workers,
        "production_instances": instances,
        "worker_class": worker_class,
        "async_mode": async_mode,
        "socketio_transports": transports,
        "socketio_message_queue": _redact_url(queue),
        "rate_limit_storage_uri": _redact_url(rate_url),
        "shared_state_redis_url": _redact_url(shared_url),
        "live_check": bool(live_check),
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "items": [item.__dict__ for item in items],
    }


def blocking_topology_errors(settings: dict[str, Any]) -> list[str]:
    """Errors that should block production startup before Gunicorn exec."""
    # For scaled deployments, perform a live Redis ping before Gunicorn exec so
    # admins get a clear install/start Redis fix instead of a later worker crash.
    _settings = dict(settings or {})
    apply_scaled_runtime_safety_defaults(_settings)
    _scaled = _production_workers(_settings) > 1 or _production_instances(_settings) > 1
    report = build_redis_socketio_report(_settings, live_check=_scaled)
    blocking_codes = {"production-workers", "socketio-message-queue", "scaled-instance-queue", "shared-state-redis", "redis-python-package", "redis-live-ping"}
    out: list[str] = []
    for item in report.get("items") or []:
        if item.get("level") == "fail" and item.get("code") in blocking_codes:
            text = str(item.get("title") or item.get("code"))
            detail = str(item.get("detail") or "").strip()
            fix = str(item.get("fix") or "").strip()
            if detail:
                text += f" — {detail}"
            if fix:
                text += f" Fix: {fix}"
            out.append(text)
    return out


def format_redis_socketio_report(report: dict[str, Any]) -> str:
    marker = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    lines = [
        "Hui Chat Redis + Socket.IO Production Checker",
        "",
        f"Overall: {str(report.get('overall') or 'unknown').upper()}",
        f"Mode: {report.get('mode') or 'unknown'}",
        f"Workers per instance: {report.get('production_workers')}",
        f"Planned instances: {report.get('production_instances')}",
        f"Worker class: {report.get('worker_class')}",
        f"Socket.IO async: {report.get('async_mode')}",
        f"Socket.IO transports: {', '.join(report.get('socketio_transports') or [])}",
        f"Rate-limit storage: {report.get('rate_limit_storage_uri') or '(not set)'}",
        f"Socket.IO queue: {report.get('socketio_message_queue') or '(not set)'}",
        f"Shared-state Redis: {report.get('shared_state_redis_url') or '(not set)'}",
        "",
        f"Summary: {report.get('pass_count', 0)} pass, {report.get('warn_count', 0)} warn, {report.get('fail_count', 0)} fail",
        "",
    ]
    for item in report.get("items") or []:
        level = str(item.get("level") or "warn")
        lines.append(f"{marker.get(level, 'CHECK')}  {item.get('title') or item.get('code')}")
        detail = str(item.get("detail") or "").strip()
        if detail:
            lines.append(f"      {detail}")
        fix = str(item.get("fix") or "").strip()
        if fix:
            lines.append(f"      Fix: {fix}")
    lines.extend([
        "",
        "Beginner-safe rule:",
        "  Use production_workers=1 for each Gunicorn instance.",
        "  Use production_instance_count=1-10 to plan separate one-worker instances.",
        f"  Use {RECOMMENDED_RATE_LIMIT_REDIS} for rate limits when public.",
        f"  Use {RECOMMENDED_SOCKETIO_QUEUE_REDIS} for Socket.IO before scaling.",
        f"  Use {RECOMMENDED_SHARED_STATE_REDIS} for shared realtime state before scaling.",
        "  Setup auto-fills those Redis URLs when production_instance_count is greater than 1.",
        "  Scale later with multiple one-worker Hui Chat instances behind sticky routing.",
    ])
    return "\n".join(lines).rstrip() + "\n"
