# Cycle 10: null calibration exposed an interval-state resolution bug

Cycle 10 tried to calibrate signed residual reset evidence against stationary no-reset trajectories.

It exposed a more important production problem: half-beat intervals were represented as integer milliseconds. The difficult `that day` reset at 28.386 s changes from 357.2857 ms to 356.8571 ms; both become state 357, making that reset impossible to express as a state change.

## Null-calibration result

At integer-state resolution:

- 18.382 s: AUC 1.0, TPR 100%, empirical FPR 1.67%;
- 28.386 s: AUC 0.5, TPR 0%, score identically zero;
- 38.378 s: AUC 1.0, TPR 100%, FPR 1.67%;
- 43.762 s: AUC 1.0, TPR 100%, FPR 1.67%;
- 44.460 s: AUC 1.0, TPR 100%, FPR 1.67%;
- 64.101 s: AUC 1.0, TPR 100%, FPR 1.67%.

The 1.67% empirical null rate is 1/60; a larger initial trial had exceeded the execution limit.

## State-resolution audit

Across six persisted reset geometries:

- 1.0 ms grid: 5/6 state changes survive;
- 0.5 ms: 6/6;
- 0.25 ms: 6/6;
- 0.125 ms: 6/6.

The smallest direct fix was therefore a 0.5 ms interval grid.

## Attribution

The representation bug was discovered by the automated research cycle while pursuing the user's reset-calibration objective. The diagnostic/implementation work was primarily AI-led.
