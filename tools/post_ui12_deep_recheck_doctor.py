#!/usr/bin/env python3
"""Static checks for beta.384+ post-UI12 active-conversation unread safety."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_BETA = 384
ZIP = "Hui-Chat-v0.11.0-beta.384-post-ui12-deep-recheck.zip"
checks: list[str] = []


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


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


def beta_number(version: str) -> int:
    match = re.search(r"0\.11\.0-beta\.(\d+)$", version.strip())
    if not match:
        fail(f"VERSION.txt has unexpected beta version: {version!r}")
    return int(match.group(1))


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
]:
    require(rel, version)
require("POST_UI12_DEEP_RECHECK_NOTES.md", "Version: **0.11.0-beta.384**")

for token in [
    "function ecIsConversationWindowActive",
    "return top === winEl",
    "function ecTopVisibleConversationWindow",
    "is-mobile-active-window",
    "const win = ecTopVisibleConversationWindow();",
    "if (win) ecMarkConversationWindowSeen(win);",
]:
    require("static/js/chat_parts/0018_windows_manager.js", token)

require_regex(
    "static/js/chat_parts/0045_transfers_crypto.js",
    r"const\s+pmWindowIsActive\s*=\s*\(typeof\s+ecIsConversationWindowActive\s*===\s*'function'\).*?ecIsConversationWindowActive\(existingPmWindow\).*?const\s+suppressActivePmAlert\s*=\s*hadOpenPmWindow\s*&&\s*pmWindowWasVisible\s*&&\s*appWasFocused\s*&&\s*pmWindowIsActive",
)

require_regex(
    "static/js/chat_parts/0043_group_history_dm_windows.js",
    r"function\s+ecIsGroupConversationActive\(win\).*?ecIsConversationWindowActive\(win\)",
)
require_regex(
    "static/js/chat_parts/0043_group_history_dm_windows.js",
    r"Visible-but-background windows still get\s*\n\s*// unread attention",
)
require_regex(
    "static/js/chat_parts/0043_group_history_dm_windows.js",
    r"if\s*\(render\.readSafe\s*&&\s*messageId\s*!==\s*null\s*&&\s*groupIsActive\)\s*\{\s*markVisibleGroupMessageRead",
)

for token in [
    "Post-UI12 deep recheck beta.384",
    "active/top visible conversation",
    "tools/post_ui12_deep_recheck_doctor.py",
]:
    require("README.md", token)

for token in [
    "Post-UI12 bug hunt deep recheck — active/top conversation unread safety",
    "Next post-UI12 bug hunt pass",
]:
    require(f"Hui-Chat_Front-End_UI_Audit_Checklist_beta{beta}.md", token)
require("Hui-Chat_Front-End_UI_Audit_Checklist_beta384.md", ZIP)

for token in [
    "python tools/post_ui12_deep_recheck_doctor.py",
    f"visible version is `{version}`",
]:
    require("docs/RELEASE_HANDOFF.md", token)
require("POST_UI12_DEEP_RECHECK_NOTES.md", "beta.384")

for token in [
    "unread attention",
    "visible-but-background conversation",
    "A conversation is active only when",
]:
    require("POST_UI12_DEEP_RECHECK_NOTES.md", token)

print("\n".join(checks))
print("post-UI12 deep recheck doctor passed")
