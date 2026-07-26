#!/usr/bin/env python3
"""Regression doctor for Hui Chat's automatic production conflict guard."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from production_config_guard import (  # noqa: E402
    apply_safe_production_fixes,
    blocking_production_conflicts,
    build_production_config_report,
)
from performance_tuning import recommended_performance_settings  # noqa: E402


def _codes(report: dict, level: str) -> set[str]:
    return {str(item.get("code")) for item in report.get("items") or [] if item.get("level") == level}


def main() -> int:
    failures: list[str] = []
    bad = {
        "run_mode": "production",
        "production_mode": True,
        "debug": True,
        "server_debug": True,
        "production_workers": 4,
        "production_instance_count": 10,
        "production_instance_base_port": 5000,
        "production_instance_port_step": 1,
        "production_async_mode": "threading",
        "production_worker_class": "eventlet",
        "production_threads": 400,
        "db_pool_min": 60,
        "db_pool_max": 50,
        "db_pool_wait_seconds": 0,
        "rate_limit_storage_uri": "memory://",
        "simple_rate_limit_storage_uri": "memory://",
        "socketio_message_queue": "",
        "shared_state_redis_url": "",
        "hosting_mode": "public_beta",
        "cookie_secure": False,
        "auto_allow_lan_origins": True,
        "allowed_origins": ["*"],
        "cors_allowed_origins": ["*"],
        "trust_proxy_headers": True,
        "proxy_fix_hops": 0,
        "production_instance_bind_host": "0.0.0.0",
        "forwarded_allow_ips": "*",
        "enable_health_check_endpoint": False,
        "production_loglevel": "debug",
        "log_level": "DEBUG",
        "janitor_debug_custom_rooms": True,
        "auto_tune_production_capacity": True,
    }
    report = build_production_config_report(
        bad,
        live_check=False,
        environ={"HUI_WORKERS": "4", "FLASK_DEBUG": "1", "HUI_FORWARDED_ALLOW_IPS": "*"},
    )
    expected_failures = {
        "debug-mode",
        "worker-count",
        "async-worker",
        "db-pool-order",
        "db-pool-total",
        "db-pool-wait",
        "redis-queue",
        "redis-rate",
        "redis-shared",
        "proxy-hops",
        "forwarded-allow-ips",
        "wildcard-origin",
        "secure-cookie",
        "lan-origin-expansion",
        "environment-overrides",
    }
    missing = expected_failures - _codes(report, "fail")
    if missing:
        failures.append(f"bad topology did not report expected failures: {sorted(missing)}")

    fixed = dict(bad)
    changes = apply_safe_production_fixes(fixed)
    changed_keys = {str(item.get("key")) for item in changes}
    for key in {
        "debug",
        "server_debug",
        "production_workers",
        "production_worker_class",
        "db_pool_max",
        "db_pool_min",
        "db_pool_wait_seconds",
        "enable_health_check_endpoint",
        "production_loglevel",
        "log_level",
        "janitor_debug_custom_rooms",
        "auto_allow_lan_origins",
        "proxy_fix_hops",
        "production_instance_bind_host",
        "forwarded_allow_ips",
        "socketio_message_queue",
        "rate_limit_storage_uri",
        "shared_state_redis_url",
    }:
        if key not in changed_keys:
            failures.append(f"safe fixer did not change expected key: {key}")

    # User-intent settings remain blocking rather than silently rewritten.
    remaining = build_production_config_report(fixed, live_check=False, environ={})
    remaining_failures = _codes(remaining, "fail")
    if "wildcard-origin" not in remaining_failures or "secure-cookie" not in remaining_failures:
        failures.append("unsafe public URL/cookie/origin settings should remain explicit blocking failures")

    safe = {
        "run_mode": "production",
        "production_mode": True,
        "debug": False,
        "server_debug": False,
        "production_workers": 1,
        "production_instance_count": 1,
        "production_instance_base_port": 5500,
        "production_instance_port_step": 1,
        "production_async_mode": "threading",
        "production_worker_class": "gthread",
        "production_threads": 20,
        "db_pool_min": 1,
        "db_pool_max": 20,
        "db_pool_wait_seconds": 10,
        "hosting_mode": "lan",
        "trust_proxy_headers": False,
        "forwarded_allow_ips": "127.0.0.1",
        "enable_health_check_endpoint": True,
        "production_loglevel": "info",
        "log_level": "INFO",
        "janitor_debug_custom_rooms": False,
        "max_request_bytes": 30 * 1024 * 1024,
    }
    blocking = blocking_production_conflicts(safe, live_check=False)
    if blocking:
        failures.append(f"known-safe single-instance production config was blocked: {blocking}")

    performance = recommended_performance_settings(10)
    capacity = report.get("capacity") or {}
    if int(performance.get("production_threads") or 0) > int(capacity.get("recommended_threads") or 0):
        failures.append("interactive setup performance tuner exceeds production guard thread capacity")
    effective_instances = max(1, min(10, int(capacity.get("recommended_instances") or 1)))
    expected_db_cap = max(10, min(50, 80 // effective_instances, int(capacity.get("recommended_threads") or 20) // 2))
    if int(performance.get("db_pool_max") or 0) > expected_db_cap:
        failures.append("interactive setup performance tuner exceeds production guard DB capacity")

    guard_text = (ROOT / "production_config_guard.py").read_text(encoding="utf-8")
    for token in ("cpu.max", "cpuset.cpus.effective", "memory.max", "os.sched_getaffinity"):
        if token not in guard_text:
            failures.append(f"capacity detector missing cgroup/affinity signal: {token}")

    main_text = (ROOT / "main.py").read_text(encoding="utf-8")
    setup_text = (ROOT / "interactive_setup.py").read_text(encoding="utf-8")
    for token in ("--production-config-check", "--production-config-fix", "blocking_production_conflicts"):
        if token not in main_text:
            failures.append(f"main.py missing production guard integration token: {token}")
    for token in ("apply_safe_production_fixes", "build_production_config_report", "Setup will not finish until the FAIL items are corrected"):
        if token not in setup_text:
            failures.append(f"interactive_setup.py missing automatic guard integration token: {token}")

    if failures:
        print("❌ Production configuration guard doctor failed")
        for failure in failures:
            print(f"   - {failure}")
        return 1

    server_init = (ROOT / "server_init.py").read_text(encoding="utf-8")
    preflight = (ROOT / "preflight.py").read_text(encoding="utf-8")
    if "blocking_production_conflicts(settings, live_check=False)" not in server_init:
        failures.append("direct WSGI/create_app startup does not enforce production conflicts")
    if "apply_scaled_runtime_safety_defaults(settings)" in server_init:
        failures.append("create_app still silently rewrites scaled topology")
    if "apply_scaled_runtime_safety_defaults(settings)" in preflight:
        failures.append("preflight still silently rewrites scaled topology")
    if "forcing to 50" in server_init or "cfg_max < 50" in server_init:
        failures.append("runtime still overrides an intentionally smaller single-instance DB pool")
    if "db-pool-host-capacity" not in guard_text:
        failures.append("production guard does not compare DB pool against detected host capacity")

    if failures:
        print("❌ Production configuration guard doctor failed")
        for failure in failures:
            print(" -", failure)
        return 1

    print("✅ Production configuration guard doctor passed")
    print("   checks: worker/Socket.IO conflicts, Redis scale, DB pool pressure, host capacity, proxy/header safety, public origin/cookie blockers, environment overrides, setup auto-fix integration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
