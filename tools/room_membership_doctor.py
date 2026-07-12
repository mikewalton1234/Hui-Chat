#!/usr/bin/env python3
"""Static room catalog/custom-room/invite membership checks for Hui Chat.

This doctor verifies S13 invariants without a live database:

  - HTTP room/custom-room writes have moderation/sanction gates;
  - custom-room name collision checks are case-insensitive;
  - private custom-room invites require room owner/moderator authority;
  - generic room invite list/accept/decline are case-insensitive, block-aware,
    and do not consume private custom-room grant rows;
  - Socket.IO /invite uses the same private-room owner/moderator rule;
  - generic room invite cleanup is case-insensitive.
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
    routes_chat = _read("routes_chat.py")
    db_rooms = _read("db/rooms.py")
    rt_rooms = _read("realtime/rooms.py")

    denial = _function_body("routes_chat.py", "_room_action_denial")
    if not denial:
        failures.append("missing HTTP room action sanction helper")
    else:
        for token in ['is_user_sanctioned(user, "ban")', 'is_user_sanctioned(user, "mute")', 'is_user_sanctioned(user, "kick")']:
            if token not in denial:
                failures.append(f"room action sanction helper missing {token}")
        if 'return {"error": "Moderation status unavailable"}, 503' not in denial:
            failures.append("room action sanction helper must fail closed when moderation lookup fails")

    create_custom = _function_body("routes_chat.py", "api_create_custom_room")
    if '_room_action_denial(actor, "create")' not in create_custom:
        failures.append("custom room creation must use room action sanction gate")
    if "WHERE LOWER(name)=LOWER(%s)" not in create_custom:
        failures.append("custom room creation must detect case-insensitive name collisions")
    if "existing_chat_name != name" not in create_custom:
        failures.append("custom room creation must block different-case duplicate room names")

    invite_custom = _function_body("routes_chat.py", "api_invite_to_custom_room")
    if '_room_action_denial(actor, "invite")' not in invite_custom:
        failures.append("custom-room invite route must use room action sanction gate")
    if "can_user_moderate_custom_room(canonical_room, actor)" not in invite_custom:
        failures.append("private custom-room invites must require room owner/moderator authority")
    if "_either_blocked(actor, invitee)" not in invite_custom:
        failures.append("custom-room invites must remain block-aware")

    revoke_member = _function_body("routes_chat.py", "api_revoke_custom_room_member")
    if '_room_action_denial(actor, "member_manage")' not in revoke_member:
        failures.append("custom room durable member revoke must use sanction gate")

    list_custom_invites = _function_body("routes_chat.py", "api_list_custom_room_invites")
    if "LOWER(i.invited_user) = LOWER(%s)" not in list_custom_invites:
        failures.append("custom-room invite list must be case-insensitive by invitee")
    if "FROM blocks b" not in list_custom_invites:
        failures.append("custom-room invite list must filter blocked inviters")

    accept_custom = _function_body("routes_chat.py", "api_accept_custom_room_invite")
    if '_room_action_denial(actor, "accept_invite")' not in accept_custom:
        failures.append("custom-room invite accept must use ban/kick sanction gate")
    if "DELETE FROM custom_room_invites i" not in accept_custom or "LOWER(i.invited_user)=LOWER(%s)" not in accept_custom:
        failures.append("custom-room invite accept must consume pending invite case-insensitively")

    any_invite = _function_body("routes_chat.py", "api_invite_to_room_any")
    if '_room_action_denial(actor, "invite")' not in any_invite:
        failures.append("generic room invite helper must use room action sanction gate")
    if "can_user_moderate_custom_room(canonical_room, actor)" not in any_invite:
        failures.append("generic invite helper must enforce private-room owner/moderator authority")
    if "_either_blocked(actor, invitee)" not in any_invite:
        failures.append("generic room invite helper must remain block-aware")

    list_generic = _function_body("routes_chat.py", "api_list_room_invites")
    for token in ["JOIN chat_rooms r ON LOWER(r.name)=LOWER(i.room_name)", "LEFT JOIN custom_rooms cr", "LOWER(i.invited_user) = LOWER(%s)", "FROM blocks b", "cr.is_private = FALSE"]:
        if token not in list_generic:
            failures.append(f"generic room invite list missing {token}")

    delete_generic = _function_body("routes_chat.py", "_delete_generic_room_invite_casefold")
    for token in ["DELETE FROM room_invites i", "LOWER(i.room_name)=LOWER(r.name)", "LOWER(i.invited_user)=LOWER(%s)", "cr.is_private = FALSE"]:
        if token not in delete_generic:
            failures.append(f"generic room invite casefold delete missing {token}")

    accept_generic = _function_body("routes_chat.py", "api_accept_room_invite")
    if '_room_action_denial(actor, "accept_invite")' not in accept_generic:
        failures.append("generic room invite accept must use ban/kick sanction gate")
    if "_delete_generic_room_invite_casefold" not in accept_generic:
        failures.append("generic room invite accept must use centralized casefold/private-safe delete")
    if "_either_blocked(actor, invited_by)" not in accept_generic:
        failures.append("generic room invite accept must reject blocked legacy invites")

    decline_generic = _function_body("routes_chat.py", "api_decline_room_invite")
    if "_delete_generic_room_invite_casefold" not in decline_generic:
        failures.append("generic room invite decline must use centralized casefold/private-safe delete")

    if "DELETE FROM room_invites\n                 WHERE LOWER(room_name)=LOWER(%s)" not in db_rooms:
        failures.append("consume_room_invites must clear generic room invites case-insensitively")

    socket_send = _function_body("realtime/rooms.py", "handle_send_message")
    if "can_user_moderate_custom_room(canonical_room, username)" not in socket_send:
        failures.append("Socket.IO /invite must require private-room owner/moderator authority")
    if "Only the room owner or a room moderator" not in socket_send:
        failures.append("Socket.IO /invite must return a clear private-room invite authority error")

    if failures:
        print("❌ Room membership doctor failed")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("✅ Room membership doctor passed")
    print("   checks: custom-room sanctions, casefold room names, private invite authority, block-aware/casefold invite lifecycle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
