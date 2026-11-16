# Audio-only timing-solver attempt on `sakuzyo - that day`

## What the runner does

`pulse_beam_solver.py`:

1. separates a percussive magnitude estimate with classical median-filter HPSS;
2. creates low-band kick and broadband snare pulse evidence;
3. estimates the dominant BPM from onset-envelope autocorrelation;
4. tracks a half-beat pulse sequence with a dynamic beam;
5. estimates an attack-midpoint backtrack from the audio itself;
6. emits one red timing point per half beat.

The map is not read while generating the timing points. It is read only by the evaluation stage contained in the package.

## Result

This song contains 28 reference red timing points, including short tempo bends and phase resets between roughly 77 and 101 BPM.

An earlier conservative pulse path reached 98 / 132 mapped objects within 5 ms and 110 / 132 within 10 ms, with median error around 3 ms.

The later semi-Markov work improved the representation and reduced the number of generated timing points. The latest persisted audio-selected path reaches 96 / 132 within 5 ms with 8 timing points and median error 3.41 ms; the continuous pulse beam reaches 100 / 132 but emits 187 timing points.

The remaining misses are concentrated around abrupt phase resets and short one-half-beat tempo gimmicks.

## Important limitation

This is still not a fully general timing-section solver.

The dominant BPM and long regular sections are handled well. The missing capability is reliable change-point / phase-reset selection. Dense onset evidence alone does not reliably choose those resets, even though suitable candidate anchors exist near most reference timing points.

The Reign of Fear proof succeeded because its tempo-state topology and beat-count chain were supplied during development. This attempt shows what remains when those assumptions are removed.
