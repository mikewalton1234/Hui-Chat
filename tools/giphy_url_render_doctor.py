#!/usr/bin/env python3
"""Static doctor for GIPHY URL handling in Hui Chat.

The GIF picker must send the exact rendition URL returned by the server-side
GIPHY proxy. Rewriting selected URLs to a reconstructed /media/<id>/giphy.gif
URL before first render can produce GIPHY's "This content is not available"
placeholder, especially when URLs contain newer opaque media path segments or
case-sensitive IDs.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

CHECKS: list[tuple[str, str, str]] = [
    (
        "no room GIF pre-rewrite",
        "static/js/chat_parts/0040_room_browser_polling_embed.js",
        "_gifFallbackUrl(url) || url",
    ),
    (
        "no DM/group GIF pre-rewrite",
        "static/js/chat_parts/0043_group_history_dm_windows.js",
        "_gifFallbackUrl(url) || url",
    ),
]


def fail(msg: str) -> None:
    print(f"❌ {msg}")
    raise SystemExit(1)


def read(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        fail(f"missing expected file: {rel}")
    return p.read_text(encoding="utf-8")


def main() -> int:
    for label, rel, forbidden in CHECKS:
        text = read(rel)
        if forbidden in text:
            fail(f"{label}: selected GIPHY URLs are still rewritten before send in {rel}")

    inline = read("static/js/chat_parts/0019_gif_inline_reconnect.js")
    if "const base = safeGifUrl;" not in inline:
        fail("inline renderer must try the exact selected GIPHY URL before fallback")
    if "id.startsWith('v1.')" not in inline:
        fail("fallback helper must reject opaque v1.* media path segments")
    if not re.search(r"/\^\[A-Za-z0-9_-\]\{4,128\}\$/", inline):
        fail("fallback helper must validate case-sensitive GIPHY id characters")
    if "https://media.giphy.com/media/${id}/giphy.gif" not in inline:
        fail("fallback helper should use media.giphy.com for legacy id fallback")
    if "const canonical = (tries === 0 || !fallback) ? original : fallback;" not in inline:
        fail("retry path must use fallback only after the original URL has failed once")

    picker = read("static/js/chat_parts/0009_gif_picker.js")
    if "if (GifUI.onPick) GifUI.onPick(url);" not in picker:
        fail("picker must pass the normalized server URL to send handlers")

    print("✅ GIPHY URL render doctor passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
