# v0.11.0-beta.440 — Private-message typing hardening

This patch tightens the private-message typing indicator flow without exposing message content. Typing packets still contain only sender, recipient, boolean state, expiry time, and timestamp metadata.

## What changed

- PM typing now re-renders immediately when a PM window opens after a typing packet already arrived.
- PM/group typing start logic was centralized so focus, composition-end, reconnect, and normal input events use the same guarded path.
- Stop-typing resets the local throttle timestamp so the next typing burst can be shown immediately.
- Page-hide, tab-hide, and socket disconnect now stop local outbound typing and clear stale inbound indicators.
- Server-side `send_direct_message` now emits a best-effort `direct_stop_typing` after accepting a PM, so the receiver does not keep seeing “is typing…” if the sender browser misses a stop event.
- `direct_stop_typing` is still protected by the generic Socket.IO event guard, but it no longer uses the stricter start-typing strike path. This keeps cleanup reliable while still limiting abusive loops.

## PM typing behavior

1. User starts typing in a private-message composer.
2. Browser emits `direct_typing` no more than once per throttle window.
3. Server validates auth, stale socket state, feature flag, target username, sanctions, blocking, and rate limits.
4. Recipient receives `direct_typing` and sees `username is typing…` in that PM window.
5. The indicator clears when:
   - sender deletes the draft, blurs the composer, hides/closes the tab, or disconnects;
   - sender sends an actual PM;
   - recipient receives the real PM;
   - the typing TTL expires.

## Files changed

- `static/js/chat_parts/0043_group_history_dm_windows.js`
- `realtime/dm.py`
- `VERSION.txt`
- `README.md`
- `docs/PM_TYPING_HARDENING_beta440.md`
- `tools/pm_typing_hardening_doctor.py`
- `release_manifest_beta440_pm_typing_hardening.json`

## Validation

```bash
python -m py_compile realtime/dm.py
node --check static/js/chat_parts/0043_group_history_dm_windows.js
python tools/pm_typing_hardening_doctor.py
python -m compileall -q .
```
