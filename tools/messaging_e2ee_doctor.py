#!/usr/bin/env python3
"""Static messaging persistence/history/E2EE checks for Hui Chat.

This doctor verifies server-side invariants that should hold before live browser
QA:

  - room chat history remains live-only and does not read the shared messages table;
  - group history/read counts/attachment lookup/group deletion use only the
    g:<id> namespace unless a legacy numeric key is explicitly enabled;
  - group history hides plaintext rows when strict E2EE is required;
  - corrupt encrypted group rows are not emitted through the cipher field;
  - offline DM queues do not deliver legacy plaintext rows as ciphertext;
  - offline DM summaries and storage respect strict DM E2EE mode.
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

    main_py = _read("main.py")
    setup_py = _read("interactive_setup.py")
    groups_py = _read("realtime/groups.py")
    routes_groups_py = _read("routes_groups.py")
    dm_py = _read("realtime/dm.py")
    rooms_py = _read("realtime/rooms.py")

    if '"allow_legacy_numeric_group_history": False' not in main_py:
        failures.append("main defaults must disable legacy numeric group history")
    if '"allow_legacy_numeric_group_history": False' not in setup_py:
        failures.append("setup defaults must disable legacy numeric group history")
    if '"disable_legacy_group_file_upload": True' not in main_py:
        failures.append("main defaults must disable legacy group attachment upload")
    if '"disable_legacy_group_file_upload": True' not in setup_py:
        failures.append("setup defaults must disable legacy group attachment upload")
    if "require_group_e2ee" not in routes_groups_py or "disable_legacy_group_file_upload" not in routes_groups_py:
        failures.append("legacy group attachment upload must be disabled under strict group E2EE")

    if "def _group_history_room_values" not in groups_py:
        failures.append("group history must use a scoped room-key helper")
    if "room = ANY(%s)" not in groups_py:
        failures.append("group history queries must use explicit allowed room-key arrays")
    if "(room = %s OR room = %s)" in groups_py or "WHERE (m.room = %s OR m.room = %s)" in groups_py:
        failures.append("group history/unread stats must not always query both g:<id> and bare numeric room keys")
    if "allow_legacy_numeric_group_history" not in groups_py:
        failures.append("legacy numeric group history must be opt-in only")

    group_unread_socket = _function_body("realtime/groups.py", "_group_unread_stats")
    if "_group_history_room_values" not in group_unread_socket or "m.room = ANY(%s)" not in group_unread_socket:
        failures.append("Socket.IO group unread stats must use scoped/opt-in group room keys")

    routes_unread = _function_body("routes_groups.py", "_group_unread_stats")
    if "_group_history_room_values" not in routes_unread or "m.room = ANY(%s)" not in routes_unread:
        failures.append("HTTP group unread counts must use scoped/opt-in group room keys")

    my_groups = _function_body("routes_groups.py", "my_groups")
    if "allow_legacy_numeric_group_history" not in my_groups or "group_unread_room_sql" not in my_groups:
        failures.append("group list unread counts must make legacy numeric keys opt-in")
    if "WHERE (m.room = ('g:' || g.id::text) OR m.room = g.id::text)" in my_groups:
        failures.append("group list unread counts must not unconditionally query bare numeric room keys")

    load_attachment = _function_body("routes_groups.py", "_load_attachment_for_group")
    if "_group_history_room_values" not in load_attachment or "m.room = ANY(%s)" not in load_attachment:
        failures.append("legacy group attachment lookup must use scoped/opt-in group room keys")
    if "AND (m.room = %s OR m.room = %s)" in load_attachment:
        failures.append("legacy group attachment lookup must not always query bare numeric room keys")

    delete_group = _function_body("routes_groups.py", "delete_group")
    if "_group_history_room_values" not in delete_group or "DELETE FROM messages WHERE room = ANY(%s)" not in delete_group:
        failures.append("group deletion must use scoped/opt-in group room keys")
    if "DELETE FROM messages WHERE room = %s OR room = %s" in delete_group:
        failures.append("group deletion must not unconditionally delete bare numeric room messages")

    group_msg = _function_body("realtime/groups.py", "handle_group_message")
    if "require_group_e2ee" not in group_msg or "not cipher" not in group_msg:
        failures.append("group_message must enforce require_group_e2ee")
    if "_is_group_cipher_envelope" not in group_msg:
        failures.append("group_message must validate group cipher envelopes")
    if "INSERT INTO messages" not in group_msg or "True if cipher else False" not in group_msg:
        failures.append("group_message must persist explicit is_encrypted state")

    group_formatter = _function_body("socket_handlers.py", "_format_group_history_rows")
    if "hidden_legacy" not in group_formatter:
        failures.append("group history formatter must hide legacy plaintext when strict E2EE is on")
    if "hidden_invalid_cipher" not in group_formatter or "_looks_like_group_cipher_envelope" not in group_formatter:
        failures.append("group history formatter must validate full ECG1 envelope shape before emitting cipher")

    room_hist = _function_body("realtime/rooms.py", "handle_get_room_history")
    if "_room_history_disabled_payload" not in room_hist:
        failures.append("room history handler must return disabled live-only payload")
    if "FROM messages" in room_hist or "SELECT id, sender, message" in room_hist:
        failures.append("room history handler must not read shared messages table")
    if "Room chat is live-only by design" not in rooms_py:
        failures.append("room live-only history policy comment/check missing")

    fetch_offline = _function_body("realtime/dm.py", "handle_fetch_offline_pms")
    if "_offline_pm_wire_item" not in dm_py:
        failures.append("offline PM fetch must normalize persisted rows through a safe wire helper")
    if "quarantined_legacy" not in fetch_offline or "quarantine_ids" not in fetch_offline:
        failures.append("offline PM fetch must quarantine non-E2EE legacy rows")
    if '"cipher": cipher' in fetch_offline:
        failures.append("offline PM fetch must not blindly return DB message as cipher")

    offline_helper = _function_body("realtime/dm.py", "_offline_pm_wire_item")
    if "_looks_like_dm_cipher_envelope" not in offline_helper:
        failures.append("offline PM helper must validate EC1 envelopes")
    if "legacy_plaintext" not in offline_helper:
        failures.append("offline PM helper must label explicit legacy plaintext fallback")
    if "require_e2ee or not allow_plain" not in offline_helper:
        failures.append("offline PM helper must hide plaintext unless fallback is explicit")

    store_offline = _function_body("socket_handlers.py", "_store_offline_pm")
    if "dropped_non_e2ee" not in store_offline or "require_dm_e2ee" not in store_offline:
        failures.append("offline PM storage must drop non-E2EE rows in strict mode")
    if "_looks_like_dm_cipher_envelope" not in store_offline or "dropped_bad_e2ee_envelope" not in store_offline:
        failures.append("offline PM storage must validate full EC1 envelope shape, not just the prefix")

    summary = _function_body("socket_handlers.py", "_emit_missed_pm_summary")
    if "message LIKE 'EC1:%'" not in summary:
        failures.append("offline PM summary must not count non-EC1 rows in strict mode")

    if failures:
        print("❌ Messaging/E2EE doctor failed")
        for f in failures:
            print(f" - {f}")
        return 1

    print("✅ Messaging/E2EE doctor passed")
    print("   checks: live-only room history, scoped group history/unread/attachments/deletion, strict group history formatting, offline DM E2EE filtering/quarantine, legacy group attachment disable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
