# BeatSolver Timeline v0.4.9 — Worker ownership hotfix

## Confirmed incoming Windows baseline (v0.4.8)

- The unmodified regression suite passed 114/114 tests on Windows, and standalone full-song Hanatan / WAV GUI smoke passed.
- Extended native workflow crashed with exit `0xC0000409` and `QThread: Destroyed while thread is still running`.
- A controlled stale `finished` delivery showed the old completion deleting the new active worker. The spectral defaults, stale viewer guard, merge compatibility and ordinary GUI startup were separately verified.

## Corrections

1. **Graph worker ownership:** completion uses `QObject.sender()` to identify the actual completed thread and calls `deleteLater()` on that thread alone. It never clears the new active worker or resets its busy indicator when receiving an old callback. `@Slot()` declarations establish GUI-thread queued delivery.
2. **Queued Run requests:** treat `candidate_worker is not None` as busy, including after its native thread has terminated but before its Qt completion callback runs. Coalesce newer Run requests and dispatch after the old worker is retired. Late `produced` and `failed` signals from superseded workers cannot modify current Preview or produce stale error dialogs.
3. **Spectrogram completion:** apply the same identity-safe cleanup and do not install a replacement spectrogram thread until queued completion is processed. A pending redraw is resumed afterward.
4. **Decode completion:** dispose the actual emitting decode thread, rather than whichever decoder is currently assigned.
5. **Extended QA harness:** traverse upstream edges from the selected Candidate Output when choosing its Spectrogram node. Tests now wait for queued completion and pending reruns before asserting the final graph output, without substituting sleeps for the regression.
6. **Safe shutdown:** a completed worker awaiting Qt cleanup is retired without blocking the close event, queued reruns are cancelled, and the window ignores later deferred analysis starts.
7. **Regression coverage:** added Qt tests for old-completion/new-worker identity, pending-run coalescing, stale Preview output, and old spectrogram completion; added Qt-free tests for the exact slot control-flow decisions and selected-output ancestry.

## Preserved functionality

- Default spectral novelty + Burst Cleanup pipeline and visible spectrogram on fresh startup.
- Legacy spectral window semantics, provenance, Preview/Current separation, annotations, projects and audio playback paths.
- No additional harmonic or onset detection claims; no change to spectral algorithm parameters.

## QA status

Local non-GUI suite: **97 passed**, **12 skipped** without Qt. Python compilation passed. The new Qt-specific tests and full native Windows workflow still need execution on the user's Windows machine; do not claim this P1 crash is fully resolved in production until then.
