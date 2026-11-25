# BeatSolver Timeline v0.4.10 — Graph consistency hotfix

**Scope:** correct two reproducible v0.4.9 edge cases, without changing onset detection, playback, manual annotations, Current candidates or the fresh-session spectral/Burst Cleanup graph.

## 1. Latest graph request wins

- Every user-initiated graph run receives a monotonically increasing request generation.
- A run queued while an older Qt worker is completing retains that request generation in its deferred timer.
- If the user launches a newer output before the old timer executes, the obsolete timer is discarded. Old results/errors from superseded workers also cannot overwrite the newer Preview or show a stale error dialog.
- Retains v0.4.9's owner-specific `QThread.finished` cleanup. The actual emitting worker is retired; an unrelated active worker is never deleted.
- New-song loads and window shutdown invalidate queued requests; pending reruns remain coalesced to the latest output.

## 2. Explicit evidence source for Candidate Merge graphs

- Analysis now has an **Evidence** selector listing each typed Spectrogram node actually contributing to the active Candidate Output.
- Auto selects the first contributing Spectrogram in stable graph-node order, rather than mixing LIFO graph traversal with the last evaluated spectrum.
- For a two-branch merged graph, users can explicitly display the *legacy FFT* or *exact FFT* branch, even if graph evaluation/merge port order differs. Changing the selection only changes the viewer and doesn't recompute or overwrite Preview or Current.
- The selected Spectrogram **node ID**, not just a matching FFT signature, determines the displayed numerical feature object.
- An optional `spectrogram_source_id` field is saved in `.beatsolver.json`. Older projects load with Auto (no schema migration).
- Spectrogram sources not connected to the active output are excluded.

## 3. Validation

- Deterministic pure tests cover multiple typed spectra with both Merge input orderings; graph serialization; numerical feature-object selection; and generation supersession.
- New Qt tests exercise delayed completion timers and the live viewer's explicit branch switching. Existing Qt lifecycle tests remain in the suite.
- No audio matching threshold or FFT resolution default has changed. Full Windows GUI/Qt regression and hardware playback checks still require the user's Windows environment.
