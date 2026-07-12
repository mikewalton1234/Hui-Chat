#!/usr/bin/env python3
"""Deep static checks for beta.380 optimistic composer send recovery."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.11.0-beta.380"
ZIP = "Hui-Chat-v0.11.0-beta.380-optimistic-composer-deep-recheck.zip"
MIN_BETA = 380

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)

def require(text: str, token: str, rel: str) -> None:
    if token not in text:
        fail(f"{rel} missing {token!r}")

def beta_number(version: str) -> int:
    match = re.search(r"beta\.(\d+)", version)
    if not match:
        fail(f"VERSION.txt has unexpected beta version: {version!r}")
    return int(match.group(1))

project_version = read("VERSION.txt").strip()
if beta_number(project_version) < MIN_BETA:
    fail(f"VERSION.txt is {project_version!r}, expected beta.{MIN_BETA} or newer")

helpers = read("static/js/chat_parts/0007_dom_theme_helpers.js")
windows = read("static/js/chat_parts/0018_windows_manager.js")
embed = read("static/js/chat_parts/0040_room_browser_polling_embed.js")
css = read("static/css/chat.css")
notes = read("OPTIMISTIC_COMPOSER_SEND_HOTFIX_NOTES.md")
checklist = read("Hui-Chat_Front-End_UI_Audit_Checklist_beta380.md")
readme = read("README.md")

for token in [
    "function ecComposerPendingCount",
    "function ecComposerStopTypingAfterClear",
    "function ecComposerSaveFailedDraft",
    "function ecComposerRestoreSavedFailedDraft",
    "function ecComposerBindFailedDraftShortcut",
    "Ctrl+↑",
    "data-ec-failed-draft",
    "ecConversationTypingStop(input, { force: true })",
    "ecRoomTypingStop(input._ecTypingRoom, input, { force: true })",
    "const canRestoreNow = !current.trim() && !state.hasPendingInput",
]:
    require(helpers, token, "static/js/chat_parts/0007_dom_theme_helpers.js")

for rel, text in [("static/js/chat_parts/0018_windows_manager.js", windows), ("static/js/chat_parts/0040_room_browser_polling_embed.js", embed)]:
    require(text, "e.preventDefault();", rel)
    require(text, "!e.shiftKey && !e.isComposing", rel)

for token in [
    ".ym-input[data-ec-failed-draft=\"1\"]",
    "#roomEmbedInput[data-ec-failed-draft=\"1\"]",
]:
    require(css, token, "static/css/chat.css")

for token in [
    "Version: **0.11.0-beta.380**",
    "beta.380 deeper recheck additions",
    "Failed drafts can be restored with **Ctrl+ArrowUp**",
    "python3 tools/optimistic_composer_send_deep_doctor.py",
]:
    require(notes, token, "OPTIMISTIC_COMPOSER_SEND_HOTFIX_NOTES.md")

for token in [
    "Current version: **0.11.0-beta.380**",
    "Hotfix deep recheck — Optimistic composer failure recovery",
    ZIP,
]:
    require(checklist, token, "Hui-Chat_Front-End_UI_Audit_Checklist_beta380.md")

require(readme, "Optimistic composer deep recheck beta.380", "README.md")
print("PASS: beta.380 optimistic composer deep recheck static checks passed")
