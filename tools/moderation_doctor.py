#!/usr/bin/env python3
"""Static moderation/sanctions checks for Hui Chat beta builds.

This does not connect to PostgreSQL.  It catches the specific regression class
where active sanctions are queried case-sensitively or where an expired newest
row can hide an older permanent/active sanction.  It also verifies that IP-ban
sanctions are not merely recorded/revoked once, but enforced on future auth and
Socket.IO entry points.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(msg: str) -> None:
    print(f"❌ {msg}")
    raise SystemExit(1)


def require_contains(path: str, needle: str, label: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    if needle not in text:
        fail(f"{label}: missing {needle!r} in {path}")


def main() -> int:
    moderation = (ROOT / "moderation.py").read_text(encoding="utf-8")
    routes = (ROOT / "routes_admin_tools.py").read_text(encoding="utf-8")
    socket_handlers = (ROOT / "socket_handlers.py").read_text(encoding="utf-8")
    routes_auth = (ROOT / "routes_auth.py").read_text(encoding="utf-8")
    account_status = (ROOT / "account_status.py").read_text(encoding="utf-8")
    schema = (ROOT / "db" / "schema.py").read_text(encoding="utf-8")
    migration = (ROOT / "migrations" / "m0021_moderation_sanctions_hardening.py").read_text(encoding="utf-8")

    require_contains("moderation.py", "LOWER(username) = LOWER(%s)", "case-insensitive sanction lookup")
    require_contains("moderation.py", "AND (expires_at IS NULL OR expires_at > NOW())", "active-only sanction lookup")
    require_contains("moderation.py", "ORDER BY created_at DESC, id DESC", "deterministic active sanction ordering")
    require_contains("moderation.py", "def expire_sanctions", "sanction clearing helper")

    if re.search(r"SELECT\s+expires_at\s+FROM\s+user_sanctions[\s\S]{0,260}ORDER BY created_at DESC\s+LIMIT 1", moderation, re.I):
        fail("moderation.py still appears to order newest sanction before filtering active rows")

    require_contains("socket_handlers.py", "LOWER(username) = LOWER(%s)", "Socket.IO sanction detail lookup")
    require_contains("routes_admin_tools.py", "privileged_target_check_failed", "S06 fail-closed privileged target guard")
    require_contains("routes_admin_tools.py", "def unmute_user_admin", "unmute admin endpoint")
    require_contains("routes_admin_tools.py", "def unsuspend_user", "unsuspend admin endpoint")
    require_contains("routes_admin_tools.py", "def unshadowban_user", "unshadowban admin endpoint")
    require_contains("routes_admin_tools.py", "def unban_from_room", "room unban admin endpoint")
    require_contains("routes_admin_tools.py", "def clear_user_sanctions", "role-manager sanction clearing endpoint")

    require_contains("moderation.py", "def is_ip_sanctioned", "persistent IP-ban helper")
    require_contains("moderation.py", "def add_ip_sanction", "IP-ban add helper")
    require_contains("moderation.py", "def expire_ip_sanctions", "IP-ban clear helper")
    require_contains("routes_admin_tools.py", "def unban_ip", "IP unban admin endpoint")
    require_contains("routes_auth.py", "_current_request_ip_banned", "HTTP auth IP-ban gate")
    require_contains("routes_auth.py", "login_ip_ban_blocked", "login IP-ban audit event")
    require_contains("routes_auth.py", "refresh_ip_ban_blocked", "refresh IP-ban audit event")
    require_contains("routes_auth.py", "session_ip_ban_blocked", "active session IP-ban audit event")
    require_contains("socket_handlers.py", "_current_socket_ip_banned", "Socket.IO IP-ban gate")
    require_contains("routes_auth.py", "chat_ip_ban_blocked", "/chat IP-ban shell gate")
    require_contains("routes_auth.py", "register_ip_ban_blocked", "registration IP-ban gate")
    require_contains("routes_auth.py", "username_available_ip_ban_blocked", "username availability IP-ban gate")
    require_contains("routes_auth.py", "forgot_password_ip_ban_blocked", "forgot-password IP-ban gate")
    require_contains("routes_auth.py", "reset_password_ip_ban_blocked", "reset-password IP-ban gate")
    require_contains("routes_admin_tools.py", "disconnected_sockets", "admin IP-ban hard socket disconnect count")
    require_contains("routes_admin_tools.py", "socketio.server.disconnect", "admin IP-ban hard socket disconnect")
    require_contains("socket_handlers.py", "socket_ip_ban_blocked", "Socket.IO IP-ban audit event")
    require_contains("account_status.py", "conn.rollback()", "rollback-safe account-status fallback")

    require_contains("db/schema.py", "idx_user_sanctions_user_type_active", "fresh schema sanction index")
    require_contains("migrations/m0021_moderation_sanctions_hardening.py", "idx_user_sanctions_user_type_active", "migration sanction index")
    require_contains("migrations/m0021_moderation_sanctions_hardening.py", "LOWER(BTRIM(sanction_type))", "migration sanction type normalization")

    print("✅ Moderation sanctions doctor passed")
    print("checks: active sanction ordering, safe clears, IP-ban enforcement, chat/auth form gates, hard socket disconnect, rollback-safe fallback")
    return 0


if __name__ == "__main__":
    sys.exit(main())
