#!/usr/bin/env python3
"""Dependency-light end-to-end setup and capacity checker for Hui Chat."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from env_loader import load_project_dotenv
from performance_tuning import recommended_performance_settings

load_project_dotenv()


def add(items: list[dict], level: str, code: str, message: str, fix: str = "") -> None:
    items.append({"level": level, "code": code, "message": message, "fix": fix})


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def run(args: argparse.Namespace) -> int:
    items: list[dict] = []
    config = Path(args.config).expanduser().resolve()
    python_bin = ROOT / ".venv/bin/python"
    if not python_bin.exists():
        python_bin = Path(sys.executable)

    add(items, "pass", "python", f"Python executable: {python_bin}")
    if sys.version_info < (3, 10):
        add(items, "fail", "python-version", f"Python {sys.version.split()[0]} is too old", "Install Python 3.10 or newer.")
    else:
        add(items, "pass", "python-version", f"Python {sys.version.split()[0]} is supported")

    required = [
        "flask", "flask_socketio", "flask_jwt_extended", "flask_limiter",
        "psycopg2", "redis", "gunicorn", "simple_websocket", "cryptography", "argon2",
    ]
    probe = "import importlib.util,json; print(json.dumps({m:bool(importlib.util.find_spec(m)) for m in " + repr(required) + "}))"
    try:
        found = json.loads(subprocess.check_output([str(python_bin), "-c", probe], text=True, stderr=subprocess.STDOUT, timeout=20))
        missing = [name for name, present in found.items() if not present]
    except Exception as exc:
        missing = required
        add(items, "fail", "dependency-probe", f"Could not inspect the runtime environment: {exc}", "Run scripts/install_production_deps.sh.")
    if missing:
        add(items, "fail", "dependencies", "Missing Python packages: " + ", ".join(missing), "Run scripts/install_production_deps.sh.")
    else:
        add(items, "pass", "dependencies", "All required production Python packages are installed")

    settings: dict = {}
    if not config.exists():
        add(items, "fail", "config", f"Configuration is missing: {config}", "Run .venv/bin/python main.py --setup-only.")
    else:
        try:
            settings = json.loads(config.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError("top-level value is not an object")
            add(items, "pass", "config", f"Configuration JSON is valid: {config}")
        except Exception as exc:
            add(items, "fail", "config", f"Configuration is invalid: {exc}", "Restore the last good config or rerun setup.")
            settings = {}

    instances = max(1, min(10, _safe_int(settings.get("production_instance_count"), 1)))
    workers = _safe_int(settings.get("production_workers"), 1)
    threads = _safe_int(settings.get("production_threads") or settings.get("gunicorn_threads"), 100)
    db_pool_max = _safe_int(settings.get("db_pool_max"), 50)
    recommended = recommended_performance_settings(instances)

    if workers != 1:
        add(items, "fail", "workers", f"production_workers={workers}; Socket.IO requires one worker per instance", "Set production_workers=1 and scale with separate instances.")
    else:
        add(items, "pass", "workers", "One Gunicorn worker per instance is configured")

    possible_db_connections = instances * db_pool_max
    if possible_db_connections > 85:
        add(items, "warn", "db-capacity", f"Potential web DB connections={possible_db_connections}", "Lower db_pool_max, reduce instances, or deploy PgBouncer/PostgreSQL capacity explicitly.")
    else:
        add(items, "pass", "db-capacity", f"Potential web DB connections={possible_db_connections}")

    if threads > recommended["production_threads"] * 2:
        add(items, "warn", "thread-capacity", f"{threads} threads per instance is high for {instances} instances", f"Use about {recommended['production_threads']} threads per instance.")
    else:
        add(items, "pass", "thread-capacity", f"{threads} threads per instance for {instances} instance(s)")

    for relative in ["logs", "uploads", "private_uploads", "instance", "static/uploads"]:
        path = ROOT / relative
        try:
            path.mkdir(parents=True, exist_ok=True)
            marker = path / ".hui-write-test"
            marker.write_text("ok", encoding="utf-8")
            marker.unlink()
            add(items, "pass", "writable-" + relative.replace("/", "-"), f"Writable runtime path: {path}")
        except Exception as exc:
            add(items, "fail", "writable-" + relative.replace("/", "-"), f"Runtime path is not writable: {path}: {exc}", "Fix ownership for the Hui Chat service user.")

    db_url = str(os.getenv("DATABASE_URL") or settings.get("database_url") or "")
    if not db_url:
        add(items, "fail", "database-url", "DATABASE_URL/database_url is empty", "Run setup and configure PostgreSQL.")
    elif not db_url.startswith(("postgresql://", "postgres://")):
        add(items, "fail", "database-url", "Database URL is not PostgreSQL", "Use a postgresql:// DSN.")
    else:
        add(items, "pass", "database-url", "PostgreSQL DSN is configured")

    redis_urls = [
        str(os.getenv("HUI_RATE_LIMIT_STORAGE_URI") or settings.get("rate_limit_storage_uri") or ""),
        str(os.getenv("HUI_SOCKETIO_MESSAGE_QUEUE") or settings.get("socketio_message_queue") or ""),
        str(os.getenv("HUI_SHARED_STATE_REDIS_URL") or settings.get("shared_state_redis_url") or ""),
    ]
    redis_configured = all(url.startswith(("redis://", "rediss://")) for url in redis_urls)
    if instances > 1 and not redis_configured:
        add(items, "fail", "redis-topology", "Scaled deployment is missing one or more explicit Redis URLs", "Configure Redis DB 0 for rate limits, 1 for Socket.IO, and 2 for shared state.")
    elif redis_configured:
        add(items, "pass", "redis-topology", "Redis URLs are configured for rate limits, Socket.IO, and shared state")
    else:
        add(items, "warn", "redis-topology", "Single-instance mode can start, but Redis-backed rate limits are recommended")

    if args.live:
        if shutil.which("pg_isready") and db_url:
            parsed = urlparse(db_url)
            command = ["pg_isready", "-h", parsed.hostname or "localhost", "-p", str(parsed.port or 5432)]
            if parsed.path.strip("/"):
                command += ["-d", parsed.path.strip("/")]
            if parsed.username:
                command += ["-U", parsed.username]
            rc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False).returncode
            add(items, "pass" if rc == 0 else "fail", "postgres-live", "PostgreSQL is reachable" if rc == 0 else "PostgreSQL is not reachable", "Start PostgreSQL and verify DATABASE_URL." if rc else "")
        else:
            add(items, "warn", "postgres-live", "pg_isready is unavailable; live PostgreSQL check skipped")

        redis_cli = shutil.which("redis-cli") or shutil.which("valkey-cli")
        redis_target = next((url for url in redis_urls if url.startswith(("redis://", "rediss://"))), "")
        if redis_cli and redis_target:
            rc = subprocess.run([redis_cli, "-u", redis_target, "ping"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False).returncode
            add(items, "pass" if rc == 0 else "fail", "redis-live", "Redis is reachable" if rc == 0 else "Redis is not reachable", "Start Redis/Valkey and verify the configured URL." if rc else "")
        elif redis_target:
            add(items, "warn", "redis-live", "redis-cli/valkey-cli is unavailable; live key-value-store check skipped")

    fail_count = sum(item["level"] == "fail" for item in items)
    warn_count = sum(item["level"] == "warn" for item in items)
    pass_count = sum(item["level"] == "pass" for item in items)
    print("Hui Chat Setup Readiness Doctor")
    print(f"Summary: {pass_count} pass, {warn_count} warn, {fail_count} fail")
    for item in items:
        print(f"{item['level'].upper():4}  {item['message']}")
        if item["fix"]:
            print("      Fix: " + item["fix"])
    return 2 if fail_count else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Hui Chat setup readiness doctor")
    parser.add_argument("--config", default="server_config.json")
    parser.add_argument("--live", action="store_true")
    raise SystemExit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
