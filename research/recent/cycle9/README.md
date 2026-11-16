# Cycle 9: factorized residual reset table

Cycle 8's transition table was mathematically usable but computationally wasteful because robust residual features were recomputed for every `(old_h, new_h)` pair.

Cycle 9 factorized the score: compute the interval-state features once in O(H), then construct the H×H transition matrix by broadcasting.

## Results

For six real `that day` reset-node times on an H=21 equivalence domain:

- slow reference: 2.1826 s;
- factorized: 0.05110 s;
- speedup: 42.72×;
- maximum absolute score error: 1.91e-6;
- mean absolute score error: 1.05e-7.

For the full H=126 production interval domain:

- factorized runtime for six nodes: 0.3106 s;
- table shape: 6 × 126 × 126;
- memory: 0.363 MiB;
- transition diagonal: exactly zero.

The prior residual/DP integration regressions still passed.

## Limitation

This is an optimization/equivalence result, not a new acoustic-accuracy result.

## Attribution

AI-led optimization subexperiment inside the user-directed BeatSolver research objective.
