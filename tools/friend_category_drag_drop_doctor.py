#!/usr/bin/env python3
"""Static checks for friend category drag/drop behavior."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "js" / "chat_parts" / "0029_friends_requests_blocks.js"
CSS = ROOT / "static" / "css" / "chat.css"
VERSION = ROOT / "VERSION.txt"

checks = []

def require(text: str, needle: str, label: str):
    ok = needle in text
    checks.append((label, ok))

js = JS.read_text(encoding="utf-8")
css = CSS.read_text(encoding="utf-8")
version = VERSION.read_text(encoding="utf-8").strip()

require(version, "friend-category-drag", "VERSION.txt is a friend-category-drag release")
require(js, "let EC_FRIEND_DRAG_SUPPRESS_CLICK_UNTIL = 0;", "click suppression timer exists")
require(js, "el.addEventListener('dragenter', markOver);", "drop targets highlight on dragenter")
require(js, "el.dataset.friendDropKey = groupKey;", "drop target records group key")
require(js, "if (payload.sourceGroupKey === targetKey) return false;", "same-group drops are ignored in shared move helper")
require(js, "bindFriendGroupDropTarget(li, groupKey);", "friend rows are drop targets too")
require(js, "actionTarget && li.dataset.dragArmed !== '1'", "action buttons do not start accidental row drag")
require(js, "application/x-hui-friend", "custom friend drag payload is set")
require(js, "Drag this row to another friend category.", "friend row tooltip explains drag behavior")
require(css, ".friendItem.dragOver", "friend row drop hover style exists")
require(css, ".friendItem[draggable=\"true\"]", "friend row drag cursor style exists")

failed = [label for label, ok in checks if not ok]
for label, ok in checks:
    print(("PASS" if ok else "FAIL") + f" - {label}")
if failed:
    print("\nFriend category drag/drop doctor failed:", file=sys.stderr)
    for label in failed:
        print(f" - {label}", file=sys.stderr)
    sys.exit(1)
print("\nFriend category drag/drop doctor passed.")
