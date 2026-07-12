#!/usr/bin/env python3
"""Hui Chat password reset doctor.

Diagnoses why /forgot-password did not send a reset email even when SMTP itself
works. It checks the exact account lookup rules used by the route, duplicate
email ambiguity, active reset-token budget, public_base_url, and effective SMTP
configuration. It can also clear outstanding active reset tokens for the matched
account so the next /forgot-password request sends one fresh link.

Examples:
  source .venv/bin/activate
  python tools/password_reset_doctor.py --config server_config.json --email you@example.com
  python tools/password_reset_doctor.py --config server_config.json --email you@example.com --username youruser
  python tools/password_reset_doctor.py --config server_config.json --email you@example.com --username youruser --clear-active
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import psycopg2  # type: ignore
except Exception as exc:  # pragma: no cover - operator environment diagnostic
    print(f"❌ Could not import psycopg2: {exc}")
    print("   Activate your virtualenv and install project requirements first.")
    raise SystemExit(2)

from constants import get_db_connection_string, redact_postgres_dsn, server_display_name  # noqa: E402
from emailer import describe_smtp_settings  # noqa: E402


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return obj


def overlay_env(settings: dict[str, Any]) -> dict[str, Any]:
    out = dict(settings)
    db = os.getenv("DB_CONNECTION_STRING") or os.getenv("DATABASE_URL")
    if db:
        out["database_url"] = db
    public_base_url = os.getenv("HUI_PUBLIC_BASE_URL") or os.getenv("PUBLIC_BASE_URL")
    if public_base_url:
        out["public_base_url"] = public_base_url
    return out


def valid_public_base_url(value: str) -> bool:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return False
    parsed = urlparse(raw)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def print_smtp(settings: dict[str, Any]) -> None:
    d = describe_smtp_settings(settings)
    print("\nEffective SMTP config used by password reset (secrets redacted):")
    for key in ["enabled", "provider", "host", "port", "starttls", "use_ssl", "username_set", "password_set", "from_email", "timeout"]:
        print(f"  {key}: {d.get(key)}")
    if d.get("from_warning"):
        print(f"  from_warning: {d['from_warning']}")
    if d.get("mode_hint"):
        print(f"  hint: {d['mode_hint']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose this server's forgot-password account/token/email conditions.")
    ap.add_argument("--config", default="server_config.json", help="Path to server_config.json")
    ap.add_argument("--email", required=True, help="Email submitted on /forgot-password")
    ap.add_argument("--username", default="", help="Optional username hint submitted on /forgot-password")
    ap.add_argument("--clear-active", action="store_true", help="Delete outstanding active reset tokens for the matched account(s)")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    try:
        settings = overlay_env(load_json(cfg_path))
    except Exception as exc:
        print(f"❌ Could not read {cfg_path}: {exc}")
        return 2

    email = str(args.email or "").strip().lower()
    username_hint = str(args.username or "").strip().lower()
    if not email:
        print("❌ --email is required")
        return 2

    print(f"Password reset doctor for {server_display_name(settings)}")
    print_smtp(settings)

    public_base_url = str(settings.get("public_base_url") or "").strip().rstrip("/")
    print("\nPassword reset link base URL:")
    if valid_public_base_url(public_base_url):
        print(f"  ✅ public_base_url: {public_base_url}")
    else:
        print("  ⚠️  public_base_url is missing/invalid.")
        print("     Localhost/LAN browser requests can derive this automatically, but public deployments need it set.")

    dsn = get_db_connection_string(settings)
    print("\nDatabase:")
    print(f"  DSN: {redact_postgres_dsn(dsn)}")
    if not dsn:
        print("  ❌ No database_url / DB_CONNECTION_STRING found.")
        return 2

    try:
        conn = psycopg2.connect(dsn)
    except Exception as exc:
        print(f"  ❌ Could not connect to database: {type(exc).__name__}: {exc}")
        return 2

    try:
        with conn, conn.cursor() as cur:
            print("\nAccount lookup, using the same rules as /forgot-password:")
            if username_hint:
                cur.execute(
                    """
                    SELECT username, email, recovery_pin_hash IS NOT NULL AS has_pin
                      FROM users
                     WHERE LOWER(username) = LOWER(%s);
                    """,
                    (username_hint,),
                )
                row = cur.fetchone()
                if not row:
                    print(f"  ❌ No user exists with username={username_hint!r}.")
                    return 1
                username, db_email, has_pin = row
                if str(db_email or "").strip().lower() != email:
                    print(f"  ❌ Username exists, but its email is {db_email!r}, not {email!r}.")
                    return 1
                matched = [(username, db_email, has_pin)]
                print(f"  ✅ Matched username+email: username={username} email={db_email} recovery_pin_set={bool(has_pin)}")
            else:
                cur.execute(
                    """
                    SELECT username, email, recovery_pin_hash IS NOT NULL AS has_pin
                      FROM users
                     WHERE LOWER(email) = %s
                     ORDER BY username;
                    """,
                    (email,),
                )
                matched = list(cur.fetchall() or [])
                if not matched:
                    print(f"  ❌ No account has email={email!r}. The route will not send an email.")
                    return 1
                if len(matched) > 1:
                    print(f"  ❌ {len(matched)} accounts share email={email!r}. The route requires --username / username field.")
                    for username, db_email, has_pin in matched:
                        print(f"     - username={username} email={db_email} recovery_pin_set={bool(has_pin)}")
                    return 1
                username, db_email, has_pin = matched[0]
                print(f"  ✅ Matched unique email: username={username} email={db_email} recovery_pin_set={bool(has_pin)}")

            print("\nActive reset-token budget:")
            for username, db_email, has_pin in matched:
                cur.execute(
                    """
                    SELECT COUNT(*)
                      FROM password_reset_tokens
                     WHERE LOWER(username) = LOWER(%s)
                       AND created_at > (CURRENT_TIMESTAMP - INTERVAL '15 minutes')
                       AND used_at IS NULL;
                    """,
                    (username,),
                )
                active_count = int((cur.fetchone() or [0])[0])
                print(f"  username={username}: {active_count} active unused reset token(s) in last 15 minutes")
                if active_count >= 3 and not args.clear_active:
                    print("  ⚠️  This can stop the route from sending another email until old tokens expire.")
                    print("     Re-run with --clear-active, or wait 15 minutes, then submit forgot password again.")

            if args.clear_active:
                total = 0
                for username, _db_email, _has_pin in matched:
                    cur.execute(
                        """
                        DELETE FROM password_reset_tokens
                         WHERE LOWER(username) = LOWER(%s)
                           AND used_at IS NULL;
                        """,
                        (username,),
                    )
                    total += int(getattr(cur, "rowcount", 0) or 0)
                print(f"\n✅ Cleared {total} outstanding active reset token(s). Submit /forgot-password again now.")

    finally:
        conn.close()

    print("\nResult:")
    print("  If SMTP doctor sends but /forgot-password still does not, restart the chat server and watch the server log for:")
    print("  'Password reset email accepted by SMTP' or 'Password reset email not sent'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
