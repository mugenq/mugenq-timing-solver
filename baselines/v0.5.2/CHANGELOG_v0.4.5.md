# BeatSolver Timeline v0.4.5 — Burst Cleanup + PCM playback sync

## Candidate processing

- Added built-in **Burst Cleanup** (`candidates.burst_cleanup`) node with exactly
  one **Candidates** input and one **Candidates** output. Compatible with any
  detector that produces the standard `CandidateSet`; no audio or curve input
  required.
- The filter groups nearby candidates into *bounded* bursts using `neighbor_gap_ms`
  (35 ms default) and `max_burst_ms` (110 ms default). Within each burst it
  retains candidates with `score >= max_score * weak_retention` (default 0.60).
  Similar-strength close notes remain. Weak candidates can be filtered out.
- It never moves a timestamp or edits upstream detections, manually confirmed
  annotations, committed Current output, or audio. An upstream Candidate Output
  can still be used for A/B comparison. This filter alone cannot determine
  acoustic event identity from timestamps and scores.
- **Graph → Insert Burst Cleanup before Preview output** automatically inserts
  the node into the selected graph branch. Alternatively add/connect it manually.
- Existing saved projects, including plugin-based graphs, remain compatible.
  The node is only added when the user requests it.

## Audio / playhead timing

- Added **PCM sync**, checked by default, to the Playback tab. This routes
  ordinary music playback (even when Continuous clicks is OFF) through the
  existing bounded-memory stereo `QAudioSink` stream. The waveform and the
  streamed song use the same decoder family, same native sample rate, and the
  same decoded-sample timebase. The playhead follows processed output frames
  through the v0.4.4 speed ledger, avoiding a separate MP3 QMediaPlayer clock.
- Cursor sync compensation is now interpreted in **wall-clock milliseconds**
  and converted to song-timeline time using the selected speed. For example,
  40 ms visual correction at 25% shifts 10 ms on the song timeline.
- PCM stereo decode checks its sample rate and frame count against the cached
  analysis waveform; mismatches fall back safely to media-player playback.
- PCM streaming can remain active without generating any hitsound. Enabling or
  disabling Continuous clicks adds/removes its events without changing clocks.
- Short audition now pauses the PCM stream as well as the legacy player so the
  original song cannot continue audibly behind the audition excerpt.
- When PCM playback is unavailable or the 256 MiB stereo cache limit is
  exceeded, the application disables PCM sync and falls back to the legacy
  QMediaPlayer path. The previous file-based clicks path remains available.
- The PCM streaming backend changes pitch with speed; the Qt multimedia
  preserve-pitch checkbox only applies to legacy player playback.

## Limits and verification

- PCM sync makes decoded sample coordinates consistent with the displayed
  waveform. QAudioSink's processed output clock still cannot measure unknown
  DAC/driver/headphone latency; **sample-perfect audible/display sync is not
  yet physically verified**. A wall-time Cursor sync adjustment remains available.
- Pure regression tests and Python syntax checks passed locally. Windows Qt
  playback, actual audio-device timing, visual refresh, and the new menu action
  need to be checked with `RUN_QA_WINDOWS.bat` on the user's Windows machine.
- QA stays in the existing shared `reports` directory; test files should be
  deleted by the shared QA runner, preserving only reports.
