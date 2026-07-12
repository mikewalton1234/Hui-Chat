#!/usr/bin/env python3
"""Static checks for beta.446 repaired friend category drag/drop behavior."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "js" / "chat_parts" / "0029_friends_requests_blocks.js"
CSS = ROOT / "static" / "css" / "chat.css"
VERSION = ROOT / "VERSION.txt"

checks = []

def require(text: str, needle: str, label: str):
    checks.append((label, needle in text))

js = JS.read_text(encoding="utf-8")
css = CSS.read_text(encoding="utf-8")
version = VERSION.read_text(encoding="utf-8").strip()

require(version, "v0.11.0-beta.446-friend-category-drag-repair", "VERSION.txt bumped to beta.446")
require(js, "function moveFriendDragPayloadToGroup(payload, groupKey, opts = {})", "native and pointer drops share one move helper")
require(js, "setFriendCollapsed(targetKey, false);", "target category auto-expands after friend move")
require(js, "function bindFriendPointerDrag(li, friend, groupKey)", "pointer drag fallback exists")
require(js, "getFriendDropTargetFromPoint(ev.clientX, ev.clientY)", "pointer drop resolves target under cursor")
require(js, "EC_FRIEND_DRAG_SUPPRESS_CLICK_UNTIL = Date.now() + 500;", "pointer drop suppresses follow-up click")
require(js, "EC_FRIEND_POINTER_DRAG && EC_FRIEND_POINTER_DRAG.sourceEl === li", "native row drag is suppressed while pointer fallback owns the row drag")
require(js, "Date.now() < EC_FRIEND_DRAG_SUPPRESS_CLICK_UNTIL", "category header click is suppressed after drop")
require(js, "li.querySelectorAll('img').forEach((img) => { img.draggable = false; });", "nested avatar images cannot steal row drag")
require(css, ".friendDragGhost", "pointer drag ghost style exists")
require(css, "pointer-events: none;", "drag ghost cannot block drop hit-testing")

failed = [label for label, ok in checks if not ok]
for label, ok in checks:
    print(("PASS" if ok else "FAIL") + f" - {label}")
if failed:
    print("\nFriend category drag repair doctor failed:", file=sys.stderr)
    for label in failed:
        print(f" - {label}", file=sys.stderr)
    sys.exit(1)
print("\nFriend category drag repair doctor passed.")
