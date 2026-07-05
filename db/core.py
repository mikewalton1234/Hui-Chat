#!/usr/bin/env python3
"""Core database connection, identity, and app bootstrap helpers."""

from __future__ import annotations

import logging
import os

import psycopg2
from psycopg2.pool import PoolError
from flask import g

from constants import get_db_connection_string, sanitize_postgres_dsn, redact_postgres_dsn
from db.bootstrap import ensure_database_ready
from db import shared


def prepare_runtime_database(settings: dict) -> dict:
    """Normalize runtime DSNs and best-effort prepare the target database."""
    runtime_dsn = str(sanitize_postgres_dsn(str(settings.get("database_url") or get_db_connection_string(settings))))
    bootstrap_dsn = (
        os.getenv("ECHOCHAT_DB_BOOTSTRAP_URL")
        or os.getenv("DATABASE_BOOTSTRAP_URL")
        or settings.get("database_bootstrap_url")
        or ""
    )
    if not runtime_dsn:
        raise RuntimeError(
            "PostgreSQL DSN is empty. Run `python main.py --setup` and choose/create a local "
            "PostgreSQL database, or set DATABASE_URL/DB_CONNECTION_STRING. Example: "
            "DATABASE_URL=postgresql://$USER@localhost:5432/echochat"
        )
    settings["database_url"] = runtime_dsn
    if bootstrap_dsn:
        settings["database_bootstrap_url"] = str(bootstrap_dsn)
    ensure_database_ready(runtime_dsn, recreate=False, bootstrap_dsn=bootstrap_dsn or None)
    return {"runtime_dsn": runtime_dsn, "bootstrap_dsn": bootstrap_dsn or None}

def init_db_pool(minconn: int = 1, maxconn: int = 50, dsn: str | None = None, *, allow_direct_fallback: bool | None = None) -> None:
    """Initialise a global ThreadedConnectionPool.

    Safe to call multiple times (no-op after first init).
    """
    if shared._POOL is not None:
        return

    shared._DSN = str(sanitize_postgres_dsn(dsn or get_db_connection_string()))
    shared._POOL_CONFIGURED = True
    shared._DB_POOL_MIN = int(minconn)
    shared._DB_POOL_MAX = int(maxconn)
    if allow_direct_fallback is None:
        raw = os.getenv("ECHOCHAT_DB_POOL_DIRECT_FALLBACK", "").strip().lower()
        allow_direct_fallback = raw in {"1", "true", "yes", "on"}
    shared._ALLOW_DIRECT_FALLBACK = bool(allow_direct_fallback)

    try:
        shared._POOL = shared.ThreadedConnectionPool(
            minconn=int(minconn),
            maxconn=int(maxconn),
            dsn=shared._DSN,
        )
        shared._POOL_INIT_ERROR = None
        logging.info("✅  Postgres connection pool ready (min=%s max=%s)", minconn, maxconn)
    except Exception as e:
        shared._POOL = None
        shared._POOL_INIT_ERROR = str(e)
        if shared._ALLOW_DIRECT_FALLBACK:
            logging.warning("⚠️  Could not initialise Postgres pool; direct DB fallback is explicitly enabled: %s", e)
        else:
            logging.error("❌ Could not initialise Postgres pool and direct fallback is disabled: %s", e)


def _acquire_conn():
    """Acquire a connection either from the configured pool or a direct connection.

    Once ``init_db_pool()`` has configured a bounded pool, that bound is treated
    as real capacity.  Echo-Chat used to open unbounded temporary direct
    connections when the pool was exhausted; that defeated db_pool_max and could
    overload PostgreSQL in scaled deployments.  Direct fallback now requires the
    explicit ECHOCHAT_DB_POOL_DIRECT_FALLBACK=1 escape hatch.
    """
    if shared._POOL is not None:
        try:
            return shared._POOL.getconn(), True
        except PoolError as e:
            if not shared._ALLOW_DIRECT_FALLBACK:
                raise RuntimeError(
                    "PostgreSQL connection pool is exhausted. Increase db_pool_max, reduce planned instances, "
                    "or add PgBouncer; Echo-Chat will not open unbounded direct DB connections."
                ) from e
            logging.warning("Postgres pool exhausted; direct DB fallback is explicitly enabled: %s", e)
        except Exception as e:
            if not shared._ALLOW_DIRECT_FALLBACK:
                raise RuntimeError("PostgreSQL pool getconn failed and direct DB fallback is disabled") from e
            logging.warning("Postgres pool getconn failed; direct DB fallback is explicitly enabled: %s", e)
    elif shared._POOL_CONFIGURED and not shared._ALLOW_DIRECT_FALLBACK:
        raise RuntimeError(
            "PostgreSQL connection pool was configured but failed to initialize; "
            f"direct DB fallback is disabled. Last pool error: {shared._POOL_INIT_ERROR or 'unknown'}"
        )
    return psycopg2.connect(shared._DSN or get_db_connection_string()), False


def _release_conn(conn, from_pool: bool) -> None:
    if conn is None:
        return
    if shared._POOL is not None and from_pool:
        try:
            # Ensure a clean connection is returned to the pool.
            conn.rollback()
        except Exception:
            pass
        shared._POOL.putconn(conn)
    else:
        conn.close()

def get_db() -> psycopg2.extensions.connection:
    """
    Return one psycopg2 connection per Flask request context (stored in g.db).
    Uses get_db_connection_string() for runtime evaluation.
    """
    if not hasattr(g, "db"):
        conn, from_pool = _acquire_conn()
        g.db = conn
        g.db_from_pool = from_pool
    return g.db


def close_db(error=None):
    """
    Teardown: close the connection stored in g.db (if any).
    Called automatically via app.teardown_appcontext.
    """
    db_conn = g.pop("db", None)
    from_pool = bool(g.pop("db_from_pool", False))
    if db_conn is not None:
        try:
            _release_conn(db_conn, from_pool)
        except Exception as e:
            logging.error("Error releasing DB connection: %s", e)
    if error:
        logging.error("DB teardown error: %s", error)

def init_database():
    """Apply tracked schema migrations and seed baseline data when needed."""
    logging.info("🔧  Initialising DB via tracked migrations…")
    from db.migrations import apply_migrations

    result = apply_migrations()
    applied = result.get("applied") or []
    skipped = result.get("skipped") or []
    logging.info("Migration result: applied=%s skipped=%s", ", ".join(applied) if applied else "none", ", ".join(skipped) if skipped else "none")
    # Log the *effective* runtime DSN used by the pool/direct connection layer.
    # This matters when Echo-Chat is started with --config or env overrides: the
    # default server_config.json may point somewhere else, and logging that older
    # value makes wrong-database investigations misleading.
    effective_dsn = shared._DSN or get_db_connection_string()
    logging.info("✅  DB ready at %s", redact_postgres_dsn(effective_dsn))
    try:
        logging.info("Tracked schema state: %s", get_schema_version())
    except Exception:
        pass
    return result


def get_db_identity() -> dict:
    """Return runtime identity information for the current DB connection.

    Helps detect 'wrong database / wrong role' mistakes quickly.
    """
    conn = get_db()
    out = {
        "current_user": None,
        "current_database": None,
        "server_addr": None,
        "server_port": None,
        "server_version": None,
    }
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_user, current_database(), inet_server_addr(), inet_server_port(), version();"
            )
            row = cur.fetchone()
        if row:
            out["current_user"] = row[0]
            out["current_database"] = row[1]
            out["server_addr"] = str(row[2]) if row[2] is not None else None
            out["server_port"] = int(row[3]) if row[3] is not None else None
            out["server_version"] = str(row[4]) if row[4] is not None else None
    except Exception as exc:
        out["error"] = str(exc)
    return out


def get_schema_version() -> str:
    """Best-effort schema version string."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.echochat_schema_meta');")
            row = cur.fetchone()
            reg = row[0] if row else None
            if reg:
                cur.execute(
                    "SELECT version, applied_at FROM echochat_schema_meta WHERE success = TRUE ORDER BY applied_at DESC, version DESC LIMIT 1;"
                )
                latest = cur.fetchone()
                cur.execute("SELECT count(*) FROM echochat_schema_meta WHERE success = TRUE;")
                applied_count = int((cur.fetchone() or [0])[0] or 0)
                if latest and latest[0]:
                    return f"{latest[0]} ({applied_count} applied migrations)"
            cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public';")
            n_tables = cur.fetchone()[0]
        return f"untracked schema (public tables={n_tables})"
    except Exception as exc:
        return f"unknown ({exc})"

def init_app(app):
    """
    Call in server_init.py after creating the Flask app:

        from database import init_app as init_db
        app = Flask(__name__)
        init_db(app)

    This runs init_database() once and registers teardown.
    """
    init_database()
    app.teardown_appcontext(close_db)
