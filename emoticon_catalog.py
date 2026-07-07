#!/usr/bin/env python3
"""Neutral emoticon catalog and asset helpers.

The built-in catalog maps typed shortcuts to image assets. Image files can be
served from the project-root ``emoticons`` directory or from an admin-configured
external image base URL. The picker displays images only; shortcut text stays
behind the scenes for insertion and message replacement.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote
from typing import Any
from urllib.parse import urlparse


_ALLOWED_EXTS = ("gif", "webp", "png", "jpg", "jpeg")
_SAFE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.IGNORECASE)
_SAFE_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.(gif|webp|png|jpg|jpeg)$", re.IGNORECASE)
_SAFE_CODE_RE = re.compile(r"^[^\r\n\t]{1,32}$")
_DEFAULT_EXTERNAL_ASSET_BASE_URL = "https://raw.githubusercontent.com/chinhodado/ym_emo_fb/master/images"
_DEFAULT_EXTERNAL_REPO_URL = "https://github.com/chinhodado/ym_emo_fb"


def safe_existing_file_under(root: Path, relative_name: str) -> Path | None:
    try:
        base = root.resolve()
        candidate = (base / str(relative_name or "")).resolve()
        if base == candidate or base in candidate.parents:
            return candidate
    except Exception:
        return None
    return None


# name, display label, local filename, external filename, typed shortcut aliases, category
BUILTIN_EMOTICONS: tuple[tuple[str, str, str, str, tuple[str, ...], str], ...] = (('happy', 'Happy', '1.gif', 'happy.gif', (':)', ':-)'), 'base'), ('sad', 'Sad', '2.gif', 'sad.gif', (':(', ':-('), 'base'), ('winking', 'Winking', '3.gif', 'winking.gif', (';)', ';-)'), 'base'), ('grin', 'Big grin', '4.gif', 'grin.gif', (':D', ':-D', ':d', ':-d'), 'base'), ('batting', 'Batting eyelashes', '5.gif', 'batting.gif', (';;)',), 'base'), ('hug', 'Big hug', '6.gif', 'hug.gif', ('>:D<', '>:d<'), 'base'), ('confused', 'Confused', '7.gif', 'confused.gif', (':-/', ':-\\'), 'base'), ('love', 'Love struck', '8.gif', 'love.gif', (':x', ':-x', ':X', ':-X'), 'base'), ('blushing', 'Blushing', '9.gif', 'blushing.gif', (':">',), 'base'), ('tongue', 'Tongue', '10.gif', 'tongue.gif', (':-P', ':P', ':-p', ':p'), 'base'), ('kiss', 'Kiss', '11.gif', 'kiss.gif', (':-*', ':*'), 'base'), ('broken', 'Broken heart', '12.gif', 'broken.gif', ('=((',), 'version6'), ('surprised', 'Surprised', '13.gif', 'surprised.gif', (':-O', ':O', ':-o', ':o'), 'base'), ('angry', 'Angry', '14.gif', 'angry.gif', ('X-(', 'x-(', 'X(', 'x('), 'base'), ('smug', 'Smug', '15.gif', 'smug.gif', (':->', ':>'), 'base'), ('cool', 'Cool', '16.gif', 'cool.gif', ('B-)', 'b-)'), 'base'), ('worried', 'Worried', '17.gif', 'worried.gif', (':-S', ':-s'), 'base'), ('whew', 'Whew', '18.gif', 'whew.gif', ('#:-S', '#:-s'), 'version6'), ('devil', 'Devil', '19.gif', 'devil.gif', ('>:)',), 'base'), ('crying', 'Crying', '20.gif', 'crying.gif', (':((',), 'base'), ('laughing', 'Laughing', '21.gif', 'laughing.gif', (':))', ':-))'), 'base'), ('straight', 'Straight face', '22.gif', 'straight.gif', (':|', ':-|'), 'base'), ('raised', 'Raised eyebrows', '23.gif', 'raised.gif', ('/:)',), 'base'), ('rolling', 'Rolling on the floor', '24.gif', 'rolling.gif', ('=))',), 'version6'), ('angel', 'Angel', '25.gif', 'angel.gif', ('O:-)', 'o:-)', '0:-)', '0:)'), 'base'), ('nerd', 'Nerd', '26.gif', 'nerd.gif', (':-B', ':-b'), 'base'), ('talkhand', 'Talk to the hand', '27.gif', 'talkhand.gif', ('=;',), 'base'), ('sleepy', 'Sleepy', '28.gif', 'sleepy.gif', ('I-)', 'i-)', '|-)'), 'base'), ('rollingeyes', 'Rolling eyes', '29.gif', 'rollingeyes.gif', ('8-|',), 'base'), ('loser', 'Loser', '30.gif', 'loser.gif', ('L-)', 'l-)'), 'version6'), ('sick', 'Sick', '31.gif', 'sick.gif', (':-&',), 'base'), ('donttell', "Don't tell", '32.gif', 'donttell.gif', (':-$',), 'base'), ('notalking', 'No talking', '33.gif', 'notalking.gif', ('[-(',), 'base'), ('clown', 'Clown', '34.gif', 'clown.gif', (':o)', ':O)'), 'base'), ('silly', 'Silly', '35.gif', 'silly.gif', ('8-}',), 'base'), ('party', 'Party', '36.gif', 'party.gif', ('<:-P', '<:-p'), 'version6'), ('yawn', 'Yawn', '37.gif', 'yawn.gif', ('(:|',), 'base'), ('drooling', 'Drooling', '38.gif', 'drooling.gif', ('=P~', '=p~'), 'base'), ('thinking', 'Thinking', '39.gif', 'thinking.gif', (':-?',), 'base'), ('doh', "D'oh", '40.gif', 'doh.gif', ('#-o', '#-O'), 'base'), ('applause', 'Applause', '41.gif', 'applause.gif', ('=D>', '=d>'), 'base'), ('nail', 'Nail biting', '42.gif', 'nail.gif', (':-SS', ':-Ss', ':-sS', ':-ss'), 'version6'), ('hipno', 'Hypnotized', '43.gif', 'hipno.gif', ('@-)',), 'base'), ('liar', 'Liar', '44.gif', 'liar.gif', (':^O', ':^o'), 'base'), ('waiting', 'Waiting', '45.gif', 'waiting.gif', (':-w', ':-W'), 'version6'), ('sigh', 'Sigh', '46.gif', 'sigh.gif', (':-<',), 'version6'), ('phbbt', 'Phbbbbt', '47.gif', 'phbbt.gif', ('>:P', '>:p'), 'version6'), ('cowboy', 'Cowboy', '48.gif', 'cowboy.gif', ('<):)',), 'base'), ('pig', 'Pig', '49.gif', 'pig.gif', (':@)',), 'base'), ('cow', 'Cow', '50.gif', 'cow.gif', ('3:-O', '3:-o'), 'base'), ('monkey', 'Monkey', '51.gif', 'monkey.gif', (':(|)',), 'base'), ('chicken', 'Chicken', '52.gif', 'chicken.gif', ('~:>',), 'base'), ('rose', 'Rose', '53.gif', 'rose.gif', ('@};-',), 'base'), ('goodluck', 'Good luck', '54.gif', 'goodluck.gif', ('%%-',), 'base'), ('flag', 'Flag', '55.gif', 'flag.gif', ('**==',), 'base'), ('pumpkin', 'Pumpkin', '56.gif', 'pumpkin.gif', ('(~~)',), 'base'), ('coffee', 'Coffee', '57.gif', 'coffee.gif', ('~o)', '~O)'), 'base'), ('idea', 'Idea', '58.gif', 'idea.gif', ('*-:)',), 'base'), ('skull', 'Skull', '59.gif', 'skull.gif', ('8-X', '8-x'), 'base'), ('bug', 'Bug', '60.gif', 'bug.gif', ('=:)',), 'base'), ('alien', 'Alien', '61.gif', 'alien.gif', ('>-)',), 'base'), ('frustrated', 'Frustrated', '62.gif', 'frustrated.gif', (':-L', ':-l'), 'base'), ('praying', 'Praying', '63.gif', 'praying.gif', ('[-o<', '[-O<'), 'base'), ('money', 'Money eyes', '64.gif', 'money.gif', ('$-)',), 'base'), ('whistling', 'Whistling', '65.gif', 'whistling.gif', (':-"',), 'base'), ('beatup', 'Punch', '66.gif', 'beatup.gif', ('b-(', 'B-('), 'base'), ('peace', 'Peace sign', '67.gif', 'peace.gif', (':)>-',), 'base'), ('shame', 'Shame on you', '68.gif', 'shame.gif', ('[-X', '[-x'), 'base'), ('dancing', 'Dancing', '69.gif', 'dancing.gif', ('\\:D/', '\\:d/'), 'base'), ('bringit', 'Bring it on', '70.gif', 'bringit.gif', ('>:/',), 'version6'), ('hehe', 'Hee hee', '71.gif', 'hehe.gif', (';))',), 'version6'), ('hiro', 'Hiro', '72.gif', 'hiro.gif', ('o->', 'O->'), 'extra'), ('billy', 'Billy', '73.gif', 'billy.gif', ('o=>', 'O=>'), 'extra'), ('april', 'April', '74.gif', 'april.gif', ('o-+', 'O-+'), 'extra'), ('yinyang', 'Yinyang', '75.gif', 'yinyang.gif', ('(%)',), 'extra'), ('chatterbox', 'Chatterbox', '76.gif', 'chatterbox.gif', (':-@',), 'version6'), ('notworthy', 'Not worthy', '77.gif', 'notworthy.gif', ('^:)^',), 'version6'), ('ohgoon', 'Oh go on', '78.gif', 'ohgoon.gif', (':-j', ':-J'), 'version6'), ('star', 'Star', '79.gif', 'star.gif', ('(*)',), 'version6'), ('phone', 'On the phone', '100.gif', 'phone.gif', (':)]',), 'version7'), ('callme', 'Call me', '101.gif', 'callme.gif', (':-c',), 'version7'), ('witsend', "At wit's end", '102.gif', 'witsend.gif', ('~x(', '~X('), 'version7'), ('bye', 'Wave', '103.gif', 'bye.gif', (':-h',), 'version7'), ('timeout', 'Timeout', '104.gif', 'timeout.gif', (':-t', ':-T'), 'version7'), ('daydreaming', 'Daydreaming', '105.gif', 'daydreaming.gif', ('8->',), 'version7'), ('dontknow', "I don't know", '106.gif', 'dontknow.gif', (':-??',), 'version7'), ('notlistening', 'Not listening', '107.gif', 'notlistening.gif', ('%-(',), 'version7'), ('puppy', 'Puppy', '108.gif', 'puppy.gif', (':O3', ':o3'), 'version8'), ('dontsee', "I don't want to see", '109.gif', 'dontsee.gif', ('X_X',), 'version9'), ('hurryup', 'Hurry up', '110.gif', 'hurryup.gif', (':!!',), 'version9'), ('rockon', 'Rock on', '111.gif', 'rockon.gif', ('\\m/',), 'version9'), ('thumbdown', 'Thumbs down', '112.gif', 'thumbdown.gif', (':-q',), 'version9'), ('thumbup', 'Thumbs up', '113.gif', 'thumbup.gif', (':-bd',), 'version9'), ('wasnotme', "It wasn't me", '114.gif', 'wasnotme.gif', ('^#(^',), 'version9'), ('bee', 'Bee', '115.gif', 'bee.gif', (':bz',), 'version9'), ('cheer', 'Cheer', '120.gif', 'cheer.gif', ('~^o^~', '~^O^~'), 'version11'), ('dizzy', 'Dizzy', '121.gif', 'dizzy.gif', ("'@^@|||",), 'version11'), ('cook', 'Cook', '122.gif', 'cook.gif', ('[]---',), 'version11'), ('eat', 'Eat', '123.gif', 'eat.gif', ('^o^||3', '^O^||3'), 'version11'), ('giveup', 'Give up', '124.gif', 'giveup.gif', (':-(||>',), 'version11'), ('cold', 'Cold', '125.gif', 'cold.gif', ("'+_+",), 'version11'), ('hot', 'Hot', '126.gif', 'hot.gif', (':::^^:::',), 'version11'), ('music', 'Music', '127.gif', 'music.gif', ('o|^_^|o', 'O|^_^|O'), 'version11'), ('vomit', 'Vomit', '128.gif', 'vomit.gif', (':puke!', ':PUKE!'), 'version11'), ('sing', 'Sing', '129.gif', 'sing.gif', ('o|\\~', 'O|\\~'), 'version11'), ('catch', 'Catch', '130.gif', 'catch.gif', ('o|:-)', 'O|:-)'), 'version11'), ('exercise', 'Exercise', '131.gif', 'exercise.gif', ('[]==[]',), 'version11'), ('highfive', 'High five', '132.gif', 'highfive.gif', (':-)/\\:-)',), 'version11'), ('gaming', 'Gaming', '133.gif', 'gaming.gif', (':(game)', ':(GAME)'), 'version11'), ('searchme', 'Search me', '134.gif', 'searchme.gif', ("'@-@",), 'version11'), ('spooky', 'Spooky', '135.gif', 'spooky.gif', (':->~~',), 'version11'), ('studying', 'Studying', '136.gif', 'studying.gif', ('?@_@?',), 'version11'), ('tv', 'TV', '137.gif', 'tv.gif', (':(tv)', ':(TV)'), 'version11'), ('gift', 'Gift', '138.gif', 'gift.gif', ('&[]',), 'version11'), ('unlucky', 'Unlucky', '139.gif', 'unlucky.gif', ('%||:-{',), 'version11'), ('downonluck', 'Down on luck', '140.gif', 'downonluck.gif', ('%*-{',), 'version11'), ('fight', 'Fight', '141.gif', 'fight.gif', (':(fight)', ':(FIGHT)}'), 'version11'), ('pirate', 'Pirate', 'pirate.gif', 'pirate.gif', (':ar!', ':pirate:'), 'web'), ('transformer', 'Transformer', 'transformer.gif', 'transformer.gif', ('[..]', ':trans:'), 'web'), ('pacman', 'Pac-Man', 'fb-pacman.png', 'pacman.gif', (':v', ':V'), 'web'), ('colon-three', 'Colon three', 'fb-colonthree.png', 'colon-three.gif', (':3',), 'web'))


def _builtin_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for order, (name, label, local_file, external_file, codes, category) in enumerate(BUILTIN_EMOTICONS):
        clean_codes: list[str] = []
        for code in codes:
            code = str(code or "").strip()
            key = code.lower()
            if not code or key in seen_codes or not _SAFE_CODE_RE.match(code):
                continue
            seen_codes.add(key)
            clean_codes.append(code)
        if not clean_codes:
            continue
        rows.append({
            "name": name,
            "label": label,
            "code": clean_codes[0],
            "codes": clean_codes,
            "category": category,
            "local_file": local_file,
            "external_file": external_file,
            "source": "builtin",
            "order": order,
        })
    return rows


def project_root() -> Path:
    return Path(__file__).resolve().parent


def local_emoticon_root(settings: dict[str, Any] | None = None) -> Path:
    raw = str((settings or {}).get("emoticons_local_root") or "emoticons").strip() or "emoticons"
    root = Path(raw)
    if not root.is_absolute():
        root = project_root() / root
    return root.resolve()


def local_emoticon_roots(settings: dict[str, Any] | None = None) -> list[Path]:
    """Return all safe local roots to try for emoticon assets.

    The first root is the admin-configured root.  The extra roots make the
    loader tolerant of common installs: running from the project directory,
    extracting the image pack into the project root, or using the bundled
    fallback under static/emoticons.
    """
    candidates = [
        local_emoticon_root(settings),
        project_root() / "emoticons",
        Path.cwd() / "emoticons",
        project_root() / "static" / "emoticons",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for root in candidates:
        try:
            resolved = root.resolve()
        except Exception:
            continue
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            out.append(resolved)
    return out



def _file_cache_token(path: Path | None) -> str:
    """Return a compact cache-busting token for a local immutable asset URL."""
    if not path:
        return ""
    try:
        stat = path.stat()
        return f"{int(stat.st_mtime_ns):x}-{int(stat.st_size):x}"
    except Exception:
        return ""


def _versioned_local_emoticon_url(filename: str, path: Path | None = None) -> str:
    """Build a safe /emoticons URL with a content-derived version token."""
    safe = str(filename or "").strip().replace("\\", "/")
    if "/" in safe or safe.startswith(".") or not _SAFE_FILENAME_RE.match(safe):
        safe = "emoticon.gif"
    url = f"/emoticons/{quote(safe)}"
    token = _file_cache_token(path)
    return f"{url}?v={quote(token)}" if token else url


def _versioned_static_emoticon_url(filename: str) -> str:
    """Build a /static/emoticons fallback URL with a local file version token when possible."""
    safe = str(filename or "").strip().replace("\\", "/")
    if "/" in safe or safe.startswith(".") or not _SAFE_FILENAME_RE.match(safe):
        return ""
    candidate = project_root() / "static" / "emoticons" / safe
    token = _file_cache_token(candidate if candidate.is_file() else None)
    url = f"/static/emoticons/{quote(safe)}"
    return f"{url}?v={quote(token)}" if token else url


def _normalize_external_bases(raw: Any) -> list[str]:
    """Normalize an external image base URL and include repo-folder fallbacks.

    Admins may paste either a raw image-folder URL or the normal GitHub repo
    URL.  For GitHub repo URLs we include both the repo's current ``images``
    folder and the older zip layout's ``emoticons`` folder so either source can
    be selected without editing code.
    """
    base = str(raw or "").strip() or _DEFAULT_EXTERNAL_REPO_URL
    parsed = urlparse(base)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        return []
    host = parsed.netloc.lower()
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    bases: list[str] = []

    def add(url: str) -> None:
        url = str(url or "").rstrip("/")
        if url and url not in bases:
            bases.append(url)

    if host == "github.com" and len(path_parts) >= 2:
        owner, repo = path_parts[0], path_parts[1]
        branch = "master"
        if len(path_parts) >= 5 and path_parts[2] in {"tree", "blob"}:
            branch = path_parts[3]
            folder = "/".join(path_parts[4:]).strip("/")
            if folder:
                add(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{folder}")
        # Known layouts for the image pack: the live repo advertises images/,
        # while downloaded zips commonly contain emoticons/.  Try both.
        add(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/images")
        add(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/emoticons")
        return bases

    if host == "raw.githubusercontent.com" and len(path_parts) >= 3:
        add(base)
        # If a raw folder URL ends in images/emoticons, add the sibling folder
        # too so the admin can paste either one.
        if path_parts[-1].lower() in {"images", "emoticons"}:
            sibling = "emoticons" if path_parts[-1].lower() == "images" else "images"
            add("/".join([f"https://raw.githubusercontent.com", *path_parts[:-1], sibling]))
        return bases

    add(base)
    return bases


def _normalize_external_base(raw: Any) -> str:
    bases = _normalize_external_bases(raw)
    return bases[0] if bases else ""


def _candidate_local_files(row: dict[str, Any]) -> list[str]:
    names: list[str] = []
    local_file = str(row.get("local_file") or "").strip()
    external_file = str(row.get("external_file") or "").strip()
    name = str(row.get("name") or "").strip()
    for value in (local_file, external_file, f"{name}.gif"):
        value = str(value or "").strip().replace("\\", "/")
        if "/" in value or value.startswith(".") or not _SAFE_FILENAME_RE.match(value):
            continue
        if value.lower() not in {n.lower() for n in names}:
            names.append(value)
    return names


def _local_asset_for_row(roots: list[Path], row: dict[str, Any]) -> tuple[str, bool, str]:
    candidates = _candidate_local_files(row)
    for filename in candidates:
        for root in roots:
            candidate = safe_existing_file_under(root, filename)
            if candidate and candidate.is_file():
                # Always serve through the safe /emoticons route.  That route
                # searches the same roots and prevents leaking arbitrary files.
                # The ?v= token is derived from the file timestamp/size so the
                # browser can keep the image cache hot across chat reconnects
                # while still refreshing when the local file is replaced.
                return _versioned_local_emoticon_url(filename, candidate), True, filename.rsplit(".", 1)[-1].lower()
    fallback = candidates[0] if candidates else f"{row.get('name') or 'emoticon'}.gif"
    return _versioned_local_emoticon_url(fallback), False, fallback.rsplit(".", 1)[-1].lower() if "." in fallback else "gif"


def _external_asset_candidates_for_row(bases: str | list[str], row: dict[str, Any]) -> list[str]:
    if isinstance(bases, str):
        bases = [bases] if bases else []
    bases = [str(base or "").rstrip("/") for base in bases if str(base or "").strip()]
    if not bases:
        return []
    names: list[str] = []
    for key in ("external_file", "local_file", "filename"):
        filename = str(row.get(key) or "").strip().replace("\\", "/")
        if "/" in filename or filename.startswith(".") or not _SAFE_FILENAME_RE.match(filename or ""):
            continue
        if filename.lower() not in {n.lower() for n in names}:
            names.append(filename)
    name = str(row.get("name") or "").strip()
    if _SAFE_NAME_RE.match(name or ""):
        filename = f"{name}.gif"
        if filename.lower() not in {n.lower() for n in names}:
            names.append(filename)
    out: list[str] = []
    for base in bases:
        for filename in names:
            url = f"{base}/{filename}"
            if url not in out:
                out.append(url)
    return out


def _external_asset_for_row(base: str, row: dict[str, Any]) -> str:
    candidates = _external_asset_candidates_for_row(base, row)
    return candidates[0] if candidates else ""


def _parse_codes(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for code in raw:
        code = str(code or "").strip()
        key = code.lower()
        if code and key not in seen and _SAFE_CODE_RE.match(code):
            seen.add(key)
            out.append(code)
    return out


def _parse_custom_entries(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    raw = (settings or {}).get("emoticons_custom_entries") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen_codes = {code.lower() for r in _builtin_rows() for code in r.get("codes", [])}
    seen_names = {str(r["name"]).lower() for r in _builtin_rows()}
    for order, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        codes = _parse_codes(item.get("codes") if "codes" in item else item.get("code"))
        if not _SAFE_NAME_RE.match(name or "") or not codes:
            continue
        if name.lower() in seen_names or any(code.lower() in seen_codes for code in codes):
            continue
        label = str(item.get("label") or name.replace("_", " ").replace("-", " ").title()).strip()[:80]
        category = str(item.get("category") or "custom").strip()[:40] or "custom"
        filename = str(item.get("filename") or f"{name}.gif").strip().replace("\\", "/")
        if "/" in filename or filename.startswith(".") or not _SAFE_FILENAME_RE.match(filename):
            filename = f"{name}.gif"
        url = str(item.get("url") or "").strip()
        seen_names.add(name.lower())
        for code in codes:
            seen_codes.add(code.lower())
        out.append({
            "name": name,
            "label": label,
            "code": codes[0],
            "codes": codes,
            "category": category,
            "local_file": filename,
            "external_file": filename,
            "filename": filename,
            "url": url,
            "source": "custom",
            "order": 10000 + order,
        })
    return out


def _animation_stop_ms(settings: dict[str, Any] | None = None) -> int:
    try:
        return max(0, min(60000, int((settings or {}).get("emoticons_animation_stop_ms", 4500) or 0)))
    except Exception:
        return 4500


def emoticon_catalog(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    enabled = bool(settings.get("emoticons_enabled", True))
    local_enabled = bool(settings.get("emoticons_local_enabled", True))
    external_enabled = bool(settings.get("emoticons_external_enabled", False))
    external_bases = _normalize_external_bases(settings.get("emoticons_external_asset_base_url"))
    external_base = external_bases[0] if external_bases else ""
    prefer_external = str(settings.get("emoticons_asset_mode") or "local_first").strip().lower() == "external_first"
    roots = local_emoticon_roots(settings)

    entries: list[dict[str, Any]] = []
    for row in _builtin_rows() + _parse_custom_entries(settings):
        local_src, available_local, local_ext = _local_asset_for_row(roots, row)
        custom_url = str(row.get("url") or "").strip()
        external_candidates = _external_asset_candidates_for_row(external_bases, row)
        external_src = custom_url or (external_candidates[0] if external_candidates else "")
        src = ""
        source_mode = "missing"
        if prefer_external and external_enabled and external_src:
            src = external_src
            source_mode = "external" if not custom_url else "custom_url"
        elif local_enabled and available_local:
            src = local_src
            source_mode = "local"
        elif external_enabled and external_src:
            src = external_src
            source_mode = "external" if not custom_url else "custom_url"
        elif local_enabled:
            src = local_src
            source_mode = "local_expected"
        elif custom_url:
            src = custom_url
            source_mode = "custom_url"
        fallback_srcs: list[str] = []
        static_candidates = [_versioned_static_emoticon_url(name) for name in _candidate_local_files(row)]
        for candidate in [local_src, *static_candidates, custom_url, *external_candidates]:
            if candidate and candidate not in fallback_srcs:
                fallback_srcs.append(candidate)
        entries.append({
            "name": row["name"],
            "label": row["label"],
            "code": row["code"],
            "codes": list(row.get("codes") or [row["code"]]),
            "category": row.get("category") or "custom",
            "src": src,
            "fallback_srcs": fallback_srcs,
            "source": source_mode,
            "available_local": bool(available_local),
            "asset_ext": str(local_ext or "gif").lower(),
            "animation_stop_ms": _animation_stop_ms(settings),
            "order": int(row.get("order") or 0),
        })

    entries.sort(key=lambda e: (int(e.get("order") or 0), str(e.get("name") or "")))
    return {
        "enabled": enabled,
        "local_enabled": local_enabled,
        "external_enabled": external_enabled,
        "asset_mode": "external_first" if prefer_external else "local_first",
        "external_asset_base_url": external_base,
        "animation_stop_ms": _animation_stop_ms(settings),
        "local_root": str(roots[0]) if roots else "",
        "local_roots": [str(root) for root in roots],
        "count": len(entries),
        "code_count": sum(len(e.get("codes") or []) for e in entries),
        "entries": entries if enabled else [],
    }



def emoticon_shortcut_codes(settings: dict[str, Any] | None = None) -> list[str]:
    """Return active typed emoticon shortcuts for server-side abuse guards."""
    try:
        cat = emoticon_catalog(settings or {})
    except Exception:
        rows = _builtin_rows()
        return [str(code) for row in rows for code in (row.get("codes") or []) if str(code or "").strip()]
    if not bool(cat.get("enabled", True)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in cat.get("entries") or []:
        for code in entry.get("codes") or []:
            text = str(code or "").strip()
            key = text.lower()
            if text and key not in seen and _SAFE_CODE_RE.match(text):
                seen.add(key)
                out.append(text)
    return out


def _compiled_emoticon_shortcut_re(settings: dict[str, Any] | None = None) -> re.Pattern[str] | None:
    codes = emoticon_shortcut_codes(settings)
    if not codes:
        return None
    codes.sort(key=lambda item: (-len(item), item.lower()))
    return re.compile("|".join(f"({re.escape(code)})" for code in codes), re.IGNORECASE)


def clamp_max_emoticons_per_message(value: Any, default: int = 15) -> int:
    """Bound the per-message emoticon shortcut limit. 0 explicitly disables it."""
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(0, min(100, parsed))


def filter_excess_emoticon_shortcuts(message: Any, settings: dict[str, Any] | None = None, *, max_count: int | None = None) -> tuple[str, int, int]:
    """Drop typed emoticon shortcuts after the configured per-message limit.

    Returns ``(filtered_message, kept_count, removed_count)``. Extra shortcuts are
    removed instead of left as raw ``:):):)`` text, which prevents room/PM/group
    transcript floods even when a client is old or hand-edited.
    """
    text = str(message or "")
    limit = clamp_max_emoticons_per_message(
        max_count if max_count is not None else (settings or {}).get("max_emoticons_per_message", 15),
        default=15,
    )
    if limit <= 0 or not text:
        return text, 0, 0
    pattern = _compiled_emoticon_shortcut_re(settings or {})
    if pattern is None:
        return text, 0, 0

    out: list[str] = []
    last = 0
    seen = 0
    kept = 0
    removed = 0
    codes = {code.lower() for code in emoticon_shortcut_codes(settings or {})}
    for match in pattern.finditer(text):
        code = match.group(0) or ""
        if code.lower() not in codes:
            continue
        start, end = match.span()
        if start > last:
            out.append(text[last:start])
        seen += 1
        if seen <= limit:
            out.append(code)
            kept += 1
        else:
            removed += 1
        last = end
    if last < len(text):
        out.append(text[last:])
    return "".join(out), kept, removed
