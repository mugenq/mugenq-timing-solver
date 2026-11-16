# BeatSolver research checkpoints

This document compares the strongest historical BeatSolver results with the latest persisted research cycles.

The experiments are not all measuring the same thing. In particular, the Reign of Fear result is a song-specific proof solver, the `that day` result is a substantially more automatic audio-selected timing test, and Cycles 14-16 are controlled solver-component experiments because the dense real-audio residual arrays were not available in the persisted state.

## Summary

| Checkpoint | Scope | Main result | Important limitation |
| --- | --- | --- | --- |
| Multi-song detector study | Real-song candidate generation | Full candidate coverage on 4rd Sanctuary (434/434), Reign of Fear (1,587/1,587), and `that day` (132/132); ExtraTrees pruning reduced total candidates 16.1% while preserving those labels | Candidate counts remained high; not a timing-section solver |
| Source-held-out synthetic pruning | Held-out synthetic percussive sources | 1,016/1,016 events with 2,438 candidates, a 51.83% reduction from the 5,061-candidate high-recall generator | Transfer to the real `floating architecture` map failed: 440/504 objects after pruning |
| HPSS rescue / floating architecture | Real-song candidate rescue and grid inference | Frozen rescue union covered 501/504 objects; local quarter-beat grid placed 499/504 objects near the inferred grid | Direct candidate coverage and grid inference are different objectives |
| Reign of Fear proof solver | End-to-end, song-specific | 1,587/1,587 objects within 5 ms; median 1.681 ms; p95 2.887 ms; max 3.663 ms | Tempo states, transition regions, calibrations, and topology were developed on this song |
| `sakuzyo - that day` | More automatic audio-selected timing | Continuous pulse beam: 100/132 within 5 ms; semi-Markov audio-selected path: 96/132 within 5 ms with 8 timing points and 3.41 ms median error | Reset/BPM sequence selection remains the bottleneck |
| Cycle 14 | Controlled residual-transition recurrence | 6/6 persisted resets recover the true predecessor; 500/500 calibrated-null trials exactly match the no-residual solver | No real end-to-end rerun available |
| Cycle 15 | Weight sensitivity | At frozen residual weight 0.055: worst-boundary success 96.416%, mean 99.380% across six persisted reset geometries | Monte Carlo sensitivity analysis, not real-audio validation |
| Cycle 16 | Objective-consistency audit | Found that DP and final path selection used inconsistent objectives; six reset terms contribute 1.4994 raw weighted bonus and 1.1494 net after common interval-change cost | This is a correctness fix; no new end-to-end accuracy number is claimed |

## Historical detector results

### Multi-song candidate generation

A detector revision introduced midpoint anchors and a quiet-relative peak rescue. Trusted-label coverage reached:

- 4rd Sanctuary: 434 / 434
- Reign of Fear: 1,587 / 1,587
- `sakuzyo - that day`: 132 / 132

An acoustic ExtraTrees scorer, evaluated with eight contiguous time folds, then reduced total candidates from 22,343 to 18,746 while preserving all of those labels, a 16.1% reduction.

This was useful evidence that acoustic ranking could remove candidates without immediately destroying recall, but the remaining candidate population was still far too large.

### Source-held-out synthetic pruning

On 12 unique held-out hitsound sources, 36 datasets, and 1,016 true events:

- high-recall SuperFlux generator: 5,061 candidates, 1,016 / 1,016 recall;
- best legal held-out pruning rule: 2,438 candidates, 1,016 / 1,016 recall;
- candidate reduction: 51.83%.

The exact held-out waveform oracle retained 2,435 candidates, only three fewer than the legal rule.

The important failure was transfer. On the real `floating architecture` map, the same synthetic pruning rule reduced the baseline from 2,541 candidates / 499 of 504 object matches to 1,013 candidates / only 440 of 504 matches. This is why the synthetic result should not be presented as a general real-song solution.

### HPSS rescue and grid inference

A later branch treated percussive evidence as a positive rescue/confidence source rather than an intersection gate.

Source-held-out synthetic benchmark:

- general detector: 5,061 candidates, 1,016 / 1,016;
- simple HPSS-percussive SuperFlux: 1,730 candidates, 1,016 / 1,016;
- leave-one-source-out with threshold safety backoff: 1,848 candidates, 1,016 / 1,016.

On `floating architecture`:

- general detector: 2,541 candidates, 499 / 504;
- frozen HPSS rescue union: 2,543 candidates, 501 / 504;
- exploratory lower HPSS threshold: 2,546 candidates, 503 / 504;
- intersection-only pruning: 1,558 candidates, 456 / 504.

Despite only 439 direct object matches from the percussive candidates, the inferred local quarter-beat grid still placed 498 / 504 objects near the grid. This was a key structural result: candidate generation is evidence for the timing model, not an obligation to directly recover every mapped object.

## Historical timing-solver bests

### Reign of Fear proof solver

The strongest historical end-to-end timing result is the Reign of Fear proof solver:

- 18 generated red timing points;
- 1,587 / 1,587 objects within 5 ms;
- median error: 1.681 ms;
- 95th percentile: 2.887 ms;
- maximum: 3.663 ms.

This proves that the acoustic candidate evidence can support a very accurate timing solution. It does not establish generalization because the tempo-state sequence, approximate transition windows, anchor calibrations, and beat-count topology were developed while studying this track.

### `sakuzyo - that day`

This track was used to remove those song-specific assumptions.

Latest persisted evaluation:

- continuous pulse beam: 187 timing points, 100 / 132 objects within 5 ms, median 2.25 ms;
- semi-Markov audio-selected reset path: 8 timing points, 96 / 132 within 5 ms, median 3.41 ms;
- semi-Markov development-best path: 11 timing points, 96 / 132 within 5 ms, median 3.00 ms.

The reset candidate graph itself was much better than the selected path:

- candidate node within 3 ms of 24 / 28 official red timing points;
- candidate node within 5 ms of 25 / 28 official red timing points;
- using candidate offsets with the official BPM sequence gives 126 / 132 objects within 5 ms.

That isolates the main failure: selecting the correct reset/BPM sequence from audio evidence, rather than simply generating plausible reset candidates.

## Recent research cycles

### Cycle 14: calibrated residual evidence

Cycle 14 injected calibrated signed residual evidence into ordinary local interval transitions, not only large rescue transitions.

Frozen reset weight: 0.055.

Controlled results:

- 6 / 6 persisted reset geometries select their true predecessor;
- weakest P10 case at 64.101 s still has positive net margin: 0.003304;
- 28.386 s reset has P10 net margin: 0.006818;
- 500 / 500 calibrated-null trials are exactly equivalent to the no-residual local solver.

No end-to-end `that day` improvement is claimed because the persisted dense offset arrays and audio evidence were unavailable during the cycle.

### Cycle 15: residual-weight sensitivity

Cycle 15 tested whether 0.055 was a knife-edge parameter.

Using 100,000 Monte Carlo samples per boundary across six persisted reset geometries:

- smallest tested weight with >=90% success on every boundary: 0.0475;
- >=95%: 0.055;
- >=97.5%: 0.0600;
- >=99%: 0.0675.

At the frozen 0.055 weight:

- worst-boundary success probability: 0.96416;
- mean boundary success: 0.993797;
- weakest boundary: 64.101 s;
- persisted calibrated-null false-positive rate: 0.0125.

Again, this is robustness analysis rather than an end-to-end song benchmark.

### Cycle 16: objective-consistency audit

Cycle 16 found a production-level inconsistency.

The semi-Markov solver generated paths under one DP objective, but the final `audio_selected` path was chosen with a different fixed objective that did not include the new calibrated residual term. A residual-favored path could therefore be generated correctly and then rejected by a selector that was literally unable to see the evidence that favored it.

For the six persisted reset geometries at weight 0.055:

- positive residual contribution sum: 1.499400;
- common 0.014/ms interval-change cost: 0.350000;
- net reset contribution: 1.149400.

For comparison, the existing 8-section audio-selected path contains 28.0 ms of adjacent interval change, worth 0.392 under that same fixed change penalty. The residual evidence is therefore not numerically negligible relative to the path-selection regularizer.

The proposed production patch uses one frozen audio-only objective for recurrence and final selection:

- section penalty: 0.85;
- interval-change penalty: 0.014/ms;
- weak-node penalty: 0.04;
- calibrated residual weight: 0.055.

The next legitimate end-to-end test is to regenerate/materialize the real `that day` dense residual evidence and run this unified objective without retuning, then run Reign of Fear unchanged as a regression test.

## Current interpretation

The research progression is now fairly clear:

1. Candidate generation can achieve very high recall.
2. Aggressive pruning can look excellent on synthetic data and still fail on real music.
3. Grid inference can succeed even when direct candidate-to-object matching is incomplete.
4. The strongest song-specific proof demonstrates that the acoustic evidence is sufficient.
5. General timing-section selection, especially short BPM bends and phase resets, remains the central unsolved problem.
6. The latest cycles focus on making the reset evidence and solver objective statistically and mechanically consistent before claiming another headline accuracy number.
