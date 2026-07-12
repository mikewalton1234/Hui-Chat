#!/usr/bin/env python3
"""Static checks for beta.380 optimistic composer send deep recheck."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.11.0-beta.380"
ZIP = "Hui-Chat-v0.11.0-beta.380-optimistic-composer-deep-recheck.zip"

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)

def require(text: str, token: str, rel: str) -> None:
    if token not in text:
        fail(f"{rel} missing {token!r}")

version = read("VERSION.txt").strip()
if version != VERSION:
    fail(f"VERSION.txt is {version!r}, expected {VERSION}")

helpers = read("static/js/chat_parts/0007_dom_theme_helpers.js")
room_embed = read("static/js/chat_parts/0040_room_browser_polling_embed.js")
room_window = read("static/js/chat_parts/0041_rooms_runtime.js")
convo = read("static/js/chat_parts/0043_group_history_dm_windows.js")
torrent = read("static/js/chat_parts/0006_torrent_helpers.js")
css = read("static/css/chat.css")
notes = read("OPTIMISTIC_COMPOSER_SEND_HOTFIX_NOTES.md")
checklist = read("Hui-Chat_Front-End_UI_Audit_Checklist_beta380.md")
readme = read("README.md")

for token in [
    "function ecComposerBeginOptimisticSend",
    "input.value = '';",
    "restore(reason = '')",
    "input._ecLastFailedDraft = text",
    "button.classList.add('ecComposerSending')",
]:
    require(helpers, token, "static/js/chat_parts/0007_dom_theme_helpers.js")

for token in [
    "ecComposerBeginOptimisticSend(input, { text: msg, button: sendBtn })",
    "optimistic?.commit?.()",
    "optimistic?.restore?.(res?.error || \"Send failed\")",
    "apiJson(\"/api/rooms/invite\"",
    "sendRoomTo(room, JSON.stringify(wire))",
]:
    require(room_embed, token, "static/js/chat_parts/0040_room_browser_polling_embed.js")

for token in [
    "ecComposerBeginOptimisticSend(input, { text: msg, button: sendBtn })",
    "optimistic?.commit?.()",
    "optimistic?.restore?.(res?.error || \"Send failed\")",
    "ecRoomTypingStop(room, input, { force: true })",
]:
    require(room_window, token, "static/js/chat_parts/0041_rooms_runtime.js")

for token in [
    "sendGroupTo(groupId, msg",
    "optimistic?.restore?.(res?.error || \"Group send failed\")",
    "sendPrivateTo(peer, sendText)",
    "optimistic?.restore?.('PM send failed')",
    "optimistic?.restore?.('Message empty after emoticon filter')",
]:
    require(convo, token, "static/js/chat_parts/0043_group_history_dm_windows.js")

for token in [
    "const ok = await sendPrivateTo(toUser, JSON.stringify(meta));",
    "if (!ok) return null;",
]:
    require(torrent, token, "static/js/chat_parts/0006_torrent_helpers.js")

for token in [
    ".ym-input.ecComposerSending",
    "#roomEmbedInput.ecComposerSending",
    ".ym-send.ecComposerSending::after",
]:
    require(css, token, "static/css/chat.css")

for token in [
    f"Version: **{VERSION}**",
    "Room embedded composer now clears immediately",
    "PM windows now clear immediately",
    "failed draft is saved",
]:
    require(notes, token, "OPTIMISTIC_COMPOSER_SEND_HOTFIX_NOTES.md")

for token in [
    f"Current version: **{VERSION}**",
    "Hotfix — Optimistic composer send",
    "UI12 — Final front-end release smoke and handoff",
    ZIP,
]:
    require(checklist, token, "Hui-Chat_Front-End_UI_Audit_Checklist_beta380.md")

require(readme, "Optimistic composer deep recheck beta.380", "README.md")
print("PASS: beta.380 optimistic composer send static checks passed")
