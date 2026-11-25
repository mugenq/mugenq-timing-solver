# BeatSolver Timeline v0.5.0 — osu! Reference Import & Onset Export

Built on the Windows-verified v0.4.10 handoff. The previous playback engine,
spectral graph, Burst Cleanup, manual edits and Current/Preview independence
are preserved. No new BPM estimation is claimed.

## New workflow

1. **File → Import osu! reference…** reads an existing `.osu` (osu!standard,
   `Mode: 0`) without changing it. Imports unique circle and slider-head onset
   timestamps and all timing points. It keeps red uninherited BPM points and
   green inherited points separate. Slider tails/repeats and spinner starts
   are not treated as note-onset ground truth. Multiple hit objects with the
   same timestamp count as one onset reference.
2. **View → Show osu! hit-object heads** shows a cyan/blue lower marker strip.
   **View → Show osu! red / green timing points** shows distinct red and green
   tick strips near the top of the waveform. They never modify the graph,
   Current, or timing guides.
3. **Timing → Compare to imported osu! map…** compares visible Preview or
   Current against the imported partial-positive references with user-set
   tolerance (default ±30 ms). The dialog displays matched references,
   unmatched references, unlabelled detections, median absolute timing error,
   and a histogram of signed errors. Matching is one-to-one, maximum
   cardinality followed by minimum total absolute deviation, using sparse
   ordered matching; extra detections are never automatically called false.
4. **Timing → Export osu! comparison CSV…** writes a new CSV containing
   matched/unmatched/unlabelled rows and signed offsets. Export from the
   comparison dialog reflects the selected source and tolerance.
5. **Timing → Export Current as osu! onset-reference map…** creates a **new**
   `.osu` file alongside the loaded MP3/OGG/WAV. Every committed Current
   candidate becomes a circle at its nearest osu! millisecond. By default
   one **placeholder 120 BPM red timing point at 0 ms** is written; users can
   instead explicitly reuse the imported map's original red and green timing
   points. The placeholder must never be interpreted as estimated song BPM.
   Metadata is copied when available, with Version `BeatSolver Onset Reference`;
   BeatmapID/SetID are cleared. No original map/audio is overwritten.
   Sub-millisecond events that round to the same osu! millisecond collapse;
   the export dialog reports the count.
6. Existing `.beatsolver.json` projects preserve an optional read-only
   `osu_reference_path` and the two overlay visibility flags under the
   **same schema 1**. Older projects load unchanged. Missing reference files
   do not destroy the other project data.

### Safety and limitations

- `.osu` imports are read-only. No source audio bytes are modified or copied.
- The export is exclusive-create (`open('x')`), with no overwrite of existing
  files, and must be saved beside the actual audio so `AudioFilename` resolves.
- BeatSolver can analyze FLAC, but osu! onset export requires MP3/OGG/WAV.
- When the imported map names a different audio file, explicit confirmation
  is required at import and again before reusing its timing rows. Nothing is
  shifted automatically; recording alignment and encoder delays are unknown.
- Precision and F1 cannot be computed from partially mapped references.
- v0.5.0 does not fit BPM sections, quantize notes, write actual timing
  solutions, identify slider tails as onsets, or import osu!mania.

## QA and compatibility

- Local source tests: **112 passed, 14 Qt-dependent modules skipped**.
- Actual supplied Yuuhizaka map: 46 unique circle/slider-head timestamps,
  453 timing points (325 red, 128 green). 
- Real Yuuhizaka **11.1-second excerpt-only** spectral graph: 188 cleaned
  candidates and 43/45 imported intro matches within ±30ms; this uses an
  excerpt-local percentile and must NOT be compared with the prior full-track
  41/45 without controlling the threshold calculation.
- Shared QA scripts include an in-memory/window real native GUI roundtrip of
  synthetic `.osu` imports and exports. The extended historical spectral
  references are now bundled under `qa/`, avoiding the previous dependency
  on an installed v0.4.6 directory. The real Windows Qt run is required.

Run `BeatSolver_Timeline_v0.5.0\RUN_QA_WINDOWS.bat` after extraction.
