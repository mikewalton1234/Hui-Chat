#!/usr/bin/env python3
"""Static checks for beta.376 UI10 deep mobile/responsive recheck."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_BETA = 377

def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)

def require(text: str, token: str, rel: str) -> None:
    if token not in text:
        fail(f"{rel} missing {token!r}")

def beta_number(version: str) -> int:
    match = re.search(r"beta\.(\d+)", version)
    if not match:
        fail(f"VERSION.txt has unexpected beta version: {version!r}")
    return int(match.group(1))

version = read("VERSION.txt").strip()
if beta_number(version) < MIN_BETA:
    fail(f"VERSION.txt is {version!r}, expected beta.{MIN_BETA} or newer")

mobile_js = read("static/js/chat_parts/0050_mobile_layout.js")
mobile_css = read("static/css/mobile.css")
checklist = read("Hui-Chat_Front-End_UI_Audit_Checklist_beta377.md")
notes = read("UI10_DEEP_MOBILE_RESPONSIVE_RECHECK_NOTES.md")

for token in [
    "function closeMobileWindowTools",
    "function closeMobileProfileEditDrawer",
    "function resetMobileOnlyWindowState",
    "const managed = node.dataset.kind === \"dm\" || node.dataset.kind === \"group\" || node.classList.contains(\"ecProfileWindow\")",
    "node.setAttribute(\"aria-modal\", active ? \"true\" : \"false\")",
    "document.querySelectorAll('.ym-window').forEach((win) => resetMobileOnlyWindowState(win))",
    "function syncAfterViewportChange",
    "function scheduleMobileViewportSync",
    "requestAnimationFrame(run)",
    "visualViewport.addEventListener(\"resize\", () => scheduleMobileViewportSync",
    "orientationchange",
]:
    require(mobile_js, token, "static/js/chat_parts/0050_mobile_layout.js")

for token in [
    "UI10 deep mobile/responsive recheck",
    ".ym-window[aria-hidden=\"true\"]",
    ".ecProfileWindow[aria-hidden=\"true\"]",
    "body.ec-mobile-keyboard-open #appRoot.is-mobile-shell .windowsLayer",
    "body.ec-mobile-shell-active #settingsModal:not(.hidden)",
    "@media (orientation: landscape) and (max-height: 520px)",
    "overscroll-behavior: contain",
    "max-height: calc(var(--ecMobileViewportH",
]:
    require(mobile_css, token, "static/css/mobile.css")

for token in [
    "Current version: **0.11.0-beta.377**",
    "UI10 deep recheck",
    "mobile orientation/keyboard/window-state safety",
    "UI11 — Voice/webcam UI",
    "Completed in beta.376",
]:
    require(checklist, token, "Hui-Chat_Front-End_UI_Audit_Checklist_beta377.md")

for token in [
    "Version: **0.11.0-beta.377**",
    "debounced viewport/orientation synchronization",
    "aria-modal",
    "Leaving mobile shell",
    "short landscape viewports",
]:
    require(notes, token, "UI10_DEEP_MOBILE_RESPONSIVE_RECHECK_NOTES.md")

print("PASS: UI10 deep mobile/responsive recheck static checks passed")
