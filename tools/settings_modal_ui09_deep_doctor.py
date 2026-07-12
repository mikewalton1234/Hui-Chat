#!/usr/bin/env python3
"""Static checks for the UI09 deep Settings modal recheck."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding='utf-8')

def fail(msg: str) -> None:
    print(f'FAIL: {msg}')
    sys.exit(1)

def require(text: str, token: str, rel: str) -> None:
    if token not in text:
        fail(f'{rel} missing {token!r}')

js = read('static/js/chat_parts/0047_settings_modal.js')
html = read('templates/chat.html')
css = read('static/css/chat.css')
notes = read('UI09_SETTINGS_MODAL_DEEP_RECHECK_NOTES.md')
checklist = read('Hui-Chat_Front-End_UI_Audit_Checklist_beta374.md')

for token in [
    'const EC_SETTINGS_BUSY_CONTROL_IDS',
    'function ecSettingsFocusableElements',
    'function ecTrapSettingsFocus',
    "ev.key === 'Tab' && ecTrapSettingsFocus(ev, modal)",
    "modal.dataset.settingsSaving === '1'",
    'function ecSetSettingsControlDisabled',
    'function ecUpdateSettingsLivePreviewFromControls',
    'el.addEventListener(\'input\', ecUpdateSettingsLivePreviewFromControls)',
    'EC_SETTINGS_OPEN_SEQ += 1',
    'modal.dataset.settingsOpenSeq',
    'const saveSeq = ++EC_SETTINGS_SAVE_SEQ',
    'closeSettings({ revertPreview: false, force: true })',
    'save.dataset.prevHtml',
    "toast('ℹ️ Settings are saving. Please wait…', 'info')",
]:
    require(js, token, '0047_settings_modal.js')

for token in [
    'tabindex="-1"',
    'aria-keyshortcuts="Control+S Meta+S"',
    'aria-label="Close settings and revert unsaved previews"',
]:
    require(html, token, 'templates/chat.html')

for token in [
    '#settingsModal[aria-modal="true"]',
    'overscroll-behavior: contain',
    '#settingsModal[data-settings-saving="1"] .settingsStatus',
    'overflow-wrap: anywhere',
]:
    require(css, token, 'static/css/chat.css')

for token in ['Version: **0.11.0-beta.373**', 'focus trap', 'save-state guards', 'Manual smoke checklist']:
    require(notes, token, 'UI09_SETTINGS_MODAL_DEEP_RECHECK_NOTES.md')

for token in ['Current version: **0.11.0-beta.374**', 'UI09 deep recheck', 'UI10 — Mobile/responsive pass']:
    require(checklist, token, 'Hui-Chat_Front-End_UI_Audit_Checklist_beta374.md')

print('settings_modal_ui09_deep_doctor: PASS')
