# beta.436 — Multiline composer Ctrl+Enter

## Goal
Allow users to insert a new line inside chat message composers without sending the message.

## Behavior

- Plain **Enter** still sends the current message.
- **Ctrl+Enter** inserts a newline in the room chat composer, private message composer, and group message composer.
- **Shift+Enter** also inserts a newline for users who expect the common multiline-chat shortcut.
- The hidden message value stores the newline, so sending preserves the multiline text.
- Rendered messages already use pre-wrap styling, so multiline messages display with line breaks.

## Files changed

- `static/js/chat_parts/0008_emoji_picker.js`
- `static/js/chat_parts/0018_windows_manager.js`
- `static/js/chat_parts/0040_room_browser_polling_embed.js`
- `VERSION.txt`
- `README.md`
- `docs/MULTILINE_COMPOSER_CTRL_ENTER_beta436.md`
- `release_manifest_beta436_multiline_composer_ctrl_enter.json`

## Validation

- `python -m compileall -q .`
- `node --check static/js/chat_parts/*.js static/js/*.js static/vendor/*.js`
- `unzip -t Echo-Chat-v0.11.0-beta.436-multiline-composer-ctrl-enter.zip`
- `sha256sum -c Echo-Chat-v0.11.0-beta.436-multiline-composer-ctrl-enter.zip.sha256`
