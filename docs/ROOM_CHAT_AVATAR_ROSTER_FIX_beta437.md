# beta.437 — Room chat avatar + room roster profile picture fix

## Problem

End users could see their own profile picture in the dock/profile area, but other room members still rendered as letter bubbles in room chat and in the room users panel.

The root cause was that room roster payloads were plain username arrays and live room chat payloads did not carry the sender avatar URL. That meant the client only had profile pictures for the current user or friends already present in the dock presence cache.

## Fix

- Kept the compatible `room_users.users` username list.
- Added side-channel avatar metadata to `room_users` as `avatars` and `user_profiles`.
- Added `avatar_url` to live room `chat_message` payloads.
- Added frontend avatar caching for room roster payloads and room chat payloads.
- Rendered the room users panel with the real profile picture when available.
- Preserved existing avatar cache entries when a presence update has no avatar field.
- Refreshed already-rendered room message avatars after roster/avatar cache hydration.

## Expected behavior

- Room chat bubbles show the sender's profile picture when that sender has an avatar.
- The room users list shows profile pictures instead of only green-dot/name rows.
- Non-friends in the same room can still display their profile pictures through the roster metadata.
- Users without profile pictures still fall back to initials.

## Validation

- `python -m compileall -q .`
- `node --check static/js/chat_parts/*.js static/js/*.js static/vendor/*.js`
- `unzip -t Echo-Chat-v0.11.0-beta.437-room-chat-avatar-roster-fix.zip`
- `sha256sum -c Echo-Chat-v0.11.0-beta.437-room-chat-avatar-roster-fix.zip.sha256`
