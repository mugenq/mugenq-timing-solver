# Cycle 13: residual-gated rescue shell

Cycle 12 created a conflict: 0.5 ms resolution was necessary, but a strict ±8 ms sparse band deleted the real 10 ms transition at 43.762 s.

Cycle 13 implemented a two-tier predecessor policy:

- <=8 ms: always reachable;
- (8,16] ms: reachable only when calibrated signed residual evidence is positive.

## Results

- the persisted 43.762 s 10 ms transition is not selected by local-only search;
- adding its calibrated positive residual evidence recovers the correct predecessor;
- 300/300 calibrated-null trials are bit-for-bit identical to local-only search;
- false rescue admissions: 0;
- sparse scored transitions per node: 8,017 vs 63,001 exhaustive (12.73%);
- pure-Python benchmark speedup: 5.36×.

## 0.5 ms recalibration of the 28.386 s reset

Re-running the earlier calibration at 0.5 ms gave:

- AUC: 1.0000;
- TPR above empirical 99th-percentile null: 100%;
- calibrated mean: 0.3453;
- calibrated P10: 0.2512.

All six persisted reset geometries now have non-degenerate half-ms transitions.

## Limitation

The positive evidence comes from controlled calibration rather than newly materialized real dense audio evidence. This validates reachability/gating mechanics, not end-to-end acoustic accuracy.

## Attribution

Primarily AI-led controlled experiment under the user-set research goal.
