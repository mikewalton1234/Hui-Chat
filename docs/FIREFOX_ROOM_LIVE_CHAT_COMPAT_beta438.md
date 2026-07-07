# beta.438 - Firefox room live chat compatibility

This release improves the embedded room transcript behavior when testing in Firefox and other non-Chromium browsers.

## Why

Room chat is intentionally live-only in this build. It is not loaded from shared server history. A second browser can therefore look blank after joining even though another browser still has messages in its in-memory transcript.

## Changes

- Added a visible live-only empty state so an empty room log no longer looks broken.
- Normalized room-name matching before routing incoming `chat_message` events to the active embedded room view.
- Added a guarded ACK-render fallback for the sender: if a browser receives the successful send ACK but the matching broadcast does not become visible, the client renders the message once using the server message id.
- Added console diagnostics when a `chat_message` arrives with no active room view.

## Notes

The server-side room-history policy remains unchanged: room messages are still live-only and are not persisted as shared room history.
