#!/usr/bin/env python3
"""Detect case-insensitive username duplicates before migration 0017.

This tool is read-only. It helps admins fix rows that would block the
users_username_lower_unique index.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2

from constants import get_db_connection_string, sanitize_postgres_dsn, redact_postgres_dsn
from main import apply_env_overrides, load_settings


def _load_dsn(config: str) -> str:
    settings = load_settings(Path(config))
    apply_env_overrides(settings)
    return str(sanitize_postgres_dsn(str(settings.get("database_url") or get_db_connection_string(settings))))


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only Hui Chat username casefold duplicate doctor")
    ap.add_argument("--config", default="server_config.json")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    dsn = _load_dsn(args.config)
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.users');")
            if not (cur.fetchone() or [None])[0]:
                print("No public.users table found.")
                return 2
            cur.execute(
                """
                SELECT LOWER(BTRIM(username)) AS username_key,
                       COUNT(*) AS n,
                       ARRAY_AGG(json_build_object('id', id, 'username', username) ORDER BY id) AS users
                  FROM users
                 WHERE username IS NOT NULL AND BTRIM(username) <> ''
                 GROUP BY LOWER(BTRIM(username))
                HAVING COUNT(*) > 1
                 ORDER BY username_key;
                """
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()

    payload = {
        "ok": not rows,
        "database": redact_postgres_dsn(dsn),
        "duplicate_groups": [
            {"username_key": str(row[0]), "count": int(row[1]), "users": row[2] or []}
            for row in rows
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    elif not rows:
        print("✅ No case-insensitive username duplicates found.")
    else:
        print("❌ Case-insensitive username duplicates found. Rename or merge these before migration 0017 can add the unique index.")
        for row in payload["duplicate_groups"]:
            users = ", ".join(f"id={u.get('id')} username={u.get('username')!r}" for u in row["users"])
            print(f"- {row['username_key']}: {users}")
        print("\nThis tool is read-only. Fix the duplicate users intentionally, then re-run migrations.")
    return 0 if not rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
