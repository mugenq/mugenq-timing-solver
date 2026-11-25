# BeatSolver Timeline v0.4.2 — Timing diagnostics and beta stream mixer

- Added `TimingMonitor`: bounded 5-second Qt repaint and timer-update statistics, callback jitter percentiles, QMediaPlayer-versus-interpolated-clock discrepancy. Does **not** measure screen panel Hz or physical audible latency.
- Added a Diagnostics dock (button and tab next to Layer Inspector) with a 20-bin heuristic candidate-score distribution and summary statistics.
- Added `PCMBufferMixer` to stream stereo 16-bit audio buffers, with hitsounds mixed against the same sample indices, adjustable sources/gains and variable playback speed. Candidate edits update events directly, without full-track WAV regeneration.
- Added experimental `QAudioSink` backend and asynchronous native stereo decode, bounded to 256 MiB. Incompatible formats, oversized songs and startup errors fall back to the validated v0.4.1 WAV renderer.
- Retains the existing Preview/Current separation, middle-click panning, BPM ranges and click auditioning.
- Project JSON stores `stream_mixer` preference (defaults to off for older projects).
- Added pure-python tests for rate, seek, buffer boundaries, zero-volume output, bounded decode, cleanup and diagnostics, plus optional Qt tests for controls and PCM QIODevice.
- Windows QA runner updated to point to v0.4.2 and suppress pytest cache/bytecode in application source folders.

Known caveats: streaming is opt-in beta, physical output-device timing is unmeasured, high playback speeds use pitch-changing interpolation, and very long songs will use the fallback. No modifications to the built-in onset detector.
