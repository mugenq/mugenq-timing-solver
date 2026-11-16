# Cycle 12: production half-ms patch + sparse-band audit

Cycle 12 patched the actual persisted semi-Markov solver to 0.5 ms interval states, including:

- edge quantization;
- DP dimensions;
- initialization;
- predecessor search;
- preservation of physical units for the change penalty.

## Results

Quantization:

- 1 ms mean error: 0.2196 ms;
- 0.5 ms mean error: 0.1101 ms;
- 1 ms maximum error: 0.5000 ms;
- 0.5 ms maximum error: 0.2143 ms.

Persisted adjacent half-ms state changes were:

`[3.0, 3.0, 0.5, 2.0, 10.0, 8.0, 1.5]` ms.

A strict ±8 ms predecessor band therefore covers only 6/7 transitions; the 43.762 s 359 -> 349 ms bend requires a 10 ms change.

The next proposed implementation became a dense ±8 ms band plus an acoustically gated 8–16 ms rescue shell.

## Attribution

Primarily AI-led production patch/audit under the user's continuing reset-selection research direction.
