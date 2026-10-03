# Timing Solver / BeatSolver

Experimental rhythm-analysis software for recovering timing structure from music.

The current implementation estimates tempo, phase, timing sections, and beat-grid candidates from audio. Predictions are evaluated separately against osu! beatmaps, so map data is used as ground truth for evaluation rather than as input to the predictor.

## Project focus

This repository explores a practical question: how much rhythm-game timing structure can be recovered directly from audio with interpretable signal-processing methods?

The work includes:

- onset-based rhythm analysis;
- tempo and phase candidate estimation;
- local timing-section detection;
- beat-grid matching;
- one-to-one onset/grid evaluation;
- explicit separation between prediction and evaluation;
- iterative benchmarking across experimental versions.

## Main files

- `beattimer_core.py` — audio-only timing predictor.
- `beattimer_evaluation.py` — osu! parsing and evaluation utilities.
- `beattimer_dev_v0.5.ipynb` — predictor development notebook.
- `beattimer_evaluator_v0.5.ipynb` — evaluation and reporting notebook.
- `benchmark_dump/` — saved benchmark outputs.
- `history/` — selected earlier implementations showing the development path.

## v0.5 changes

The cleaned v0.5 line includes:

- one-to-one onset/grid matching;
- fallback sections that preserve the original global phase;
- cross-window phase comparison using actual grids at a common time;
- phase diagnostics computed before segmentation;
- continuation scoring with BPM and phase held fixed;
- onset-derived phase candidates instead of a 2,000-step brute-force scan;
- separate evaluation of the initial timing anchor and later section boundaries;
- corrected summaries, paths, benchmark output, and duplicate keys.

## Setup

```bash
python -m pip install -r requirements.txt
```

Place local audio and corresponding evaluation maps under `songs/<name>/`, then edit `song_selection` in the notebooks.

Audio files and third-party beatmaps are intentionally not redistributed.

## Status

This is an experimental research/development project, not a finished timing product. Some older bulky development artifacts remain in the broader university programming archive rather than being duplicated here.
