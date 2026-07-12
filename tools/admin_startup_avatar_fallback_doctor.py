#!/usr/bin/env python3
"""Static checks for beta.444 admin startup bridge and missing avatar fallback."""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        fail(f"missing {label}: {needle}")


def main() -> int:
    admin = (ROOT / "admin_panel_inject.py").read_text(encoding="utf-8")
    routes = (ROOT / "routes_main.py").read_text(encoding="utf-8")

    require(admin, "const adminRuntimeFns = {", "admin runtime bridge")
    require(admin, "Object.assign(adminRuntimeFns", "admin runtime function assignment")
    for name in [
        "refreshVoiceSettings",
        "refreshIceSettings",
        "refreshMediaStatus",
        "refreshStats",
        "refreshSecurityStatus",
        "refreshDiagnostics",
        "refreshAnalytics",
        "runSearch",
    ]:
        require(admin, f"adminRuntimeFns.{name}", f"runtime call for {name}")

    start_match = re.search(r"function startAdminPanelRuntime\(\)\{(?P<body>.*?)\n  \}\n\n  async function requestAdminPanelStartupUnlock", admin, re.S)
    if not start_match:
        fail("could not find startAdminPanelRuntime body")
    body = start_match.group("body")
    banned = [
        "refreshVoiceSettings();",
        "refreshIceSettings();",
        "refreshMediaStatus();",
        "refreshStats();",
        "refreshSecurityStatus();",
        "refreshDiagnostics();",
        "refreshAnalytics();",
        "runSearch();",
    ]
    for bare in banned:
        if bare in body and f"adminRuntimeFns.{bare}" not in body:
            fail(f"bare inner-scope runtime call still present: {bare}")

    require(routes, "def _missing_avatar_fallback_response", "missing avatar fallback helper")
    require(routes, "X-HuiChat-Avatar-Fallback", "missing avatar response header")
    require(routes, "return _missing_avatar_fallback_response(filename)", "avatar route fallback return")
    require(routes, "_render_avatar_preset_svg(\"initials\"", "generated initials fallback")

    print("PASS: beta.444 admin startup bridge and avatar fallback checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
