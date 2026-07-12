#!/usr/bin/env python3
"""Static checks for beta.376 UI10 mobile/responsive pass baseline."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)

version = read("VERSION.txt").strip()
if version != "0.11.0-beta.377":
    fail(f"VERSION.txt is {version!r}, expected 0.11.0-beta.377")

mobile_css = read("static/css/mobile.css")
mobile_js = read("static/js/chat_parts/0050_mobile_layout.js")
checklist = read("Hui-Chat_Front-End_UI_Audit_Checklist_beta376.md")
notes = read("UI10_MOBILE_RESPONSIVE_PASS_NOTES.md")

css_tokens = [
    "UI10 mobile/responsive pass",
    "--mobileNavBaseH: 62px",
    "env(safe-area-inset-bottom",
    ".ym-window:not(.is-mobile-active-window):not(.hidden)",
    "is-mobile-active-window",
    "body.ec-mobile-shell-active #ecAdminPanel .ecap-modalBackdrop",
    "body.ec-mobile-keyboard-open #appRoot.is-mobile-shell .ym-window",
    "min-height: 44px",
    ".toastStack",
    ".ec-emojiPopover",
]
for token in css_tokens:
    if token not in mobile_css:
        fail(f"missing CSS token: {token}")

js_tokens = [
    "function setMobileActiveWindow",
    "function chooseTopMobileWindow",
    "function bindMobileWindowActivation",
    "window.bringToFront.__ecMobileWrapped",
    "is-mobile-active-window",
    "forceClear",
    "MutationObserver(scheduleMobileWindowSync)",
    "attributeFilter: [\"class\", \"style\", \"aria-hidden\"]",
]
for token in js_tokens:
    if token not in mobile_js:
        fail(f"missing JS token: {token}")

for token in ["Current version: **0.11.0-beta.377**", "UI10 — Mobile/responsive pass", "Hui-Chat-v0.11.0-beta.377-ui10-deep-mobile-responsive-recheck.zip"]:
    if token not in checklist:
        fail(f"missing checklist token: {token}")

for token in ["Version: **0.11.0-beta.375**", "one-active-window mobile sheet handling", "safe-area-aware mobile bottom navigation"]:
    if token not in notes:
        fail(f"missing notes token: {token}")

print("PASS: UI10 mobile/responsive static checks passed")
