"""Conservative, host-aware production-capacity defaults for Hui Chat.

The interactive setup uses these recommendations before the final production
configuration guard runs.  Both paths intentionally share the same cgroup- and
affinity-aware capacity detector so setup never presents a larger topology than
the runtime will accept.
"""
from __future__ import annotations
from typing import Any


def _positive_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 10000) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def recommended_performance_settings(instance_count: Any) -> dict[str, int]:
    # Import lazily to keep this lightweight module usable by setup without
    # creating an import cycle during application startup.
    from production_config_guard import detect_host_capacity

    requested_instances = _positive_int(instance_count, 1, maximum=10)
    capacity = detect_host_capacity()
    effective_instances = min(requested_instances, int(capacity.get("recommended_instances") or 1))
    threads = int(capacity.get("recommended_threads") or 20)
    db_max = max(10, min(50, 80 // max(1, effective_instances), threads // 2))
    return {
        "production_threads": threads,
        "db_pool_min": min(2, db_max),
        "db_pool_max": db_max,
        "db_pool_wait_seconds": 10,
        "production_worker_connections": max(200, threads * 4),
    }


def apply_performance_safety_defaults(settings: dict[str, Any], *, force: bool = False) -> dict[str, tuple[Any, Any]]:
    if not bool(settings.get("auto_tune_performance", True)):
        return {}
    recommended = recommended_performance_settings(settings.get("production_instance_count") or 1)
    changed: dict[str, tuple[Any, Any]] = {}
    for key, value in recommended.items():
        old = settings.get(key)
        if force or old is None or str(old).strip() == "":
            if old != value:
                settings[key] = value
                changed[key] = (old, value)
    return changed


def performance_summary_lines(settings: dict[str, Any]) -> list[str]:
    instances = int(settings.get("production_instance_count") or 1)
    db_max = int(settings.get("db_pool_max") or 10)
    return [
        f"Instances requested: {instances} (one Gunicorn worker each)",
        f"Capacity-aware threads per instance: {int(settings.get('production_threads') or 20)}",
        f"PostgreSQL pool per instance: {int(settings.get('db_pool_min') or 1)}-{db_max}",
        f"DB burst wait: {int(settings.get('db_pool_wait_seconds') or 10)} seconds",
        f"Potential requested web DB connections: {instances * db_max}",
        "The final production guard may reduce the instance count to this host's CPU/memory limit.",
    ]
