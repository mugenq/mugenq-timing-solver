# BeatSolver Timeline v0.5.2 — Spectral detail, transport precision, diagnostic curves

## Fixed: playback control

- Playback speed is limited to **0.0–135.0%**, with **0.1% precision** in both the slider and the numeric spin box. Arrow/numeric increments are 0.1%, page step is 1.0%; a narrow slider cannot physically distinguish all 1,351 possible positions by mouse drag alone, so use the numeric input/keyboard for exact tenths.
- No new coarse steps or rate presets are imposed. The **1×** playback button still restores 100.0%; speed 0% still pauses.
- Projects from older versions with saved rates above 135% are accepted but clamped to the new cap on reopening.

## Fixed: Energy Changes visibility

- Spectral Novelty's active processing graph does **not** include the old `wave.energy → signal.transients` branch. Previously the Layer Inspector showed only curves executed upstream of the chosen Candidate Output, so "Energy Changes" disappeared even though the node still existed.
- The Layer Inspector now also displays independent visual **Wave Energy (reference)** and **Energy Changes (reference)** curves after a graph run. Their computations reuse the original node implementations and the selected analysis-envelope resolution. Both are enabled by default, can be hidden/recolored individually in **Layers**, and are cached per decoded song and analysis interval.
- These diagnostics **never contribute to Preview**, change spectral candidate scores, alter `Current`, or rewrite existing graphs/projects. For a graph to use Energy Changes *as a detector*, it still needs to be connected explicitly.

## New: numerical spectrogram time detail

Under **Analysis → Spectral detail**, choose:

| Setting | Numerical hop | Relative time samples |
| --- | ---: | ---: |
| 1× (default) | 6 ms | 1× |
| 2× | 3 ms | 2× |
| 4× | 1.5 ms | 4× |

- The selector changes the **contributing typed Spectrogram node** on the currently selected Preview output. It recomputes the spectral graph and the viewer from the **same** data and **may change detected candidates**. Existing snapshots are not overwritten; `Current` stays independent.
- FFT window length, actual physical Hann support, FFT frequency bins, and source audio remain unchanged. Higher time detail does not automatically give higher frequency resolution or guarantee more accurate onset positions.
- The viewer estimates contrast from a bounded time subsample to avoid sorting millions of bins on the UI thread; the rendered data itself retains its full time resolution.
- 2×/4× are explicit opt-ins. They increase STFT work and memory use considerably, particularly on full songs. Save projects to keep the chosen graph hop value.

## Validation

- Local: **121 non-GUI tests passed**, **16 Qt-dependent tests skipped** because PySide6 is not installed in the build environment. Numerical checks cover 0.1% speed conversion, the capped range, reference curve alignment, default graph preservation, and 1×/2×/4× actual STFT sample counts.
- Native Windows GUI coverage added for decimal transport, reference-layer visibility, and 4× graph settings; **Windows QA is still required**.
- Run `BeatSolver_Timeline_v0.5.2\RUN_QA_WINDOWS.bat`. QA artifacts are stored in the shared root `reports` directory, with temporary files and QA-owned process trees cleaned by the versioned runner.
