#!/usr/bin/env python3
"""Static S19 deployment/operations doctor for Hui Chat.

This checks the generated service templates and operator scripts for the most
important production-safety invariants:
  - one worker per web instance;
  - config/topology ExecStartPre gates in generated systemd services;
  - a separate janitor service with an explicit config path;
  - quoted paths so systemd/install scripts survive spaces in project folders;
  - install commands create/chown runtime writable directories;
  - production dependency script does not execute an unquoted PYTHON value.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deployment_wizard import (  # noqa: E402
    generate_janitor_service,
    generate_systemd_instance_template,
    generate_systemd_service,
    write_deployment_kit,
)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _assert_contains(failures: list[str], label: str, text: str, tokens: list[str]) -> None:
    for token in tokens:
        if token not in text:
            failures.append(f"{label} missing token: {token}")


def main() -> int:
    failures: list[str] = []
    sample = {
        "systemd_working_directory": "/tmp/Hui Chat S19",
        "systemd_python": "/tmp/Hui Chat S19/.venv/bin/python",
        "systemd_env_file": "/etc/hui chat/hui.env",
        "rate_limit_storage_uri": "rediss://127.0.0.1:6380/0",
        "socketio_message_queue": "redis://127.0.0.1:6379/1",
        "shared_state_redis_url": "redis://127.0.0.1:6379/2",
        "production_instance_count": 2,
        "dm_upload_root": "secure dm",
        "group_upload_root": "secure group",
    }

    single = generate_systemd_service(sample)
    instance = generate_systemd_instance_template(sample)
    janitor = generate_janitor_service(sample)

    _assert_contains(failures, "single systemd service", single, [
        'WorkingDirectory="/tmp/Hui Chat S19"',
        'EnvironmentFile="/etc/hui chat/hui.env"',
        "ExecStartPre=",
        "tools/config_doctor.py --config",
        "--redis-socketio-check",
        "HUI_CONFIG=",
        "HUI_PRODUCTION_WORKERS=1",
        "HUI_WORKERS=1",
        "After=network-online.target redis.service",
        'ReadWritePaths="/tmp/Hui Chat S19/secure dm"',
        'ReadWritePaths="/tmp/Hui Chat S19/server_config.json"',
    ])
    if "HUI_WORKERS=2" in single or "WEB_CONCURRENCY=2" in single:
        failures.append("single systemd service appears to allow unsafe multi-worker Socket.IO startup")

    _assert_contains(failures, "instance systemd service", instance, [
        "hui-chat@.service",
        'HUI_BIND="127.0.0.1:%i"',
        "HUI_PRODUCTION_WORKERS=1",
        "HUI_WORKERS=1",
        "--redis-socketio-check",
    ])

    _assert_contains(failures, "janitor systemd service", janitor, [
        "janitor_runner.py --config",
        "tools/config_doctor.py --config",
        "Run exactly one janitor",
        'WorkingDirectory="/tmp/Hui Chat S19"',
        "After=network-online.target redis.service",
        "Wants=network-online.target redis.service",
    ])
    if "main.py --production" in janitor:
        failures.append("janitor service must not start the web server")

    deployment_wizard = _read("deployment_wizard.py")
    _assert_contains(failures, "deployment_wizard.py", deployment_wizard, [
        "def _systemd_quote",
        "def _runtime_path_values",
        "def _runtime_mkdir_commands",
        "sudo install -d -o",
        "sudo chown root:",
        "sudo chmod 640",
        "rediss://",
    ])

    install_deps = _read("scripts/install_production_deps.sh")
    _assert_contains(failures, "install_production_deps.sh", install_deps, [
        '"$PYTHON_BIN" -m pip install --upgrade pip',
        '"$PYTHON_BIN" -m pip install -r requirements.txt',
        '"$PYTHON_BIN" - <<',
    ])
    if "$PYTHON_BIN -m pip" in install_deps or "$PYTHON_BIN - <<" in install_deps:
        failures.append("install_production_deps.sh still contains unquoted $PYTHON_BIN execution")

    with tempfile.TemporaryDirectory(prefix="hui-s19-kit-") as tmp:
        out = Path(tmp) / "kit"
        written = write_deployment_kit(sample, out, proxy="all", settings_file=ROOT / "server_config.json", repo_root=ROOT)
        names = {Path(item.path).name for item in written}
        for required in {"hui-chat.service", "hui-chat@.service", "hui-chat-janitor.service", "hui-chat.env.example", "install-commands.sh", "README.md"}:
            if required not in names:
                failures.append(f"deployment kit did not write {required}")
        install_commands = (out / "install-commands.sh").read_text(encoding="utf-8")
        _assert_contains(failures, "generated install-commands.sh", install_commands, [
            "sudo install -d -o hui -g hui -m 0750",
            "sudo systemctl daemon-reload",
            "hui-chat-janitor.service",
        ])

    if failures:
        print("❌ Deployment/ops doctor failed")
        for failure in failures:
            print(f"   - {failure}")
        return 1

    print("✅ Deployment/ops doctor passed")
    print("   checks: systemd preflight gates, one-worker services, separate janitor, Redis-aware janitor ordering, quoted paths, runtime dir install, dependency script quoting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
