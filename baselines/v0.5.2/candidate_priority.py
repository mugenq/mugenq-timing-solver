"""Non-destructive candidate hierarchy and time-local duplicate suppression.

Scores are heuristic rankings, not calibrated probabilities. Original candidate
records are never modified, and human annotations are handled by the UI outside
this pruning layer.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np

from node_engine import CandidateSet, _subset_candidates


@dataclass(frozen=True)
class PriorityResult:
    confidence: np.ndarray
    strength: np.ndarray
    local_rank: np.ndarray
    kept: np.ndarray
    reasons: tuple[str, ...]


def rank_candidates(candidates: CandidateSet, *, threshold: float = 0.0,
                    duplicate_radius_ms: float = 0.0,
                    neighborhood_s: float = 3.0) -> PriorityResult:
    """Score by strength and *local* percentile and suppress weak/duplicates.

    `threshold` is 0..1. For each neighborhood, strength is compared with other
    events nearby, avoiding the common error of wiping out quiet introductions.
    Deduping picks higher scored events first. Suppression is reversible because
    the original CandidateSet never changes. Different attacks closer than the
    merge radius can still be distinct; default duplicate radius is disabled.
    """
    times = np.asarray(candidates.times, dtype=np.float64)
    raw = np.asarray(candidates.scores, dtype=np.float64)
    n = len(times)
    if not 0 <= threshold <= 1 or not math.isfinite(threshold):
        raise ValueError('Threshold must be within 0..1')
    if not math.isfinite(duplicate_radius_ms) or duplicate_radius_ms < 0:
        raise ValueError('Duplicate radius must be nonnegative')
    if not math.isfinite(neighborhood_s) or neighborhood_s <= 0:
        raise ValueError('Neighborhood must be positive')
    if not n:
        empty = np.array([], dtype=np.float32)
        return PriorityResult(empty,empty,empty,np.array([],dtype=bool),())
    lo, hi = float(np.min(raw)), float(np.max(raw))
    strength = np.clip(raw / max(hi, 1e-9), 0, 1)
    local = np.zeros(n, dtype=np.float64)
    # Rank among candidates in a centered neighborhood, including ties. This
    # remains O(n * neighborhood population), no signal-processing rerun needed.
    for i, t in enumerate(times):
        start = np.searchsorted(times, t - neighborhood_s / 2, side='left')
        end = np.searchsorted(times, t + neighborhood_s / 2, side='right')
        block = raw[start:end]
        local[i] = (np.count_nonzero(block <= raw[i])) / max(1,len(block))
    # Preserve very strong global events, but permit locally prominent soft ones.
    confidence = np.clip(.40*strength + .60*local, 0, 1)
    kept = confidence >= threshold
    reasons = ['kept' if item else 'below threshold' for item in kept]
    if duplicate_radius_ms > 0:
        radius_s = duplicate_radius_ms / 1000
        sorted_by_priority = np.argsort(-confidence, kind='stable')
        accepted: list[float] = []
        import bisect
        for i in sorted_by_priority:
            if not kept[i]:
                continue
            index = bisect.bisect_left(accepted, times[i])
            if ((index and times[i] - accepted[index-1] <= radius_s) or
                    (index < len(accepted) and accepted[index]-times[i] <= radius_s)):
                kept[i] = False
                reasons[i] = 'near stronger candidate'
            else:
                accepted.insert(index, float(times[i]))
    return PriorityResult(confidence.astype(np.float32),
                          strength.astype(np.float32), local.astype(np.float32),
                          kept, tuple(reasons))


def prune_candidates(candidates: CandidateSet, result: PriorityResult) -> CandidateSet:
    selected = _subset_candidates(candidates, result.kept, 'preview.priority_display')
    return CandidateSet(selected.times, result.confidence[result.kept].copy(),
                        dict(selected.metadata, display_confidence_is_heuristic=True),
                        selected.provenance)
