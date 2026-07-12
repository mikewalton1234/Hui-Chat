#!/usr/bin/env python3
"""Static friends/blocks/notifications/presence backend checks for Hui Chat.

This doctor verifies S12 invariants without requiring a live DB:

  - social/presence write events use ban/mute-aware gates;
  - profile lookup canonicalizes target usernames and uses case-insensitive friendship checks;
  - block cleanup drops live P2P/voice sessions and refreshes stale social surfaces;
  - unblock/reject refresh both affected users' pending/friend-request state;
  - profile notification routes are user-scoped, block-aware, and return unread counts after read updates.
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
    presence_py = _read("realtime/presence_social.py")
    main_py = _read("routes_main.py")

    denial = _function_body("realtime/presence_social.py", "_social_action_denial")
    if not denial:
        failures.append("missing centralized social/presence sanction helper")
    else:
        for token in ['is_user_sanctioned(username, "ban")', 'is_user_sanctioned(username, "mute")']:
            if token not in denial:
                failures.append(f"social sanction helper missing {token}")
        for token in ['"friend_request"', '"accept_friend"', '"presence"']:
            if token not in denial:
                failures.append(f"social sanction helper missing muted action {token}")

    required_social_gates = {
        "handle_send_friend_request": 'action="friend_request"',
        "handle_accept_friend_request": 'action="accept_friend"',
        "handle_set_my_presence": 'action="presence"',
        "handle_remove_friend": 'action="remove_friend"',
        "handle_block_user": 'action="block"',
        "handle_unblock_user": 'action="unblock"',
        "handle_reject_friend_request": 'action="reject_friend"',
    }
    for fn, token in required_social_gates.items():
        body = _function_body("realtime/presence_social.py", fn)
        if "_social_action_denial" not in body or token not in body:
            failures.append(f"{fn} must use _social_action_denial with {token}")

    profile = _function_body("realtime/presence_social.py", "handle_get_user_profile")
    if not profile:
        failures.append("missing get_user_profile handler")
    else:
        if "target = _resolve_canonical_username(target)" not in profile:
            failures.append("get_user_profile must canonicalize target usernames before lookup")
        if "LOWER(from_user) = LOWER(%s)" not in profile or "LOWER(to_user) = LOWER(%s)" not in profile:
            failures.append("get_user_profile friendship check must be case-insensitive")

    cleanup = _function_body("realtime/presence_social.py", "_cleanup_social_pair_realtime_sessions")
    if not cleanup:
        failures.append("block cleanup must drop live realtime sessions between the blocked pair")
    else:
        for token in ["P2P_FILE_SESSIONS", "VOICE_DM_SESSIONS", "_mark_p2p_transfer_id_closed", "voice_dm_end"]:
            if token not in cleanup:
                failures.append(f"live block cleanup missing {token}")

    block = _function_body("realtime/presence_social.py", "handle_block_user")
    if "_cleanup_social_pair_realtime_sessions" not in block or '"live_cleanup"' not in block:
        failures.append("block_user must drop live P2P/voice sessions and report live_cleanup")
    for token in ["removed_room_invites", "removed_group_invites", "removed_offline_pms", "removed_profile_notifications", "social_alert_cleanup"]:
        if token not in block:
            failures.append(f"block_user cleanup missing {token}")

    if "profile_notification_ids" not in block or "UPDATE notifications SET is_read = TRUE" not in block:
        failures.append("block_user must mark persisted profile-post notifications between the blocked pair as read")

    unblock = _function_body("realtime/presence_social.py", "handle_unblock_user")
    if "_emit_friend_request_state(blocked)" not in unblock:
        failures.append("unblock_user must refresh pending/friend state for the unblocked user too")

    reject = _function_body("realtime/presence_social.py", "handle_reject_friend_request")
    if "_emit_friend_request_state(from_user)" not in reject:
        failures.append("reject_friend_request must refresh requester pending/friend state")

    for fn in ("get_profile_notification_settings", "save_profile_notification_settings", "list_profile_post_notifications", "mark_profile_post_notifications_read"):
        body = _function_body("routes_main.py", fn)
        if not body:
            failures.append(f"missing profile notification route {fn}")
        elif "get_jwt_identity" not in body:
            failures.append(f"{fn} must scope to the current JWT user")

    visible = _function_body("routes_main.py", "_profile_notification_visible_to_user")
    list_notifs = _function_body("routes_main.py", "list_profile_post_notifications")
    if not visible or "_either_blocked" not in visible or "return False" not in visible:
        failures.append("profile notification listing must filter blocked actors and fail closed")
    if list_notifs and "_profile_notification_visible_to_user" not in list_notifs:
        failures.append("profile notification list/unread count must apply blocked-actor filtering")

    migration_text = _read("migrations/m0022_social_block_compatibility.py") if (ROOT / "migrations/m0022_social_block_compatibility.py").exists() else ""
    if "blocked_users" not in migration_text or "INSERT INTO blocks" not in migration_text:
        failures.append("missing legacy blocked_users -> blocks compatibility migration")

    mark_read = _function_body("routes_main.py", "mark_profile_post_notifications_read")
    if mark_read and "n.user_id = u.id" not in mark_read or (mark_read and "n.type LIKE 'profile_post_%%'" not in mark_read):
        failures.append("notification read updates must remain user-scoped and profile-notification scoped")
    if mark_read and '"unread_count"' not in mark_read:
        failures.append("notification read route must return the remaining unread_count")

    if failures:
        print("❌ Social/presence doctor failed")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("✅ Social/presence doctor passed")
    print("   checks: social sanctions, canonical profile lookup, block live-session cleanup, blocked notification filtering, legacy block compatibility")
    return 0


if __name__ == "__main__":
    sys.exit(main())
