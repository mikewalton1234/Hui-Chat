#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

def read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')

def require(haystack, needle, label):
    if needle not in haystack:
        print(f"FAIL: missing {needle!r} in {label}")
        sys.exit(1)

js = read('static/js/chat_parts/0047_settings_modal.js')
html = read('templates/chat.html')
css = read('static/css/chat.css')
checklist = read('Hui-Chat_Front-End_UI_Audit_Checklist_beta374.md')
notes = read('UI09_SETTINGS_MODAL_DEEP_RECHECK_NOTES.md')
version = read('VERSION.txt').strip()

for token in [
    'const SETTINGS_ALL_PREF_KEYS',
    'function ecSettingsSnapshotPrefs',
    'function ecSettingsDraftFromControls',
    'function updateSettingsDirtyState',
    'function setSettingsBusy',
    'function bindSettingsModalKeys',
    "ev.key === 'Escape'",
    "String(ev.key || '').toLowerCase() === 's'",
    "['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']",
    'ecApplySettingsPrefsObject(ecSettingsReadSnapshot(modal), { syncControls: false })',
    'SETTINGS_COMPOSER_KEYS.forEach',
    'Settings.set(key, UIState.prefs[key])',
    'function ecSettingsFocusableElements',
    'function ecTrapSettingsFocus',
    'function ecUpdateSettingsLivePreviewFromControls',
    'EC_SETTINGS_OPEN_SEQ',
    'EC_SETTINGS_SAVE_SEQ',
]:
    require(js, token, '0047_settings_modal.js')

for token in [
    'aria-describedby="settingsDescription settingsStatus"',
    'id="settingsDescription"',
    'id="settingsStatus"',
    'aria-live="polite"',
    'aria-keyshortcuts="Control+S Meta+S"',
    'btnSaveSettings" class="primaryBtn" type="button"',
    'btnCloseSettings" class="ghostBtn" type="button"',
]:
    require(html, token, 'templates/chat.html')

for token in [
    '.settingsStatus',
    '#settingsModal.is-dirty',
    '#settingsModal button.isBusy',
    '.settingsTabBtn:focus-visible',
    '#settingsModal[data-settings-saving="1"] .settingsStatus',
    '#settingsModal .settingsPanel',
]:
    require(css, token, 'static/css/chat.css')

for token in [
    'Current version: **0.11.0-beta.374**',
    'UI09 — Settings modal',
    'UI09 deep recheck',
    'UI10 — Mobile/responsive pass',
    'Hui-Chat-v0.11.0-beta.374-admin-reauth-deep-recheck.zip',
]:
    require(checklist, token, 'Hui-Chat_Front-End_UI_Audit_Checklist_beta374.md')

for token in ['0.11.0-beta.373', 'focus trap', 'stale modal state', 'roll back all unsaved previews']:
    require(notes, token, 'UI09_SETTINGS_MODAL_DEEP_RECHECK_NOTES.md')

if version != '0.11.0-beta.374':
    print(f'FAIL: VERSION.txt is {version!r}')
    sys.exit(1)

print('settings_modal_ui09_doctor: PASS')
