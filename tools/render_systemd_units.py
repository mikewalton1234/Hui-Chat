#!/usr/bin/env python3
"""Render Hui Chat's systemd templates for an installed server path.

The checked-in units intentionally use the documented /opt/hui defaults.  The
root installer calls this helper so HUI_INSTALL_DIR, HUI_ENV_DIR, service user,
and service group cannot drift from the unit files that systemd actually runs.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
from typing import Iterable

UNIT_NAMES = ("hui-chat.service", "hui-chat@.service", "hui-chat-janitor.service")
_ACCOUNT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def _safe_systemd_path(value: str | Path, label: str) -> Path:
    text = str(value)
    if not text.startswith("/"):
        raise ValueError(f"{label} must be an absolute path")
    if any(ch.isspace() or ord(ch) < 32 for ch in text):
        raise ValueError(f"{label} cannot contain whitespace or control characters")
    return Path(text)


def _safe_account(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not _ACCOUNT_RE.fullmatch(text):
        raise ValueError(f"{label} is not a valid system account name")
    return text


def render_systemd_units(
    project_root: str | Path,
    output_dir: str | Path,
    *,
    env_path: str | Path,
    service_user: str,
    service_group: str,
    unit_names: Iterable[str] = UNIT_NAMES,
) -> list[Path]:
    root = _safe_systemd_path(project_root, "project root")
    target_dir = _safe_systemd_path(output_dir, "systemd output directory")
    env_file = _safe_systemd_path(env_path, "environment file")
    user = _safe_account(service_user, "service user")
    group = _safe_account(service_group, "service group")

    template_dir = root / "deploy" / "systemd"
    target_dir.mkdir(parents=True, exist_ok=True)
    replacements = {
        "/opt/hui/hui-chat": str(root),
        "/etc/hui/hui-chat.env": str(env_file),
        "User=hui": f"User={user}",
        "Group=hui": f"Group={group}",
    }
    written: list[Path] = []
    for name in unit_names:
        if name not in UNIT_NAMES:
            raise ValueError(f"unsupported Hui Chat unit: {name}")
        source = template_dir / name
        text = source.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        destination = target_dir / name
        destination.write_text(text, encoding="utf-8")
        destination.chmod(0o644)
        written.append(destination)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Hui Chat systemd units for an install")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--env-path", required=True)
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--service-group", required=True)
    args = parser.parse_args()
    for path in render_systemd_units(
        args.project_root,
        args.output_dir,
        env_path=args.env_path,
        service_user=args.service_user,
        service_group=args.service_group,
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
