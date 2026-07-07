# v0.11.0-beta.439 — Voice realtime diagnostics + shared-state hardening

## Problem

Sometimes the GUI could show a user online while a 1:1 voice call failed as if the user was offline. The real failure mode was not always offline status; it could be missing live Socket.IO delivery state, a reconnect race, or an unsafe multi-worker topology.

## Changes

- Added voice delivery diagnostics for failed DM and room voice signaling.
- Changed voice failure text away from misleading `offline` wording.
- Added explicit realtime diagnostic payloads in voice ACK responses.
- Added server warning logs with sender/target sid counts, DB online status, presence status, shared-state status, Socket.IO queue status, and scaled-topology status.
- Added shared Redis-backed voice DM session helpers when `shared_state_redis_url` is enabled.
- Added shared Redis-backed room voice roster helpers when `shared_state_redis_url` is enabled.
- Updated disconnect/block cleanup to remove voice DM sessions through the shared helper layer.
- Added a topology guard: if Echo-Chat is configured for multiple workers/instances but lacks `socketio_message_queue` or working `shared_state_redis_url`, voice returns a clear configuration error instead of claiming the user is offline.

## Files changed

- `realtime/state.py`
- `realtime/voice.py`
- `realtime/presence_social.py`
- `socket_handlers.py`
- `static/js/chat_parts/0014_voice_dm_calls.js`

## Validation

```bash
python -m compileall -q .
find static -type f -name '*.js' -print0 | xargs -0 -n1 node --check
python shared voice-state fallback smoke test
unzip -t Echo-Chat-v0.11.0-beta.439-voice-realtime-diagnostics-shared-state.zip
sha256sum -c Echo-Chat-v0.11.0-beta.439-voice-realtime-diagnostics-shared-state.zip.sha256
```
