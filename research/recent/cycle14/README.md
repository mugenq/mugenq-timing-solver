# Cycle 14: calibrated residual evidence on ordinary transitions

Cycle 13 solved sharp-reset reachability, but still applied residual evidence only to the 8–16 ms rescue shell. That omitted an important remaining case: the 28.386 s reset is only 357.5 -> 357.0 ms, so it lives inside the ordinary <=8 ms predecessor band.

This cycle applies the calibrated signed residual bonus to every nonzero local interval transition while retaining the positive-evidence gate for rescue transitions.

A controlled reset weight is frozen at **0.055** without using map-object accuracy. The freeze rule is mechanical: the P10 calibrated reset evidence must be sufficient to pay the solver's existing 0.014-per-ms interval change cost at all six persisted reset geometries. The largest required value was **0.047523**, from the weak 64.101 s reset; 0.055 leaves **0.007477** margin.

Executed results:

- all **6/6** persisted reset transitions recover their true predecessor in the controlled tied-competition test;
- 28.386 s receives a local residual bonus, with P10 net margin **0.006818** after its physical change cost;
- 64.101 s, the weakest P10 case, still has positive net margin **0.003304**;
- **500/500** calibrated-null trials are exactly equivalent to the no-residual local solver.

## What this does not establish

No real `that day` dense offset arrays, `evidence.npz`, graph CSV, or `bg.mp3` were available in the accessible persisted state during this run. This is the completed controlled production recurrence, not an end-to-end audio benchmark and not a claimed improvement over 96/132.

## Next blocker

Materialize or regenerate the real dense general/percussion offset windows for `that day`, compute calibrated reset bonuses at actual candidate nodes, and run this frozen 0.055 recurrence without further weight tuning. Reign of Fear should then be run unchanged as regression.
