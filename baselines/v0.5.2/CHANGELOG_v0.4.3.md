# BeatSolver Timeline v0.4.3 — UI and native audio hotfix

## Source changes

- Compact five-tab control area replaces four always-visible scroll strips plus the standalone analysis row, improving timeline usable height. Initial graph dock target width reduced from 490 to 380 px.
- Diagnostics dock now uses explicit show/raise; tab visibility notifications cannot recursively hide it. Layer dock tab toggle also corrected.
- QAudioSink EOF handling: completed PCM stream emits `finished`, stops transport, updates the GUI Play button, and restarts from zero if Play is pressed again. Poll-based fallback covers missing idle notification.
- Cached stream activation exceptions now call `_stream_failed`, restoring the legacy media-player path.
- Optional Windows Qt tests added for those regressions. Original tests and control widget attributes retained.

## Evidence and validation

- The user-provided QA report for **v0.4.2** showed 63/63 included tests passed, yet extended Windows real-device checks reproduced dock-opening failure, EOF transport failure, and uncaught injected cached-startup failure. Prior successful native streaming behaviors were preserved.
- **v0.4.3 local checks:** Python sources compiled; 56 non-GUI tests passed, 6 Qt-dependent modules skipped because PySide6 is unavailable here.
- **Not yet validated:** patched v0.4.3 Windows GUI, native device EOF event delivery, physical audio latency, real 240 Hz monitor scanout. Rerun shared Codex QA and the user's `qa/verify_v042_stream.py` on Windows.
