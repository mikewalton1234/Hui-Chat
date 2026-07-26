#!/usr/bin/env python3
"""Regression checks for beta.450 scaled DB pool burst waiting."""
from __future__ import annotations

import threading
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from psycopg2.pool import PoolError
from db import shared
from db import core
from scaled_redis_autoconfig import apply_scaled_runtime_safety_defaults
from redis_socketio_readiness import build_redis_socketio_report


class FakeConn:
    closed = 0
    def rollback(self):
        return None


class FakePool:
    def __init__(self):
        self.available = []
    def getconn(self):
        if not self.available:
            raise PoolError("connection pool exhausted")
        return self.available.pop()
    def putconn(self, conn, close=False):
        if not close:
            self.available.append(conn)


def main() -> int:
    original_pool = shared._POOL
    original_wait = shared._DB_POOL_WAIT_SECONDS
    try:
        pool = FakePool()
        shared._POOL = pool
        shared._DB_POOL_WAIT_SECONDS = 1.0
        conn = FakeConn()
        def release_later():
            time.sleep(0.1)
            core._pool_putconn(conn)
        thread = threading.Thread(target=release_later)
        thread.start()
        started = time.monotonic()
        got = core._pool_getconn_with_wait()
        elapsed = time.monotonic() - started
        thread.join(timeout=2)
        assert got is conn
        assert elapsed >= 0.08, elapsed
        assert elapsed < 1.0, elapsed

        settings = {
            "production_instance_count": 10,
            "production_workers": 1,
            "production_threads": 25,
            "db_pool_max": 50,
            "rate_limit_storage_uri": "memory://",
            "simple_rate_limit_storage_uri": "memory://",
            "socketio_message_queue": "",
            "shared_state_redis_url": "",
            "production_async_mode": "threading",
            "production_worker_class": "gthread",
            "socketio_transports": ["polling"],
        }
        changed = apply_scaled_runtime_safety_defaults(settings)
        assert settings["db_pool_max"] == 8
        assert settings["db_pool_wait_seconds"] == 10
        assert changed["db_pool_wait_seconds"] is True
        report = build_redis_socketio_report(settings, live_check=False)
        failures = [item for item in report.get("items", []) if item.get("level") == "fail"]
        assert not failures, failures
        burst = [item for item in report.get("items", []) if item.get("code") == "db-pool-burst-wait"]
        assert burst and burst[0].get("level") == "pass", burst
        print("PASS beta.450 scaled DB pool burst guard")
        return 0
    finally:
        shared._POOL = original_pool
        shared._DB_POOL_WAIT_SECONDS = original_wait


if __name__ == "__main__":
    raise SystemExit(main())
