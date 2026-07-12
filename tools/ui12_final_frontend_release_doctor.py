#!/usr/bin/env python3
"""Static checks for beta.382 UI12 final front-end release smoke/handoff."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
UI12_VERSION = "0.11.0-beta.382"
MIN_BETA = 382
ZIP = "Hui-Chat-v0.11.0-beta.382-ui12-final-frontend-release.zip"

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

def beta_number(version: str) -> int:
    match = re.search(r"beta\.(\d+)", version)
    if not match:
        fail(f"VERSION.txt has unexpected beta version: {version!r}")
    return int(match.group(1))


version = read("VERSION.txt").strip()
if beta_number(version) < MIN_BETA:
    fail(f"VERSION.txt is {version!r}, expected beta.{MIN_BETA} or newer")

for rel in [
    "UI12_FINAL_FRONTEND_RELEASE_NOTES.md",
    "Hui-Chat_Front-End_UI_Audit_Checklist_beta382.md",
]:
    require(rel, UI12_VERSION)

template = read("templates/admin_test_lab.html")
for token in [
    "function recomputeResultSummary(data)",
    "return { ...base, ok: failed === 0, summary: { passed, failed, skipped } };",
    "return recomputeResultSummary(merged);",
    "renderAll(mergeReleaseGateRows(data))",
    "const data = mergeReleaseGateRows(summarizeRows(rows, 'browser'));",
    "status.textContent = rows.every(r => r && r.ok) ? 'Browser P2P diagnostics passed.'",
    "browser-release-gate",
]:
    if token not in template:
        fail(f"templates/admin_test_lab.html missing {token!r}")
    checks.append(f"PASS templates/admin_test_lab.html: {token}")

for token in [
    "UI12 — Final front-end release smoke and handoff",
    "Completed in beta.382",
    ZIP,
    "beta.381 Hotfix — missed message notification icon",
]:
    require("Hui-Chat_Front-End_UI_Audit_Checklist_beta382.md", token)

for token in [
    "UI12 final front-end release beta.382",
    "Admin Test Lab Browser release gate",
    "tools/ui12_final_frontend_release_doctor.py",
]:
    require("README.md", token)

for token in [
    "python tools/ui12_final_frontend_release_doctor.py",
    "Browser release gate",
    f"visible version is `{version}`",
]:
    require("docs/RELEASE_HANDOFF.md", token)

for token in [
    "Hui-Chat_Front-End_UI_Audit_Checklist_beta",
    "server_checklists = sorted",
    "latest_server_checklist",
]:
    require("tools/release_packaging_doctor.py", token)

print("\n".join(checks))
print("ui12 final front-end release doctor passed")
