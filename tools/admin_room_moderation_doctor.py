#!/usr/bin/env python3
"""Static admin room/moderation tooling checks for Hui Chat.

This doctor verifies S14 invariants without a live database:

  - admin room/moderation routes canonicalize room names through chat_rooms;
  - room lock/read-only/slowmode writes remove wrong-case legacy policy rows;
  - room unlock clears casefold-matching lock rows;
  - room ban/kick/unban/delete actions use canonical rooms;
  - Socket.IO room policy readers match room policy rows case-insensitively.
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
    socket_handlers = _read("socket_handlers.py")

    canonical = _function_body("routes_admin_tools.py", "_canonical_room_or_error")
    if not canonical:
        failures.append("missing canonical admin room resolver")
    else:
        for token in [
            "SELECT name",
            "FROM chat_rooms",
            "WHERE LOWER(name) = LOWER(%s)",
            "ORDER BY CASE WHEN name = %s THEN 0 ELSE 1 END",
            '"Room not found"',
        ]:
            if token not in canonical:
                failures.append(f"canonical room resolver missing {token}")

    cleanup = _function_body("routes_admin_tools.py", "_delete_casefold_room_policy_rows")
    if not cleanup:
        failures.append("missing casefold room policy cleanup helper")
    else:
        if 'table not in {"room_locks", "room_readonly", "room_slowmode"}' not in cleanup:
            failures.append("room policy cleanup helper must whitelist mutable table names")
        if "DELETE FROM {table} WHERE LOWER(room)=LOWER(%s) AND room <> %s" not in cleanup:
            failures.append("room policy cleanup helper must delete wrong-case duplicates")

    for fn in ["kick_from_room", "ban_from_room", "unban_from_room", "lock_room", "unlock_room", "clear_room", "set_room_readonly", "set_room_slowmode", "admin_room_delete"]:
        body = _function_body("routes_admin_tools.py", fn)
        if not body:
            failures.append(f"missing admin route {fn}")
        elif "_canonical_room_or_error" not in body:
            failures.append(f"{fn} must canonicalize room names before acting")

    lock = _function_body("routes_admin_tools.py", "lock_room")
    if '_delete_casefold_room_policy_rows(cur, "room_locks", room)' not in lock:
        failures.append("lock_room must clean wrong-case room_locks rows before upsert")

    readonly = _function_body("routes_admin_tools.py", "set_room_readonly")
    if '_delete_casefold_room_policy_rows(cur, "room_readonly", room)' not in readonly:
        failures.append("set_room_readonly must clean wrong-case room_readonly rows before upsert")

    slowmode = _function_body("routes_admin_tools.py", "set_room_slowmode")
    if '_delete_casefold_room_policy_rows(cur, "room_slowmode", room)' not in slowmode:
        failures.append("set_room_slowmode must clean wrong-case room_slowmode rows before upsert")
    if "_state_set_room_slowmode_cache(room, seconds)" not in slowmode:
        failures.append("set_room_slowmode must update realtime slowmode cache immediately")

    unlock = _function_body("routes_admin_tools.py", "unlock_room")
    if "DELETE FROM room_locks WHERE LOWER(room) = LOWER(%s)" not in unlock:
        failures.append("unlock_room must clear lock rows case-insensitively")

    policy_snapshot = _function_body("routes_admin_tools.py", "_room_policy_snapshot")
    for table in ["room_locks", "room_readonly", "room_slowmode"]:
        if f"FROM {table}" not in policy_snapshot or "WHERE LOWER(room) = LOWER(%s)" not in policy_snapshot:
            failures.append(f"room policy snapshot must read {table} case-insensitively")

    for fn in ["_room_locked", "_room_readonly", "_room_slowmode_seconds"]:
        body = _function_body("socket_handlers.py", fn)
        if not body:
            failures.append(f"missing Socket.IO policy reader {fn}")
        else:
            if "WHERE LOWER(room) = LOWER(%s)" not in body:
                failures.append(f"{fn} must read policy rows case-insensitively")
            if "ORDER BY CASE WHEN room = %s THEN 0 ELSE 1 END" not in body:
                failures.append(f"{fn} must prefer exact-case policy rows when duplicates exist")

    for fn in ["mute_user_admin", "kick_from_room", "ban_from_room", "unban_from_room", "lock_room", "unlock_room", "clear_room", "set_room_readonly", "set_room_slowmode", "global_broadcast"]:
        body = _function_body("routes_admin_tools.py", fn)
        if body and "@require_recent_admin_auth" not in routes[routes.find(f"def {fn}")-200:routes.find(f"def {fn}")]:
            failures.append(f"{fn} must require recent admin reauth")

    if failures:
        print("❌ Admin room/moderation doctor failed")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print("✅ Admin room/moderation doctor passed")
    print("   checks: canonical room actions, casefold policy cleanup/reads, recent reauth on destructive room/moderation routes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
