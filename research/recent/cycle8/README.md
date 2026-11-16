# Cycle 8: semi-Markov signed-residual transition integration

This cycle integrated signed residual reset evidence at the actual state transition in the persisted semi-Markov solver.

The DP chooses a proposed new half-beat state `h2` from previous state `hp` at candidate node `a`. The patch adds:

```
+ reset_weight * reset_bonus[a, hp, h2]
```

The bonus compares continuation of the old interval against the proposed new interval using:

- signed continuation-grid offset jump;
- reduction in post-reset residual spread;
- reduction in post-reset residual slope;
- independent percussion evidence as positive confidence.

The diagonal `hp == h2` is zero, so passing through a node without changing state does not receive a reset reward.

## Executed tests

- six persisted real `that day` reset geometries;
- 80 controlled dense-path trials per boundary;
- independent general/percussion noise;
- a DP sanity case where reset evidence overturns a persistent-interval winner.

## Limitation

This was an integration/reproduction test. The real `dense_general.csv`, `dense_percussion.csv`, or `bg.mp3` were not available in the persisted state, so no end-to-end real-song accuracy claim is made.

## Attribution

This was part of the later automated R&D phase: the project/reset-selection objective was user-directed; the specific implementation and controlled experiment were primarily AI-led.
