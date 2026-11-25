# BeatSolver Timeline v0.4.4 — source-locked playhead

- Streaming playhead now maps QAudioSink processed output frames to decoded
  source frames using a piecewise speed ledger. Already queued audio retains
  its original speed after the user changes playback rate, correcting a
  slow-playback cursor drift from v0.4.3.
- Request a smaller ~50ms Qt sink buffer (the device can round/ignore this).
- The streaming PCM buffer renderer snapshots speed at buffer start, avoiding
  mixed-rate sample selection and time advancement during rapid GUI edits.
- Media-player interpolation re-anchors more tightly to backend timestamps,
  reducing lag at slow playback rates.
- Added a separately saved **Cursor sync** control (±300ms, default 0), for
  visual-only manual calibration of unmeasured hardware output latency. This
  never changes music, candidates, waveform, global timing offset, or exports.
- Fixed the `range(4)` vs five expected tabs QA typo in the included Qt test.
- Added regression tests for queued-frame speed changes, 0.25x playback,
  seeking, pause, end-of-buffer, and Qt-specific cursor behavior.

## Verification boundary
Pure-Python tests pass locally. The real Windows Qt audio backend has not
been exercised here; run RUN_QA_WINDOWS.bat and listen at 25/50/100%.

This improves synchronization with the *reported audio device clock* but
cannot guarantee sample-perfect alignment with headphone sound without an
external loopback latency measurement. MP3 decoder padding may add additional
fixed cross-decoder offsets, which should be measured before correction.
