#!/usr/bin/env python3
"""Static admin user/account tooling checks for Hui Chat.

This S15 doctor verifies account-management invariants without a live database:

  - admin account routes resolve target usernames to the stored canonical spelling;
  - high-risk account actions keep the privileged-target guard;
  - self-target destructive account actions remain blocked;
  - account deletion cleans username-owned rows case-insensitively, including
    custom-room grants/owned-room state and auth session/token rows;
  - password/2FA/role/session-changing actions revoke live auth state.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _function_body(rel: str, name: str) -> str:
    text = _read(rel)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    return ""


def main() -> int:
    failures: list[str] = []
    routes = _read("routes_admin_tools.py")

    if "canonical_username" not in routes:
        failures.append("routes_admin_tools must import/use canonical_username for admin target resolution")

    resolver = _function_body("routes_admin_tools.py", "_canonical_user_or_error")
    if not resolver:
        failures.append("missing canonical admin user resolver")
    else:
        for token in [
            "canonical_username(conn, raw)",
            "SELECT id FROM users WHERE username = %s LIMIT 1",
            '"User not found"',
            '"resolve_user"',
            "Invalid username",
        ]:
            if token not in resolver:
                failures.append(f"canonical admin user resolver missing {token}")

    canonicalized_routes = [
        "admin_user_detail",
        "admin_user_activity_timeline",
        "admin_set_recovery_pin",
        "delete_user",
        "suspend_user",
        "deactivate_user",
        "force_logout",
        "admin_reset_password",
        "view_logins",
        "assign_role",
        "mute_user_admin",
        "kick_from_room",
        "ban_from_room",
        "shadowban_user",
        "_clear_user_sanctions_response",
        "unban_from_room",
        "revoke_2fa",
        "set_user_quota",
        "set_user_status",
        "list_user_permissions",
        "list_user_roles",
        "remove_role_from_user",
        "explain_permission",
    ]
    for fn in canonicalized_routes:
        body = _function_body("routes_admin_tools.py", fn)
        if not body:
            failures.append(f"missing account/admin route helper {fn}")
        elif "_canonical_user_or_error" not in body:
            failures.append(f"{fn} must canonicalize target usernames before acting")

    guarded_routes = [
        "admin_set_recovery_pin",
        "delete_user",
        "suspend_user",
        "deactivate_user",
        "force_logout",
        "admin_reset_password",
        "assign_role",
        "mute_user_admin",
        "kick_from_room",
        "ban_from_room",
        "shadowban_user",
        "_clear_user_sanctions_response",
        "unban_from_room",
        "revoke_2fa",
        "set_user_quota",
        "set_user_status",
    ]
    for fn in guarded_routes:
        body = _function_body("routes_admin_tools.py", fn)
        if body and "_deny_privileged_target_without_admin" not in body:
            failures.append(f"{fn} must protect privileged/admin targets")

    self_guarded = [
        "admin_set_recovery_pin",
        "delete_user",
        "suspend_user",
        "deactivate_user",
        "force_logout",
        "admin_reset_password",
        "assign_role",
        "mute_user_admin",
        "kick_from_room",
        "ban_from_room",
        "shadowban_user",
        "_clear_user_sanctions_response",
        "unban_from_room",
        "revoke_2fa",
        "set_user_quota",
        "set_user_status",
        "remove_role_from_user",
    ]
    for fn in self_guarded:
        body = _function_body("routes_admin_tools.py", fn)
        if body and "_is_self_target(username)" not in body:
            failures.append(f"{fn} must block self-target admin account actions")

    delete_body = _function_body("routes_admin_tools.py", "delete_user")
    for token in [
        "LOWER(sender) = LOWER(%s) OR LOWER(receiver) = LOWER(%s)",
        "offline_messages",
        "pending_messages",
        "dm_files",
        "message_reactions",
        "message_reads",
        "LOWER(from_user) = LOWER(%s) OR LOWER(to_user) = LOWER(%s)",
        "LOWER(blocker) = LOWER(%s) OR LOWER(blocked) = LOWER(%s)",
        "group_files",
        "custom_rooms WHERE LOWER(created_by) = LOWER(%s)",
        "delete_custom_room_persisted_state(cur, owned_custom_rooms)",
        "custom_room_invites",
        "custom_room_members",
        "room_invites",
        "profile_post_reports",
        "profile_post_comments",
        "profile_post_reactions",
        "profile_posts",
        "user_profile_badges",
        "user_profile_notification_settings",
        "user_recent_rooms",
        "user_quotas",
        "password_reset_tokens",
        "DELETE FROM auth_tokens WHERE LOWER(username) = LOWER(%s)",
        "DELETE FROM auth_sessions WHERE LOWER(username) = LOWER(%s)",
        "owner_account_deleted",
        "revoke_all_sessions_and_tokens_for_user(username, reason=\"admin_delete_user\")",
    ]:
        if token not in delete_body:
            failures.append(f"delete_user missing case-insensitive related-data cleanup: {token}")

    reset_body = _function_body("routes_admin_tools.py", "admin_reset_password")
    for token in [
        "generate_user_keypair_for_password(new_pw)",
        "auth_version = COALESCE(auth_version, 0) + 1",
        "UPDATE auth_sessions",
        "UPDATE auth_tokens",
        "_disconnect_user(username)",
    ]:
        if token not in reset_body:
            failures.append(f"admin_reset_password missing auth/E2EE reset invariant: {token}")

    revoke_2fa_body = _function_body("routes_admin_tools.py", "revoke_2fa")
    for token in [
        "two_factor_secret = NULL",
        "auth_version = COALESCE(auth_version, 0) + 1",
        "UPDATE auth_sessions",
        "UPDATE auth_tokens",
        "_disconnect_user(username)",
        "force_logout",
        "revoked_sessions",
        "revoked_tokens",
    ]:
        if token not in revoke_2fa_body:
            failures.append(f"revoke_2fa missing revocation invariant: {token}")

    quota_body = _function_body("routes_admin_tools.py", "set_user_quota")
    for token in [
        "DELETE FROM user_quotas WHERE LOWER(username) = LOWER(%s) AND username <> %s",
        "ON CONFLICT (username) DO UPDATE",
    ]:
        if token not in quota_body:
            failures.append(f"set_user_quota missing canonical quota collapse invariant: {token}")

    for fn in ["admin_set_recovery_pin", "delete_user", "suspend_user", "deactivate_user", "force_logout", "admin_reset_password", "revoke_2fa", "set_user_quota", "set_user_status", "assign_role", "remove_role_from_user"]:
        body = _function_body("routes_admin_tools.py", fn)
        if body and "@require_recent_admin_auth" not in routes[routes.find(f"def {fn}")-220:routes.find(f"def {fn}")]:
            failures.append(f"{fn} must require recent admin reauth")

    if failures:
        print("❌ Admin account tooling doctor failed")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("✅ Admin account tooling doctor passed")
    print("   checks: canonical account targets, privileged/self guards, deep deletion cleanup, password/2FA/session revocation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
