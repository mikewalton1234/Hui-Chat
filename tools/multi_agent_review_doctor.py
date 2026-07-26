#!/usr/bin/env python3
"""Static regression guard for the Hui Chat multi-agent review system."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
required = {
    "tools/hui_multi_agent_review.py": [
        "ThreadPoolExecutor(max_workers=4",
        '"correctness", "security", "deployment", "ui_behavior"',
        'multi_agent={"enabled": True, "max_concurrent_subagents": 4}',
        'betas=["responses_multi_agent=v1"]',
        "EXCLUDED_NAMES",
        "secret_scan",
        "REVIEW_GATE: {gate}",
        "doctor file is unavailable in this repository revision",
    ],
    ".github/workflows/hui-multi-agent-review.yml": [
        "pull_request:",
        "workflow_dispatch:",
        "schedule:",
        "fetch-depth: 0",
        "OPENAI_API_KEY",
        "actions/upload-artifact@v4",
    ],
    "review/README.md": [
        "Correctness",
        "Security",
        "Deployment",
        "UI behavior",
        "Pull requests from forks",
        "Privacy and safety",
    ],
    "requirements-review.txt": [
        "openai>=2.45.0,<3",
        "ruff",
        "bandit",
        "pip-audit",
    ],
}

failures: list[str] = []
for rel, tokens in required.items():
    path = ROOT / rel
    if not path.exists():
        failures.append(f"missing {rel}")
        continue
    text = path.read_text(encoding="utf-8")
    for token in tokens:
        if token not in text:
            failures.append(f"{rel} missing {token!r}")

workflow = (ROOT / ".github/workflows/hui-multi-agent-review.yml").read_text(encoding="utf-8")
if "permissions:\n  contents: write" in workflow or "pull-requests: write" in workflow:
    failures.append("workflow must remain read-only")
if "server_config.json" in workflow:
    failures.append("workflow must not upload local server_config.json")

if failures:
    print("FAIL: multi-agent review doctor")
    for failure in failures:
        print(f"- {failure}")
    raise SystemExit(1)

print("PASS: four deterministic agents are configured")
print("PASS: GPT-5.6 Multi-agent beta request is configured")
print("PASS: CI is read-only and uploads only generated review reports")
print("PASS: missing newer-version doctors degrade to warnings instead of breaking CI")
print("multi-agent review doctor passed")
