# BeatSolver Timeline v0.4.1

## Candidate hierarchy

- Score-based Preview filtering that leaves original graph output intact.
- Detector strength and neighborhood rank combined into a 0–1 heuristic priority.
- Optional time-radius duplicate suppression keeps higher-priority events.
- Lower-priority predictions may be shown as faint non-editable ghosts.
- Human manual/confirmed Preview annotations and Current output are never pruned.
- Filter threshold and deduplication radius save to project JSON.

## Music and candidate hitsounds

- Independent Music and Hitsound gain sliders; short audition respects both.
- Optional continuous clicks: Current, Preview, Both, or BPM grid.
- Full-length stereo WAV generated asynchronously with clicks in PCM timebase.
- Rebuilds after candidate edits, offset and volume changes. Playback switches to updated mix when finished.
- Mixed WAVs are stored in an app-owned TemporaryDirectory and removed as released or on shutdown.

## Baseline v0.4.0 QA fixes

- Prevented creating zero-speed audition WAVs; cleanup no longer relies on `audition_active`.
- BPM timing guide ranges exclude end timestamps.
- Corrected Qt timer regression to change the actual combo selection.

## Tests

- 48 pure-Python regressions pass in Linux; 4 Qt-dependent tests skipped because PySide6/miniaudio are absent here.
- User's real Windows report for v0.4.0 is analyzed in `QA_BUILD_REPORT.md`.
- `RUN_QA_WINDOWS.bat` invokes shared `qa/RUN_CODEX_QA.ps1` from the parent BeatSolver directory. No extra venv.

## Limitations

- A full-track render can consume disk space and cause a brief pause when switching audio sources.
- Actual audio hardware synchronization and visual display frame rate remain unmeasured.
- Priority heuristic ranks supplied detector scores; it is not an ML classifier or calibrated probability.
- GUI/audio changes await Windows testing.
