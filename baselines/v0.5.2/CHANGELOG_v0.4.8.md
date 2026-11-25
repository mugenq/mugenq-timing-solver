# BeatSolver Timeline v0.4.8 — Spectral default and Windows QA hotfix

## Fixes from the November 24 Windows v0.4.7 report

1. **Stale spectrogram callback (P2):** a background legacy FFT viewer worker can no longer overwrite the numerical spectral object or viewer selected by a typed exact-window graph. The graph's active Candidate Output ancestry determines the expected `(window_mode, requested_window_ms, hop_samples)` signature. Viewer callbacks from another file or mode are discarded, even if they arrive late. The viewer is cleared during an incompatible analysis switch until the matching graph output arrives.
2. **Candidate Merge compatibility (P2):** restored v0.4.6's rolling *currently selected strongest candidate* time anchor. v0.4.7 incorrectly anchored clusters at the first member. Event lineage/provenance is still accumulated from all merged parent events. A representative [1.000, 1.009, 1.018] sec / [0.1,0.5,0.9] case with 10 ms tolerance once again yields exactly [1.018] sec. Seeded compatibility tests compare this algorithm to the historical selection loop.
3. **Qt test teardown hang:** the v0.4.5 graph-insertion test now marks the window clean before closing, avoiding a blocking unsaved-project modal dialog during headless pytest. The test expectations were adjusted for the new default graph and the Burst Cleanup action's idempotence.

## New unsaved-session defaults

- **Spectrogram visible by default** (Attack bands; users may toggle it off in Analysis).
- **Active graph starts as:** Audio Source → Shared Spectrogram (`exact_window=0`, legacy 46 ms *requested* / 85.333 ms *physical* at 48 kHz) → Novelty from Spectrogram → Peak Picker (percentile 60, minimum spacing 12 ms) → Burst Cleanup → Preview Candidate Output.
- Burst Cleanup keeps its existing reversible, non-destructive settings (35 ms neighbor gap, 110 ms maximum burst, 60% weak retention). Current/human edits are never overwritten by a graph run.
- The old five-node `starter_graph` helper is preserved for historical code and plugins; old saved projects load their serialized graphs unchanged, and saved spectrogram visibility is respected.
- **Graph → Reset starter graph…** now restores the new spectral+cleanup default.
- The longer legacy FFT physical window remains the new default because it outperformed the exact shorter physical window on the current Hanatan mapped-intro benchmark. No spectral detection algorithm or playback engine has changed.

## Checks / limitations

- 92 local deterministic tests passed; 11 Qt-specific modules skipped because PySide6 is unavailable in the Linux build environment.
- New tests exercise default graph serialization and execution, legacy starter compatibility, seeded merge selection with parent provenance, and Qt-only stale-viewer callback and startup behavior.
- A real 11.1 second Hanatan excerpt decoded read-only and processed with the new default: 204 candidates before Burst Cleanup, 188 after, with aligned per-event provenance (excerpt-local thresholds; **not** the full-song benchmark).
- Python compilation succeeded; no Windows GUI or real acoustic latency test was performed here. Windows native QA is mandatory before considering the release fully validated.
- The packaged `qa/` folder uses uniquely named v0.4.8 tools and does not overwrite old v0.4.7 QA scripts. No user settings, source MP3 or beatmaps are modified.
