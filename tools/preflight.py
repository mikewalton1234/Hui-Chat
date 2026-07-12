#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask

from main import load_settings, apply_env_overrides, configure_logging
from constants import server_display_name
from preflight import run_preflight, format_preflight_report


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hui Chat preflight tool for the configured server")
    p.add_argument("--config", default="server_config.json", help="path to server config JSON")
    p.add_argument("--no-db", action="store_true", help="skip the live database check")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg_path = Path(args.config)
    settings = load_settings(cfg_path)
    apply_env_overrides(settings)
    configure_logging(settings)

    app = Flask(__name__)
    with app.app_context():
        result = run_preflight(settings, settings_file=cfg_path, init_db_pool_if_needed=not args.no_db, include_database=not args.no_db)
        print(f"Preflight report for {server_display_name(settings)}")
        print(format_preflight_report(result))
        if result.get("overall") == "fail":
            raise SystemExit(2)


if __name__ == '__main__':
    main()
