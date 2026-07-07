# beta.444 — Admin Startup Bridge + Avatar Fallback

This patch fixes two browser-console problems seen after the emoticon cache/path fixes.

## Fixed: admin panel unlock ReferenceError

The injected admin panel uses a startup password gate.  The live refresh functions
for voice, ICE, media, stats, security, diagnostics, analytics, and user search
are created inside `buildPanel()` because they depend on DOM nodes created there.
The unlock/runtime function lived outside `buildPanel()` and called those inner
functions by bare name, which could throw:

```text
ReferenceError: refreshVoiceSettings is not defined
```

The runtime now uses an explicit `adminRuntimeFns` bridge that is populated after
`buildPanel()` creates the panel.  Unlocking the admin panel now calls the bridge
instead of bare inner-scope names.

## Fixed: stale uploaded avatar 404 spam

If a database row pointed at a missing local uploaded avatar such as:

```text
/media/avatars/big_quy-1783116626-c0f4c514.png
```

browsers repeatedly logged 404s whenever the friend list, room list, or hub
re-rendered.  The avatar route now returns a generated initials SVG fallback for
missing local avatar files instead of a JSON 404.  The response includes:

```text
X-EchoChat-Avatar-Fallback: missing-local-avatar
```

This keeps the UI clean and stops console spam while still making the stale media
reference diagnosable.

## Files changed

- `admin_panel_inject.py`
- `routes_main.py`
- `tools/admin_startup_avatar_fallback_doctor.py`
- `VERSION.txt`
- `README.md`
