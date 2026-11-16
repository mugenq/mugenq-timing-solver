# BeatSolver / BeatTimer — Full Development and Attribution History

This document reconstructs the development history of BeatSolver from the surviving notebooks, benchmark dumps, research bundles, handoff notes, and scheduled R&D records.

It has two purposes:

1. record what changed and what was learned at each stage;
2. make authorship and LLM involvement explicit instead of pretending every stage had the same development process.

The project did **not** follow one clean versioning scheme. Early work used notebook/code versions such as `v0.1` through `v0.6`; some internal engine snapshots used `0.11` / `0.12`; later work shifted into named research experiments and scheduled R&D Cycles. A saved `benchmark_v0.8.json` also exists but is byte-identical to the v0.7 benchmark.

## Attribution scale

The labels below describe my contribution to each stage. They are deliberately qualitative. A percentage such as "73% human-written" would imply evidence that does not exist.

| Level | Meaning |
| --- | --- |
| **A — User-led** | I originated the problem and technical direction, made the major algorithm/evaluation decisions, and was substantially involved in development/review. LLM use was mainly implementation assistance. |
| **B — User-led, substantial LLM implementation** | I set the architecture, constraints, and success criteria; LLMs contributed substantially to implementation, debugging, or experiment scaffolding. |
| **C — Collaborative research** | I set the project goal and chose/steered directions; detailed methods and implementation emerged through iterative collaboration with LLMs. |
| **D — User-directed, AI-led experiment** | I supplied the research objective/context and decided whether to continue or reject a direction; an automated/LLM research loop proposed and executed much of the specific experiment. |
| **E — AI-led subexperiment under project constraints** | The specific diagnostic/optimization was proposed and executed by the research agent within goals and evaluation rules I had already established. I reviewed/collected the result and decided how it affected the project. |

These levels describe **development process**, not ownership of the underlying project idea. The original BeatSolver problem, its motivation from osu! mapping, the goal of proposing/verifying difficult timing, and the decision to treat map data as evaluation rather than prediction input came from me.

---

# Phase 1 — Prototype and first audio solver

## v0.1 — Synthetic two-layer beat-candidate prototype

**Artifact:** `history/beattimerv0.1.ipynb`

### What changed / what this introduced

The first preserved prototype tested a deliberately simple two-layer idea on synthetic data:

1. estimate a local baseline, compute relative prominence, and detect peaks;
2. retain peaks that have approximately periodic support.

The test used 10,000 synthetic samples with 200 jittered true spikes. It compared a convolution baseline against a leave-window baseline and then applied a periodic-support filter.

### Results

Best high-recall Layer-1 setting in the preserved run:

- 199 / 200 true spikes recovered;
- 1,060 false positives;
- precision 0.158;
- recall 0.995;
- F1 0.273.

The first Layer-2 periodic filter barely helped:

- 199 / 200 true spikes retained;
- false positives fell only from 1,060 to 1,058;
- F1 remained 0.273.

The notebook explicitly identified the next problem: the periodic filter was still given the true interval (`BEAT_INTERVAL=50`) and needed to infer tempo rather than receive it as ground truth.

### What this taught us

High recall was easy to obtain by over-generating candidates. The hard part was extracting rhythmic structure without destroying recall. This recall-versus-candidate-count tradeoff became a recurring theme throughout the project.

### My contribution

**Level A — User-led.**

The project problem and two-stage intuition were mine. LLM assistance was used on technical implementation details, but this was still the early co-development phase rather than autonomous research.

---

## v0.2 — First real-audio timing model

**Archived artifact:** `mugenq-university-programming-playground/beatsolver/history/beattimer_v0.2.ipynb`

The dedicated BeatSolver repo originally omitted this large notebook; the historical artifact still exists in the older university repository.

### What changed

v0.2 moved from synthetic spikes to real audio, using `4rd_sanctuary.mp3`.

It introduced:

- spectral/audio onset analysis with librosa;
- BPM estimation by autocorrelation;
- phase search;
- a predicted beat grid;
- matching detected peaks to that grid;
- piecewise timing-grid segmentation and residual inspection.

This is the first preserved version that looks recognizably like the later BeatSolver architecture.

### Results

Preserved run on `4rd_sanctuary`:

- duration: 79.25 s;
- 175 detected rising onsets;
- estimated BPM: 199.944, rounded/used as 200 BPM;
- best phase: approximately 0.05895 s;
- 264 predicted beats.

For the main matched sequence, the preserved residual summary reported:

- median error: 1.958 ms;
- 95th percentile: 5.991 ms;
- maximum: 23.439 ms.

Synthetic segmentation checks also correctly separated a controlled 200 BPM -> 198 BPM change.

### Limitation

The result measured fit to selected audio observations, not yet independent osu! map accuracy. A low residual against one's own selected onset/grid observations can look excellent while still being wrong about the actual map structure.

### My contribution

**Level A/B boundary — user-led with substantial implementation assistance.**

I drove the move from synthetic beat candidates to real audio, tempo, phase, and piecewise timing. LLM assistance was increasingly useful for the technical signal-processing/notebook implementation.

---

## v0.3 — Plug-and-play audio workflow

**Artifact:** `history/beattimer_v0.3.ipynb`

### What changed

v0.3 consolidated the experiment into a one-file workflow:

1. load audio;
2. compute spectral flux;
3. estimate BPM and phase;
4. match onset evidence to the beat grid;
5. find a minimum number of timing sections;
6. render a scrollable segment visualizer.

It added a more structured rising-onset detector and made the solver easier to run on a new song.

### Results

No reliable standalone benchmark dump for the preserved v0.3 notebook was recovered. It should therefore be treated as an architecture/workflow milestone rather than assigned a later benchmark result retroactively.

### My contribution

**Level B — user-led, substantial LLM implementation.**

The solver decomposition and intended use came from me; implementation and notebook engineering were increasingly collaborative/LLM-assisted.

---

## v0.3 Local Tempo — overlapping local-window tracker

**Artifact:** preserved in the project Library as `beattimer_v0.3_local.ipynb`

### What changed

This branch added two explicit analysis paths:

- a global fixed-BPM control;
- overlapping local-tempo windows for heavily redlined or drifting songs.

It also added configurable decimal BPM rounding.

The key conceptual change was to stop assuming one global tempo model was enough for songs with short tempo bends or changing local structure.

### Results

The preserved artifact is primarily an implementation snapshot; no independent benchmark dump is attached to this exact revision.

### My contribution

**Level B.**

I chose to pursue local timing sections as the response to complex/redlined songs. LLM assistance handled a substantial portion of the mechanics.

---

## v0.3.1 Local Tempo — repair of local tracking

**Artifact:** `history/beattimer_v0.3.1_local_fixed.ipynb`

### What changed

The notebook states that this revision fixed:

- octave-doubled local BPM estimates;
- overlapping timing sections;
- oversized/blank Plotly output in VS Code.

It also added more direct evaluation against `.osu` data.

### Results on `sakuzyo - that day`

Audio analysis:

- raw global BPM: 84.0359;
- used BPM: 84.0;
- 91 onset candidates;
- 58 global-grid matched observations;
- global-grid median error: 4.870 ms;
- 69 valid local windows;
- 32 timing sections.

Early redline evaluation:

- 28 ground-truth redlines;
- 31 detected redlines;
- 8 matched;
- precision 0.258;
- recall 0.286;
- F1 0.271.

Object evaluation before later cleanup:

- 128 / 132 objects scored;
- raw median error: 38.534 ms;
- best diagnostic global shift: -40.5 ms;
- shifted median: 7.130 ms;
- shifted p95: 63.921 ms.

### What this taught us

Local BPM fitting could produce plausible-looking local sections while still over-segmenting badly and failing to identify mapper redlines. This is one of the first clear demonstrations that **local grid fit and structural boundary detection are separate problems**.

### My contribution

**Level B.**

I drove the local-tempo direction and evaluation goal; LLM assistance was substantial in implementation, visualization, and debugging.

---

## v0.4 — prediction/evaluation separation

**Artifacts:** `history/beattimer_v0.4.ipynb`, later split development/evaluator notebooks, `benchmark_dump/benchmark_v0.4.json`

### What changed

v0.4 made an important methodological change:

- the predictor reads only audio;
- `.osu` objects/redlines are external evaluation data;
- diagnostic global phase correction is reported separately and never fed back into the prediction;
- scratch cells were consolidated into named functions;
- object-grid error and redline agreement became explicit metrics.

This separation was deliberate: the map should not leak into a system that is supposed to provide an independent timing proposal.

### Results

The early monolithic v0.4 notebook still showed the older local tracker:

- aligned median object error: 7.130 ms;
- 57.8% of scored objects within 10 ms;
- redline F1: 0.271.

The later saved **v0.4 benchmark snapshot** used a simplified one-section/global-grid configuration on the same song and reported, after a -40.5 ms diagnostic alignment:

- 132 objects;
- median error: 0.429 ms;
- 87.1% within 10 ms;
- 90.9% within 20 ms;
- boundary F1: 0.000.

This apparent improvement must be read carefully: the grid aligned very well after diagnostic phase correction while the model detected **none** of the 28 structural redlines. It is a perfect example of why the two tasks must be reported separately.

### My contribution

**Level B.**

The prediction/evaluation separation, leakage concern, and choice to report failures separately were user-led design decisions. LLMs contributed heavily to refactoring and evaluation plumbing.

---

## v0.5 — corrected matching, phase handling, and split project structure

**Artifacts:** `beattimer_dev_v0.5.ipynb`, `beattimer_evaluator_v0.5.ipynb`, core/evaluation modules

### What changed

The cleaned v0.5 line added/fixed:

- one-to-one onset/grid matching;
- fallback sections preserving the original global phase;
- cross-window phase comparison using actual grids at a common time;
- phase diagnostics before segmentation;
- continuation scoring with BPM and phase held fixed;
- onset-derived phase candidates instead of a 2,000-step brute-force phase scan;
- separate evaluation of the initial timing anchor and later section boundaries;
- corrected summaries, paths, benchmark output, and duplicate keys.

The development notebook deliberately excludes map data to reduce leakage opportunities.

### Results on `yuyoyuppe_leia`

Saved benchmark, after -36.5 ms diagnostic alignment:

- 346 objects;
- global BPM ~189.95, used 189.9;
- 9 timing sections;
- median error: 3.271 ms;
- 54.6% within 5 ms;
- 79.2% within 10 ms;
- 89.3% within 20 ms;
- boundary F1: 0.057.

Later project notes flag this Leia map as unfinished, so this is an exploratory snapshot rather than a clean benchmark.

### My contribution

**Level B/C boundary.**

I remained responsible for the architecture, what counted as leakage, and what the system should optimize. The correction/refactoring work had substantial LLM implementation involvement.

---

# Phase 2 — trajectory-aware engine and denser diagnostics

## v0.6 family — trajectory-aware prediction engine

**Artifacts:** successive `beattimer_core.py` snapshots preserved in the project Library.

### What changed

The v0.6 family moved beyond isolated onset peaks toward a trajectory-aware engine. Successive internal snapshots added:

- richer trajectory features around onset rises;
- dense rhythmic-evidence diagnostics;
- sliding windows for local grid evidence;
- dense offset estimation;
- temporal tracking of phase/residual behavior.

The branch then used internal code versions:

- **0.11:** `DENSE_OFFSET_METHOD="viterbi_unwrapped"`;
- **0.12:** `DENSE_OFFSET_METHOD="viterbi_unwrapped_segmented"`.

0.12 added segmented/re-anchored dense-offset trajectories and accepted/rejected local segments.

These `0.11` / `0.12` identifiers are internal engine versions inside the broader v0.6-era development line. They should not be confused with a clean public release sequence after v0.6.

### Results

No isolated `benchmark_v0.6.json` survived in the dedicated repository, so no later result is assigned to v0.6 by assumption.

The practical outcome was the richer candidate/trajectory representation used by the subsequent detector studies and timing solvers.

### My contribution

**Level C — collaborative research.**

I set the research direction and kept the solver focused on interpretable timing evidence. The detailed trajectory/dense-residual implementation increasingly came from LLM-assisted experimentation.

---

## v0.7 benchmark snapshot — Reign of Fear

**Artifact:** `benchmark_dump/benchmark_v0.7.json`

### Results

On `reign_of_fear`, after a -20 ms diagnostic global phase alignment:

- 1,587 objects;
- 41 predicted timing sections;
- median object error: 1.657 ms;
- 77.8% within 5 ms;
- 94.3% within 10 ms;
- 98.5% within 20 ms;
- boundary precision: 0.050;
- boundary recall: 0.118;
- boundary F1: 0.070.

### Interpretation

Grid alignment had become strong on this track, while structural section/redline recovery was still weak. This result is one of the clearest numerical demonstrations of the project's central decomposition.

### My contribution

**Level C.**

I chose the problem framing, evaluation split, and direction; implementation and experimentation were substantially LLM-assisted.

---

## v0.8 benchmark snapshot — duplicate result

**Artifact:** `benchmark_dump/benchmark_v0.8.json`

The saved v0.8 JSON is byte-identical to v0.7 and contains exactly the same Reign of Fear metrics.

### Result

No distinct measured improvement is preserved.

### Interpretation

Treat v0.8 as a duplicate/copy of the v0.7 benchmark unless an unrecovered source artifact proves otherwise. It should not be advertised as an independent result.

### My contribution

No separate contribution claim is meaningful because no distinct surviving experiment is attached to this snapshot.

---

# Phase 3 — candidate-generation and solver research checkpoints

These studies no longer map cleanly to a single `v0.x` code version.

## Multi-song detector study

### What changed

The detector was tested across 4rd Sanctuary, Reign of Fear, and `that day`, rather than being trusted from one song.

An aggressive pruning configuration that looked good on 4rd Sanctuary failed to generalize:

- 4rd Sanctuary: 968 candidates, 432 / 434 covered;
- Reign: 5,998, 1,542 / 1,587;
- `that day`: 932, 126 / 132.

Two additions restored full trusted-label coverage:

- midpoint alternatives inside valley-to-peak rises;
- quiet-relative peak rescue.

Coverage became:

- 434 / 434;
- 1,587 / 1,587;
- 132 / 132.

An ExtraTrees acoustic scorer with contiguous time folds reduced total candidates from 22,343 to 18,746 (16.1%) while preserving all trusted labels.

### My contribution

**Level C/D boundary.**

I defined the overall candidate-generation problem and recall/false-proposal tradeoff. Much of the multi-song experimental machinery and model comparison was performed by the research agent under that direction.

---

## Source-held-out synthetic pruning

### What changed

A controlled source-held-out benchmark was created from:

- 12 unique hitsound sources;
- 36 datasets;
- 1,016 true events.

The exact held-out waveform was excluded from its fold.

### Results

High-recall generator:

- 5,061 candidates;
- 1,016 / 1,016 events.

Best legal held-out rule:

- 2,438 candidates;
- 1,016 / 1,016;
- 51.83% candidate reduction;
- about 2.40 candidates per true event.

Template-oracle diagnostic:

- 2,435 candidates, only three fewer than the legal held-out rule.

### Transfer failure

Applied unchanged to `floating architecture`:

- SuperFlux baseline: 2,541 candidates, 499 / 504 objects;
- synthetic pruning rule: 1,013 candidates, only 440 / 504.

This is why the synthetic 99%/near-100%-recall story must be paired with its transfer limitation.

### My contribution

**Level D.**

I chose high recall with candidate compression as a key objective and reviewed the result. The detailed held-out experiment/scoring implementation was largely AI-led.

---

## HPSS rescue + `floating architecture`

### What changed

Percussive evidence stopped being treated as an intersection gate and became an independent rescue/confidence branch.

### Results

Synthetic held-out:

- general detector: 5,061 candidates, 1,016 / 1,016;
- simple HPSS-percussive: 1,730, 1,016 / 1,016;
- threshold-safety-backoff version: 1,848, 1,016 / 1,016.

Real `floating architecture`:

- general: 2,541 candidates, 499 / 504 direct matches;
- frozen HPSS rescue union: 2,543, 501 / 504;
- exploratory lower threshold: 2,546, 503 / 504;
- intersection-only: 1,558, only 456 / 504.

All candidate populations recovered a raw ~95 BPM mode and resolved it to 190 BPM.

At a local quarter-beat grid:

- general candidates: 499 / 504 objects near grid;
- percussive candidates: 498 / 504 near grid despite only 439 direct matches;
- intersection: 499 / 504 near grid.

### What this taught us

Directly detecting every map object is unnecessary. Candidate evidence can still infer a correct rhythmic grid. This shifted the project away from "classify each beat perfectly" toward structured timing inference.

### My contribution

**Level D.**

The overall hypothesis and interpretation fit the user-defined project direction; the specific HPSS rescue experiment was mainly research-agent execution.

---

## Reign of Fear proof solver

### What changed

A song-specific end-to-end solver tested whether the acoustic evidence was sufficient to reconstruct a highly accurate timing solution when the state topology was strongly constrained.

### Results

- 18 red timing points;
- 1,587 / 1,587 objects within 5 ms;
- 1,548 / 1,587 within 3 ms;
- median: 1.681 ms;
- p95: 2.887 ms;
- maximum: 3.663 ms.

### Critical limitation

The tempo-state sequence, rough transition windows, anchor calibrations, and beat-count topology were developed while studying Reign of Fear. The map was repeatedly used for evaluation.

This proves that the acoustic evidence can support an excellent timing solution. It does **not** prove held-out automatic generalization.

### My contribution

**Level D.**

I chose the project objective, supplied/selected the song/problem context, and used the result to decide the next direction. The specialized solver implementation and detailed experiment were predominantly AI-led.

---

## `sakuzyo - that day` generalization attempt

### What changed

The next experiments removed much of Reign's song-specific topology and tried to infer timing changes from audio.

### Persisted results

Continuous pulse beam:

- 187 timing points;
- latest handoff records 100 / 132 objects within 5 ms;
- median 2.25 ms.

A prior package contains a reproducible conservative pulse path at 98 / 132 within 5 ms. Because the later 100 / 132 handoff number does not currently have the same complete artifact trail, both numbers are preserved rather than silently replacing one with the other.

Semi-Markov:

- audio-selected path: 8 timing points, 96 / 132 within 5 ms, median 3.41 ms;
- development-best path: 11 timing points, 96 / 132 within 5 ms, median 3.00 ms.

Candidate graph quality:

- a reset candidate within 3 ms of 24 / 28 official red timing points;
- within 5 ms of 25 / 28;
- candidate offsets + official BPM sequence: 126 / 132 objects within 5 ms.

### What this taught us

Candidate generation and graph topology were already fairly strong. The dominant failure became **selecting the correct reset/BPM sequence from audio evidence**.

### My contribution

**Level D.**

I set the goal of moving beyond a song-specific proof and directed continued work on general timing/reset inference. The concrete solver experiments were mostly AI-led.

---

# Phase 4 — automated implementation/research loop

From August 2026 onward, the development process changed substantially.

I had explicitly asked for repeated idea/test/optimization cycles. The project objective and constraints remained mine, and I reviewed/steered the results, but many specific hypotheses, code patches, and controlled experiments were generated and executed autonomously by the research loop.

This phase should therefore **not** be described as though I personally wrote each experiment.

## Unnumbered implementation cycles, August 3–10

Recovered scheduled-task records include the following sequence:

### August 3

- null-calibrated timeline scorer;
- adaptive channel fusion;
- branch-local sibling-edge fusion;
- anchor-aware residual diagnostics;
- reset-score calibration;
- semi-Markov integration work;
- aligned onset-shape coherence;
- duration-bias correction.

**Result:** the work increasingly isolated reset scoring and path selection as the bottleneck; some apparently useful evidence channels saturated or failed to improve the final path.

**Contribution:** **Level D/E.** I set the ongoing research objective and reviewed direction; detailed subexperiments were largely agent-proposed/executed.

### August 4

- counterfactual reset gain;
- three-gate reset acceptance;
- edge-length normalization;
- percussion innovation channel;
- rolling/local/event-conditioned percussion novelty;
- complete timeline-list ranking;
- K-best reranking;
- counterfactual reranking;
- percussion reliability gating.

**Result:** several local signals improved boundary discrimination, but the long ~84 BPM path remained overly attractive under the global objective. The research shifted from inventing more candidates toward fixing objective/ranking behavior.

**Contribution:** **Level E** for most individual subexperiments.

### August 5–6

- attack-shape gating;
- interval-support tests;
- reliability-gated percussion in the complete timeline objective;
- confidence-weighted morphology templates.

**Result:** some false resets were cut substantially, but better local classification still did not automatically produce the correct global path. This reinforced the idea that path selection, not merely feature quality, was the limiting subsystem.

**Contribution:** **Level E**, with user review/continuation decisions.

### August 8–10

- section-local percussion confidence;
- corrected reset geometry;
- competitive percussion scoring;
- reset log-likelihood-ratio scoring;
- tempered reset scoring;
- negative pairwise-reset-penalty test;
- signed residual model;
- blind/shared calibration;
- bidirectional reset evidence;
- repeated interval support;
- dense reset channel validation.

**Result:** signed residual discontinuity emerged as a more principled reset observation than simply adding more novelty features. This line became the basis of the later numbered R&D Cycles.

**Contribution:** **Level E** for detailed method development; I remained the project owner/director and decided to continue this line.

---

# Phase 5 — numbered R&D Cycles 8–16

Cycles 1–7 are not preserved as clean numbered bundles in the accessible artifacts. Cycle 8 is the first numbered bundle that survives with a reproducible artifact. The August experiments above are therefore recorded by date/topic rather than retroactively inventing cycle numbers.

## Cycle 8 — signed-residual transition integration

### Change

Integrated reset evidence at the actual semi-Markov state transition:

`+ reset_weight * reset_bonus[a, hp, h2]`

The bonus compared continuation of the old interval against a proposed new interval using:

- signed grid-offset jump;
- post-reset spread reduction;
- post-reset slope reduction;
- percussion evidence as positive confidence.

The diagonal `hp == h2` remained zero so merely visiting a node could not earn reset reward.

### Result

Controlled tests:

- six real `that day` reset geometries;
- 80 controlled dense-path trials per boundary;
- independent general/percussion noise;
- DP sanity case where residual evidence overturns a persistent-interval winner.

No end-to-end audio benchmark was claimed because the real dense arrays/audio were absent from the persisted research state.

### My contribution

**Level D/E.**

I had directed the project toward solving reset selection. The specific signed-residual integration and controlled test implementation were AI-led.

---

## Cycle 9 — factorized residual transition table

### Change

The mathematically correct Cycle-8 table was computationally wasteful. Cycle 9 factorized interval features from O(H²) repeated robust fits into O(H) feature computation plus broadcast construction.

### Results

For six `that day` reset nodes, H=21 equivalence domain:

- slow reference: 2.1826 s;
- factorized: 0.05110 s;
- speedup: 42.72x;
- max score error: 1.91e-6;
- mean score error: 1.05e-7.

Full H=126 interval domain:

- six-node runtime: 0.3106 s;
- table shape: 6 × 126 × 126;
- memory: 0.363 MiB;
- diagonal exactly zero.

### My contribution

**Level E.**

This was an optimization subexperiment generated/executed by the research loop inside the solver direction I had set.

---

## Cycle 10 — null calibration exposes state-resolution bug

### Change

Attempted to calibrate raw reset evidence against a stationary-null distribution.

### Result / discovery

At the production integer-millisecond interval representation, the important 28.386 s reset changes:

- 357.2857 ms -> 356.8571 ms,

but both quantize to 357 ms. The production DP therefore could not represent that reset as a state change.

Null calibration at integer resolution:

- five boundaries: AUC 1.0, TPR 100%, empirical FPR 1.67%;
- 28.386 s: AUC 0.5, TPR 0%, score identically zero.

State-resolution audit:

- 1.0 ms grid: 5 / 6 real reset changes survive;
- 0.5 ms: 6 / 6;
- 0.25 ms: 6 / 6;
- 0.125 ms: 6 / 6.

### My contribution

**Level E.**

The research agent found the representation bug while pursuing the user-directed reset-calibration objective. I reviewed/retained the finding as a required production fix.

---

## Cycle 11 — half-millisecond sparse-predecessor prototype

### Change

Implemented the next production idea implied by Cycle 10:

- 0.5 ms interval states;
- sparse/local predecessor transitions to avoid the naive ~4x quadratic expansion.

### Result

The scheduled research record reports that half-millisecond DP preserved the previously erased reset while controlling state-transition growth.

The standalone Cycle-11 bundle is not present in the current Library, so I do not attach numerical performance claims that cannot be independently recovered.

### My contribution

**Level E.**

AI-led implementation under the project constraint established by the preceding cycle.

---

## Cycle 12 — production half-ms patch + sparse-band audit

### Change

Patched the actual persisted semi-Markov solver to 0.5 ms states across:

- edge quantization;
- DP dimensions;
- initialization;
- predecessor search;
- physical-unit change penalty.

### Results

Quantization:

- mean error at 1 ms: 0.2196 ms;
- mean at 0.5 ms: 0.1101 ms;
- max at 1 ms: 0.5000 ms;
- max at 0.5 ms: 0.2143 ms.

Adjacent persisted path interval changes:

`[3.0, 3.0, 0.5, 2.0, 10.0, 8.0, 1.5] ms`

A strict ±8 ms predecessor band covers only 6 / 7, because the real 359 -> 349 ms transition is a 10 ms change.

### My contribution

**Level E.**

Detailed production patch and audit were AI-led; I supplied the continuing research goal and accepted the resulting two-tier predecessor direction.

---

## Cycle 13 — residual-gated rescue shell

### Change

Introduced a two-tier predecessor policy:

- <=8 ms interval difference: always reachable;
- (8,16] ms: reachable only when calibrated signed residual evidence is positive.

### Results

- recovered the persisted 43.762 s 10 ms transition that local-only search could not select;
- 300 / 300 calibrated-null trials bit-for-bit identical to local-only search;
- 0 false rescue admissions;
- sparse scored transitions: 8,017 vs 63,001 exhaustive (12.73%);
- pure-Python speedup: 5.36x.

Recalibrating the formerly erased 28.386 s reset at 0.5 ms gave:

- AUC 1.0;
- TPR 100%;
- calibrated mean 0.3453;
- calibrated P10 0.2512.

### My contribution

**Level E.**

AI-led controlled implementation/analysis under the user-defined reset-selection objective.

---

## Cycle 14 — residual bonus on ordinary transitions

### Change

Cycle 13 applied residual evidence mainly to the rescue shell. Cycle 14 extended it to every nonzero local transition so the small 357.5 -> 357.0 ms reset could also benefit.

A reset weight of **0.055** was frozen without map-object accuracy. The rule was that P10 calibrated evidence had to pay the existing 0.014/ms physical change cost at all six persisted reset geometries.

### Results

- 6 / 6 persisted reset transitions recover the true predecessor;
- minimum required weight from the weakest boundary: 0.047523;
- frozen margin: 0.007477;
- 28.386 s P10 net margin: 0.006818;
- 64.101 s weakest P10 net margin: 0.003304;
- 500 / 500 calibrated-null trials exactly equal to the no-residual local solver.

No end-to-end improvement over 96 / 132 was claimed because the real dense evidence was unavailable.

### My contribution

**Level E.**

The particular calibration rule and experiment were AI-led within goals I had established; I reviewed and preserved the result.

---

## Cycle 15 — frozen weight sensitivity

### Change

Tested whether `RESET_WEIGHT=0.055` was a knife-edge choice.

100,000 Monte Carlo samples per boundary were generated for six persisted reset geometries; weights 0..0.10 were swept without using map-object accuracy.

### Results

At 0.055:

- worst-boundary success: 0.96416;
- mean boundary success: 0.99380;
- weakest boundary: 64.101 s.

Smallest tested weight meeting the criterion on every boundary:

- >=90%: 0.0475;
- >=95%: 0.055;
- >=97.5%: 0.0600;
- >=99%: 0.0675.

Persisted calibrated-null FPR: 0.0125.

### My contribution

**Level E.**

AI-led robustness diagnostic, user-reviewed.

---

## Cycle 16 — objective-consistency audit

### Change / discovery

Cycle 16 audited the actual production objective and found a structural bug:

1. the solver generated paths under a sweep of 70 DP parameter combinations;
2. DP `change_penalty` values were 0, .002, .005, .010, .018;
3. final `audio_selected` selection used a different fixed score:

`raw - .85*sections - .014*interval_change - .04*weak`

The final selector had **no calibrated residual term**.

A residual-favored path could therefore be generated correctly and then rejected by a selector unable to see the evidence that favored it.

### Quantitative scale

At frozen reset weight 0.055 across the six persisted reset geometries:

- positive residual contribution sum: 1.499400;
- common interval-change cost: 0.350000;
- net residual contribution: 1.149400.

The existing 8-section audio-selected path has 28.0 ms total adjacent interval change, worth 0.392 under the fixed .014/ms penalty.

The missing residual term is therefore large relative to the selector's regularizer.

### Proposed unified objective

Use one audio-only objective for recurrence and final selection:

- section penalty: 0.85;
- interval-change penalty: 0.014/ms;
- weak-node penalty: 0.04;
- calibrated residual weight: 0.055.

### Result status

This is a **correctness result**, not a new end-to-end accuracy number. The real `that day` dense arrays/audio were still absent, so the unified objective could not yet be honestly rerun on the real track.

### My contribution

**Level D/E.**

The audit itself was AI-led. My contribution was owning the overall research question, authorizing repeated solver investigation, reviewing the diagnosis, and deciding that correctness should be fixed before claiming another accuracy improvement.

---

# Parallel ML branch, September 2026

A separate exploratory branch tested whether a small neural network could help prune/pick beat candidates.

### What changed

The branch added:

- TensorFlow/Keras experiments;
- synthetic-data generation;
- spectrogram/transient features;
- caching;
- explicit train/validation/test splits;
- time-preserving convolutional onset prediction.

A small single-example network could overfit and produce peaks roughly aligned with its target, proving the training/inference plumbing worked but not generalization.

Synthetic experiments later reached roughly **99% recall with about 2.5 candidate beats per true beat** in the version discussed in interviews/application preparation.

### What this taught me

The ML model itself was only one component. It introduced:

- training-data quality and provenance requirements;
- authored/verified data collection;
- compute and retraining costs;
- deployment/website requirements if collecting community timing data;
- additional transparency concerns because AI-assisted tooling is viewed skeptically in parts of the osu! mapping community.

This led me to become more conservative about adding ML simply because it was available.

### My contribution

**Level C/D.**

I decided why ML was worth testing, what role it should play, and how its system-level costs should be judged. Much of the TensorFlow/data-pipeline implementation was LLM-assisted and should not be represented as hand-written-by-me proof of deep-learning engineering.

---

# Current / post-Cycle-16 direction

Two directions remain conceptually important.

## 1. Finish the audio solver honestly

The next valid experiment is:

1. regenerate/materialize real `that day` dense residual evidence;
2. run the unified Cycle-16 objective **without retuning**;
3. compare against the persisted 96 / 132 audio-selected baseline;
4. run Reign of Fear unchanged as a regression test.

Until that is done, Cycle 16 should remain a correctness finding rather than an accuracy claim.

## 2. QuantPrep / MIDI formulation

A later design discussion considered narrowing the problem:

- use MIDI or already-separated musical layers rather than solving source separation from MP3;
- infer a shared rhythmic structure across layers;
- search candidate subdivision branches, roughly 1/1 through 1/16;
- use synthetic and/or quantized-vs-unquantized examples;
- produce a preparation layer for downstream quantization.

This is a **conceptual successor direction**, not an implemented BeatSolver version yet.

### My contribution

**Level A for the problem reformulation/design discussion.**

The decision to question the MP3/source-separation scope and reformulate the tool around structured MIDI/layer input came from my project-level reasoning. Implementation has not yet happened.

---

# Contribution summary

The cleanest description of my role across the project is:

- **Original problem and motivation:** mine.
- **Core product goal:** mine — make complex timing easier to propose and verify for mappers/reviewers.
- **Early algorithmic direction:** user-led, with LLM implementation assistance.
- **Evaluation philosophy:** user-led — prediction/evaluation separation, explicit leakage avoidance, report recall and over-proposal separately, preserve negative results.
- **Early notebooks and solver architecture:** collaborative; I should not imply every function was hand-written unaided.
- **Later scheduled R&D:** substantially AI-led at the experiment/code level, under research objectives and constraints I set; I reviewed results and directed whether/how to continue.
- **Interpretation and product decisions:** mine, including the conclusion that extra ML may increase total system cost enough to be counterproductive.
- **Current responsibility:** I am responsible for understanding what is claimed, distinguishing controlled tests from end-to-end evidence, and not presenting AI-generated experiments as personal hand-coded work.

For interviews or applications, the defensible short version is:

> I originated BeatSolver's problem, architecture goals, and evaluation philosophy. Early versions were co-developed with LLM implementation assistance. As the project matured I also used scheduled AI research cycles to explore and implement experiments under objectives I set, then reviewed the evidence and chose the direction. I therefore claim the project/design/research ownership, but I do not claim that every line of later experimental code was manually written by me.

---

# Evidence and provenance notes

This history was reconstructed from:

- preserved v0.1–v0.5 notebooks;
- the archived v0.2 notebook in the older university repository;
- v0.6-era core snapshots;
- benchmark v0.4/v0.5/v0.7/v0.8 JSON;
- multi-song, source-held-out, Reign, and `that day` research summaries;
- persisted Cycle 8–16 research artifacts where available;
- scheduled R&D records for unbundled experiments.

Where an artifact is missing or a later handoff number has weaker provenance, that uncertainty is stated explicitly rather than silently promoted into a benchmark.
