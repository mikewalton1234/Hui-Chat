"""Production configuration conflict detection and safe auto-tuning for Hui Chat.

The setup wizard and production launcher both use this module.  The goal is to
catch combinations that are individually valid settings but become unsafe,
broken, or unnecessarily slow when combined in production.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import socket
from typing import Any, Iterable
from urllib.parse import urlparse

from public_beta_readiness import infer_hosting_mode
from scaled_redis_autoconfig import apply_scaled_runtime_safety_defaults
from runtime_timing import apply_runtime_timing_safety_defaults, build_runtime_timing_report


@dataclass(frozen=True)
class ProductionConfigItem:
    level: str
    code: str
    title: str
    detail: str = ""
    fix: str = ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "production", "prod"}


def _int(value: Any, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        out = int(str(value).strip())
    except Exception:
        out = int(default)
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def _first_configured(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return value
    return default


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _is_loopback_host(host: str) -> bool:
    raw = str(host or "").strip().lower().strip("[]")
    return raw in {"localhost", "127.0.0.1", "::1"}


def _is_redis_url(value: Any) -> bool:
    return str(value or "").strip().lower().startswith(("redis://", "rediss://", "unix://"))


def _redis_identity(value: Any) -> tuple[str, str, int | None, str] | None:
    raw = str(value or "").strip()
    if not _is_redis_url(raw):
        return None
    try:
        parsed = urlparse(raw)
        return (
            parsed.scheme.lower(),
            (parsed.hostname or "").lower(),
            parsed.port,
            (parsed.path or "/0").rstrip("/") or "/0",
        )
    except Exception:
        return None


def _read_positive_int(path: str | Path) -> int | None:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
        if not raw or raw.lower() == "max":
            return None
        value = int(raw)
        return value if value > 0 else None
    except Exception:
        return None


def _physical_memory_total_mb() -> int | None:
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                return max(1, int(line.split()[1]) // 1024)
    except Exception:
        pass
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return max(1, int(pages * page_size) // (1024 * 1024))
    except Exception:
        return None


def _cgroup_memory_limit_mb() -> int | None:
    candidates = (
        "/sys/fs/cgroup/memory.max",  # cgroup v2
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",  # cgroup v1
    )
    limits = [_read_positive_int(path) for path in candidates]
    plausible = [value for value in limits if value and value < (1 << 60)]
    return max(1, min(plausible) // (1024 * 1024)) if plausible else None


def _cpuset_count(value: str) -> int | None:
    count = 0
    try:
        for part in str(value or "").strip().split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                first, last = part.split("-", 1)
                count += max(0, int(last) - int(first) + 1)
            else:
                int(part)
                count += 1
    except Exception:
        return None
    return count or None


def _cgroup_cpuset_limit() -> int | None:
    for path in (
        "/sys/fs/cgroup/cpuset.cpus.effective",  # cgroup v2
        "/sys/fs/cgroup/cpuset/cpuset.cpus",  # cgroup v1
    ):
        try:
            count = _cpuset_count(Path(path).read_text(encoding="utf-8"))
            if count:
                return count
        except Exception:
            continue
    return None


def _cgroup_cpu_quota_limit() -> int | None:
    try:
        raw = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="utf-8").strip().split()
        if len(raw) >= 2 and raw[0] != "max":
            quota, period = int(raw[0]), int(raw[1])
            if quota > 0 and period > 0:
                return max(1, quota // period)
    except Exception:
        pass
    quota = _read_positive_int("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    period = _read_positive_int("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if quota and period:
        return max(1, quota // period)
    return None


def detect_host_capacity() -> dict[str, int | None]:
    reported_cpus = max(1, int(os.cpu_count() or 1))
    try:
        affinity_cpus = max(1, len(os.sched_getaffinity(0)))
    except Exception:
        affinity_cpus = None
    cpuset_cpus = _cgroup_cpuset_limit()
    quota_cpus = _cgroup_cpu_quota_limit()
    cpu_candidates = [reported_cpus] + [value for value in (affinity_cpus, cpuset_cpus, quota_cpus) if value]
    cpu_count = max(1, min(cpu_candidates))

    physical_memory_mb = _physical_memory_total_mb()
    cgroup_memory_mb = _cgroup_memory_limit_mb()
    memory_candidates = [value for value in (physical_memory_mb, cgroup_memory_mb) if value]
    memory_mb = min(memory_candidates) if memory_candidates else None

    # One instance is intentionally one Gunicorn worker. Reserve memory for
    # PostgreSQL, Valkey/Redis, the OS, reverse proxy, and the janitor process.
    cpu_limit = max(1, min(10, cpu_count))
    if memory_mb is None:
        memory_limit = 10
    else:
        usable_mb = max(512, memory_mb - 1024)
        memory_limit = max(1, min(10, usable_mb // 512))
    recommended_instances = max(1, min(cpu_limit, memory_limit))

    recommended_threads = max(20, min(100, cpu_count * 8))
    if memory_mb is not None and memory_mb < 2048:
        recommended_threads = min(recommended_threads, 32)
    elif memory_mb is not None and memory_mb < 4096:
        recommended_threads = min(recommended_threads, 64)

    return {
        "cpu_count": cpu_count,
        "cpu_reported_count": reported_cpus,
        "cpu_affinity_count": affinity_cpus,
        "cpu_cpuset_count": cpuset_cpus,
        "cpu_quota_count": quota_cpus,
        "memory_mb": memory_mb,
        "memory_physical_mb": physical_memory_mb,
        "memory_cgroup_mb": cgroup_memory_mb,
        "recommended_instances": recommended_instances,
        "recommended_threads": recommended_threads,
    }


def _run_mode(settings: dict[str, Any]) -> str:
    raw = str(settings.get("run_mode") or ("production" if _truthy(settings.get("production_mode")) else "development"))
    return "production" if raw.strip().lower().replace("_", "-") in {"production", "prod", "public", "public-beta"} else "development"


def _worker_class(settings: dict[str, Any], env: dict[str, str]) -> str:
    value = (
        env.get("HUI_GUNICORN_WORKER_CLASS")
        or settings.get("production_worker_class")
        or settings.get("gunicorn_worker_class")
        or "gthread"
    )
    value = str(value).strip().lower()
    return "gthread" if value == "threading" else value


def _async_mode(settings: dict[str, Any], env: dict[str, str]) -> str:
    return str(env.get("HUI_SOCKETIO_ASYNC") or settings.get("production_async_mode") or "threading").strip().lower()


def _workers(settings: dict[str, Any], env: dict[str, str]) -> int:
    return _int(
        env.get("HUI_WORKERS")
        or env.get("HUI_PRODUCTION_WORKERS")
        or settings.get("production_workers")
        or 1,
        1,
        1,
    )


def _instances(settings: dict[str, Any], env: dict[str, str]) -> int:
    return _int(
        env.get("HUI_PRODUCTION_INSTANCES")
        or settings.get("production_instance_count")
        or 1,
        1,
        1,
        10,
    )


def _planned_ports(settings: dict[str, Any], env: dict[str, str]) -> list[int]:
    count = _instances(settings, env)
    base = _int(
        _first_configured(
            env.get("HUI_INSTANCE_BASE_PORT"),
            settings.get("production_instance_base_port"),
            settings.get("server_port"),
            settings.get("port"),
            default=5000,
        ),
        5000,
    )
    step = _int(_first_configured(settings.get("production_instance_port_step"), default=1), 1)
    return [base + index * step for index in range(count)]


def _effective_live_bind(settings: dict[str, Any], env: dict[str, str], planned_ports: list[int]) -> tuple[str, list[int]]:
    raw = str(env.get("HUI_BIND") or env.get("HUI_PRODUCTION_BIND") or "").strip()
    if raw:
        try:
            if raw.startswith("[") and "]:" in raw:
                host, port_text = raw[1:].rsplit("]:", 1)
            else:
                host, port_text = raw.rsplit(":", 1)
            return host.strip() or "127.0.0.1", [_int(port_text, planned_ports[0] if planned_ports else 5000, 1, 65535)]
        except Exception:
            pass
    host = str(settings.get("production_instance_bind_host") or "127.0.0.1").strip() or "127.0.0.1"
    return host, list(planned_ports)


def _port_available(host: str, port: int) -> tuple[bool, str]:
    target = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    family = socket.AF_INET6 if ":" in target else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.35)
            result = sock.connect_ex((target, port))
        if result == 0:
            return False, f"{target}:{port} is already accepting connections"
        return True, f"{target}:{port} appears available"
    except Exception as exc:
        return True, f"could not conclusively probe {target}:{port}: {exc}"


def _append_unique(items: list[ProductionConfigItem], item: ProductionConfigItem) -> None:
    if not any(existing.code == item.code and existing.detail == item.detail for existing in items):
        items.append(item)


def build_production_config_report(
    settings: dict[str, Any],
    *,
    live_check: bool = False,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    cfg = dict(settings or {})
    items: list[ProductionConfigItem] = []
    timing_report = build_runtime_timing_report(cfg)
    for timing_item in timing_report.get("items") or []:
        items.append(ProductionConfigItem(
            str(timing_item.get("level") or "warn"),
            str(timing_item.get("code") or "runtime-timing"),
            "Runtime timing policy",
            str(timing_item.get("detail") or ""),
            str(timing_item.get("fix") or ""),
        ))
    capacity = detect_host_capacity()
    mode = _run_mode(cfg)
    hosting_mode = infer_hosting_mode(cfg)
    workers = _workers(cfg, env)
    instances = _instances(cfg, env)
    threads = _int(env.get("HUI_GUNICORN_THREADS") or cfg.get("production_threads") or cfg.get("gunicorn_threads") or 100, 100, 1, 500)
    worker_class = _worker_class(cfg, env)
    async_mode = _async_mode(cfg, env)
    ports = _planned_ports(cfg, env)

    if mode != "production":
        items.append(ProductionConfigItem("warn", "run-mode", "Production mode is not selected", f"run_mode={mode}", "Select run_mode=production before using the production service."))
    else:
        items.append(ProductionConfigItem("pass", "run-mode", "Production mode selected", "run_mode=production"))

    if _truthy(cfg.get("debug")) or _truthy(cfg.get("server_debug")) or _truthy(env.get("FLASK_DEBUG")):
        items.append(ProductionConfigItem("fail", "debug-mode", "Debug mode conflicts with production", "debug/server_debug/FLASK_DEBUG is enabled", "Disable all debug and Flask reloader settings."))
    else:
        items.append(ProductionConfigItem("pass", "debug-mode", "Debug mode is disabled"))

    if workers != 1:
        items.append(ProductionConfigItem("fail", "worker-count", "Multiple Gunicorn workers conflict with Socket.IO", f"effective workers per instance={workers}", "Use exactly one Gunicorn worker per Hui Chat instance; scale with separate instances and Redis."))
    else:
        items.append(ProductionConfigItem("pass", "worker-count", "One Gunicorn worker per instance", "workers=1"))

    expected_worker = "eventlet" if async_mode == "eventlet" else "gthread"
    if worker_class != expected_worker:
        items.append(ProductionConfigItem("fail", "async-worker", "Socket.IO async mode and Gunicorn worker conflict", f"async_mode={async_mode}; worker_class={worker_class}", f"Use worker_class={expected_worker}."))
    else:
        items.append(ProductionConfigItem("pass", "async-worker", "Socket.IO async mode and worker class align", f"async_mode={async_mode}; worker_class={worker_class}"))

    recommended_instances = int(capacity["recommended_instances"] or 1)
    if instances > recommended_instances:
        level = "fail" if instances > max(2, recommended_instances * 2) else "warn"
        items.append(ProductionConfigItem(level, "instance-capacity", "Configured instance count exceeds detected host capacity", f"instances={instances}; detected CPUs={capacity['cpu_count']}; memory={capacity['memory_mb'] or 'unknown'} MB; recommended maximum={recommended_instances}", "Reduce production_instance_count or deploy on a larger host."))
    else:
        items.append(ProductionConfigItem("pass", "instance-capacity", "Instance count fits detected host capacity", f"instances={instances}; recommended maximum={recommended_instances}"))

    recommended_threads = int(capacity["recommended_threads"] or 32)
    if threads > max(128, recommended_threads * 2):
        items.append(ProductionConfigItem("fail", "thread-capacity", "Thread count can create heavy scheduling and database contention", f"threads={threads}; recommended={recommended_threads}", f"Use production_threads={recommended_threads} on this host."))
    elif threads > recommended_threads:
        items.append(ProductionConfigItem("warn", "thread-capacity", "Thread count is above the detected host recommendation", f"threads={threads}; recommended={recommended_threads}", f"Use production_threads={recommended_threads} unless load testing proves a higher value helps."))
    else:
        items.append(ProductionConfigItem("pass", "thread-capacity", "Thread count fits detected host capacity", f"threads={threads}; recommended maximum={recommended_threads}"))

    db_pool_min = _int(cfg.get("db_pool_min") or 1, 1, 1)
    db_pool_max = _int(cfg.get("db_pool_max") or 50, 50, 1)
    db_wait = _int(_first_configured(env.get("HUI_DB_POOL_WAIT_SECONDS"), cfg.get("db_pool_wait_seconds"), default=10), 10)
    total_pool = instances * db_pool_max
    host_db_cap = max(10, min(50, int(capacity["recommended_threads"] or 20) // 2, 80 // max(1, instances)))
    capacity["recommended_db_pool_per_instance"] = host_db_cap
    if db_pool_min > db_pool_max:
        items.append(ProductionConfigItem("fail", "db-pool-order", "Database pool minimum exceeds maximum", f"db_pool_min={db_pool_min}; db_pool_max={db_pool_max}", "Set db_pool_min less than or equal to db_pool_max."))
    if total_pool > 100:
        items.append(ProductionConfigItem("fail", "db-pool-total", "Planned database pools can exhaust PostgreSQL", f"instances={instances}; db_pool_max={db_pool_max}; possible connections={total_pool}", "Keep total application pool capacity at or below about 80 unless PostgreSQL/PgBouncer is explicitly sized higher."))
    elif total_pool > 80:
        items.append(ProductionConfigItem("warn", "db-pool-total", "Planned database pools are near a common PostgreSQL connection limit", f"possible connections={total_pool}", "Lower db_pool_max or verify PostgreSQL max_connections and reserved connections."))
    else:
        items.append(ProductionConfigItem("pass", "db-pool-total", "Database pool scale is bounded", f"possible connections={total_pool}"))
    if db_pool_max > max(20, host_db_cap * 2):
        items.append(ProductionConfigItem("fail", "db-pool-host-capacity", "Database pool is excessive for detected host capacity", f"db_pool_max={db_pool_max}; host recommendation={host_db_cap} per instance", f"Use db_pool_max={host_db_cap} and keep db_pool_wait_seconds at least 10."))
    elif db_pool_max > host_db_cap:
        items.append(ProductionConfigItem("warn", "db-pool-host-capacity", "Database pool is above the detected host recommendation", f"db_pool_max={db_pool_max}; host recommendation={host_db_cap} per instance", f"Use db_pool_max={host_db_cap} unless PostgreSQL/PgBouncer was sized explicitly."))
    else:
        items.append(ProductionConfigItem("pass", "db-pool-host-capacity", "Database pool fits detected host capacity", f"db_pool_max={db_pool_max}; recommended maximum={host_db_cap}"))
    if threads > db_pool_max and db_wait <= 0:
        items.append(ProductionConfigItem("fail", "db-pool-wait", "Request bursts will fail instead of waiting for a database connection", f"threads={threads}; db_pool_max={db_pool_max}; db_pool_wait_seconds={db_wait}", "Set db_pool_wait_seconds to at least 10."))
    else:
        items.append(ProductionConfigItem("pass", "db-pool-wait", "Database burst wait is enabled", f"db_pool_wait_seconds={db_wait}"))

    queue = env.get("HUI_SOCKETIO_MESSAGE_QUEUE") or str(cfg.get("socketio_message_queue") or "")
    rate = env.get("HUI_RATE_LIMIT_STORAGE_URI") or str(cfg.get("rate_limit_storage_uri") or cfg.get("rate_limit_storage") or "")
    simple_rate = env.get("HUI_SIMPLE_RATE_LIMIT_STORAGE_URI") or str(cfg.get("simple_rate_limit_storage_uri") or "")
    shared = env.get("HUI_SHARED_STATE_REDIS_URL") or str(cfg.get("shared_state_redis_url") or "")
    scaled = instances > 1 or workers > 1
    for code, label, value in (
        ("redis-queue", "Socket.IO queue", queue),
        ("redis-rate", "rate-limit storage", rate),
        ("redis-simple-rate", "simple rate-limit storage", simple_rate),
        ("redis-shared", "shared-state storage", shared),
    ):
        if scaled and not _is_redis_url(value):
            items.append(ProductionConfigItem("fail", code, f"Scaled production requires Redis-backed {label}", f"configured value={value or '(blank)'}", "Use separate Redis databases for rate limits, Socket.IO, and shared state."))
    identities = [(name, _redis_identity(value)) for name, value in (("rate limits", rate), ("Socket.IO", queue), ("shared state", shared)) if _redis_identity(value)]
    for index, (name_a, id_a) in enumerate(identities):
        for name_b, id_b in identities[index + 1 :]:
            if id_a == id_b:
                items.append(ProductionConfigItem("warn", "redis-db-collision", "Redis workloads share the same database", f"{name_a} and {name_b} resolve to the same Redis database", "Use /0 for rate limits, /1 for Socket.IO, and /2 for shared state."))

    port_step = _int(_first_configured(cfg.get("production_instance_port_step"), default=1), 1)
    if port_step < 1:
        items.append(ProductionConfigItem("fail", "port-step", "Production instance port step is invalid", f"production_instance_port_step={port_step}", "Use a positive step, normally 1."))
    if len(set(ports)) != len(ports) or any(port < 1 or port > 65535 for port in ports):
        items.append(ProductionConfigItem("fail", "port-range", "Production instance ports overlap or are invalid", f"planned ports={ports}", "Choose a valid base port and positive port step."))
    else:
        items.append(ProductionConfigItem("pass", "port-range", "Production instance ports are unique and valid", f"planned ports={ports[0]}-{ports[-1]}" if len(ports) > 1 else f"planned port={ports[0]}"))

    trust_proxy = _truthy(cfg.get("trust_proxy_headers"))
    proxy_hops = _int(_first_configured(cfg.get("proxy_fix_hops"), default=0), 0, 0)
    instance_host = str(cfg.get("production_instance_bind_host") or "127.0.0.1").strip()
    forwarded = str(env.get("HUI_FORWARDED_ALLOW_IPS") or cfg.get("forwarded_allow_ips") or "127.0.0.1").strip()
    if trust_proxy and proxy_hops < 1:
        items.append(ProductionConfigItem("fail", "proxy-hops", "Proxy headers are enabled without a trusted hop count", f"proxy_fix_hops={proxy_hops}", "Set proxy_fix_hops=1 for one local reverse proxy."))
    if trust_proxy and not _is_loopback_host(instance_host):
        items.append(ProductionConfigItem("warn", "proxy-bind", "Reverse-proxied instances are exposed beyond loopback", f"production_instance_bind_host={instance_host}", "Bind backend instances to 127.0.0.1 unless the proxy is on another trusted host."))
    if forwarded == "*":
        items.append(ProductionConfigItem("fail" if not _is_loopback_host(instance_host) else "warn", "forwarded-allow-ips", "Gunicorn trusts forwarded headers from every address", "HUI_FORWARDED_ALLOW_IPS=*", "Use 127.0.0.1 or the exact reverse-proxy address."))

    origins = _list(cfg.get("allowed_origins")) + _list(cfg.get("cors_allowed_origins"))
    if any(origin == "*" for origin in origins):
        items.append(ProductionConfigItem("fail", "wildcard-origin", "Wildcard origins conflict with authenticated production traffic", "allowed_origins/cors_allowed_origins contains *", "List exact HTTPS origins."))
    if hosting_mode == "public_beta":
        if not _truthy(cfg.get("cookie_secure")):
            items.append(ProductionConfigItem("fail", "secure-cookie", "Public production has insecure cookies", "cookie_secure=false", "Enable secure cookies and terminate HTTPS before inviting users."))
        if _truthy(cfg.get("auto_allow_lan_origins")):
            items.append(ProductionConfigItem("fail", "lan-origin-expansion", "Automatic LAN origins can broaden public CORS policy", "auto_allow_lan_origins=true", "Disable automatic LAN origins in public_beta mode."))
        public_url = str(cfg.get("public_base_url") or "").strip().lower()
        if public_url and not public_url.startswith("https://"):
            items.append(ProductionConfigItem("fail", "public-url", "Public base URL is not HTTPS", str(cfg.get("public_base_url") or ""), "Use the final https:// URL."))

    if str(cfg.get("production_loglevel") or "info").strip().lower() in {"debug", "trace"} or str(cfg.get("log_level") or "info").strip().lower() in {"debug", "trace"}:
        items.append(ProductionConfigItem("warn", "verbose-logging", "Verbose production logging can reduce throughput and grow logs quickly", f"production_loglevel={cfg.get('production_loglevel')}; log_level={cfg.get('log_level')}", "Use info normally and enable debug only temporarily."))
    if _truthy(cfg.get("janitor_debug_custom_rooms")):
        items.append(ProductionConfigItem("warn", "janitor-debug", "Janitor debug logging is enabled", "janitor_debug_custom_rooms=true", "Disable janitor debug logging in normal production."))
    if not _truthy(cfg.get("enable_health_check_endpoint")):
        items.append(ProductionConfigItem("warn", "health-endpoint", "Production health endpoint is disabled", "enable_health_check_endpoint=false", "Enable /health so systemd and the reverse proxy can detect failures quickly."))

    max_request = _int(cfg.get("max_request_bytes") or 31_457_280, 31_457_280, 1)
    if max_request > 100 * 1024 * 1024:
        items.append(ProductionConfigItem("warn", "request-size", "Very large request limit can cause memory and bandwidth pressure", f"max_request_bytes={max_request}", "Keep the limit below 100 MiB unless large uploads have been load-tested."))

    # Explicit environment variables override the saved JSON.  Surface the most
    # dangerous ones because admins often forget they remain in .env/systemd.
    env_dangers: list[str] = []
    if _int(env.get("HUI_WORKERS") or env.get("HUI_PRODUCTION_WORKERS") or 1, 1) != 1:
        env_dangers.append("HUI_WORKERS/HUI_PRODUCTION_WORKERS")
    if _truthy(env.get("FLASK_DEBUG")):
        env_dangers.append("FLASK_DEBUG")
    if str(env.get("HUI_FORWARDED_ALLOW_IPS") or "").strip() == "*":
        env_dangers.append("HUI_FORWARDED_ALLOW_IPS=*")
    if env_dangers:
        items.append(ProductionConfigItem("fail", "environment-overrides", "Environment variables override safe setup values", ", ".join(env_dangers), "Remove or correct the conflicting variables in .env and the systemd EnvironmentFile."))

    if live_check:
        host, live_ports = _effective_live_bind(cfg, env, ports)
        for port in live_ports:
            available, detail = _port_available(host, port)
            items.append(ProductionConfigItem("pass" if available else "fail", "port-live-check", "Production port is available" if available else "Production port is already in use", detail, "Stop the conflicting service or choose a different production_instance_base_port." if not available else ""))
        workdir = Path(str(cfg.get("systemd_working_directory") or Path(__file__).resolve().parent)).expanduser()
        python_path = Path(str(cfg.get("systemd_python") or (workdir / ".venv/bin/python"))).expanduser()
        if str(cfg.get("systemd_working_directory") or "").strip() and not workdir.exists():
            items.append(ProductionConfigItem("warn", "systemd-workdir", "Configured systemd working directory does not exist on this host", str(workdir), "Install/copy Hui Chat to that directory before enabling the service."))
        if str(cfg.get("systemd_python") or "").strip() and not python_path.exists():
            items.append(ProductionConfigItem("warn", "systemd-python", "Configured systemd Python does not exist on this host", str(python_path), "Create the project .venv and install requirements before enabling the service."))

    fail_count = sum(item.level == "fail" for item in items)
    warn_count = sum(item.level == "warn" for item in items)
    pass_count = sum(item.level == "pass" for item in items)
    overall = "fail" if fail_count else "warn" if warn_count else "pass"
    return {
        "overall": overall,
        "mode": mode,
        "hosting_mode": hosting_mode,
        "workers": workers,
        "instances": instances,
        "threads": threads,
        "worker_class": worker_class,
        "async_mode": async_mode,
        "planned_ports": ports,
        "capacity": capacity,
        "live_check": bool(live_check),
        "pass_count": pass_count,
        "warn_count": warn_count,
        "fail_count": fail_count,
        "items": [asdict(item) for item in items],
    }


def apply_safe_production_fixes(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply deterministic fixes that do not need credentials or user intent."""
    cfg = settings
    changes: list[dict[str, Any]] = []

    def set_value(key: str, value: Any, reason: str) -> None:
        old = cfg.get(key)
        if old != value:
            cfg[key] = value
            changes.append({"key": key, "old": old, "new": value, "reason": reason})

    set_value("debug", False, "Flask debug mode is unsafe and slower in production")
    set_value("server_debug", False, "server debug mode is unsafe and slower in production")
    set_value("production_workers", 1, "Flask-SocketIO requires one Gunicorn worker per instance")

    async_mode = str(cfg.get("production_async_mode") or "threading").strip().lower()
    set_value("production_worker_class", "eventlet" if async_mode == "eventlet" else "gthread", "align Gunicorn worker class with Socket.IO async mode")
    if async_mode not in {"eventlet", "threading"}:
        set_value("production_async_mode", "threading", "use the supported default production async mode")
        set_value("production_worker_class", "gthread", "use the supported default production worker")

    if _int(_first_configured(cfg.get("production_instance_port_step"), default=1), 1) < 1:
        set_value("production_instance_port_step", 1, "prevent overlapping instance ports")
    instances = _int(cfg.get("production_instance_count") or 1, 1, 1, 10)
    capacity = detect_host_capacity()
    if _truthy(cfg.get("auto_tune_production_capacity", True)) and instances > int(capacity["recommended_instances"] or 1):
        instances = int(capacity["recommended_instances"] or 1)
        set_value("production_instance_count", instances, "fit the number of instances to detected CPU and memory")

    if _truthy(cfg.get("auto_tune_production_capacity", True)):
        recommended_threads = int(capacity["recommended_threads"] or 32)
        current_threads = _int(cfg.get("production_threads") or cfg.get("gunicorn_threads") or 100, 100, 1, 500)
        if current_threads > recommended_threads:
            set_value("production_threads", recommended_threads, "avoid excessive thread scheduling and database contention")

    total_db_cap = max(10, min(50, 80 // max(1, instances)))
    if _truthy(cfg.get("auto_tune_production_capacity", True)):
        host_db_cap = max(10, min(50, int(capacity["recommended_threads"] or 20) // 2))
        db_cap = min(total_db_cap, host_db_cap)
    else:
        db_cap = total_db_cap
    current_db_max = _int(cfg.get("db_pool_max") or 50, 50, 1)
    if current_db_max > db_cap:
        set_value("db_pool_max", db_cap, "fit each PostgreSQL pool to detected host capacity and keep the planned total bounded")
    db_max = _int(cfg.get("db_pool_max") or db_cap, db_cap, 1)
    if _int(cfg.get("db_pool_min") or 1, 1, 1) > db_max:
        set_value("db_pool_min", min(2, db_max), "database pool minimum cannot exceed its maximum")
    if _int(_first_configured(cfg.get("db_pool_wait_seconds"), default=0), 0) < 10:
        set_value("db_pool_wait_seconds", 10, "queue short database bursts instead of failing immediately")

    if _run_mode(cfg) == "production":
        set_value("enable_health_check_endpoint", True, "allow the proxy and service manager to detect unhealthy workers")
        if str(cfg.get("production_loglevel") or "info").strip().lower() in {"debug", "trace"}:
            set_value("production_loglevel", "info", "avoid high-volume production logging")
        if str(cfg.get("log_level") or "info").strip().lower() in {"debug", "trace"}:
            set_value("log_level", "INFO", "avoid high-volume production logging")
        set_value("janitor_debug_custom_rooms", False, "avoid unnecessary janitor debug I/O")

    if infer_hosting_mode(cfg) == "public_beta":
        set_value("auto_allow_lan_origins", False, "do not broaden public origin policy with LAN addresses")

    if _truthy(cfg.get("trust_proxy_headers")):
        if _int(_first_configured(cfg.get("proxy_fix_hops"), default=0), 0) < 1:
            set_value("proxy_fix_hops", 1, "one local reverse proxy requires one trusted hop")
        if not _is_loopback_host(str(cfg.get("production_instance_bind_host") or "")):
            set_value("production_instance_bind_host", "127.0.0.1", "keep reverse-proxied backend instances private")
        if str(cfg.get("forwarded_allow_ips") or "127.0.0.1").strip() == "*":
            set_value("forwarded_allow_ips", "127.0.0.1", "do not trust forwarded headers from arbitrary clients")

    base_port = _int(cfg.get("production_instance_base_port") or cfg.get("server_port") or 5000, 5000, 1, 65535)
    if _truthy(cfg.get("trust_proxy_headers")) and _int(cfg.get("reverse_proxy_backend_port") or 0, 0) != base_port:
        set_value("reverse_proxy_backend_port", base_port, "reverse proxy backend must target the first production instance")

    timing_changes = apply_runtime_timing_safety_defaults(cfg)
    changes.extend(timing_changes)

    redis_old_values = {
        "rate_limit_storage_uri": cfg.get("rate_limit_storage_uri") or cfg.get("rate_limit_storage"),
        "simple_rate_limit_storage_uri": cfg.get("simple_rate_limit_storage_uri"),
        "socketio_message_queue": cfg.get("socketio_message_queue"),
        "shared_state_redis_url": cfg.get("shared_state_redis_url"),
        "db_pool_max": cfg.get("db_pool_max"),
        "db_pool_wait_seconds": cfg.get("db_pool_wait_seconds"),
    }
    redis_changes = apply_scaled_runtime_safety_defaults(cfg)
    for key, changed in redis_changes.items():
        if changed:
            changes.append({
                "key": key,
                "old": redis_old_values.get(key),
                "new": cfg.get(key),
                "reason": "scaled production requires Redis-backed shared services",
            })

    return changes


def blocking_production_conflicts(settings: dict[str, Any], *, live_check: bool = False) -> list[str]:
    report = build_production_config_report(settings, live_check=live_check)
    out: list[str] = []
    for item in report.get("items") or []:
        if item.get("level") != "fail":
            continue
        text = str(item.get("title") or item.get("code") or "production conflict")
        detail = str(item.get("detail") or "").strip()
        fix = str(item.get("fix") or "").strip()
        if detail:
            text += f": {detail}"
        if fix:
            text += f" Fix: {fix}"
        out.append(text)
    return out


def format_production_config_report(report: dict[str, Any]) -> str:
    symbol = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    capacity = report.get("capacity") or {}
    lines = [
        "Hui Chat production configuration guard",
        "=" * 39,
        f"Overall: {str(report.get('overall') or 'unknown').upper()}",
        f"Mode: {report.get('mode')} / hosting={report.get('hosting_mode')}",
        f"Topology: {report.get('instances')} instance(s), {report.get('workers')} worker each, {report.get('threads')} threads",
        f"Runtime: async={report.get('async_mode')}; worker={report.get('worker_class')}",
        f"Detected host: CPUs={capacity.get('cpu_count')}; memory={capacity.get('memory_mb') or 'unknown'} MB; recommended instances<={capacity.get('recommended_instances')}; threads<={capacity.get('recommended_threads')}; DB pool<={capacity.get('recommended_db_pool_per_instance') or 'n/a'} per instance",
        f"Checks: {report.get('pass_count', 0)} passed, {report.get('warn_count', 0)} warning(s), {report.get('fail_count', 0)} failure(s)",
        "",
    ]
    for item in report.get("items") or []:
        level = str(item.get("level") or "warn")
        lines.append(f"[{symbol.get(level, level.upper())}] {item.get('title')}")
        if item.get("detail"):
            lines.append(f"  {item.get('detail')}")
        if item.get("fix") and level != "pass":
            lines.append(f"  Fix: {item.get('fix')}")
    return "\n".join(lines).rstrip() + "\n"
