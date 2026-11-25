# BeatSolver Timeline v0.5.2 — spectral controls and diagnostics

See [CHANGELOG_v0.5.2.md](CHANGELOG_v0.5.2.md).

- **Playback:** 0.0–135.0% with 0.1% steps on the numeric control and keyboard; slider uses the same internal precision.
- **Layers:** Spectral Novelty plus Wave Energy and Energy Changes reference curves appear after processing a song. They can be toggled individually in Layers without changing candidate output.
- **Analysis → Spectral detail:** 1×/2×/4× time sampling (6/3/1.5 ms hop). Choosing a mode reruns the selected spectral Preview branch. Higher settings can be slow or memory-heavy on a full song.
- **Windows QA:** Run `BeatSolver_Timeline_v0.5.2\RUN_QA_WINDOWS.bat`, saving reports to `D:\gensokyo-magicraft\beatsolver\reports`.

---

# BeatSolver Timeline v0.5.1 — osu! export usability hotfix

**New export behavior:** all exported circles use `256,192`; `StackLeniency: 0`; MP3 input maps reference `audio.mp3`. If necessary, BeatSolver copies the loaded MP3 as `audio.mp3` next to the new `.osu` file, refusing to overwrite an existing different recording. The `.osu` output can now be saved in a separate folder (recommended: one folder per song), with the audio copy created there. For OGG or WAV input, the original audio extension is preserved: renaming those bytes to `.mp3` would produce a broken file.

The export selection now defaults to **Lightweight 80 ms**, with a **Moderate 40 ms** option and **Full fidelity (no thinning)**. Lightweight modes skip densely spaced markers **in the exported `.osu` only**; the app's Preview, Current and saved project are unchanged. Lower density may improve osu! runtime responsiveness, but actual game performance must be tested. The lightweight mode also uses AR9 instead of AR5 to shorten approach-circle overlap. Full fidelity retains all 1ms-distinct onsets, AR5. All choices use the same placeholder BPM or imported red/green timing.

Export via **Timing → Export Current as osu! onset-reference map…**. Choose an empty song-specific folder if `audio.mp3` already exists for a different recording. BeatSolver never overwrites the source recording, imported map, exported `.osu`, or an unrelated `audio.mp3`.

Run `BeatSolver_Timeline_v0.5.1\RUN_QA_WINDOWS.bat`; reports go to the shared `reports/` directory. The native Windows/Qt tests have not been executed in this Linux build environment.

---

# BeatSolver Timeline v0.5.0 — osu! integration

Install the two folders `BeatSolver_Timeline_v0.5.0/` and `qa/` in
`D:\gensokyo-magicraft\beatsolver\` beside the shared `.venv`.
Start `BeatSolver_Timeline_v0.5.0\START_WINDOWS.bat`.

## Using the new osu! workflow

1. **File → Open audio…** Load the recording. The Spectrogram and default
   Spectral Novelty → Peak Picker → Burst Cleanup graph remain enabled.
2. **File → Import osu! reference…** Select an existing osu!standard `.osu`.
   Accept the mismatch warning ONLY if you know it is the same aligned
   recording. Red/green timing points appear near the top of the waveform;
   mapped hit-object starts appear near the bottom. Toggle these in **View**.
3. **Timing → Compare to imported osu! map…** Choose Preview or Current and
   a matching tolerance. Read reference recall and signed-error histogram;
   additional detections are unlabelled, not proven mistakes. Export results
   using the dialog's CSV button.
4. Commit accepted candidates into **Current** (or use **Timing → Paste
   preview into Current output…**). Only these explicit Current candidates
   are eligible for `.osu` export. Your Preview predictions remain separate.
5. **Timing → Export Current as osu! onset-reference map…** Choose either a
   single **arbitrary 120 BPM** placeholder red line or existing imported
   timing rows. Save as a **NEW `.osu` file next to the loaded audio**.
   Each committed onset becomes a circle; the file's beatmap Version is
   `BeatSolver Onset Reference`. Never mistake this for a solved BPM map.

Existing saved BeatSolver projects remain schema 1 compatible and now also
retain the optional imported reference path and reference overlay toggles.
The original `.osu` file and audio are never altered by import or export.

Run `BeatSolver_Timeline_v0.5.0\RUN_QA_WINDOWS.bat` on Windows to validate
GUI load/close, import, comparison, export and QA cleanup. It includes a native
synthetic `.osu` round-trip in addition to the existing regression tests.
See `CHANGELOG_v0.5.0.md` and `QA_BUILD_REPORT.md` for safety and limitations.

---

## Archived v0.4.10 release notes

# BeatSolver Timeline v0.4.10 — Graph consistency fixes

Install into `D:\gensokyo-magicraft\beatsolver\`, alongside the shared `.venv`. Start `BeatSolver_Timeline_v0.4.10\START_WINDOWS.bat`.

**Fixed:** stale queued Run Graph callbacks can no longer replace a newer explicit selection. In a merged graph with multiple typed Spectrogram branches, the **Analysis → Evidence** selector now explicitly chooses which contributing node appears in the spectrogram viewer. Auto uses the first contributing source in stable graph order. The selected source is saved with projects, using a backward-compatible optional field.

**Preserved:** v0.4.9 worker lifetime safeguards, default spectral analysis with visible spectrogram, automatically applied Burst Cleanup, Preview/Current separation, notes, PCM playback, old saved graphs, and numerical detector defaults. This is a correctness update, not a claimed accuracy improvement.

**Windows QA:** run `BeatSolver_Timeline_v0.4.10\RUN_QA_WINDOWS.bat` or the extended read-only real-song harness from the root:

```powershell
& .\.venv\Scripts\python.exe .\qa\verify_v0410_extended.py --modes science,native,regression
```

See `CHANGELOG_v0.4.10.md` and `QA_BUILD_REPORT.md` for regression coverage and limitations.

---

## Previous release notes (v0.4.9)

# BeatSolver Timeline v0.4.9 — QThread completion safety

**Install:** extract this release into `D:\gensokyo-magicraft\beatsolver\`, keeping the shared `.venv` and older version folders. Launch `BeatSolver_Timeline_v0.4.9\START_WINDOWS.bat`.

## Key fix: background graph-worker ownership

Windows v0.4.8 extended QA reproducibly crashed because a delayed old-worker `finished` callback deleted a newer QThread. In v0.4.9, all graph completion handlers use the actual Qt signal sender and only clear the *active* worker when that worker is the sender. Stale graph results/errors from older workers are ignored. A finished worker stays owned until its queued completion arrives; a Run Graph request during this interval is coalesced and starts after cleanup. The similar spectrogram worker lifecycle is also owner-scoped.

The extended QA harness has been corrected to find the `signal.spectrogram` feeding the **selected Candidate Output**, instead of whichever spectrum happens to be first in graph-node order. Dedicated Qt regression tests reproduce the old late-finished scenario with safe QObject stand-ins.

## Default launch workflow (preserved from v0.4.8)

Fresh sessions launch with **spectrogram visible** and the active graph:

`Audio → Shared Spectrogram (legacy FFT) → Spectral Novelty → Peak Picker → Burst Cleanup → Preview Candidate Output`

Opening an audio file automatically runs that graph. The default spectral window is the original legacy mode (`window_ms=46`, actual 85.333 ms at 48 kHz), Peak Picker is percentile 60 with 12 ms minimum spacing, and Burst Cleanup uses 35 ms neighbor gap / 110 ms maximum burst / 60% weak retention. These values are unchanged from v0.4.8 because that configuration was verified in native Windows GUI QA.

The original audio, manual Current candidates, annotations, and saved custom project graphs are not overwritten. Loading a saved project restores its serialized graph and spectrogram visibility; the new default applies to fresh sessions or a deliberate graph reset.

## Windows testing

Run `BeatSolver_Timeline_v0.4.9\RUN_QA_WINDOWS.bat` for the normal regression and native GUI open/close smoke. This runs **local QA without automatically invoking Codex CLI**. For the extended real-song workflow and detailed thread-lifecycle checks, run from the project root:

```powershell
& .\.venv\Scripts\python.exe .\qa\verify_v049_extended.py --modes science,native,regression
```

The extended workflow uses the user-local Hanatan, THYME and hitsound files when present. It does not bundle original music. All QA-owned temporary artifacts are scoped to `reports\_qa_*` and deleted; retained QA evidence belongs to the shared `reports` folder. No unrelated processes should be touched.

**Local Linux verification:** 97 tests passed, 12 Qt-dependent tests skipped (Qt unavailable here). Code compilation and archive validation performed. The fix still requires full native Windows verification; physical acoustic/audio-device latency has not been measured.

See `CHANGELOG_v0.4.9.md`, `QA_BUILD_REPORT.md` and `qa/README_v049.md`.

---

## Previous release documentation (v0.4.8)

# BeatSolver Timeline v0.4.8 — Spectral Onsets + Burst Cleanup by Default

**Install:** extract the ZIP into `D:\gensokyo-magicraft\beatsolver\`, alongside the existing shared `.venv`. Launch `BeatSolver_Timeline_v0.4.8\START_WINDOWS.bat`. Old version folders and their tests remain untouched.

## Default launch workflow

The spectrogram viewer is ON automatically. Load a song to generate spectral Preview candidates immediately, using:

`Audio → Shared Spectrogram (legacy FFT) → Spectral Novelty → Peak Picker → Burst Cleanup → Preview Output`

The default spectral window is **legacy FFT** (`window_ms=46`, actual physical 85.333 ms at 48 kHz), because it currently finds more mapped piano onsets than the 46 ms physical window. The Peak Picker starts at percentile 60 and min spacing 12 ms; Burst Cleanup starts at a 35 ms neighbor gap, 110 ms burst span, and 60% weak retention. All settings remain editable on their graph nodes.

Spectrogram and Preview correspond to the selected typed graph's actual analysis settings. Late callbacks from an earlier viewer worker are rejected. Toggle spectrogram in Analysis and switch Attack / Harmonic views there.

Your human-committed **Current** timing candidates and annotations remain separate from generated Preview candidates. Existing `.beatsolver.json` projects load with their previous graphs and saved spectrogram visibility, without silent migration. **Graph → Reset starter graph…** will use the new defaults when explicitly requested.

## More experiments

- **Graph → Create typed Spectrogram comparison branch** creates a separate, exact-window spectral branch for independent comparison; existing branch is preserved.
- **Graph → Create Spectral Attack comparison branch** creates the historical Audio → Curve spectral branch.
- **Graph → Insert Evidence-Aware Cleanup** is available when you want a Candidates + independent attack curve filter instead of the default Candidates-only Burst Cleanup.
- **Timing → Export Preview evidence to CSV** writes timestamp, score and provenance. The generated data is not automatically promoted to Current.

## Windows QA

Run `BeatSolver_Timeline_v0.4.8\RUN_QA_WINDOWS.bat` for the full regression suite, real GUI smoke, cleanup checks and (if available) an opt-in read-only Codex CLI review. QA uses only versioned `qa/*v048*` scripts, and puts retained reports under `D:\gensokyo-magicraft\beatsolver\reports`. It terminates only processes it spawned, removing all temporary QA-owned media and projects. See `CHANGELOG_v0.4.8.md` for regression details.

**Local verification:** 92 Python tests passed, 11 Qt test modules skipped in this Linux environment. Real Windows Qt/physical playback synchronization remains to be verified.

---

## Previous release documentation (v0.4.7)

# BeatSolver Timeline v0.4.7 — Shared Spectrogram and Research Traceability

**Complete release.** Extract the ZIP into `D:\gensokyo-magicraft\beatsolver\`.
It contains the application folder **and a versioned `qa/` directory**.
Keep your shared `.venv` in the parent directory, then run
`BeatSolver_Timeline_v0.4.7\START_WINDOWS.bat`. Your old installations and
QA scripts are not overwritten.

## Recommended experiment

1. Load an MP3 with the existing Preview workflow; inspect how the old
   spectral branch behaves. It retains the **legacy window** by default.
2. Choose **Graph → Create typed Spectrogram comparison branch** and **Run
   Graph**. The new teal Spectrogram port connects to the Novelty from
   Spectrogram node; this branch defaults to **exact physical Hann window**.
3. Click each node's parameters to compare `exact_window=1` (46.0 ms physical
   Hann window at 48 kHz, FFT 4096) with `exact_window=0` (85.333 ms physical
   Hann window at 48 kHz, FFT 4096). Keep both Candidate Output nodes so the
   experiment remains reversible. Timing detection is not guaranteed better
   with the shorter window.
4. Enable the spectrogram in **Analysis**. If the selected graph contains a
   typed Spectrogram, the viewer reuses that same numerical output.
5. Hover over a curve name in the Layer Inspector to inspect its actual
   window length, FFT length, hop and timing semantics.
6. Select **Timing → Export Preview evidence to CSV** to obtain each generated
   candidate's timestamp, raw score and processing lineage. The existing
   Current CSV export remains a simple list of committed timestamps.

**Compatibility:** v0.4.6 `.beatsolver` project graphs continue to load
without conversion. Existing Spectral Attack Novelty nodes still default to
legacy FFT semantics; the newly added typed node defaults to exact mode.
The playback engine, PCM sync controls, candidate annotations and Current
output are unchanged.

**Do not confuse window length with timestamp bias.** Both modes center the
window at the recorded sample time. They inspect audio on either side of that
sample, but do not automatically advance or delay the candidate. FFT zero
padding changes the sampled frequency grid, not the amount of observed audio.

## QA on Windows

Run `BeatSolver_Timeline_v0.4.7\RUN_QA_WINDOWS.bat`. The included versioned
QA runner opens and closes a real GUI, runs regression tests, and optionally
requests a read-only Codex review. The runner owns only its spawned PIDs and
tries to kill their entire child tree on timeout. Persistent reports are
written to `D:\gensokyo-magicraft\beatsolver\reports\`. Temporary QA
files, logs, wavs and settings are isolated under `reports/_qa_*` and should
be removed before QA returns. Check the report for any failed cleanup.

The experimental Harmonic Reattack prototype was **not promoted** to a
production plugin. The Codex experiment recovered fewer mapped intro starts
than the existing Spectral Attack Novelty baseline and still misses repeated
same-pitch strikes. We should improve and validate it independently.

See `CHANGELOG_v0.4.7.md` and `QA_BUILD_REPORT.md` for implementation details.

---

## Historical v0.4.6 documentation

# BeatSolver Timeline v0.4.6 — Spectral Attacks, Evidence Cleanup & Spectrogram

**This is a complete version folder**, not a one-file replacement. Extract it
beside your shared `beatsolver/.venv` and run `START_WINDOWS.bat`.

## What's new

1. **Spectral Attack Novelty** (`Audio -> Curve`): native decoded PCM is analyzed
   in individual FFT frequency bands rather than only amplitude. The output
   emphasizes new harmonic activity even over a sustained piano note.
2. **Evidence-Aware Cleanup** (`Candidates + Curve -> Candidates`): an optional
   burst filter that tries to preserve faint but independent reattacks when the
   spectral curve shows separate peaks and valleys. It keeps candidate times
   unchanged and never modifies manually committed Current candidates.
3. **Synchronized spectrogram**: a third collapsible lane under the waveform,
   with **Attack bands** and **Harmonics / sustain** viewing modes. Same scroll,
   middle-drag pan, seek, hover and playhead as the waveform.

## Suggested first session (piano trill around 2.7–4.0 seconds)

1. Open the MP3. In **Analysis**, check **Spectrogram**. The analysis is
   performed on a background worker and only uses in-memory audio data.
2. Switch the new mode selector between **Attack bands** (new frequency
   changes) and **Harmonics / sustain** (the audible notes' ongoing energy).
3. Choose **Graph -> Create Spectral Attack comparison branch**, then **Run
   Graph**. A second Candidate Output is created; the original branch remains.
   Select either output in the node editor to compare candidate suggestions.
4. On the selected output, choose **Graph -> Insert Evidence-Aware Cleanup
   before Preview output**, then **Run Graph** again. Adjust `neighbor_gap_ms`,
   `max_burst_ms`, `weak_retention`, `spectral_valley`, and `search_ms` by clicking
   the new node. The upstream output can be connected to a separate Candidate
   Output for unfiltered A/B comparison.
5. Listen at 25%, 50%, and 100% with PCM sync if desired; this version **does
   not change the v0.4.5 audio engine**. Never interpret raw candidate markers
   as human-verified music timing.

The spectral novelty uses centered ~46 ms FFT windows at roughly 6 ms hops.
Consequently, feature peaks are **not guaranteed to be sample-accurate note
onsets**; a follow-up onset-edge refinement stage may still be valuable. The
spectrogram's attack mode is an *analysis representation*. It does not strip
piano sustain from the original file or rewrite the audio.

## Compatibility, memory, and testing

- v0.4.5 projects still load. The new graph node IDs are built in; v0.4.6
  projects using them need v0.4.6 or newer.
- Spectral features require the native mono PCM cache (192 MiB cap). If a song
  exceeds that cap the original waveform and existing graph still work, but the
  new spectral analysis displays a clear limitation instead of inventing data.
- Long songs may take time to analyze. The spectrogram worker does not block the
  main UI, and identical spectral parameters reuse a one-result-per-song cache.
- Run `RUN_QA_WINDOWS.bat` to use the existing shared Codex/Windows QA harness.
  Keep persistent reports under `D:\gensokyo-magicraft\beatsolver\reports`;
  temporary test assets must be cleaned. Do not terminate unrelated processes.
- Local deterministic suite: **78 passed; 9 Qt-dependent tests skipped**
  because PySide6 is unavailable in this environment. Windows device behavior,
  mouse ergonomics and real GUI rendering still require the Windows QA pass.
- Verified on the supplied piano MP3's first five seconds: the spectral method
  produced local changes around 2.784, 2.886, 2.952, and 2.994 seconds. These
  timestamps are *unverified onset hypotheses*, not reference annotations.

---

## Previous release notes (v0.4.5 and earlier)

# BeatSolver Timeline v0.4.5 — Burst Cleanup and sample-clock playback

**What's new:** A built-in optional **Candidates → Candidates** Burst Cleanup
node, and **PCM sync** for normal song playback, enabled by default without
requiring hitsounds. Both were developed from the v0.4.4 source.

## Try the filter

1. Load a song. Use **Graph → Insert Burst Cleanup before Preview output**.
2. Click **Run Graph**. The Preview output now passes through Burst Cleanup.
3. Select the Burst Cleanup node in the graph to adjust its parameters:
   - **neighbor_gap_ms** (35): maximum gap between consecutive detections in
     the same candidate burst.
   - **max_burst_ms** (110): maximum length of one group; prevents a fast
     stream of notes from being swallowed by a long chain.
   - **weak_retention** (0.60): keep peaks whose score is at least 60% of
     the strongest peak in that burst. `0` disables suppression.
4. To compare unfiltered data, attach another **Candidate Output** directly
   to the upstream candidate node, then select either output in the graph.
   The original and manual annotations are never deleted by this filter.

The score threshold is **relative within each burst**, and scores come from
upstream detectors. It cannot prove that adjacent weak piano notes are false.

## Test the synchronized song cursor

- Keep **Playback → PCM sync** checked (the new default). Load MP3/OGG/WAV;
  the app decodes stereo PCM, uses the same native sample timebase as the
  waveform, and tracks source frames consumed by `QAudioSink`. **Continuous
  clicks can remain off**; you should hear only music.
- Change playback speed to 25%, 50%, 100%, then 150%, including changes while
  playing. Seek/pause/replay. Disable PCM sync to compare the previous media
  backend; re-enable it to return to decoded playback.
- Use **Cursor sync** only for a residual *measured* output-device delay.
  It now represents wall-clock milliseconds: at 25% speed a 40 ms correction
  corresponds to 10 ms of song-timeline displacement.
- Native PCM streaming changes pitch when changing speed. The Qt legacy player
  can still be chosen if its Preserve Pitch option is more useful.
- If the native device refuses PCM, the timebases differ, or the 256 MiB stereo
  cache is exceeded, the app automatically falls back to the legacy player.

**Precision caveat:** software clocks are not acoustic loopback measurements.
We have made the waveform and normal playback use the same decoded audio
sample coordinates; the physical output device may still introduce latency.

## Setup / testing

Extract this version folder beside the existing shared `.venv`, then launch
`START_WINDOWS.bat`. The source file changes are split across modules, so
replace the complete folder rather than only the main `.py`.
Run `RUN_QA_WINDOWS.bat` for Windows/Qt verification; the shared QA workflow
writes persistent reports to `D:\gensokyo-magicraft\beatsolver\reports` and
should clean up all temporary QA files. See `QA_BUILD_REPORT.md`.

---

# Playback synchronization (v0.4.4)

At reduced speed in the experimental Streaming (beta) mode, the playhead
uses a ledger of exact output-PCM buffers and their original source-frame
positions, sampled by QAudioSink.processedUSecs. This avoids applying the
**new** speed to audio already queued at the **old** speed.

In the **Playback** tab, **Cursor sync** (default 0ms) is a visual-only
calibration for device latency, independent of the candidate **Global offset**.
A positive value moves only the cursor later; a negative value moves it
earlier. Only adjust after comparing to clicks played through a real device.

Test slow playback at 25%, 50%, and 100% with Streaming (beta) ON and OFF.
The 240 Hz timer improves animation smoothness but cannot prove the display
refreshes at 240 Hz, and QAudioSink clock alone cannot measure acoustic output
latency. Run RUN_QA_WINDOWS.bat for regression checks.

# BeatSolver Timeline v0.4.4 — UI and streaming reliability hotfix

## v0.4.3 corrections from Windows QA (2026-11-23)

This release addresses the supplied v0.4.2 screenshot and `QA_v042_stream_20261123_222604.zip` report. The prior Windows run found three distinct streaming/UI defects despite the ordinary regression suite passing 63/63. This version has not yet been run on the user's Windows machine.

1. **Smaller, more usable workspace**: the formerly always-visible transport, analysis, candidate hierarchy, Current/BPM and audio strips are now **five tabs** under the timeline. Click **Playback**, **Analysis**, **Candidates**, **Current / BPM**, or **Audio** to switch. The old control widgets remain available under the same source attributes for project compatibility. Tabs still allow horizontal scrolling when necessary, but only one toolbar scroll area competes for screen height.
2. **Diagnostics button**: uses `show()` plus `raise_()` to activate its tabified dock; avoids recursive two-way `visibilityChanged`/button toggle wiring. The Layer Inspector's tab toggle follows the same approach. Closing Diagnostics hides the panel; reopening it should work repeatedly.
3. **Streaming natural end**: QAudioSink `IdleState` with fully consumed PCM now marks the stream finished and updates Play/Pause. A 240-Hz cursor-timer poll is a fallback for missed state signals. Pressing **Play** after EOF starts from 0. Pausing at an ordinary non-end timestamp preserves the position.
4. **Streaming startup failure**: failure to reactivate a cached stereo buffer is now caught and routed through the existing legacy-player fallback. This is especially important after toggling Streaming off and back on when the output device fails to initialize.
5. **Tests**: `tests/test_v043_qt_optional.py` adds Qt-only regressions for dock visibility, EOF/replay, and cached-startup fallback. The previous 56 pure tests continue to pass; these GUI tests need Windows execution.

The normal shared-environment launcher and no-zombie QA workflow are retained. For the dedicated extended Windows QA, also rerun `..\qa\verify_v042_stream.py` from inside this version folder if the earlier Codex QA placed that script in your shared `qa` directory. All generated QA fixtures should remain under `..\reports` and be removed by the tester after completion; persistent Markdown/text reports are allowed.

**Priority for real Windows verification:** (a) open Diagnostics twice, (b) play a short synthetic WAV to its end and press Play again, (c) exercise simulated cached device startup failure and verify fallback, (d) resize the app to around 1230×785 and compare waveform space to the provided screenshot. Physical audio latency and actual 240 Hz scanout are still unmeasured.

Native Windows/Python waveform and candidate editor with a modular processing graph. This release builds on the experimental timing-workflow features while retaining the v0.3.3 middle-drag navigation, candidate balls, replacement ranges, node plugins, offset control, curve inspection, project persistence, and shared-`.venv` launcher.

## v0.4.2 additions: timing diagnostics and optional streaming audio

This release keeps **File mixer (v0.4.1)** as the default for compatibility. The new streaming system is explicitly **beta** until the real Windows hardware QA confirms it.

1. Open the **Diagnostics** tab/button to view actual **Qt Paint event delivery** (rolling 5 s), playhead timer callbacks, paint-interval 95th/99th percentiles and the difference between the *interpolated UI clock and QMediaPlayer position reports*. **This does not measure display scanout, DAC latency, or external audio onset alignment.**
2. The same tab contains a **20-bin histogram** for current graph candidate scores, plus median/kept counts. The scores are *heuristic rankings*, not calibrated probabilities. Reversible Preview filtering does not affect Current.
3. To try streaming: load a song, enable **Continuous clicks**, and then check **Streaming (beta)** in the mixer toolbar. Stereo PCM is decoded **once** into a bounded RAM cache (up to **256 MiB**). On regular-length tracks, candidate edits, audio gain changes, and click-source switches update click events in the audio buffers rather than decoding and writing a new full-track WAV.
4. Music and hitsounds still share the exact same decoded PCM sample positions **within the streaming mixer**. Seeking, 0–1000% playback and pausing are supported. Playback at speeds other than 100% uses *linear resampling* and changes pitch. **Qt preserve-pitch processing does not apply to the streaming backend.**
5. If the current output device does not accept native stereo/16-bit/sample-rate audio, the song exceeds the memory limit, or streaming startup fails, the application **unchecks the beta toggle and falls back** to the previous full-file renderer. No new packages are required.
6. A song change or closing the app cancels any in-progress stereo decoder. Streaming does **not** generate rendered WAVs, though the older short Audition and full-file fallback modes still do. Their temporary directories remain cleaned at shutdown.

**Important Windows validation:** QAudioSink/PySide6 integration has *not* been executed in this Linux build environment. Test the beta stream on Windows before using it for timing-critical listening. Compare it to the known-working file mode, particularly while seeking, toggling 0% speed, changing clicks and closing. The QA script tests both Python/Qt regressions and a real GUI open/close, but full hardware timing requires an additional physical loopback measurement.

**QA runner:** use `RUN_QA_WINDOWS.bat` with the existing shared `../qa/` runner. Its persistent Markdown results go only to `D:\gensokyo-magicraft\beatsolver\reports`. No temporary generated audio or QA fixtures are included in this release ZIP; the shared runner removes its `_qa_*` folders. The wrapper also disables pytest cache and bytecode files.

## Installation

1. Extract the complete `BeatSolver_Timeline_v0.4.3` folder alongside your previous versions and `.venv` folder, for example `BeatSolver/.venv/` and `BeatSolver/BeatSolver_Timeline_v0.4.3/`.
2. Double-click `START_WINDOWS.bat`. It checks and reuses `../.venv`; compatible dependencies are not installed every time.
3. Drag an MP3, OGG, FLAC, or WAV into the editor. The starter candidate graph runs automatically.
4. If startup fails, run `VIEW_LOG_WINDOWS.bat` or inspect `%LOCALAPPDATA%\BeatSolverTimeline\startup.log`. The console only stays open on errors.

## Inherited v0.4.1 features: candidate hierarchy, continuous hitsounds and mixer

- **Min score** slider (0–100%) ranks generated Preview events using their detector scores and time-local rank; low-scoring events become faint ghosts. **Dedup** (0–100 ms, disabled by default) retains the stronger nearby prediction. Neither control deletes graph data or modifies human-confirmed candidates or Current.
- **Continuous clicks** checkbox plays clicks over the entire song via a background-rendered stereo PCM mix (Preview, Current, Both with different tones, or BPM grid). Clicks are mixed at exact decoded sample positions rather than triggered by the 240 Hz GUI timer. The mix is regenerated after edits, and audio switches to the new file when ready; a short pause can occur while switching.
- **Music** and **Hitsound** sliders independently adjust the music/click gain used for rendered continuous playback and short click auditions. With continuous clicks disabled, Music changes the ordinary song volume immediately.
- **Fixed** zero-speed audition file leak, exclusive BPM range ends and an erroneous Qt refresh-rate test. See `QA_BUILD_REPORT.md`.
- Project JSON saves the hierarchy controls and audio settings; legacy v0.4.0 projects load with disabled filtering/continuous clicks by default.
- **QA**: the included `RUN_QA_WINDOWS.bat` uses the previously installed shared parent `qa` runner and read-only Codex review. Reports belong in `D:\gensokyo-magicraft\beatsolver\reports`, with test files deleted by that runner. GUI/audio behavior must be verified on Windows.

## 1. Smooth playback cursor

- The transport **Cursor Hz** selector offers 60, 120, and 240 target redraws per second (default **240**; a 4 ms precise Qt timer requests ~250 ticks per second).
- A monotonic clock interpolates between sparse `QMediaPlayer.positionChanged` callbacks, using backend timestamps to gently correct drift. Pauses, seeks, speed changes, and playback ending are handled explicitly.
- The waveform and curve viewer now repaint small dirty strips around the old and new playhead positions. This avoids recalculating the entire 2-minute waveform 240 times a second.
- **The Hz control is a request, not a measured render frame rate or a promise of sample-accurate A/V synchronization.** Real update rate depends on Qt, OS scheduling, CPU, and display refresh. Backend MP3 decoder delays can still differ from the decoded analysis cache.
- Qt 6.10+ multimedia backends may expose optional **Preserve pitch**. If unavailable, the checkbox is disabled. The original song is still played through Qt at its native quality; the display timer does not resample or improve the MP3 itself.

## 2. Candidate click audition

- Click **Audition clicks** to play a short excerpt with synthesized clicks mixed directly into the decoded audio samples at candidate timestamps.
- Choose **Preview** or **Current** as the source, and select the excerpt duration (1–12 seconds, default 6 s). The excerpt is centered around the last hovered timestamp, or the playhead if none.
- **Right-click any candidate ball → Audition clicks near this marker** to audition the relevant source layer immediately.
- The main song pauses during audition, and the audition playhead follows the excerpt; after it ends, the regular song remains paused where you left it.
- The audition mix is a local, temporary **16-bit mono PCM WAV**, generated from the retained decoded waveform (sample indices are exact within that PCM). Clicks do not rely on a second real-time timer, so their *relative* alignment in the excerpt is stable.
- **Limitations:** Audition only works for songs whose decoded PCM fits the existing **192 MiB cache**. Excerpts are mono, not the original stereo. Audio output/device latency remains possible, though it affects song and click together. Extremes of playback speed depend on Qt Multimedia. The app does not upload any audio.

## 3. BPM ranges and visual beat subdivisions

Open **BPM ranges…** in the workspace toolbar or **Timing → Manage BPM guide ranges…**.

- Each range has independent **start time (first downbeat)**, **end time**, **BPM**, **beats per bar**, **meter denominator**, and **subdivisions per beat**.
- Example: **180 BPM**, **4/4**, **1/3** generates **12 ticks per bar**: one every 111.111... ms, with beats every 333.333... ms and a new bar every 1.333333... s.
- Faint subdivision lines, medium beat lines, and stronger downbeat lines appear over the waveform. The **BPM guides** checkbox toggles them.
- These guides **never generate, delete, snap, or move candidates**. Overlapping guide ranges are allowed for experimentation. A separate timing editor can use them as constraints later.
- BPM means **quarter notes per minute**; for 6/8 or other compound meters, specify the intended quarter-note-equivalent tempo. The range start anchors the first bar (phase).
- Guide ranges persist in `.beatsolver.json` projects; old projects open with none.

## 4. Preview versus Current candidate layers

- **Preview (orange)**: the output of the currently selected graph. Rerunning the graph replaces these predictions. Existing preview annotation edits remain supported.
- **Current output (bright magenta)**: a committed candidate set with independent edit, undo, project persistence and CSV export. Graph reruns and Global Offset changes do not move it.
- Both layers have individual **Preview** and **Current output** visibility checkboxes.
- **Paste preview → Current…** offers `Add to Current` (keep previous edits and add new timestamps) or `Replace Current` (use the preview as the new final set). Both can be undone with **Ctrl+Z**.
- **Right-click a preview candidate ball → Copy this marker to Current output** adds that one timestamp without deleting the preview.
- **Right-click empty timeline** now adds a marker to **Current output**, instead of adding a manual preview annotation. Existing preview balls remain movable for experimenting; Current balls can also be dragged, deleted, and moved to an exact timestamp.
- Candidate replacement proximity now operates **within the same layer**, so dragging a Current candidate does not accidentally destroy a preview prediction.
- Use **Timing → Export Current output to CSV…** to save a `time_seconds,type` table. This is the first product-export format, not a completed osu! `.osu` timing-point exporter.
- The existing exclusion masks and generated candidate annotations affect **Preview**. Previously committed Current timestamps stay untouched by graph reruns, exclusions, and offset changes.

## Quick workflow

1. Load audio and run the starter graph.
2. Use the zoom wheel, middle-mouse panning, and 240 Hz cursor setting to inspect attacks.
3. Open **BPM ranges…**, add a region such as 180, 4/4, 1/3, and watch the faint guides against the waveform.
4. Audition the Preview clicks around a questionable candidate. Adjust predictions or the Global Offset if useful.
5. Use **Paste preview → Current…** to commit the selected graph output. Visually inspect both layers or hide Preview entirely.
6. Right-click empty waveform space to add to Current. Drag Current balls to correct timing. Audition Current clicks.
7. **Ctrl+S** saves the graph, preview edits, Current output, guides and other project parameters. **Timing → Export Current output to CSV…** exports the committed timestamps.

## Important limitations

- The built-in energy-change/peak detector has **not been replaced**. Its predictions can still be late. Onset-edge refinement remains a future plugin.
- The smooth cursor uses **clock interpolation**, not the physical audio interface clock. It improves apparent responsiveness, without certifying perfect synchronized output at 240 Hz.
- The click audition is mixed in the same decoded PCM data and therefore avoids independent metronome-scheduling jitter. The decoding/playback timebase difference between `miniaudio` and `QMediaPlayer` should still be measured against a known reference if absolute MP3 onset accuracy becomes important.
- Current is a timestamp collection; formal musical grid fitting, snapping, per-event confidence and osu! timing export remain future milestones.
- Audio and project files remain local; external Python node plugins execute code and must only be loaded from trusted sources.
- PySide6 and miniaudio are required. Neither GUI runtime was available in the development environment, so Windows mouse/audio behavior has **not been verified by a real Qt run**.

## Files

- `beatsolver_timeline.py`: window, waveform, candidate interaction, playback and menus.
- `timing_workspace.py`: typed BPM regions and committed candidate data structures (pure Python).
- `timing_ui.py`: BPM range editor and manager.
- `playback_clock.py`: interpolation between media backend position callbacks (pure Python).
- `audition_audio.py`: sample-aligned short click WAV generation (pure Python).
- `audio_resolution.py`: decoded PCM cache and waveform display resolution.
- `timeline_layers.py`: intermediate processing curves.
- `merge_logic.py`: time-based same-layer candidate replacement.
- `node_engine.py`, `node_graph.py`, `annotations.py`: processing graph and preview-edit persistence.
- `plugins/`: plugin example.
- `tests/`: automated engine, workspace, audio and optional Qt GUI tests.

## Validation

**48 pure-Python tests passed in the v0.4.2 Linux build**, including the old graph engine, waveform timebase, candidate merging, exact 180 BPM triplet grid, guide persistence, Current output persistence, a 7,200-second dense-grid performance guard, sample-index-aligned click WAV generation, and visual clock interpolation. **4 optional Qt tests skipped** because PySide6/miniaudio could not be imported in the Linux build environment. Python syntax compilation and ZIP CRC checks completed. Windows playback, 240 Hz rendering, dialog geometry, and native media backend performance still need confirmation on a Windows machine.

## Inherited v0.4.1 files

- `candidate_priority.py`: reversible time-local priority scoring and duplicate suppression.
- `live_mix.py`: streaming sample-aligned continuous hitsound renderer.
- `RUN_QA_WINDOWS.bat`: reuse the shared parent QA runner; reports go to the parent `reports` folder.
- `QA_BUILD_REPORT.md`: QA baseline analysis, fixes, status and Windows rerun steps.
