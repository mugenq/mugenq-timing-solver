# BeatTimer multi-song detector study

## Scope

The study covered:

- 4rd Sanctuary, evaluated against a 434-object map;
- Reign of Fear, 1,587 objects;
- sakuzyo - that day, 132 objects;
- Leia - Remind, 346 currently mapped objects.

Leia was excluded from false-positive training and precision claims because its timings and object set were unfinished. It was used only as a coverage sanity check.

## Correction to an earlier aggressive pruning result

An earlier full-band-parent/top-two result was specific to 4rd Sanctuary and did not generalize.

Using strict one-candidate-to-one-object maximum matching, the same aggressive configuration gave approximately:

| Song | Candidates | Covered |
| --- | ---: | ---: |
| 4rd Sanctuary | 968 | 432/434 |
| Reign of Fear | 5,998 | 1,542/1,587 |
| that day | 932 | 126/132 |

It should not be promoted into the detector.

## Full-recall candidate generation

Two additions recovered every trusted label:

1. A midpoint alternative inside each valley-to-peak rise.
2. A quiet-relative peak rescue for low-absolute-energy but locally persistent peaks.

Coverage became:

- 4rd Sanctuary: 434/434;
- Reign of Fear: 1,587/1,587;
- that day: 132/132.

## Acoustic pruning

An ExtraTrees scorer was evaluated with eight contiguous time folds. Features were acoustic only and included detector support, band and scale persistence, rise shape, energy ratios, local candidate density, and multiscale full-band parent evidence.

Out-of-fold family classification:

| Song | AUROC | Average precision |
| --- | ---: | ---: |
| 4rd Sanctuary | 0.989 | 0.995 |
| Reign of Fear | 0.882 | 0.807 |
| that day | 0.836 | 0.662 |

Using one common retained fraction, 83.74%, and always retaining the quiet rescue families preserved every trusted label:

| Song | Candidates | Covered |
| --- | ---: | ---: |
| 4rd Sanctuary | 1,636 | 434/434 |
| Reign of Fear | 14,584 | 1,587/1,587 |
| that day | 2,526 | 132/132 |

Total candidates fell from 22,343 to 18,746, a 16.1% reduction.

This was useful, but still far from the desired compression target.
