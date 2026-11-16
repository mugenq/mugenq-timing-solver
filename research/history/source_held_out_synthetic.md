# Source-held-out synthetic beat experiment

## Corpus

- 12 unique uploaded hitsound waveforms after exact deduplication.
- 3 generated conditions per source: regular/clean, irregular/noisy, and dense with percussive decoys.
- 36 datasets and 1,016 exact positive events.
- The true source waveform is excluded from the deployable template bank in its held-out fold.

## High-recall generator

Fixed SuperFlux prominence 1.03 produced 5,061 candidates and covered 1,016 / 1,016 events.

## Best source-held-out rule

The best non-oracle rule was `score_combined_bonus` with a local comparison radius of 80 ms and score margin 0.025:

- 2,438 candidates;
- 1,016 / 1,016 events;
- all 12 held-out source folds and all 36 datasets at full recall;
- 51.83% candidate reduction.

The score starts with broadband support, multiscale persistence, ring contrast, and prominence, then adds only positive bonuses for Mel-domain percussive evidence and similarity to the other 11 hitsound templates.

The template-oracle diagnostic, which leaks the exact held-out waveform, retained 2,435 candidates. That is only three fewer than the legal source-held-out score, so exact waveform memorization provided almost no advantage here.

## Important real-song transfer failure

Applied unchanged to the uploaded real map:

- SuperFlux baseline: 2,541 candidates, 499 / 504 objects;
- combined synthetic rule: 1,013 candidates, 440 / 504 objects.

Therefore the source-held-out synthetic result validates unseen percussive-source recognition, but not general mapped-beat selection in real music. The fixed 80 ms competition window also conflicts with real dense patterns around 79 ms.
