#!/usr/bin/env python3
"""Static regression checks for beta.447 room media toggle teardown fixes."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WEBCAM = ROOT / "static" / "js" / "chat_parts" / "0012_webcam_ui.js"
VOICE = ROOT / "static" / "js" / "chat_parts" / "0013_voice_core.js"
VERSION = ROOT / "VERSION.txt"

checks = []

def require(text: str, needle: str, label: str) -> None:
    checks.append((label, needle in text))

webcam = WEBCAM.read_text(encoding="utf-8")
voice = VOICE.read_text(encoding="utf-8")
version = VERSION.read_text(encoding="utf-8").strip()

require(version, "0.11.0-beta.447", "VERSION.txt is canonical beta.447")
require(webcam, "btn.dataset.ecBusyOriginalDisabled = btn.disabled ? \"1\" : \"0\";", "enhanced-media busy helper stores prior disabled state")
require(webcam, "btn.disabled = btn.dataset.ecBusyOriginalDisabled === \"1\";", "enhanced-media busy helper restores prior disabled state")
require(voice, "btn.dataset.ecVoiceBusyOriginalDisabled = btn.disabled ? \"1\" : \"0\";", "voice busy helper stores prior disabled state")
require(voice, "btn.disabled = btn.dataset.ecVoiceBusyOriginalDisabled === \"1\";", "voice busy helper restores prior disabled state")
require(webcam, "function huiCamDestroyPanelIfIdle(opts = {})", "idle webcam panel teardown helper exists")
require(webcam, "huiCamDestroyPanelIfIdle();", "camera and remote tile shutdown paths can remove empty panel")
require(webcam, "huiCamDestroyPanelIfIdle({ force: true });", "full media leave forcibly removes panel")
require(webcam, "HUI_MEDIA.panel = null;", "panel cache is cleared after teardown")
require(webcam, "HUI_MEDIA.diagnostics = null;", "diagnostics cache is cleared after teardown")

failed = [label for label, ok in checks if not ok]
for label, ok in checks:
    print(("PASS" if ok else "FAIL") + f" - {label}")
if failed:
    print("\nMedia toggle teardown doctor failed:", file=sys.stderr)
    for label in failed:
        print(f" - {label}", file=sys.stderr)
    sys.exit(1)
print("\nMedia toggle teardown doctor passed.")
