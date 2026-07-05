# Frontend Structure

Version: **0.11.0-beta.386**

## Source of truth

Echo-Chat serves the browser runtime from the explicit `constants.CHAT_SCRIPT_PARTS` manifest.
`templates/chat.html` loops over `chat_script_parts`, so the active runtime is the ordered list from that manifest, not every file present on disk.

## Why the explicit manifest matters

The project has gone through several frontend split/refactor passes. Leaving stale numbered files in `static/js/chat_parts/` can fool maintainers into editing dead files that are never served.

Current rule:

- edit active numbered source files listed in `constants.CHAT_SCRIPT_PARTS`
- serve the ordered split files directly from `static/js/chat_parts/`
- do not rely on orphaned files left behind by unzip upgrades
- keep tests aligned to active manifest files, not historical aliases

## Bundle workflow

`constants.get_chat_script_parts()` is the single source of truth for the files loaded by `templates/chat.html`.
That bundle is helpful for inspection or archival purposes, but `templates/chat.html` intentionally serves the split manifest.

## Guardrails

The test suite now checks that:

- every manifest entry exists on disk
- the template still loops over `chat_script_parts`
- no orphaned numbered part files remain in `static/js/chat_parts/`
- active helpers such as `uploadMyBannerFile()` live in served source files

## Practical editing path

For frontend changes:

1. identify the active numbered file from `constants.CHAT_SCRIPT_PARTS`
2. edit that file
3. update or add targeted tests against the active file
4. run the JavaScript syntax checks against the split runtime files


## UI12 release-gate note

Admin Test Lab Browser release gate summary recomputation is covered by `tools/ui12_final_frontend_release_doctor.py`.
