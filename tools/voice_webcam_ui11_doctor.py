#!/usr/bin/env python3
"""Static checks for beta.377 UI11 voice/webcam UI."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_BETA = 377

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding='utf-8')

def fail(msg: str) -> None:
    print(f'FAIL: {msg}')
    sys.exit(1)

def require(text: str, token: str, rel: str) -> None:
    if token not in text:
        fail(f'{rel} missing {token!r}')

def beta_number(version: str) -> int:
    match = re.search(r"beta\.(\d+)", version)
    if not match:
        fail(f"VERSION.txt has unexpected beta version: {version!r}")
    return int(match.group(1))

version = read('VERSION.txt').strip()
if beta_number(version) < MIN_BETA:
    fail(f'VERSION.txt is {version!r}, expected beta.{MIN_BETA} or newer')

webcam = read('static/js/chat_parts/0012_webcam_ui.js')
voice = read('static/js/chat_parts/0013_voice_core.js')
dm = read('static/js/chat_parts/0014_voice_dm_calls.js')
room = read('static/js/chat_parts/0040_room_browser_polling_embed.js')
group = read('static/js/chat_parts/0043_group_history_dm_windows.js')
css = read('static/css/chat.css')
mobile = read('static/css/mobile.css')
notes = read('UI11_VOICE_WEBCAM_UI_NOTES.md')
checklist = read('Hui-Chat_Front-End_UI_Audit_Checklist_beta377.md')

for token in [
    'actionLocks: new Set()',
    'function huiMediaIsBusy',
    'async function huiMediaWithBusy',
    'function huiCamRefreshDiagnostics',
    'function huiCamStopViewing',
    'Stop camera',
    'Stop viewing',
    'return huiMediaWithBusy("voice", room',
    'return huiMediaWithBusy("cam", room',
    'return huiMediaWithBusy("view", room, owner',
    'return huiMediaWithBusy("respond", room, viewer',
    'window.huiCamStopViewing = huiCamStopViewing',
    'window.huiMediaIsBusy = huiMediaIsBusy',
]:
    require(webcam, token, 'static/js/chat_parts/0012_webcam_ui.js')

for token in [
    'const VOICE_UI_BUSY',
    'function voiceActionBusy',
    'async function voiceWithBusy',
    'function voiceRefreshBusyUi',
    'aria-live',
    'aria-pressed',
    'aria-label',
]:
    require(voice, token, 'static/js/chat_parts/0013_voice_core.js')

for token in [
    'voiceStartDmCallUnlocked',
    'voiceAcceptDmCallUnlocked',
    'voiceWithBusy("dm", peer',
    'voiceSetActionBusy("dm", peer, true)',
]:
    require(dm, token, 'static/js/chat_parts/0014_voice_dm_calls.js')

for token in [
    "voiceWithBusy('room', room",
    'btnCamTop.setAttribute',
]:
    require(room, token, 'static/js/chat_parts/0040_room_browser_polling_embed.js')

for token in [
    "voiceActionBusy('group'",
    "voiceWithBusy('group'",
    "btn.setAttribute('aria-pressed'",
    "btn.setAttribute('aria-busy'",
]:
    require(group, token, 'static/js/chat_parts/0043_group_history_dm_windows.js')

for token in [
    'UI11 voice/webcam UI',
    '.ym-avDiagnostics',
    '.ym-avTileActions',
    '[aria-busy="true"]',
]:
    require(css, token, 'static/css/chat.css')

for token in [
    'UI11 voice/webcam UI mobile guardrails',
    '.ym-avDiagnostics',
    '.ym-avTileActions .miniBtn',
]:
    require(mobile, token, 'static/css/mobile.css')

for token in [
    'Version: **0.11.0-beta.377**',
    'request, view, stop',
    'live webcam diagnostics',
    'Stop camera',
]:
    require(notes, token, 'UI11_VOICE_WEBCAM_UI_NOTES.md')

for token in [
    'Current version: **0.11.0-beta.377**',
    'UI11 — Voice/webcam UI',
    'Hui-Chat-v0.11.0-beta.377-ui11-voice-webcam-ui.zip',
    'UI12 — Final front-end release smoke and handoff',
]:
    require(checklist, token, 'Hui-Chat_Front-End_UI_Audit_Checklist_beta377.md')

print('PASS: UI11 voice/webcam UI static checks passed')
