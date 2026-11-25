# BeatSolver Timeline v0.4.7 — Spectral architecture and test reliability

## Fixed: FFT timing metadata / physical window lengths

v0.4.6 requested a 46 ms spectral window but applied a Hann window over the
entire rounded-up FFT length. At 48,000 Hz, 46 ms = 2,208 samples but the
power-of-two FFT = 4,096 samples, a physical window of 85.333 ms.

v0.4.7 records **requested window**, **physical Hann window**, **FFT length**,
**hop samples**, **center sample indices**, **frequency band edges**, and
representation version separately.

- `legacy_fft`: 4,096 physical Hann samples and a 4,096-sample FFT for this
  configuration. Existing projects and the existing `Spectral Attack Novelty`
  node retain this mode by default. Numeric regression against v0.4.6 passes.
- `exact`: 2,208 physical Hann samples, zero padded to a 4,096-sample FFT.
  Enabled by default on the **new typed Shared Spectrogram node**. Intended
  for controlled A/B comparisons; it is not claimed to outperform legacy.
- **No automatic timestamp correction** from half a window. The analysis is
  centered; lookahead is recorded as metadata rather than a measured offset.

## New: typed spectral graph ports

- **Shared Spectrogram:** Audio → Spectrogram (teal sockets).
- **Novelty from Spectrogram:** Spectrogram → Curve.
- Menu: **Graph → Create typed Spectrogram comparison branch** constructs
  a complete independent branch ending in Candidate Output and selects it.
- Existing spectral Audio → Curve node still works and accepts an optional
  `exact_window` parameter (default `0` for compatibility).
- The spectrogram viewer can reuse the numerical result of the selected
  typed graph; no conversion from PNG or display colors is involved.
- Bounded (192 MiB) **per-song**, single-flight, thread-coordinated spectral
  cache replaces the old shared dictionary with cross-worker `.clear()` races.

## New: candidate provenance, no changes to committed Current data

- Generated candidates can carry time, curve index, upstream spectral
  metadata, raw heuristic scores and the producing graph node identifier.
- Built-in spacing, Burst Cleanup, Evidence-Aware Cleanup and Merge preserve
  lineage and identify their transformation; a merged event records its parents.
- Legacy third-party plugins can still return simple `(times, scores)`;
  matching upstream timestamps are used to recover parent records when possible.
- **Timing → Export Preview evidence to CSV** creates a user-selected research
  export with timestamps, scores, metadata and per-event provenance JSON.
- Existing Current timestamps, Preview/Current separation, annotations,
  projects and audio/PCM sync are unchanged. Scores remain uncalibrated.

## More reliable Windows QA process ownership

- `qa/verify_beatsolver_v047.py` and `qa/RUN_CODEX_QA_v047.ps1` are **versioned**;
  they do not overwrite your existing shared QA scripts or prior app versions.
- Windows Job Objects use kill-on-close where supported so hung child trees
  are reclaimed. A bounded taskkill `/T /F` fallback is retained.
- After timeout, tests check that the owned PID is reaped; no unbounded
  `wait()` calls. Processes started before QA are never targeted.
- QA tests, temporary settings/logs and generated audio are confined to
  `beatsolver/reports/_qa_*` and removed after the run. Persistent report:
  `beatsolver/reports/QA_YYYYMMDD_HHMMSS_ffffff.md`.
- `RUN_QA_WINDOWS.bat` invokes the versioned QA scripts.

## Deliberately excluded

- Experimental Harmonic Reattack is **not** installed as a production node:
  the supplied Codex study measured 37/45 intro matches compared with
  Spectral Novelty's 39/45 under the primary matching setup. Same-pitch
  reattacks remain unreliable. This version adds the infrastructure needed
  to research it later, while retaining the better-established baseline.
