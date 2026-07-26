#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
from pathlib import Path


VERSION_FILE = Path(__file__).resolve().parent / "VERSION.txt"


def _read_app_version() -> str:
    try:
        v = VERSION_FILE.read_text(encoding="utf-8").strip()
        return v or "0.0.0-dev"
    except Exception:
        return "0.0.0-dev"


# Application version (semantic-ish). Used for UI + packaging.
APP_VERSION = _read_app_version()

# Public project name. This is the software/repository identity.
PROJECT_NAME = "Hui Chat"

# Default visible chat-server name for first-run installs. Admins can change
# server_config.json -> server_name during setup to brand their own server.
DEFAULT_SERVER_NAME = PROJECT_NAME



def server_display_name(settings: dict | None = None, *, default: str | None = None) -> str:
    """Return the public chat-server display name for runtime/tool output.

    Hui Chat is the project/software name. ``server_name`` is the
    admin-selected name users should see for a deployed server.
    """
    fallback = str(default or DEFAULT_SERVER_NAME).replace("\r", " ").replace("\n", " ").strip() or DEFAULT_SERVER_NAME
    raw = str((settings or {}).get("server_name") or fallback).replace("\r", " ").replace("\n", " ").strip()
    return raw or fallback


def load_server_display_name(config_path: str | os.PathLike[str] | None = None, *, default: str | None = None) -> str:
    """Best-effort load of ``server_config.json -> server_name`` for helper tools.

    Tools must never fail just because branding config is missing/corrupt, so
    this returns the project default when the config cannot be read.
    """
    fallback = str(default or DEFAULT_SERVER_NAME).replace("\r", " ").replace("\n", " ").strip() or DEFAULT_SERVER_NAME
    if not config_path:
        config_path = Path(__file__).resolve().parent / CONFIG_FILE
    try:
        path = Path(config_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        return server_display_name(data if isinstance(data, dict) else {}, default=fallback)
    except Exception:
        return fallback



_MOBILE_UA_RE = re.compile(
    r"android|webos|iphone|ipod|blackberry|bb10|iemobile|opera mini|mobile|windows phone",
    re.IGNORECASE,
)
_TABLET_UA_RE = re.compile(r"ipad|tablet|kindle|silk|playbook", re.IGNORECASE)


def detect_mobile_client(user_agent: str | None = None, sec_ch_ua_mobile: str | None = None) -> dict:
    """Return non-security UI hints for the chat template.

    This is intentionally a hint, not an auth/security decision.  User-Agent
    strings can be wrong and desktop browsers can be resized narrow, so the
    browser runtime still uses matchMedia() as the source of truth for the live
    layout.  The server hint lets first paint include the correct stylesheet and
    useful metadata.
    """
    ua = str(user_agent or "")
    mobile_ch = str(sec_ch_ua_mobile or "").strip().lower()
    ch_says_mobile = mobile_ch in {"?1", "1", "true", "yes"}
    is_tablet = bool(_TABLET_UA_RE.search(ua))
    is_phone = bool(ch_says_mobile or _MOBILE_UA_RE.search(ua)) and not is_tablet
    is_mobile = bool(is_phone or is_tablet)
    profile = "tablet" if is_tablet else ("phone" if is_phone else "desktop")
    return {
        "profile": profile,
        "is_mobile": is_mobile,
        "is_phone": is_phone,
        "is_tablet": is_tablet,
        "body_class": "ec-device-mobile" if is_mobile else "ec-device-desktop",
    }


# Path to the JSON‐encrypted server configuration file
CONFIG_FILE = "server_config.json"

# Path to the file that holds your Fernet key for encrypting/decrypting CONFIG_FILE
KEY_FILE = "server_key.key"

# PostgreSQL connection string.
#   - Set DB_CONNECTION_STRING (preferred) or DATABASE_URL to override.
#   - Format: "postgresql://<username>:<password>@<host>:<port>/<dbname>"
#
# NOTE:
#   Avoid hardcoding real credentials in source control.
#   The fallback below is intentionally a placeholder.
DEFAULT_DB_CONNECTION_STRING = ""


def sanitize_postgres_dsn(dsn: str | None) -> str | None:
    """Best-effort sanitiser for Postgres DSNs.

    People sometimes paste placeholders like:
        postgresql://<user>:<pass>@<host>:5432/<db>
    which makes Postgres try to authenticate a role literally named
    "<user>" (angle brackets included).

    We defensively:
      - strip whitespace
      - remove any '<' and '>' characters
      - strip surrounding single/double quotes
    """
    if dsn is None:
        return None
    s = str(dsn).strip()
    if not s:
        return s
    # Remove common placeholder delimiters.
    if "<" in s or ">" in s:
        s = s.replace("<", "").replace(">", "")
    # Strip accidental surrounding quotes.
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    return s


def is_placeholder_postgres_dsn(dsn: str | None) -> bool:
    """Return True when a DSN still looks like example/template data.

    We specifically guard against the historical HuiChat placeholder
    postgresql://USER:PASSWORD@localhost:5432/hui_db and close variants.
    A custom database name must *not* be treated as invalid just because it is
    different from ``hui``.
    """
    s = sanitize_postgres_dsn(dsn)
    if s is None:
        return False
    s = str(s).strip()
    if not s:
        return False
    try:
        p = urlparse(s)
    except Exception:
        lowered = s.lower()
        return "user" in lowered and "password" in lowered
    user = str(p.username or "").strip().lower()
    password = str(p.password or "").strip().lower()
    host = str(p.hostname or "").strip().lower()
    db = str((p.path or "").lstrip("/") or "").strip().lower()
    return (user in {"user", "username", "your_user", "dbuser"} and password in {"password", "pass", "your_password", "dbpassword"}) or (user == "user" and db == "hui_db" and host in {"", "localhost", "127.0.0.1", "::1"})

def get_db_connection_string(settings: dict | None = None) -> str:
    """Return the PostgreSQL DSN.

    Priority:
      1) settings['database_url'] (if provided)
      2) environment variables DB_CONNECTION_STRING / DATABASE_URL
      3) DEFAULT_DB_CONNECTION_STRING
    """
    # 1) Explicit settings dict (preferred in the running server)
    if settings and settings.get("database_url"):
        candidate = str(sanitize_postgres_dsn(settings["database_url"]))
        if not is_placeholder_postgres_dsn(candidate):
            return candidate

    # 2) Environment overrides
    env = os.getenv("DB_CONNECTION_STRING") or os.getenv("DATABASE_URL")
    if env:
        candidate = str(sanitize_postgres_dsn(env))
        if not is_placeholder_postgres_dsn(candidate):
            return candidate

    # 3) If a local server_config.json exists (common in HuiChat), read it.
    #    This avoids relying on DEFAULT_DB_CONNECTION_STRING which may contain
    #    a non-existent Postgres role (e.g. OS username).
    try:
        base_dir = Path(__file__).resolve().parent
        cfg_path = base_dir / CONFIG_FILE
        if cfg_path.exists():
            data = cfg_path.read_text(encoding="utf-8").strip()
            if data.startswith("{") and data.endswith("}"):
                cfg = json.loads(data)
                if isinstance(cfg, dict) and cfg.get("database_url"):
                    candidate = str(sanitize_postgres_dsn(cfg["database_url"]))
                    if not is_placeholder_postgres_dsn(candidate):
                        return candidate
    except Exception:
        # If config is encrypted or unreadable, fall through.
        pass

    # 4) Fallback
    # Return an empty string instead of a fake/example DSN. This lets setup ask
    # for the real PostgreSQL user/host/database name, including custom DB names.
    return str(sanitize_postgres_dsn(DEFAULT_DB_CONNECTION_STRING) or "")



from urllib.parse import urlparse, urlunparse


def redact_postgres_dsn(dsn: str | None) -> str | None:
    """Return a DSN safe to print (password redacted).

    Example:
        postgresql://user:***@localhost:5432/hui_db
    """
    if dsn is None:
        return None
    s = sanitize_postgres_dsn(dsn)
    if not s:
        return s
    try:
        p = urlparse(s)
        if p.scheme and "postgres" in p.scheme:
            netloc = p.netloc
            if "@" in netloc:
                creds, hostport = netloc.rsplit("@", 1)
                if ":" in creds:
                    user, _ = creds.split(":", 1)
                    creds = f"{user}:***"
                netloc = f"{creds}@{hostport}"
            p2 = p._replace(netloc=netloc)
            return urlunparse(p2)
    except Exception:
        pass
    # Fallback: best-effort redaction for common patterns.
    return re.sub(r":([^:@/]+)@", r":***@", str(s))


def postgres_dsn_parts(dsn: str | None) -> dict:
    """Extract user/host/port/dbname from a Postgres DSN (best-effort)."""
    out = {"scheme": None, "user": None, "host": None, "port": None, "db": None}
    if not dsn:
        return out
    s = sanitize_postgres_dsn(dsn)
    try:
        p = urlparse(s)
        out["scheme"] = p.scheme
        if p.username:
            out["user"] = p.username
        if p.hostname:
            out["host"] = p.hostname
        if p.port:
            out["port"] = p.port
        if p.path and len(p.path) > 1:
            out["db"] = p.path.lstrip("/")
        return out
    except Exception:
        return out


# Backward-compatible constant (reads env at import time).
# Prefer get_db_connection_string() for runtime evaluation.
DB_CONNECTION_STRING = get_db_connection_string()


CHAT_PARTS_DIR = Path(__file__).resolve().parent / "static" / "js" / "chat_parts"
SOUND_PACKS_DIR = Path(__file__).resolve().parent / "static" / "js" / "sound_packs"
SOUND_PACK_SCRIPT_PATHS = [
    "/static/js/sound_packs/0001_hui_modern_generated.js",
    "/static/js/sound_packs/0002_classic_messenger_generated.js",
]


def normalize_sound_pack_identifier(value: object, default: str = "hui_modern_generated") -> str:
    """Return a safe client sound-pack/sound identifier.

    Sound packs are JavaScript files loaded in the browser.  The server stores
    only the selected identifier; custom online packs may define IDs the server
    has never seen, so validation is intentionally syntax-based instead of a
    fixed allow-list.  Keep this normalizer aligned with the browser sound-pack
    registry so admin-saved custom IDs remain selectable after reload.
    """
    fallback = str(default or "hui_modern_generated").strip().lower() or "hui_modern_generated"
    raw = str(value or fallback).strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    if raw.endswith(".js"):
        raw = raw[:-3]
    raw = re.sub(r"[^a-z0-9_:-]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_:")
    file_aliases = {
        "0001_hui_modern_generated": "hui_modern_generated",
        "0002_classic_messenger_generated": "classic_messenger_generated",
    }
    raw = file_aliases.get(raw, raw)
    if not raw or len(raw) > 96:
        return fallback
    return raw


def sound_pack_local_builtins_enabled(value: object, *, default: bool = True) -> bool:
    """Return the effective local sound-pack loading flag.

    Config files are often hand-edited, so values like ``"false"`` or ``"0"``
    must not behave as truthy strings in the admin UI or client config.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "none"}:
        return False
    return bool(default)


def sanitize_sound_pack_external_urls(value: object, *, max_urls: int = 12) -> list[str]:
    """Return safe HTTPS URLs for browser-loaded online sound-pack JS files.

    Admin-configured sound packs are intentionally client-side: Hui Chat only
    stores the URL list and prints script tags.  The JavaScript is fetched by the
    browser from the remote host.  Only HTTPS ``.js`` URLs without credentials
    are allowed so admins do not accidentally create mixed-content, credential,
    or arbitrary-scheme script includes.
    """
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[\r\n,]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    urls: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        raw = str(item or "").strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            parsed = urlparse(raw)
        except Exception:
            continue
        if parsed.scheme.lower() != "https" or not parsed.netloc:
            continue
        if parsed.username or parsed.password:
            continue
        path = parsed.path or ""
        if not path.lower().endswith(".js"):
            continue
        # Drop fragments; scripts should be cacheable/shareable URLs.
        clean = parsed._replace(fragment="").geturl()
        if len(clean) > 2048 or clean in seen:
            continue
        seen.add(clean)
        urls.append(clean)
        if len(urls) >= max_urls:
            break
    return urls


def sound_pack_script_src(script_url: str, app_version: str | None = None) -> str:
    """Return the script URL to render in the template.

    Local Hui Chat assets get the app-version cache buster.  Online sound-pack
    URLs are left exactly as the admin configured them, because some CDNs use
    query strings for version pins or integrity workflows.
    """
    raw = str(script_url or "").strip()
    if not raw:
        return ""
    if raw.startswith("/"):
        suffix = f"?v={app_version}" if app_version else ""
        return f"{raw}{suffix}"
    return raw


CHAT_SCRIPT_PARTS = [
    "/static/js/chat_parts/0001_core_socket_crypto.js",
    "/static/js/chat_parts/0002_state_storage.js",
    "/static/js/chat_parts/0003_help_system.js",
    "/static/js/chat_parts/0004_navigation_invites_font.js",
    "/static/js/chat_parts/0005_pm_history_utils.js",
    "/static/js/chat_parts/0006_torrent_helpers.js",
    "/static/js/chat_parts/0007_dom_theme_helpers.js",
    "/static/js/chat_parts/0008_emoji_picker.js",
    "/static/js/chat_parts/0009_gif_picker.js",
    "/static/js/chat_parts/0010_auth_refresh_lock.js",
    "/static/js/chat_parts/0010b_idle_refresh.js",
    "/static/js/chat_parts/0010c_auth_fetch_forms.js",
    "/static/js/chat_parts/0011_toasts_unlock_voicebase.js",
    "/static/js/chat_parts/0011b_media_engine_adapter.js",
    "/static/js/chat_parts/0012_webcam_ui.js",
    "/static/js/chat_parts/0013_voice_core.js",
    "/static/js/chat_parts/0014_voice_dm_calls.js",
    "/static/js/chat_parts/0015_voice_room_calls.js",
    "/static/js/chat_parts/0016_dm_crypto_helpers.js",
    "/static/js/chat_parts/0017_unlock_modal.js",
    "/static/js/chat_parts/0018_windows_manager.js",
    "/static/js/chat_parts/0019_gif_inline_reconnect.js",
    "/static/js/chat_parts/0020_chat_log_rendering.js",
    "/static/js/chat_parts/0021_dm_file_torrent_rendering.js",
    "/static/js/chat_parts/0022_p2p_transfer_ui.js",
    "/static/js/chat_parts/0023_room_reactions.js",
    "/static/js/chat_parts/0024_dock_sections_tabs.js",
    "/static/js/chat_parts/0025_dock_identity_friends.js",
    "/static/js/chat_parts/0026_dock_search_inputs.js",
    "/static/js/chat_parts/0027_dock_alert_rail.js",
    "/static/js/chat_parts/0028_dock_counts.js",
    "/static/js/chat_parts/0029_friends_requests_blocks.js",
    "/static/js/chat_parts/0030_hub_menus.js",
    "/static/js/chat_parts/0031_user_context_menu.js",
    "/static/js/chat_parts/0032_my_hub_identity.js",
    "/static/js/chat_parts/0033_avatar_presets_upload.js",
    "/static/js/chat_parts/0033b_my_profile_editor.js",
    "/static/js/chat_parts/0034_profile_window.js",
    "/static/js/chat_parts/0035_missed_presence_embed.js",
    "/static/js/chat_parts/0036_room_browser_state.js",
    "/static/js/chat_parts/0037_room_browser_data_scope.js",
    "/static/js/chat_parts/0037b_room_browser_meta_presence.js",
    "/static/js/chat_parts/0037c_room_browser_row_rendering.js",
    "/static/js/chat_parts/0038_room_browser_selection.js",
    "/static/js/chat_parts/0039_room_browser_details_actions.js",
    "/static/js/chat_parts/0039b_room_browser_modals_create_invite.js",
    "/static/js/chat_parts/0039c_room_browser_init.js",
    "/static/js/chat_parts/0040_room_browser_polling_embed.js",
    "/static/js/chat_parts/0041_rooms_runtime.js",
    "/static/js/chat_parts/0041b_room_moderator_embed.js",
    "/static/js/chat_parts/0042_group_invites.js",
    "/static/js/chat_parts/0043_group_history_dm_windows.js",
    "/static/js/chat_parts/0044_room_group_e2ee.js",
    "/static/js/chat_parts/0045_transfers_crypto.js",
    "/static/js/chat_parts/0046_transfers_signal_voice_events.js",
    "/static/js/chat_parts/0047_settings_modal.js",
    "/static/js/chat_parts/0047b_loading_splash.js",
    "/static/js/chat_parts/0048_boot_presence_dom.js",
    "/static/js/chat_parts/0048b_reconnect_restore_runtime.js",
    "/static/js/chat_parts/0048c_room_invites_webcam_ui.js",
    "/static/js/chat_parts/0049_motion_fx.js",
    "/static/js/chat_parts/0050_mobile_layout.js",
    "/static/js/chat_parts/0051_hub_collapse.js",
]


def get_chat_script_parts() -> list[str]:
    """Return the ordered frontend chat source files served by chat.html.

    Uses an explicit manifest instead of scanning the directory so stale files left
    behind by in-place unzip upgrades do not get auto-loaded into the page.
    """
    return [path for path in CHAT_SCRIPT_PARTS if (CHAT_PARTS_DIR / Path(path).name).is_file()]


def get_sound_pack_script_urls(settings: dict | None = None) -> list[str]:
    """Return UI sound-pack scripts loaded before the chat runtime parts.

    Admin-configured HTTPS URLs are listed first so online/CDN sound packs are
    the primary source.  Local built-in packs remain available as a safe offline
    fallback unless ``sound_pack_load_local_builtins`` is explicitly false.
    """
    settings = settings or {}
    urls: list[str] = []
    seen_urls: set[str] = set()

    for url in sanitize_sound_pack_external_urls(settings.get("sound_pack_external_urls")):
        urls.append(url)
        seen_urls.add(url)

    if not sound_pack_local_builtins_enabled(settings.get("sound_pack_load_local_builtins", True), default=True):
        return urls

    seen_names: set[str] = set()
    for path in SOUND_PACK_SCRIPT_PATHS:
        name = Path(path).name
        if name.endswith('.js') and (SOUND_PACKS_DIR / name).is_file():
            url = f"/static/js/sound_packs/{name}"
            if url not in seen_urls:
                urls.append(url)
                seen_urls.add(url)
            seen_names.add(name)

    try:
        for file_path in sorted(SOUND_PACKS_DIR.glob('*.js')):
            name = file_path.name
            # Only serve simple local filenames; no dotfiles, no nested paths.
            if name.startswith('.') or '/' in name or '\\' in name or name in seen_names:
                continue
            url = f"/static/js/sound_packs/{name}"
            if url not in seen_urls:
                urls.append(url)
                seen_urls.add(url)
            seen_names.add(name)
    except Exception:
        pass

    return urls


def get_chat_script_urls() -> list[str]:
    """Return the ordered frontend script URLs for chat.html.

    Hui Chat serves the modular files in ``static/js/chat_parts`` directly.
    the removed legacy bundle is intentionally no longer part of the runtime so
    the browser always uses the same files developers edit.
    """
    return get_chat_script_parts()
