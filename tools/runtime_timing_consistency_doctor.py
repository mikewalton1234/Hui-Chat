#!/usr/bin/env python3
"""Verify that Hui Chat timing defaults and cross-component relationships stay aligned."""
from __future__ import annotations
import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime_timing import DEFAULT_RUNTIME_TIMING, build_runtime_timing_report, normalized_runtime_timing


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def main() -> None:
    server = json.loads((ROOT / "server_config.example.json").read_text())
    settings = json.loads((ROOT / "settings.example.json").read_text())
    for key, expected in DEFAULT_RUNTIME_TIMING.items():
        if server.get(key) != expected:
            fail(f"server_config.example.json {key}={server.get(key)!r}, expected {expected!r}")
        if settings.get(key) != expected:
            fail(f"settings.example.json {key}={settings.get(key)!r}, expected {expected!r}")

    report = build_runtime_timing_report(server)
    if report.get("overall") == "fail":
        fail(f"default timing report failed: {report}")

    checks = {
        "server_init.py": ["socketio_ping_interval_seconds", "socketio_ping_timeout_seconds", "redis_connect_timeout_seconds", "redis_socket_timeout_seconds", "redis_health_check_interval_seconds"],
        "preflight.py": ["redis_connect_timeout_seconds", "redis_socket_timeout_seconds", "redis_health_check_interval_seconds"],
        "redis_socketio_readiness.py": ["redis_connect_timeout_seconds", "redis_socket_timeout_seconds"],
        "realtime/state.py": ["shared_state_session_ttl_seconds", "redis_connect_timeout_seconds", "redis_socket_timeout_seconds"],
        "realtime/presence_social.py": ["hui_presence_heartbeat"],
        "static/js/chat_parts/0048b_reconnect_restore_runtime.js": ["hui_presence_heartbeat", "shared_state_heartbeat_seconds"],
        "socket_handlers.py": ["p2p_file_session_ttl_seconds", "voice_dm_invite_ttl_seconds", "voice_dm_active_ttl_seconds"],
        "realtime/voice.py": ["voice_invite_cooldown_seconds", "voice_dm_invite_ttl_seconds", "voice_dm_active_ttl_seconds"],
        "routes_admin_tools.py": ["admin_ip_ban_default_minutes", "duration_minutes=duration_minutes"],
        "janitor.py": ['timing_int(settings, "janitor_interval_seconds")'],
        "replit_bootstrap.py": ["config.update(DEFAULT_RUNTIME_TIMING)"],
    }
    for rel, tokens in checks.items():
        text = (ROOT / rel).read_text(errors="ignore")
        for token in tokens:
            if token not in text:
                fail(f"{rel} missing {token}")

    # Catch the historical fallbacks that drifted from the example config.
    forbidden = {
        "socket_handlers.py": [
            'voice_dm_invite_ttl_seconds", 90',
            'voice_dm_active_ttl_seconds", 3600',
            'p2p_file_session_ttl_seconds", 900',
            'max(float(timing_int(settings, "voice_dm_active_ttl_seconds")), 120)',
            'ttl = 3600',
        ],
        "realtime/voice.py": [
            'voice_dm_invite_ttl_seconds", 90',
            'voice_dm_active_ttl_seconds", 3600',
            'voice_invite_cooldown_seconds", 2',
            'max(float(timing_int(settings, "voice_dm_invite_ttl_seconds")), 60)',
        ],
    }
    for rel, tokens in forbidden.items():
        text = (ROOT / rel).read_text(errors="ignore")
        for token in tokens:
            if token in text:
                fail(f"{rel} still contains inconsistent fallback {token}")


    server_init = (ROOT / "server_init.py").read_text(errors="ignore")
    routes_auth = (ROOT / "routes_auth.py").read_text(errors="ignore")
    setup_text = (ROOT / "interactive_setup.py").read_text(errors="ignore")
    if 'name="refresh_token_days", minimum=1, maximum=365' not in server_init:
        fail("JWT refresh-token lifetime does not match setup's 365-day bound")
    if 'refresh_days = max(1, min(365' not in routes_auth:
        fail("LAN refresh cookie lifetime does not match JWT refresh-token bound")
    for token in ('"min": 0, "max": 300', '"min": 10, "max": 3600', '"min": 30, "max": 86400'):
        if token not in setup_text:
            fail(f"voice timing setup bound missing: {token}")

    normalized = normalized_runtime_timing(server)
    assert normalized["shared_state_session_ttl_seconds"] >= normalized["shared_state_heartbeat_seconds"] * 3
    assert normalized["voice_dm_active_ttl_seconds"] >= normalized["voice_dm_invite_ttl_seconds"] * 2
    print("PASS: runtime timing defaults, Redis heartbeat/TTL, voice, P2P, janitor, and IP-ban durations are consistent")


if __name__ == "__main__":
    main()
