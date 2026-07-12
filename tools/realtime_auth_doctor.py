#!/usr/bin/env python3
"""Static Socket.IO authorization checks for Hui Chat.

This doctor intentionally does not connect to PostgreSQL. It verifies the most
important realtime security invariants for the split realtime/*.py handler
modules:

  - every Socket.IO event except disconnect is JWT-gated;
  - every authenticated event performs live session/IP/account validation;
  - every authenticated non-connect event runs the central payload/rate guard;
  - legacy mutating admin Socket.IO events remain disabled in favor of HTTP
    routes with CSRF/recent-admin-auth;
  - high-risk realtime flows have membership, permission, block, canonical
    username, or sanction checks where the server should never trust the client.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REALTIME_DIR = ROOT / "realtime"
FILES = [
    REALTIME_DIR / "admin.py",
    REALTIME_DIR / "dm.py",
    REALTIME_DIR / "files.py",
    REALTIME_DIR / "groups.py",
    REALTIME_DIR / "presence_social.py",
    REALTIME_DIR / "rooms.py",
    REALTIME_DIR / "voice.py",
]

ALLOWED_NO_JWT = {"disconnect"}
ALLOWED_NO_EVENT_GUARD = {"connect", "disconnect"}
LEGACY_ADMIN_WRITE_EVENTS = {
    "purge_user",
    "update_user_role",
    "set_message_expiry",
    "delete_all_messages",
    "clear_room",
    "lock_room",
    "set_room_readonly",
    "slowmode_toggle",
}
ROOM_MEMBER_EVENTS = {
    "get_room_media_state",
    "room_media_presence",
    "room_media_set_source",
    "room_media_vote_skip",
    "get_join_state",
    "get_room_history",
    "leave",
    "send_message",
    "get_users_in_room",
    "typing",
    "stop_typing",
    "react_to_message",
    "room_kick_user",
    "pin_message",
    "unpin_message",
    "wave_user",
    "vote_poll",
    "get_active_polls",
}
GROUP_MEMBER_EVENTS = {
    "group_message",
    "join_group_chat",
    "get_group_history",
    "mark_group_read",
    "get_group_members",
}
DM_BLOCK_EVENTS = {
    "send_direct_message",
    "p2p_file_offer",
    "p2p_file_answer",
    "p2p_file_ice",
    "voice_dm_invite",
    "voice_dm_accept",
    "voice_dm_offer",
    "voice_dm_answer",
    "voice_dm_ice",
}
VOICE_DM_EVENTS = {
    "voice_dm_invite",
    "voice_dm_accept",
    "voice_dm_decline",
    "voice_dm_end",
    "voice_dm_offer",
    "voice_dm_answer",
    "voice_dm_ice",
}
WEBCAM_TARGET_EVENTS = {
    "webcam_view_request",
    "webcam_view_response",
    "webcam_view_kick",
    "webcam_viewing",
}
SEND_LIKE_EVENTS = {
    "send_direct_message",
    "group_message",
    "send_message",
    "share_image",
    "room_media_set_source",
    "room_media_vote_skip",
    "typing",
    "react_to_message",
}


@dataclass(frozen=True)
class EventHandler:
    file: Path
    event: str
    function: str
    line: int
    decorators: tuple[str, ...]
    body: str


def _decorator_text(dec: ast.AST) -> str:
    try:
        return ast.unparse(dec)
    except Exception:
        return ""


def _socket_event_name(dec: ast.AST) -> str | None:
    text = _decorator_text(dec)
    if not text.startswith("socketio.on"):
        return None
    try:
        if isinstance(dec, ast.Call) and dec.args:
            arg = dec.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
    except Exception:
        pass
    return None


def parse_handlers() -> list[EventHandler]:
    handlers: list[EventHandler] = []
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            events = [_socket_event_name(dec) for dec in node.decorator_list]
            events = [event for event in events if event]
            if not events:
                continue
            decs = tuple(_decorator_text(dec) for dec in node.decorator_list)
            body = ast.get_source_segment(text, node) or ""
            for event in events:
                handlers.append(EventHandler(path, event, node.name, node.lineno, decs, body))
    return sorted(handlers, key=lambda h: (str(h.file), h.line, h.event))


def main() -> int:
    failures: list[str] = []
    handlers = parse_handlers()
    by_event = {h.event: h for h in handlers}

    for h in handlers:
        dec_blob = "\n".join(h.decorators)
        label = f"{h.file.relative_to(ROOT)}:{h.line} {h.event}/{h.function}"
        if h.event not in ALLOWED_NO_JWT and "jwt_required" not in dec_blob:
            failures.append(f"missing jwt_required: {label}")
        if h.event not in ALLOWED_NO_JWT and "_reject_if_stale_socket_session" not in h.body:
            failures.append(f"missing live session/IP/account gate: {label}")
        if h.event not in ALLOWED_NO_EVENT_GUARD and "_socket_event_guard" not in h.body:
            failures.append(f"missing central payload/rate guard: {label}")

    for event in LEGACY_ADMIN_WRITE_EVENTS:
        h = by_event.get(event)
        if not h:
            failures.append(f"missing legacy admin event registration: {event}")
            continue
        if "_disabled_socket_admin_write" not in h.body and "socket_admin_action_disabled" not in h.body:
            failures.append(f"legacy admin write not disabled: {event}")

    for event in ROOM_MEMBER_EVENTS:
        h = by_event.get(event)
        if not h:
            failures.append(f"missing room event handler: {event}")
            continue
        room_gate = any(token in h.body for token in [
            "_require_live_room_membership",
            "get_connected_room(request.sid) != room",
            "get_connected_room(sid) != room",
            "current_room != room",
            "current_room = _canonical_room_name",
        ])
        private_gate = "_enforce_private_room_access" in h.body or "_require_live_room_membership" in h.body or event == "leave"
        if not room_gate:
            failures.append(f"room event missing live room-membership gate: {event}")
        if not private_gate:
            failures.append(f"room event missing private-room access recheck: {event}")

    for event in GROUP_MEMBER_EVENTS:
        h = by_event.get(event)
        if not h:
            failures.append(f"missing group event handler: {event}")
            continue
        if "_is_group_member" not in h.body:
            failures.append(f"group event missing DB membership gate: {event}")

    for event in DM_BLOCK_EVENTS:
        h = by_event.get(event)
        if not h:
            failures.append(f"missing DM/voice/P2P event handler: {event}")
            continue
        if "_either_blocked" not in h.body:
            failures.append(f"DM-like event missing block check: {event}")

    for event in VOICE_DM_EVENTS:
        h = by_event.get(event)
        if not h:
            continue
        if "_resolve_canonical_username" not in h.body:
            failures.append(f"voice DM event missing canonical peer resolution: {event}")
        if event in {"voice_dm_offer", "voice_dm_answer", "voice_dm_ice"} and "_voice_dm_require_active" not in h.body:
            failures.append(f"voice DM signal missing active-call authorization: {event}")

    for event in WEBCAM_TARGET_EVENTS:
        h = by_event.get(event)
        if not h:
            failures.append(f"missing webcam target event handler: {event}")
            continue
        if "_webcam_require_target_in_room" not in h.body:
            failures.append(f"webcam event missing live target-room check: {event}")

    for event in SEND_LIKE_EVENTS:
        h = by_event.get(event)
        if not h:
            continue
        if "_require_not_sanctioned" not in h.body:
            failures.append(f"send-like event missing sanction gate: {event}")

    voice = (ROOT / "realtime" / "voice.py").read_text(encoding="utf-8")
    if "return False" not in voice[voice.find("def _webcam_room_member"):voice.find("# Hui webcam webcam view request controls")]:
        failures.append("webcam target-room membership helper must fail closed")

    rooms = (ROOT / "realtime" / "rooms.py").read_text(encoding="utf-8")
    if "Room chat is live-only by design. Do not read the shared `messages`" not in rooms:
        failures.append("get_room_history no-room-history policy comment/check missing")
    if "_live_room_message_meta" not in rooms or "Message not found in this room" not in rooms:
        failures.append("room reactions/pins must bind to live room message metadata")

    if failures:
        print("❌ Realtime auth doctor failed")
        for item in failures:
            print(f" - {item}")
        return 1

    print("✅ Realtime auth doctor passed")
    print(f"   Socket.IO handlers checked: {len(handlers)}")
    print("   checks: jwt, live-session/IP/account gates, payload/rate guards, room/group membership, DM blocks, webcam target-room checks, voice-DM canonicalization, send-like sanctions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
