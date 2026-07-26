"""Beginner-safe Redis defaults for scaled Hui Chat deployments.

Hui Chat scales Socket.IO as multiple one-worker instances. That requires
three distinct shared Redis roles when the admin selects more than one planned
instance:

* DB 0: HTTP + custom rate limiting
* DB 1: Socket.IO message queue / broadcasts
* DB 2: realtime shared state such as presence and live room state

This helper deliberately does not try to install Redis. It only fills safe local
URLs so setup and runtime do not force admins to hand-remember the DB split.
"""

from __future__ import annotations

from typing import Any

RECOMMENDED_RATE_LIMIT_REDIS = "redis://127.0.0.1:6379/0"
RECOMMENDED_SOCKETIO_QUEUE_REDIS = "redis://127.0.0.1:6379/1"
RECOMMENDED_SHARED_STATE_REDIS = "redis://127.0.0.1:6379/2"


def _truthy(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on", "auto"}:
        return True
    if raw in {"0", "false", "no", "n", "off", "manual", "disabled"}:
        return False
    return default


def _positive_int(value: Any, default: int = 1, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        out = int(value)
    except Exception:
        out = default
    if out < minimum:
        out = minimum
    if maximum is not None and out > maximum:
        out = maximum
    return out


def _production_workers(settings: dict[str, Any]) -> int:
    return _positive_int(
        settings.get("production_workers")
        or settings.get("worker_count")
        or settings.get("web_workers")
        or 1,
        1,
        minimum=1,
    )


def _production_instances(settings: dict[str, Any]) -> int:
    return _positive_int(
        settings.get("production_instance_count")
        or settings.get("production_instances")
        or settings.get("instance_count")
        or 1,
        1,
        minimum=1,
        maximum=10,
    )


def scaled_realtime_requested(settings: dict[str, Any]) -> bool:
    return _production_workers(settings) > 1 or _production_instances(settings) > 1


def auto_scaled_redis_enabled(settings: dict[str, Any]) -> bool:
    # Default to enabled because this is a beginner/admin convenience feature.
    return _truthy(settings.get("auto_configure_scaled_redis"), default=True)


def _missing_or_memory(value: Any) -> bool:
    raw = str(value or "").strip().lower()
    return raw in {"", "memory", "memory://", "none", "null", "false"}


def _redis_url(value: Any) -> bool:
    raw = str(value or "").strip().lower()
    return raw.startswith("redis://") or raw.startswith("rediss://")


def apply_scaled_redis_defaults(settings: dict[str, Any], *, annotate: bool = True) -> dict[str, bool]:
    """Mutate settings with safe local Redis URLs when scaled mode is selected.

    Returns a map describing which fields were filled. Explicit custom values are
    preserved unless they are blank/memory placeholders.
    """
    changed = {
        "rate_limit_storage_uri": False,
        "simple_rate_limit_storage_uri": False,
        "socketio_message_queue": False,
        "shared_state_redis_url": False,
        "db_pool_max": False,
        "db_pool_wait_seconds": False,
    }
    if not isinstance(settings, dict):
        return changed
    if not scaled_realtime_requested(settings):
        settings.setdefault("auto_configure_scaled_redis", True)
        return changed
    if not auto_scaled_redis_enabled(settings):
        return changed

    if _missing_or_memory(settings.get("rate_limit_storage_uri") or settings.get("rate_limit_storage")):
        settings["rate_limit_storage_uri"] = RECOMMENDED_RATE_LIMIT_REDIS
        settings["rate_limit_storage"] = RECOMMENDED_RATE_LIMIT_REDIS
        changed["rate_limit_storage_uri"] = True
    else:
        # Keep aliases synced for existing explicit values.
        value = str(settings.get("rate_limit_storage_uri") or settings.get("rate_limit_storage") or "").strip()
        if value:
            settings["rate_limit_storage_uri"] = value
            settings["rate_limit_storage"] = value

    if _missing_or_memory(settings.get("simple_rate_limit_storage_uri")):
        settings["simple_rate_limit_storage_uri"] = str(settings.get("rate_limit_storage_uri") or RECOMMENDED_RATE_LIMIT_REDIS)
        changed["simple_rate_limit_storage_uri"] = True

    if _missing_or_memory(settings.get("socketio_message_queue")):
        settings["socketio_message_queue"] = RECOMMENDED_SOCKETIO_QUEUE_REDIS
        changed["socketio_message_queue"] = True

    if _missing_or_memory(settings.get("shared_state_redis_url")):
        settings["shared_state_redis_url"] = RECOMMENDED_SHARED_STATE_REDIS
        changed["shared_state_redis_url"] = True

    instances = _production_instances(settings)
    if instances > 1:
        # Keep direct PostgreSQL connections bounded across the cluster, then
        # queue short UI/Socket.IO bursts instead of failing immediately.
        safe_pool_max = max(5, min(50, 80 // instances))
        current_pool = _positive_int(settings.get("db_pool_max") or 50, 50, minimum=1)
        if current_pool > safe_pool_max:
            settings["db_pool_max"] = safe_pool_max
            settings.setdefault("db_pool_min", 1)
            changed["db_pool_max"] = True
        try:
            current_wait = float(settings.get("db_pool_wait_seconds", 0) or 0)
        except Exception:
            current_wait = 0.0
        if current_wait < 10.0:
            settings["db_pool_wait_seconds"] = 10
            changed["db_pool_wait_seconds"] = True

    settings["auto_configure_scaled_redis"] = True
    if annotate and any(changed.values()):
        settings["scaled_redis_auto_configured"] = True
        settings["scaled_redis_layout"] = {
            "rate_limit_storage_uri": str(settings.get("rate_limit_storage_uri") or ""),
            "socketio_message_queue": str(settings.get("socketio_message_queue") or ""),
            "shared_state_redis_url": str(settings.get("shared_state_redis_url") or ""),
        }
    return changed


def apply_scaled_runtime_safety_defaults(settings: dict[str, Any], *, annotate: bool = True) -> dict[str, bool]:
    """Apply all beginner-safe scaled defaults.

    Currently this fills Redis role URLs and caps the per-instance DB pool to a
    beginner-safe value so common local PostgreSQL installs are not overwhelmed.
    """
    return apply_scaled_redis_defaults(settings, annotate=annotate)


def redis_install_hint() -> str:
    return "Install/start Valkey or Redis first; on Arch use: sudo pacman -S valkey && sudo systemctl enable --now valkey"


def scaled_redis_summary_lines(settings: dict[str, Any], changed: dict[str, bool] | None = None) -> list[str]:
    if not scaled_realtime_requested(settings):
        return []
    changed = changed or {}
    prefix = "auto-filled" if any(changed.values()) else "configured"
    return [
        f"Scaled Redis defaults {prefix} for { _production_instances(settings) } planned instance(s):",
        f"  rate limits:  {settings.get('rate_limit_storage_uri') or RECOMMENDED_RATE_LIMIT_REDIS}",
        f"  Socket.IO:    {settings.get('socketio_message_queue') or RECOMMENDED_SOCKETIO_QUEUE_REDIS}",
        f"  shared state: {settings.get('shared_state_redis_url') or RECOMMENDED_SHARED_STATE_REDIS}",
        f"  DB pool max: {settings.get('db_pool_max') or 'default'} per instance",
        f"  DB pool burst wait: {settings.get('db_pool_wait_seconds') or 10}s",
        "Redis must still be installed and running on the server.",
    ]
