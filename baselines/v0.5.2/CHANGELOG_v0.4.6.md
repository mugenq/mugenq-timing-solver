# BeatSolver Timeline v0.4.6

## Added

- Built-in `signal.spectral_attack` node (Audio -> Curve) using time-aligned,
  log-frequency-band positive spectral novelty from decoded PCM.
- Built-in `candidates.evidence_cleanup` node (Candidates + Curve -> Candidates),
  with bounded clustering and spectral-valley-based weak-reattack preservation.
- **Graph** shortcuts for creating a separate spectral candidate branch and
  inserting evidence-aware cleanup before the selected output.
- Optional spectrogram below the waveform, with Attack bands / Harmonics mode,
  shared navigation, cursors and playback, and on-demand analysis in a QThread.
- Project preferences for spectrogram visibility and mode. Per-song spectral
  results are cached in memory with bounded (single-configuration) retention.
- Seven new pure-Python regression tests, plus two new optional Qt tests.

## Preserved

- Starter graph, legacy Burst Cleanup, Preview/Current separation, candidate
  drag and replacement, MIDI-oriented timing guide workflow, PCM sync and
  playback speed control, shared .venv launchers, and existing QA workflow.

## Limitations

- Only available for songs whose decoded mono PCM is retained in memory.
- This analyzes attacks but does not perform actual acoustic-source separation.
- FFT-windowed spectral peaks are onset evidence, not guaranteed correct times.
- Windows Qt verification is pending.
