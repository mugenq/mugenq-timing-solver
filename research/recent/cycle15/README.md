# Cycle 15: frozen residual-weight robustness

No detector/HPSS/candidate work was repeated.

This cycle tests whether frozen `RESET_WEIGHT=0.055` is robust rather than a single-P10 knife edge. For each of six persisted `that day` reset geometries, a local score-noise distribution was reconstructed from the persisted controlled calibrated mean and P10. 100,000 Monte Carlo samples per boundary were drawn and weights 0..0.10 were swept without using map-object accuracy.

At 0.055:

- worst-boundary probability of paying the existing 0.014/ms physical change cost: 0.9642
- mean across boundaries: 0.9938
- weakest boundary: 64.101 s

Smallest tested weights achieving the criterion on every boundary:

- >=90%: 0.0475
- >=95%: 0.055
- >=97.5%: 0.06
- >=99%: 0.0675

Persisted calibrated-null FPR remains mean 0.0125, max 0.0125.

This is controlled sensitivity analysis, not real-audio validation. Keep 0.055 frozen; the next legitimate test is real dense evidence followed by unchanged Reign of Fear regression.
