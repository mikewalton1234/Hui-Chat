#!/usr/bin/env python3
"""Database bootstrap helpers for EchoChat.

These helpers are used both by interactive setup and by runtime startup so the
server can:
  - auto-discover one or more existing EchoChat databases on the same PostgreSQL server
  - create the configured database if it does not exist
  - optionally drop/recreate the database
  - repair ownership/schema grants when a bootstrap/admin DSN is available
"""

from __future__ import annotations

from typing import Any
import getpass
import os
import shlex
import shutil
import subprocess
from urllib.parse import quote, unquote, urlparse, urlunparse

import psycopg2
from psycopg2 import sql

from constants import sanitize_postgres_dsn


ECHOCHAT_CORE_TABLES = (
    "users",
    "roles",
    "permissions",
    "user_roles",
    "role_permissions",
    "echochat_schema_meta",
)

ECHOCHAT_MARKER_TABLES = (
    "users",
    "chat_rooms",
    "custom_rooms",
    "roles",
    "permissions",
    "user_roles",
    "role_permissions",
    "echochat_schema_meta",
    "friends",
    "friend_requests",
    "blocked_users",
    "blocks",
    "private_messages",
    "audit_log",
    "auth_sessions",
)

ECHOCHAT_REQUIRED_USER_COLUMNS = (
    "id",
    "username",
    "password",
    "email",
    "is_admin",
    "public_key",
    "encrypted_private_key",
    "recovery_pin_hash",
)

PROTECTED_POSTGRES_DATABASES = {"postgres", "template0", "template1"}


def is_protected_database_name(dbname: str) -> bool:
    """Return True for PostgreSQL maintenance/template databases setup must not mutate."""
    return str(dbname or "").strip().lower() in PROTECTED_POSTGRES_DATABASES


def _require_mutable_database_name(dbname: str, action: str) -> None:
    name = str(dbname or "").strip()
    if not name:
        raise RuntimeError(f"Cannot {action}: PostgreSQL database name is empty.")
    if is_protected_database_name(name):
        raise RuntimeError(
            f"Refusing to {action} protected PostgreSQL database '{name}'. "
            "Choose or create a dedicated Echo-Chat database instead."
        )


def _quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _is_local_postgres_target(parts: dict[str, Any]) -> bool:
    host = str(parts.get("host") or "").strip().lower()
    return host in ("", "localhost", "127.0.0.1", "::1") or host.startswith("/")


def _local_postgres_tool_env(parts: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    host = str(parts.get("host") or "").strip()
    if host.startswith("/"):
        env["PGHOST"] = host
    else:
        env.pop("PGHOST", None)
    port = str(parts.get("port") or 5432).strip()
    if port:
        env["PGPORT"] = port
    return env


def _local_postgres_prefix(system_user: str = "postgres") -> list[str]:
    if str(getpass.getuser() or "") == str(system_user):
        return []
    if shutil.which("sudo"):
        return ["sudo", "-u", system_user]
    raise RuntimeError(
        "Local PostgreSQL admin mode needs sudo so setup can run psql/createdb as the postgres system user."
    )


def _run_local_postgres_tool(args: list[str], *, parts: dict[str, Any], system_user: str = "postgres", capture: bool = False):
    if not _is_local_postgres_target(parts):
        raise RuntimeError(
            "Local PostgreSQL admin mode only supports local servers (localhost/127.0.0.1/::1 or a Unix socket path)."
        )
    cmd = _local_postgres_prefix(system_user) + list(args)
    env = _local_postgres_tool_env(parts)
    return subprocess.run(
        cmd,
        check=True,
        env=env,
        text=True,
        capture_output=capture,
    )


def _run_local_postgres_sql(sql_text: str, *, parts: dict[str, Any], database: str = "postgres", system_user: str = "postgres", capture: bool = False):
    return _run_local_postgres_tool(
        ["psql", "-v", "ON_ERROR_STOP=1", "-d", database, "-c", sql_text],
        parts=parts,
        system_user=system_user,
        capture=capture,
    )


def _grant_runtime_database_access_via_local_admin(target_dsn: str, *, system_user: str = "postgres") -> dict[str, Any]:
    runtime_parts = dsn_parts(target_dsn)
    target_db = runtime_parts["db"]
    runtime_user = str(runtime_parts.get("user") or "").strip()
    if not runtime_user:
        raise RuntimeError("Target PostgreSQL DSN must include a username for local admin repair.")
    if not _is_local_postgres_target(runtime_parts):
        raise RuntimeError(
            "Local PostgreSQL admin mode only supports local servers (localhost/127.0.0.1/::1 or a Unix socket path)."
        )

    sql_text = "\n".join((
        f"ALTER DATABASE {_quote_ident(target_db)} OWNER TO {_quote_ident(runtime_user)};",
        f"GRANT CONNECT, TEMP, CREATE ON DATABASE {_quote_ident(target_db)} TO {_quote_ident(runtime_user)};",
    ))
    _run_local_postgres_sql(sql_text, parts=runtime_parts, database="postgres", system_user=system_user, capture=False)

    schema_sql = "\n".join((
        f"CREATE SCHEMA IF NOT EXISTS public AUTHORIZATION {_quote_ident(runtime_user)};",
        f"ALTER SCHEMA public OWNER TO {_quote_ident(runtime_user)};",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {_quote_ident(runtime_user)};",
    ))
    _run_local_postgres_sql(schema_sql, parts=runtime_parts, database=target_db, system_user=system_user, capture=False)

    probe = psycopg2.connect(target_dsn)
    try:
        ident = db_identity(probe)
        perms = _runtime_schema_permissions(probe)
    finally:
        probe.close()
    return {"identity": ident, "schema_permissions": perms}


def ensure_database_ready_via_local_admin(
    dsn: str,
    *,
    recreate: bool = False,
    system_user: str = "postgres",
) -> dict[str, Any]:
    parts = dsn_parts(dsn)
    if not _is_local_postgres_target(parts):
        raise RuntimeError(
            "Local PostgreSQL admin mode only supports local servers (localhost/127.0.0.1/::1 or a Unix socket path)."
        )

    target_dsn = build_postgres_dsn(parts, parts["db"])
    created_db = False
    if recreate:
        _require_mutable_database_name(parts["db"], "recreate")
        _run_local_postgres_tool(
            ["dropdb", "--if-exists", parts["db"]],
            parts=parts,
            system_user=system_user,
            capture=False,
        )
        _run_local_postgres_tool(
            ["createdb", "--owner", parts["user"], parts["db"]],
            parts=parts,
            system_user=system_user,
            capture=False,
        )
        created_db = True
    else:
        try:
            probe = psycopg2.connect(target_dsn)
        except Exception as exc:
            if _should_treat_as_missing_db(exc):
                _require_mutable_database_name(parts["db"], "create")
                _run_local_postgres_tool(
                    ["createdb", "--owner", parts["user"], parts["db"]],
                    parts=parts,
                    system_user=system_user,
                    capture=False,
                )
                created_db = True
            else:
                raise
        else:
            probe.close()

    grant_info = _grant_runtime_database_access_via_local_admin(target_dsn, system_user=system_user)
    ident = grant_info["identity"]
    perms = grant_info["schema_permissions"]
    if not (perms.get("usage") and perms.get("create")):
        raise RuntimeError(
            "Connected to PostgreSQL, but the runtime role still does not have USAGE, CREATE on schema public after local admin repair. "
            "Run the shown psql/createdb steps manually as postgres and verify the runtime DSN user owns the database."
        )
    return {
        "dsn": target_dsn,
        "created": bool(created_db and not recreate),
        "recreated": bool(recreate),
        "identity": ident,
        "schema_permissions": perms,
        "used_bootstrap_dsn": False,
        "used_local_admin": True,
    }


def dsn_parts(dsn: str) -> dict[str, Any]:
    s = str(sanitize_postgres_dsn(dsn) or "").strip()
    if not s:
        raise ValueError("PostgreSQL DSN is empty.")
    parsed = urlparse(s)
    scheme = parsed.scheme or "postgresql"
    if scheme not in ("postgresql", "postgres"):
        raise ValueError(f"Unsupported PostgreSQL DSN scheme: {scheme}")
    dbname = (parsed.path or "").lstrip("/") or "postgres"
    return {
        "scheme": scheme,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "localhost",
        "port": int(parsed.port or 5432),
        "db": dbname,
        "query": parsed.query or "",
        "fragment": parsed.fragment or "",
    }



def build_postgres_dsn(parts: dict[str, Any], dbname: str | None = None) -> str:
    scheme = str(parts.get("scheme") or "postgresql")
    host = str(parts.get("host") or "localhost")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    user = str(parts.get("user") or "")
    password = str(parts.get("password") or "")
    auth = ""
    if user:
        auth = quote(user, safe="")
        if password:
            auth += f":{quote(password, safe='')}"
        auth += "@"
    netloc = f"{auth}{host}:{int(parts.get('port') or 5432)}"
    path = "/" + str(dbname or parts.get("db") or "postgres")
    query = str(parts.get("query") or "")
    fragment = str(parts.get("fragment") or "")
    return urlunparse((scheme, netloc, path, "", query, fragment))



def _choose_admin_seed_dsn(target_dsn: str, bootstrap_dsn: str | None = None) -> str:
    return str(sanitize_postgres_dsn(bootstrap_dsn or target_dsn) or "")



def connect_maintenance_db(target_dsn: str, *, bootstrap_dsn: str | None = None, forbid_db: str | None = None):
    base_dsn = _choose_admin_seed_dsn(target_dsn, bootstrap_dsn)
    parts = dsn_parts(base_dsn)
    last_exc: Exception | None = None
    tried: list[str] = []
    for candidate_db in ("postgres", "template1", parts["db"]):
        if not candidate_db or candidate_db == forbid_db or candidate_db in tried:
            continue
        tried.append(candidate_db)
        try:
            return psycopg2.connect(build_postgres_dsn(parts, candidate_db))
        except Exception as exc:
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Could not connect to a PostgreSQL maintenance database.")



def database_exists(conn, dbname: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s;", (dbname,))
        return cur.fetchone() is not None



def terminate_database_connections(conn, dbname: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg_terminate_backend(pid)
              FROM pg_stat_activity
             WHERE datname=%s
               AND pid <> pg_backend_pid();
            """,
            (dbname,),
        )



def create_database(conn, dbname: str, owner: str | None = None) -> None:
    _require_mutable_database_name(dbname, "create")
    stmt = sql.SQL("CREATE DATABASE {}{}").format(
        sql.Identifier(dbname),
        sql.SQL(" OWNER {}").format(sql.Identifier(owner)) if owner else sql.SQL(""),
    )
    with conn.cursor() as cur:
        cur.execute(stmt)



def drop_database(conn, dbname: str) -> None:
    _require_mutable_database_name(dbname, "drop")
    with conn.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(dbname)))



def probe_echochat_database(dsn: str) -> int:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    CASE WHEN to_regclass('public.users') IS NOT NULL THEN 1 ELSE 0 END
                  + CASE WHEN to_regclass('public.echochat_schema_meta') IS NOT NULL THEN 2 ELSE 0 END
                  + CASE WHEN to_regclass('public.roles') IS NOT NULL THEN 1 ELSE 0 END
                  + CASE WHEN to_regclass('public.user_roles') IS NOT NULL THEN 1 ELSE 0 END;
                """
            )
            row = cur.fetchone()
            return int((row or [0])[0] or 0)
    finally:
        conn.close()



def _fetch_public_table_names(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
              FROM information_schema.tables
             WHERE table_schema = 'public'
               AND table_type = 'BASE TABLE';
            """
        )
        return {str(row[0]) for row in (cur.fetchall() or [])}



def _fetch_public_columns(conn, table_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = %s;
            """,
            (table_name,),
        )
        return {str(row[0]) for row in (cur.fetchall() or [])}



def inspect_echochat_database(dsn: str) -> dict[str, Any]:
    """Inspect one PostgreSQL database and report whether it looks usable for Echo-Chat.

    This is intentionally read-mostly. The only write-like operation is the same
    savepoint-protected schema create probe used by runtime bootstrap to verify
    that the configured PostgreSQL role can create tables in public.
    """
    parts = dsn_parts(dsn)
    target_dsn = build_postgres_dsn(parts, parts["db"])
    conn = psycopg2.connect(target_dsn)
    try:
        ident = db_identity(conn)
        public_tables = _fetch_public_table_names(conn)
        present_markers = sorted(public_tables.intersection(ECHOCHAT_MARKER_TABLES))
        missing_core_tables = [name for name in ECHOCHAT_CORE_TABLES if name not in public_tables]
        users_columns = _fetch_public_columns(conn, "users") if "users" in public_tables else set()
        missing_user_columns = [name for name in ECHOCHAT_REQUIRED_USER_COLUMNS if name not in users_columns]
        applied_count = 0
        latest_migration = None
        if "echochat_schema_meta" in public_tables:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*), MAX(version)
                      FROM echochat_schema_meta
                     WHERE COALESCE(success, TRUE) = TRUE;
                    """
                )
                row = cur.fetchone() or (0, None)
                applied_count = int(row[0] or 0)
                latest_migration = str(row[1]) if row[1] is not None else None
        permissions: dict[str, bool] = {}
        try:
            permissions = _runtime_schema_permissions(conn)
        except Exception:
            # The caller still gets schema-shape information even if the role
            # cannot run the temporary CREATE/DROP probe.
            conn.rollback()
            permissions = {"usage": False, "create": False, "create_granted": False}

        public_table_count = len(public_tables)
        marker_count = len(present_markers)
        score = marker_count + (2 if "echochat_schema_meta" in public_tables else 0) + applied_count
        has_echochat_markers = marker_count > 0
        if public_table_count == 0:
            state = "empty"
        elif not has_echochat_markers:
            state = "foreign_schema"
        elif missing_core_tables or missing_user_columns:
            state = "partial_echochat"
        else:
            state = "valid_echochat"
        valid = state == "valid_echochat" and bool(permissions.get("usage")) and bool(permissions.get("create"))
        return {
            "dsn": target_dsn,
            "database": str(ident.get("current_database") or parts["db"]),
            "user": str(ident.get("current_user") or parts.get("user") or ""),
            "state": state,
            "valid": bool(valid),
            "score": int(score),
            "public_table_count": public_table_count,
            "marker_count": marker_count,
            "present_markers": present_markers,
            "missing_core_tables": missing_core_tables,
            "missing_user_columns": missing_user_columns,
            "applied_migration_count": applied_count,
            "latest_migration": latest_migration,
            "schema_permissions": permissions,
        }
    finally:
        conn.close()



def validate_echochat_database(dsn: str) -> dict[str, Any]:
    """Public setup/runtime helper: inspect the configured database target."""
    return inspect_echochat_database(dsn)



def discover_echochat_database_candidates(dsn: str, *, bootstrap_dsn: str | None = None) -> list[dict[str, Any]]:
    """Return every accessible database that appears to contain Echo-Chat tables.

    Older setup code returned only one auto-detected database. That was unsafe on
    machines where admins had multiple test/prod Echo-Chat databases. The setup
    wizard now uses this list to make the admin choose explicitly.
    """
    parts = dsn_parts(dsn)
    target_dsn = build_postgres_dsn(parts, parts["db"])
    names: list[str] = []
    try:
        admin_conn = connect_maintenance_db(target_dsn, bootstrap_dsn=bootstrap_dsn, forbid_db=None)
    except Exception:
        names = [parts["db"]]
    else:
        try:
            with admin_conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT datname
                      FROM pg_database
                     WHERE datallowconn = TRUE
                       AND datistemplate = FALSE
                       AND has_database_privilege(datname, 'CONNECT')
                     ORDER BY CASE WHEN datname=%s THEN 0 ELSE 1 END, datname;
                    """,
                    (parts["db"],),
                )
                names = [str(r[0]) for r in (cur.fetchall() or [])]
        finally:
            admin_conn.close()

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for dbname in names:
        if not dbname or dbname in seen:
            continue
        seen.add(dbname)
        candidate_dsn = build_postgres_dsn(parts, dbname)
        try:
            report = inspect_echochat_database(candidate_dsn)
        except Exception:
            continue
        if int(report.get("marker_count") or 0) > 0:
            candidates.append(report)

    candidates.sort(
        key=lambda item: (
            not bool(item.get("valid")),
            -int(item.get("score") or 0),
            str(item.get("database") or "") != str(parts["db"]),
            str(item.get("database") or ""),
        )
    )
    return candidates



def discover_existing_server_database_dsn(dsn: str, *, bootstrap_dsn: str | None = None) -> str | None:
    candidates = discover_echochat_database_candidates(dsn, bootstrap_dsn=bootstrap_dsn)
    if not candidates:
        return None
    best_score = int(candidates[0].get("score") or 0)
    best = [c for c in candidates if int(c.get("score") or 0) == best_score]
    if len(best) == 1:
        return str(best[0].get("dsn") or "") or None
    # Ambiguous tie: setup should present the full candidate list and make the
    # admin choose instead of silently picking the wrong database.
    return None



def db_identity(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT current_user, current_database(), inet_server_addr(), inet_server_port();"
        )
        row = cur.fetchone() or (None, None, None, None)
    return {
        "current_user": row[0],
        "current_database": row[1],
        "server_addr": str(row[2]) if row[2] is not None else None,
        "server_port": int(row[3]) if row[3] is not None else None,
    }



def _runtime_schema_create_probe(conn) -> bool:
    probe_name = f"__echochat_schema_probe_{os.getpid()}"
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT echochat_schema_probe;")
        try:
            cur.execute(
                sql.SQL("CREATE TABLE public.{} (id INTEGER)").format(
                    sql.Identifier(probe_name)
                )
            )
            cur.execute(
                sql.SQL("DROP TABLE public.{}").format(
                    sql.Identifier(probe_name)
                )
            )
            cur.execute("RELEASE SAVEPOINT echochat_schema_probe;")
            return True
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT echochat_schema_probe;")
            cur.execute("RELEASE SAVEPOINT echochat_schema_probe;")
            return False



def _runtime_schema_permissions(conn) -> dict[str, bool]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                COALESCE(has_schema_privilege(current_user, 'public', 'USAGE'), FALSE),
                COALESCE(has_schema_privilege(current_user, 'public', 'CREATE'), FALSE);
            """
        )
        row = cur.fetchone() or (False, False)
    usage = bool(row[0])
    granted_create = bool(row[1])
    actual_create = _runtime_schema_create_probe(conn) if usage else False
    return {
        "usage": usage,
        "create": actual_create,
        "create_granted": granted_create,
    }



def _runtime_can_prepare_schema(target_dsn: str) -> tuple[bool, dict[str, bool] | None, dict[str, Any] | None]:
    try:
        conn = psycopg2.connect(target_dsn)
    except Exception:
        return False, None, None
    try:
        perms = _runtime_schema_permissions(conn)
        ident = db_identity(conn)
        return bool(perms.get("usage") and perms.get("create")), perms, ident
    finally:
        conn.close()



def _schema_exists(conn, schema_name: str = "public") -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s);", (schema_name,))
        row = cur.fetchone() or (False,)
    return bool(row[0])



def _raise_bootstrap_privilege_error(action: str, target_dsn: str, bootstrap_dsn: str, exc: Exception):
    runtime_parts = dsn_parts(target_dsn)
    bootstrap_parts = dsn_parts(bootstrap_dsn)
    runtime_user = runtime_parts.get("user") or "(unknown runtime role)"
    bootstrap_user = bootstrap_parts.get("user") or "(unknown bootstrap role)"
    target_db = runtime_parts.get("db") or "(unknown database)"
    raise RuntimeError(
        f"Bootstrap/admin DSN role '{bootstrap_user}' does not have enough PostgreSQL privilege to {action} "
        f"for database '{target_db}' on behalf of runtime role '{runtime_user}'. "
        f"Use a PostgreSQL owner/superuser bootstrap DSN, or run the needed ALTER/GRANT statements as an admin. "
        f"Original error: {exc}"
    ) from exc



def _grant_runtime_database_access(target_dsn: str, *, bootstrap_dsn: str) -> dict[str, Any]:
    runtime_parts = dsn_parts(target_dsn)
    bootstrap_parts = dsn_parts(bootstrap_dsn)
    target_db = runtime_parts["db"]
    runtime_user = runtime_parts["user"]
    if not runtime_user:
        raise RuntimeError("Target PostgreSQL DSN must include a username for ownership/grants.")

    admin_conn = connect_maintenance_db(target_dsn, bootstrap_dsn=bootstrap_dsn, forbid_db=None)
    try:
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            if not database_exists(admin_conn, target_db):
                try:
                    create_database(admin_conn, target_db, owner=runtime_user)
                except Exception as exc:
                    _raise_bootstrap_privilege_error("create the target database", target_dsn, bootstrap_dsn, exc)
            else:
                try:
                    cur.execute(
                        sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                            sql.Identifier(target_db),
                            sql.Identifier(runtime_user),
                        )
                    )
                except Exception:
                    admin_conn.rollback()
            try:
                cur.execute(
                    sql.SQL("GRANT CONNECT, TEMP, CREATE ON DATABASE {} TO {}").format(
                        sql.Identifier(target_db),
                        sql.Identifier(runtime_user),
                    )
                )
            except Exception as exc:
                admin_conn.rollback()
                _raise_bootstrap_privilege_error(
                    "grant CONNECT/TEMP/CREATE on the database to the runtime role",
                    target_dsn,
                    bootstrap_dsn,
                    exc,
                )
    finally:
        admin_conn.close()

    admin_target_dsn = build_postgres_dsn(bootstrap_parts, target_db)
    try:
        db_conn = psycopg2.connect(admin_target_dsn)
    except Exception as exc:
        _raise_bootstrap_privilege_error(
            "connect to the target database for schema repair",
            target_dsn,
            bootstrap_dsn,
            exc,
        )
    try:
        db_conn.autocommit = True
        with db_conn.cursor() as cur:
            schema_exists = _schema_exists(db_conn, "public")
            if not schema_exists:
                try:
                    cur.execute(sql.SQL("CREATE SCHEMA public AUTHORIZATION {}").format(sql.Identifier(runtime_user)))
                except Exception as exc:
                    db_conn.rollback()
                    _raise_bootstrap_privilege_error("create schema public", target_dsn, bootstrap_dsn, exc)
            try:
                cur.execute(
                    sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(runtime_user))
                )
            except Exception:
                db_conn.rollback()
            try:
                cur.execute(
                    sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(sql.Identifier(runtime_user))
                )
            except Exception as exc:
                db_conn.rollback()
                _raise_bootstrap_privilege_error(
                    "grant USAGE, CREATE on schema public to the runtime role",
                    target_dsn,
                    bootstrap_dsn,
                    exc,
                )
            for stmt in (
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO {}"
                ).format(sql.Identifier(runtime_user)),
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO {}"
                ).format(sql.Identifier(runtime_user)),
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON FUNCTIONS TO {}"
                ).format(sql.Identifier(runtime_user)),
            ):
                try:
                    cur.execute(stmt)
                except Exception:
                    db_conn.rollback()
    finally:
        db_conn.close()

    probe = psycopg2.connect(target_dsn)
    try:
        ident = db_identity(probe)
        perms = _runtime_schema_permissions(probe)
    finally:
        probe.close()
    return {"identity": ident, "schema_permissions": perms}



def delete_database_via_bootstrap(target_dsn: str, dbname: str, *, bootstrap_dsn: str) -> dict[str, Any]:
    _require_mutable_database_name(dbname, "delete")
    if not str(sanitize_postgres_dsn(bootstrap_dsn) or "").strip():
        raise RuntimeError("A bootstrap/admin DSN is required to delete a PostgreSQL database.")
    admin_conn = connect_maintenance_db(target_dsn, bootstrap_dsn=bootstrap_dsn, forbid_db=dbname)
    try:
        admin_conn.autocommit = True
        if database_exists(admin_conn, dbname):
            terminate_database_connections(admin_conn, dbname)
            try:
                drop_database(admin_conn, dbname)
            except Exception as exc:
                _raise_bootstrap_privilege_error("delete the old database", target_dsn, bootstrap_dsn, exc)
            deleted = True
        else:
            deleted = False
    finally:
        admin_conn.close()
    return {"deleted": deleted, "database": dbname}


def _should_treat_as_missing_db(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "does not exist" in msg and "database" in msg



def _should_attempt_grant_repair(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "permission denied" in msg
        or "must be owner" in msg
        or "insufficientprivilege" in msg
        or "insufficient privilege" in msg
    )



def ensure_database_ready(
    dsn: str,
    *,
    recreate: bool = False,
    bootstrap_dsn: str | None = None,
    repair_permissions: bool = True,
) -> dict[str, Any]:
    parts = dsn_parts(dsn)
    target_dsn = build_postgres_dsn(parts, parts["db"])
    bootstrap_supplied = bool(str(sanitize_postgres_dsn(bootstrap_dsn) or "").strip())
    used_bootstrap = False
    created_db = False

    if recreate:
        _require_mutable_database_name(parts["db"], "recreate")
        admin_conn = connect_maintenance_db(target_dsn, bootstrap_dsn=bootstrap_dsn, forbid_db=parts["db"])
        used_bootstrap = bootstrap_supplied
        try:
            admin_conn.autocommit = True
            terminate_database_connections(admin_conn, parts["db"])
            drop_database(admin_conn, parts["db"])
            create_database(admin_conn, parts["db"], owner=parts["user"] or None)
        finally:
            admin_conn.close()
        created_db = True
    else:
        try:
            probe = psycopg2.connect(target_dsn)
        except Exception as exc:
            if _should_treat_as_missing_db(exc):
                _require_mutable_database_name(parts["db"], "create")
                admin_conn = connect_maintenance_db(target_dsn, bootstrap_dsn=bootstrap_dsn, forbid_db=parts["db"])
                used_bootstrap = bootstrap_supplied
                try:
                    admin_conn.autocommit = True
                    if not database_exists(admin_conn, parts["db"]):
                        create_database(admin_conn, parts["db"], owner=parts["user"] or None)
                        created_db = True
                finally:
                    admin_conn.close()
            elif repair_permissions and bootstrap_dsn and _should_attempt_grant_repair(exc):
                _grant_runtime_database_access(target_dsn, bootstrap_dsn=str(bootstrap_dsn))
                used_bootstrap = True
            else:
                raise
        else:
            try:
                ident = db_identity(probe)
                perms = _runtime_schema_permissions(probe)
            finally:
                probe.close()
            if repair_permissions and not (perms.get("usage") and perms.get("create")):
                if not bootstrap_dsn:
                    raise RuntimeError(
                        "Connected to PostgreSQL, but the runtime role does not have CREATE on schema public. "
                        "Provide a bootstrap/admin DSN or grant USAGE, CREATE on schema public to the runtime role."
                    )
                grant_info = _grant_runtime_database_access(target_dsn, bootstrap_dsn=str(bootstrap_dsn))
                ident = grant_info["identity"]
                perms = grant_info["schema_permissions"]
                used_bootstrap = True
                if not (perms.get("usage") and perms.get("create")):
                    raise RuntimeError(
                        "Connected to PostgreSQL, but the runtime role still does not have USAGE, CREATE on schema public after bootstrap repair. "
                        "Use a PostgreSQL owner/superuser bootstrap DSN, or grant those rights to the runtime role."
                    )
            return {
                "dsn": target_dsn,
                "created": False,
                "recreated": False,
                "identity": ident,
                "schema_permissions": perms,
                "used_bootstrap_dsn": used_bootstrap,
            }

    probe = psycopg2.connect(target_dsn)
    try:
        ident = db_identity(probe)
        perms = _runtime_schema_permissions(probe)
    finally:
        probe.close()
    if repair_permissions and not (perms.get("usage") and perms.get("create")):
        if bootstrap_dsn:
            grant_info = _grant_runtime_database_access(target_dsn, bootstrap_dsn=str(bootstrap_dsn))
            ident = grant_info["identity"]
            perms = grant_info["schema_permissions"]
            used_bootstrap = True
        if not (perms.get("usage") and perms.get("create")):
            raise RuntimeError(
                "Connected to PostgreSQL, but the runtime role still does not have USAGE, CREATE on schema public. Use a PostgreSQL owner/superuser bootstrap DSN, or grant those rights to the runtime role."
            )
    return {
        "dsn": target_dsn,
        "created": (created_db and not recreate),
        "recreated": recreate,
        "identity": ident,
        "schema_permissions": perms,
        "used_bootstrap_dsn": used_bootstrap,
    }
