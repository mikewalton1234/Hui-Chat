#!/usr/bin/env python3
"""Static checks for beta.378 PM/group typing indicators."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_BETA = 378

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)

def require(text: str, token: str, rel: str) -> None:
    if token not in text:
        fail(f"{rel} missing {token!r}")

def beta_number(version: str) -> int:
    match = re.search(r"beta\.(\d+)", version)
    if not match:
        fail(f"VERSION.txt has unexpected beta version: {version!r}")
    return int(match.group(1))

version = read("VERSION.txt").strip()
if beta_number(version) < MIN_BETA:
    fail(f"VERSION.txt is {version!r}, expected beta.{MIN_BETA} or newer")

rooms = read("realtime/rooms.py")
dm = read("realtime/dm.py")
groups = read("realtime/groups.py")
room_js = read("static/js/chat_parts/0041_rooms_runtime.js")
convo_js = read("static/js/chat_parts/0043_group_history_dm_windows.js")
pm_js = read("static/js/chat_parts/0045_transfers_crypto.js")
css = read("static/css/chat.css")
routes_auth = read("routes_auth.py")
admin_routes = read("routes_admin_tools.py")
admin_js = read("admin_panel_inject.py")
setup = read("interactive_setup.py")
server_example = read("server_config.example.json")
notes = read("TYPING_INDICATORS_PM_GROUP_NOTES.md")
checklist = read("Hui-Chat_Front-End_UI_Audit_Checklist_beta378.md")
readme = read("README.md")

for token in [
    '@socketio.on("direct_typing")',
    '@socketio.on("direct_stop_typing")',
    'enable_dm_typing_indicators',
    'dm_typing_rate_limit',
    '_either_blocked(sender, to)',
    '_emit_to_user(to, "direct_typing"',
    '_emit_to_user(to, "direct_stop_typing"',
]:
    require(dm, token, "realtime/dm.py")

for token in [
    '@socketio.on("group_typing")',
    '@socketio.on("group_stop_typing")',
    'enable_group_typing_indicators',
    'group_typing_rate_limit',
    '_is_group_member(group_id, user_id)',
    '_is_group_muted(group_id, sender)',
    '_emit_group_typing_block_aware',
]:
    require(groups, token, "realtime/groups.py")

for token in [
    'enable_room_typing_indicators',
    'return {"success": True, "room": room, "typing": False, "disabled": True}',
]:
    require(rooms, token, "realtime/rooms.py")

for token in [
    'function ecRoomTypingIndicatorsEnabled()',
    'ecConfigBool(cfg.enable_room_typing_indicators, false)',
    'if (!input || !ecRoomTypingIndicatorsEnabled()) return;',
]:
    require(room_js, token, "static/js/chat_parts/0041_rooms_runtime.js")

for token in [
    'const EC_CONVO_TYPING_TIMEOUT_MS',
    'function ecTypingFeatureEnabled',
    'function ecEnsureConversationTypingIndicator',
    'function ecBindDirectTypingInput',
    'function ecBindGroupTypingInput',
    "socket.on('direct_typing'",
    "socket.on('group_typing'",
    "ecTypingEmit('direct_typing'",
    "ecTypingEmit('group_typing'",
    "ecConversationTypingStop(input, { force: true })",
]:
    require(convo_js, token, "static/js/chat_parts/0043_group_history_dm_windows.js")

require(pm_js, "ecSetConversationTyping('pm', senderName, senderName, false, 0)", "static/js/chat_parts/0045_transfers_crypto.js")

for token in [
    '.ymTypingIndicator',
    '.ecRoomTypingIndicator',
    'Room typing remains disabled by default',
]:
    require(css, token, "static/css/chat.css")

for token in [
    '"enable_room_typing_indicators": _client_bool_setting("enable_room_typing_indicators", False)',
    '"enable_dm_typing_indicators": _client_bool_setting("enable_dm_typing_indicators", True)',
    '"enable_group_typing_indicators": _client_bool_setting("enable_group_typing_indicators", True)',
]:
    require(routes_auth, token, "routes_auth.py")

for token in [
    '"enable_room_typing_indicators": "bool"',
    '"enable_dm_typing_indicators": "bool"',
    '"enable_group_typing_indicators": "bool"',
    '"dm_typing_rate_limit": "str"',
    '"group_typing_rate_limit": "str"',
]:
    require(admin_routes, token, "routes_admin_tools.py")

for token in [
    'anti_enable_room_typing_indicators',
    'anti_enable_dm_typing_indicators',
    'anti_enable_group_typing_indicators',
    'anti_dm_typing_rate_limit',
    'anti_group_typing_rate_limit',
]:
    require(admin_js, token, "admin_panel_inject.py")

for token in [
    '"enable_room_typing_indicators": False',
    '"enable_dm_typing_indicators": True',
    '"enable_group_typing_indicators": True',
    '"dm_typing_rate_limit": "30@10"',
    '"group_typing_rate_limit": "30@10"',
]:
    require(setup, token, "interactive_setup.py")

for token in [
    '"enable_room_typing_indicators": false',
    '"enable_dm_typing_indicators": true',
    '"enable_group_typing_indicators": true',
    '"dm_typing_rate_limit": "30@10"',
    '"group_typing_rate_limit": "30@10"',
]:
    require(server_example, token, "server_config.example.json")

for token in [
    'Version: **0.11.0-beta.378**',
    'Room chat typing is intentionally off by default',
    'direct_typing',
    'group_typing',
]:
    require(notes, token, "TYPING_INDICATORS_PM_GROUP_NOTES.md")

for token in [
    'Current version: **0.11.0-beta.378**',
    'Typing indicators — Private messages and group messages',
    'UI12 — Final front-end release smoke and handoff',
    'Hui-Chat-v0.11.0-beta.378-typing-indicators-pm-group.zip',
]:
    require(checklist, token, "Hui-Chat_Front-End_UI_Audit_Checklist_beta378.md")

require(readme, 'Typing indicators beta.378', 'README.md')
print('PASS: beta.378 PM/group typing indicator static checks passed')
