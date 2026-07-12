#!/usr/bin/env python3
"""Static checks for beta.385+ post-UI12 room unread visibility safety."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_BETA = 385
ZIP = "Hui-Chat-v0.11.0-beta.385-post-ui12-room-unread-visibility.zip"
checks: list[str] = []


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def beta_number(version: str) -> int:
    match = re.search(r"0\.11\.0-beta\.(\d+)$", version.strip())
    if not match:
        fail(f"VERSION.txt has unexpected beta version: {version!r}")
    return int(match.group(1))


def require(rel: str, token: str) -> None:
    text = read(rel)
    if token not in text:
        fail(f"{rel} missing {token!r}")
    checks.append(f"PASS {rel}: {token}")


def require_regex(rel: str, pattern: str) -> None:
    text = read(rel)
    if not re.search(pattern, text, re.M | re.S):
        fail(f"{rel} missing pattern: {pattern}")
    checks.append(f"PASS {rel}: /{pattern}/")


version = read("VERSION.txt").strip()
beta = beta_number(version)
if beta < MIN_BETA:
    fail(f"VERSION.txt is {version!r}, expected beta.{MIN_BETA} or newer")

for rel in [
    "README.md",
    f"Hui-Chat_Front-End_UI_Audit_Checklist_beta{beta}.md",
    "docs/RELEASE_HANDOFF.md",
    "docs/UPGRADE_ROLLBACK.md",
    "docs/RELEASE_PACKAGE.md",
    "docs/FRONTEND_STRUCTURE.md",
    "POST_UI12_ROOM_UNREAD_VISIBILITY_NOTES.md",
]:
    require(rel, version)

for token in [
    "function ecRoomElementHasVisibleBox",
    "function ecIsRoomSurfaceActuallyReadable",
    "function ecIsRoomMessageQuietlyVisible",
    "data-mobile-panel') !== 'chat'",
    "room-browser-overlay-open",
    "ROOM_BROWSER !== 'undefined'",
    "ecIsConversationWindowActive(activeConversation)",
    "return !!sameRoom && !!focused && ecIsRoomSurfaceActuallyReadable(view);",
]:
    require("static/js/chat_parts/0041_rooms_runtime.js", token)

require_regex(
    "static/js/chat_parts/0041_rooms_runtime.js",
    r"if\s*\(root\?\.classList\?\.contains\?\.\('is-mobile-shell'\)\)\s*\{.*?root\.getAttribute\('data-mobile-panel'\)\s*!==\s*'chat'.*?return false",
)
require_regex(
    "static/js/chat_parts/0041_rooms_runtime.js",
    r"embed\.classList\?\.contains\?\.\('is-underlay'\).*?return false.*?siteArea\?\.classList\?\.contains\?\.\('room-browser-overlay-open'\).*?return false",
)
require_regex(
    "static/js/chat_parts/0041_rooms_runtime.js",
    r"if\s*\(room\s*&&\s*username\s*&&\s*username\s*!==\s*currentUser\).*?if\s*\(quietActiveRoomMessage\s*&&\s*room\s*===\s*UIState\.currentRoom\)\s*rbClearUnread\(room\);\s*else\s*\{\s*rbBumpUnread\(room\);",
)

for token in [
    "Post-UI12 room unread visibility beta.385",
    "current-room unread/notification edge case",
    "tools/post_ui12_room_unread_visibility_doctor.py",
]:
    require("README.md", token)

for token in [
    "room unread visibility safety",
    ZIP,
    "Mobile Rooms/Hub panels",
]:
    require(f"Hui-Chat_Front-End_UI_Audit_Checklist_beta{beta}.md", token)

for token in [
    "python tools/post_ui12_room_unread_visibility_doctor.py",
    f"visible version is `{version}`",
    ZIP,
    "beta.385 post-UI12 room unread visibility pass",
]:
    require("docs/RELEASE_HANDOFF.md", token)

print("\n".join(checks))
print("post-UI12 room unread visibility doctor passed")
