#!/usr/bin/env python3
"""Static checks for beta.383+ post-UI12 bug-hunt fixes."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_BETA = 383
ZIP = "Hui-Chat-v0.11.0-beta.383-post-ui12-bug-hunt.zip"

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
if beta_number(version) < MIN_BETA:
    fail(f"VERSION.txt is {version!r}, expected beta.{MIN_BETA} or newer")

for rel in [
    "README.md",
    f"Hui-Chat_Front-End_UI_Audit_Checklist_beta{beta_number(version)}.md",
    "docs/RELEASE_HANDOFF.md",
    "docs/UPGRADE_ROLLBACK.md",
    "docs/RELEASE_PACKAGE.md",
    "docs/FRONTEND_STRUCTURE.md",
]:
    require(rel, version)

require("POST_UI12_BUG_HUNT_NOTES.md", "Version: **0.11.0-beta.383**")

for token in [
    "function ecConversationWindowIsVisible",
    "function ecTopVisibleConversationWindow",
    "function ecMarkTopVisibleConversationWindowSeen",
    "window.__ecConversationSeenOnFocusBound",
    "window.addEventListener('focus', scheduleSeenSweep)",
    "window.addEventListener('pageshow', scheduleSeenSweep)",
    "document.addEventListener('visibilitychange'",
    "win.addEventListener(\"pointerdown\"",
    "win.addEventListener(\"focusin\"",
    "is-mobile-active-window",
]:
    require("static/js/chat_parts/0018_windows_manager.js", token)

for rel, min_beta in [
    ("tools/admin_reauth_deep_recheck_doctor.py", 374),
    ("tools/mobile_ui10_deep_doctor.py", 377),
    ("tools/voice_webcam_ui11_doctor.py", 377),
    ("tools/typing_indicators_pm_group_doctor.py", 378),
    ("tools/optimistic_composer_send_deep_doctor.py", 380),
    ("tools/ui12_final_frontend_release_doctor.py", 382),
]:
    require(rel, "def beta_number")
    require(rel, f"MIN_BETA = {min_beta}")
    require_regex(rel, r"beta_number\(version\).*<\s*MIN_BETA|beta_number\(project_version\).*<\s*MIN_BETA")

require("tools/classic_composer_layout_doctor.py", "ecBuildStyledRoomMessagePayload(filteredPlaintext)")

for token in [
    "Post-UI12 bug hunt beta.383",
    "top visible PM/group conversation",
    "tools/post_ui12_bug_hunt_doctor.py",
]:
    require("README.md", token)

for token in [
    "Post-UI12 bug hunt — focus-return unread cleanup",
    ZIP,
    "Post-UI12 bug hunt deep recheck",
]:
    require("Hui-Chat_Front-End_UI_Audit_Checklist_beta383.md", token)

for token in [
    "python tools/post_ui12_bug_hunt_doctor.py",
    f"visible version is `{version}`",
]:
    require("docs/RELEASE_HANDOFF.md", token)

for token in [
    "PM/group unread focus-return cleanup",
    "Touch and keyboard focus support",
    "Regression doctor version compatibility",
]:
    require("POST_UI12_BUG_HUNT_NOTES.md", token)

print("\n".join(checks))
print("post-UI12 bug hunt doctor passed")
