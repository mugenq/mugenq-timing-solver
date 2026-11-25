"""Pure, fast lookup of replacement zones on a sorted candidate timeline.

No Qt dependency: placement and dragging use the exact same time-based rule.
"""
from __future__ import annotations

import math
import numpy as np

KIND_NAMES = ('computed', 'manual', 'confirmed', 'current')


def nearby_candidate(times: np.ndarray, kinds: np.ndarray, at_seconds: float,
                     radius_seconds: float, *, skip: tuple[float, str] | None = None,
                     layer: str | None = None
                     ) -> tuple[float, str, float] | None:
    """Nearest candidate within the inclusive temporal merge radius.

    Returns (timestamp, kind, proximity), where proximity is 1 at the center and
    0 at the radius edge. Skip the source candidate during drags, so it never
    merges into itself. Both arrays must be sorted by time (as in display_candidates).
    """
    if (not math.isfinite(at_seconds) or not math.isfinite(radius_seconds)
            or radius_seconds <= 0 or len(times) == 0):
        return None
    low = int(np.searchsorted(times, at_seconds-radius_seconds, side='left'))
    high = int(np.searchsorted(times, at_seconds+radius_seconds, side='right'))
    closest = None
    best_distance = float('inf')
    for i in range(low, high):
        time = float(times[i])
        kind = KIND_NAMES[int(kinds[i])]
        if layer == 'current' and kind != 'current':
            continue
        if layer == 'preview' and kind == 'current':
            continue
        if skip is not None and kind == skip[1] and abs(time - skip[0]) < 1e-8:
            continue
        distance = abs(time-at_seconds)
        if distance < best_distance:
            best_distance = distance
            closest = (time, kind, max(0.0, 1.0-distance/radius_seconds))
    return closest
