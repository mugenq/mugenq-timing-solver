# BeatSolver v0.6.0 purification research initialization

This is a research starting point, not a completed v0.6.0 production release.
The frozen v0.5.2 source is under ../../baselines/v0.5.2. Production defaults,
Current/Preview, saved projects and original user media remain unchanged.

Investigate interpretable attack independence/duplicate suppression,
sustain-residual spectral analysis, multi-resolution evidence fusion, adaptive
local prominence and original-PCM onset boundary refinement, with ablations.

Evaluate one-to-one agreement with imperfect partial-positive mapped starts
at 5/10/30 ms, recall versus candidate budget, runtime and OS peak memory.
Unmapped candidates are unlabelled, never automatic false positives.

Freeze parameter families, candidate budgets and passage splits before
experimentation. Keep final held-out passages out of selection; report every
method, failed approach, limitation, input hash, Git revision and environment.
Candidate generation must not read labels. CPU/NumPy only, bounded memory,
no ML/model inference, no production registration and no automatic promotion.

The initialization commit is authorized for GitHub. Subsequent experimental
results remain local on research/v0.6-purification until separately approved.
No merge, release tag or publication of research results is authorized.
