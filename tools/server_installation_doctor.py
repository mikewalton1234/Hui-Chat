#!/usr/bin/env python3
"""Static and isolated checks for the root server installer."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_server.sh"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def require(text: str, token: str, failures: list[str]) -> None:
    if token not in text:
        failures.append(f"installer missing: {token}")


def forbid(text: str, token: str, failures: list[str]) -> None:
    if token in text:
        failures.append(f"installer still contains unsafe/stale behavior: {token}")


def main() -> int:
    failures: list[str] = []
    text = SCRIPT.read_text(encoding="utf-8")
    rc = subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode
    if rc:
        failures.append("install_server.sh fails bash -n")

    for token in (
        "pacman -Syu --needed --noconfirm python python-pip postgresql valkey rsync",
        "valkey.service valkey-server.service redis.service redis-server.service",
        "systemctl stop hui-chat.service hui-chat-janitor.service",
        "systemctl list-units --all 'hui-chat@*.service'",
        "production-config-fix --production-config-blocking-only --production-live-check",
        "--redis-socketio-check --redis-blocking-only",
        "read_env_value(p, key) or generated",
        "Preserving the existing PostgreSQL connection and credentials",
        "HUI_RESET_DATABASE_CREDENTIALS",
        "JSON is authoritative for non-secret tuning",
        "installer_defaults",
        "systemctl restart hui-chat.service",
        "hui-chat@${port}.service",
        "systemctl restart hui-chat-janitor.service",
        "tools/render_systemd_units.py",
        '--project-root "$INSTALL_DIR"',
        '--env-path "$ENV_PATH"',
        '--service-user "$SERVICE_USER"',
        '--service-group "$SERVICE_GROUP"',
        "Secrets: $ENV_PATH",
    ):
        require(text, token, failures)

    for token in (
        "generate_secret_bundle(include_crypto=True)\nsecrets =",
        "systemctl enable --now hui-chat.service hui-chat-janitor.service",
        "pacman -Syu --needed --noconfirm python python-pip postgresql redis rsync",
    ):
        forbid(text, token, failures)

    # Verify the actual secret merge primitive used by the installer preserves
    # stable keys and unrelated provider settings across repeated writes.
    from secret_manager import read_env_value, write_env_secrets
    with tempfile.TemporaryDirectory(prefix="hui-installer-env-") as tmp:
        env_path = Path(tmp) / "hui-chat.env"
        env_path.write_text(
            "SECRET_KEY=stable-session-key-abcdefghijklmnopqrstuvwxyz\n"
            "JWT_SECRET_KEY=stable-jwt-key-abcdefghijklmnopqrstuvwxyz\n"
            "HUI_GIPHY_API_KEY=keep-provider-key\n",
            encoding="utf-8",
        )
        write_env_secrets(
            {
                "SECRET_KEY": read_env_value(env_path, "SECRET_KEY") or "replacement",
                "JWT_SECRET_KEY": read_env_value(env_path, "JWT_SECRET_KEY") or "replacement",
                "DATABASE_URL": "postgresql://local/test",
            },
            path=env_path,
        )
        if read_env_value(env_path, "SECRET_KEY") != "stable-session-key-abcdefghijklmnopqrstuvwxyz":
            failures.append("secret merge rotated SECRET_KEY")
        if read_env_value(env_path, "JWT_SECRET_KEY") != "stable-jwt-key-abcdefghijklmnopqrstuvwxyz":
            failures.append("secret merge rotated JWT_SECRET_KEY")
        if "HUI_GIPHY_API_KEY=keep-provider-key" not in env_path.read_text(encoding="utf-8"):
            failures.append("secret merge deleted an unrelated provider setting")

    # Render into an isolated custom layout and verify every path/account was
    # substituted. This catches installers that advertise custom paths but keep
    # hard-coded /opt or /etc values in systemd.
    from tools.render_systemd_units import render_systemd_units
    with tempfile.TemporaryDirectory(prefix="hui-systemd-render-") as tmp:
        tmp_path = Path(tmp)
        project_copy = tmp_path / "custom-install"
        (project_copy / "deploy").mkdir(parents=True)
        os.symlink(ROOT / "deploy" / "systemd", project_copy / "deploy" / "systemd")
        output_dir = tmp_path / "units"
        env_path = tmp_path / "config" / "hui.env"
        written = render_systemd_units(
            project_copy, output_dir, env_path=env_path,
            service_user="hui_test", service_group="hui_group",
        )
        if len(written) != 3:
            failures.append("systemd renderer did not produce all three units")
        for unit_path in written:
            unit = unit_path.read_text(encoding="utf-8")
            for expected in (str(project_copy), str(env_path), "User=hui_test", "Group=hui_group"):
                if expected not in unit:
                    failures.append(f"{unit_path.name} missing rendered value: {expected}")
            if "/opt/hui/hui-chat" in unit or "/etc/hui/hui-chat.env" in unit:
                failures.append(f"{unit_path.name} retained a hard-coded install path")

    if failures:
        print("FAIL: server installation doctor")
        for failure in failures:
            print(" -", failure)
        return 1
    print("PASS: server installation/upgrade flow preserves credentials, stops all old instances, renders custom systemd paths/users, uses Valkey/Redis portably, applies production fixes, and restarts the validated topology")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
