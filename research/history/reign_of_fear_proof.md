# Reign of Fear audio-derived timing solver

## Result

The solver produced 18 red timing points, including the unmapped intro point.

Mapped-object evaluation on a 1/12-beat grid:

- objects: 1587
- within 1 ms: 179
- within 2 ms: 1067
- within 3 ms: 1548
- within 5 ms: 1587
- median error: 1.681 ms
- 95th percentile: 2.887 ms
- maximum error: 3.663 ms

Every one of the 1,587 mapped objects lands within 5 ms of the solver grid.

## Architecture

The timing stage consumes the previously extracted full-spectrum and percussive candidates. It does not read map objects or official timing points while generating its timing points.

1. Estimate the initial 182 BPM phase from calibrated slope anchors.
2. Resolve the 182 -> 184 -> 190 -> 196 -> 202 ramp using an audio-only candidate-grid beam search.
3. Use strong multiscale boundary events as noisy offset observations.
4. Fit one long beat-count graph through the 182, 180, 202, 178, 150, 133, and 120 BPM sections.
5. Detect the 132 BPM outro phase reset independently.
6. Propagate the 128 and 124 BPM changes by exact beat counts.
7. Round timing points to osu!'s millisecond timestamp resolution.

## Important limitation

This is a Reign of Fear proof solver, not a held-out general solver. The tempo state sequence, rough transition windows, anchor calibrations, and beat-count topology were developed while studying this track, and the map was repeatedly used for evaluation during development.

The final solver function does not consult objects or official timing points when run, but its configuration is song-specific. The 1,587 / 1,587 result therefore proves that the candidate evidence can support an excellent timing solution. It does not yet prove automatic transfer to unseen songs.
