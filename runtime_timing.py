"""Central timing and Redis-latency policy for Hui Chat.

Every runtime component reads timing defaults through this module so the setup
wizard, JSON examples, Socket.IO, Redis-backed presence, voice, P2P cleanup,
and moderation do not drift to different fallback values.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

DEFAULT_RUNTIME_TIMING: dict[str, int | float] = {
    "socketio_ping_interval_seconds": 20,
    "socketio_ping_timeout_seconds": 15,
    "shared_state_heartbeat_seconds": 60,
    "shared_state_session_ttl_seconds": 300,
    "redis_connect_timeout_seconds": 1.0,
    "redis_socket_timeout_seconds": 1.0,
    "redis_health_check_interval_seconds": 30,
    "janitor_interval_seconds": 60,
    "voice_invite_cooldown_seconds": 8,
    "voice_dm_invite_ttl_seconds": 30,
    "voice_dm_active_ttl_seconds": 120,
    "p2p_file_session_ttl_seconds": 300,
    "admin_ip_ban_default_minutes": 0,
}

_LIMITS: dict[str, tuple[float, float]] = {
    "socketio_ping_interval_seconds": (5, 120),
    "socketio_ping_timeout_seconds": (5, 120),
    "shared_state_heartbeat_seconds": (15, 300),
    "shared_state_session_ttl_seconds": (60, 3600),
    "redis_connect_timeout_seconds": (0.1, 10.0),
    "redis_socket_timeout_seconds": (0.1, 10.0),
    "redis_health_check_interval_seconds": (5, 300),
    "janitor_interval_seconds": (10, 3600),
    "voice_invite_cooldown_seconds": (0, 300),
    "voice_dm_invite_ttl_seconds": (10, 3600),
    "voice_dm_active_ttl_seconds": (30, 86400),
    "p2p_file_session_ttl_seconds": (30, 86400),
    "admin_ip_ban_default_minutes": (0, 525600),
}

_INTEGER_KEYS = {
    "socketio_ping_interval_seconds",
    "socketio_ping_timeout_seconds",
    "shared_state_heartbeat_seconds",
    "shared_state_session_ttl_seconds",
    "redis_health_check_interval_seconds",
    "janitor_interval_seconds",
    "voice_invite_cooldown_seconds",
    "voice_dm_invite_ttl_seconds",
    "voice_dm_active_ttl_seconds",
    "p2p_file_session_ttl_seconds",
    "admin_ip_ban_default_minutes",
}


@dataclass(frozen=True)
class TimingIssue:
    level: str
    code: str
    detail: str
    fix: str = ""


def _number(settings: dict[str, Any] | None, key: str) -> int | float:
    settings = settings or {}
    default = DEFAULT_RUNTIME_TIMING[key]
    raw = settings.get(key, default)
    try:
        value = float(raw)
    except Exception:
        value = float(default)
    minimum, maximum = _LIMITS[key]
    value = max(minimum, min(maximum, value))
    if key in _INTEGER_KEYS:
        return int(round(value))
    return float(value)


def timing_value(settings: dict[str, Any] | None, key: str) -> int | float:
    if key not in DEFAULT_RUNTIME_TIMING:
        raise KeyError(key)
    return _number(settings, key)


def timing_int(settings: dict[str, Any] | None, key: str) -> int:
    return int(timing_value(settings, key))


def timing_float(settings: dict[str, Any] | None, key: str) -> float:
    return float(timing_value(settings, key))


def normalized_runtime_timing(settings: dict[str, Any] | None) -> dict[str, int | float]:
    values = {key: timing_value(settings, key) for key in DEFAULT_RUNTIME_TIMING}

    # The application heartbeat must refresh Redis well before the SID key TTL.
    heartbeat = int(values["shared_state_heartbeat_seconds"])
    minimum_ttl = max(60, heartbeat * 3)
    if int(values["shared_state_session_ttl_seconds"]) < minimum_ttl:
        values["shared_state_session_ttl_seconds"] = minimum_ttl

    # An active voice record must outlive the invite and repeated-call cooldown.
    invite = int(values["voice_dm_invite_ttl_seconds"])
    cooldown = int(values["voice_invite_cooldown_seconds"])
    minimum_active = max(120, invite * 2, cooldown * 3)
    if int(values["voice_dm_active_ttl_seconds"]) < minimum_active:
        values["voice_dm_active_ttl_seconds"] = minimum_active

    # P2P state must survive the configured transfer timeout plus cleanup margin.
    transfer_timeout_ms = 60000
    try:
        transfer_timeout_ms = max(1000, int((settings or {}).get("p2p_file_transfer_timeout_ms", 60000)))
    except Exception:
        pass
    minimum_p2p_ttl = max(30, int(math.ceil(transfer_timeout_ms / 1000.0)) + 30)
    if int(values["p2p_file_session_ttl_seconds"]) < minimum_p2p_ttl:
        values["p2p_file_session_ttl_seconds"] = minimum_p2p_ttl

    return values


def apply_runtime_timing_safety_defaults(settings: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = normalized_runtime_timing(settings)
    changes: list[dict[str, Any]] = []
    for key, value in normalized.items():
        old = settings.get(key)
        if old != value:
            settings[key] = value
            changes.append({"key": key, "old": old, "new": value, "reason": "central runtime timing policy"})
    return changes


def build_runtime_timing_report(settings: dict[str, Any] | None) -> dict[str, Any]:
    settings = settings or {}
    normalized = normalized_runtime_timing(settings)
    issues: list[TimingIssue] = []
    for key, default in DEFAULT_RUNTIME_TIMING.items():
        raw = settings.get(key, default)
        value = normalized[key]
        if raw != value:
            issues.append(TimingIssue("warn", f"normalize:{key}", f"{key}={raw!r} normalizes to {value!r}", f"Save {key}={value!r}."))

    heartbeat = int(normalized["shared_state_heartbeat_seconds"])
    ttl = int(normalized["shared_state_session_ttl_seconds"])
    if ttl < heartbeat * 3:
        issues.append(TimingIssue("fail", "shared-state-heartbeat-ttl", f"heartbeat={heartbeat}s; TTL={ttl}s", "Keep Redis SID TTL at least three heartbeat intervals."))
    else:
        issues.append(TimingIssue("pass", "shared-state-heartbeat-ttl", f"heartbeat={heartbeat}s; TTL={ttl}s"))

    ping_interval = int(normalized["socketio_ping_interval_seconds"])
    ping_timeout = int(normalized["socketio_ping_timeout_seconds"])
    if ping_timeout > ping_interval * 3:
        issues.append(TimingIssue("warn", "socketio-ping-window", f"ping interval={ping_interval}s; timeout={ping_timeout}s", "Use a timeout no more than roughly three ping intervals."))
    else:
        issues.append(TimingIssue("pass", "socketio-ping-window", f"ping interval={ping_interval}s; timeout={ping_timeout}s"))

    invite = int(normalized["voice_dm_invite_ttl_seconds"])
    active = int(normalized["voice_dm_active_ttl_seconds"])
    if active < invite * 2:
        issues.append(TimingIssue("fail", "voice-ttl-order", f"invite TTL={invite}s; active TTL={active}s", "Active call TTL must exceed invite TTL."))
    else:
        issues.append(TimingIssue("pass", "voice-ttl-order", f"invite TTL={invite}s; active TTL={active}s"))

    fail_count = sum(i.level == "fail" for i in issues)
    warn_count = sum(i.level == "warn" for i in issues)
    return {
        "overall": "fail" if fail_count else "warn" if warn_count else "pass",
        "values": normalized,
        "items": [asdict(i) for i in issues],
        "fail_count": fail_count,
        "warn_count": warn_count,
    }
