"""Persistent, non-destructive human edits independent of computed candidates."""
from __future__ import annotations
from dataclasses import dataclass, field
import bisect
import math
import numpy as np
from node_engine import CandidateSet

EDIT_TOLERANCE = 0.025  # Reject a generated candidate within 25 ms after reruns.


@dataclass
class Annotations:
    added: list[float] = field(default_factory=list)
    confirmed: list[float] = field(default_factory=list)
    rejected: list[float] = field(default_factory=list)
    excluded: list[tuple[float, float]] = field(default_factory=list)

    def to_dict(self):
        return {"added": self.added[:], "confirmed": self.confirmed[:],
                "rejected": self.rejected[:], "excluded": [list(x) for x in self.excluded]}

    @classmethod
    def from_dict(cls, data: dict):
        result = cls()
        for k in ("added", "confirmed", "rejected"):
            values = [float(v) for v in data.get(k, [])]
            if not all(math.isfinite(x) and x >= 0 for x in values):
                raise ValueError(f"Invalid {k}")
            setattr(result, k, sorted(set(values)))
        for region in data.get("excluded", []):
            a, b = map(float, region)
            if not all(math.isfinite(x) and x >= 0 for x in (a, b)):
                raise ValueError("Invalid excluded region")
            if abs(a-b) > 1e-6:
                result.add_exclusion(a,b)
        return result

    def is_excluded(self, time: float) -> bool:
        return any(a <= time <= b for a, b in self.excluded)

    def add_exclusion(self, a: float, b: float):
        a, b = sorted((max(0, float(a)), max(0, float(b))))
        if b-a < .001:
            return
        intervals = sorted(self.excluded + [(a,b)])
        combined = []
        for start, end in intervals:
            if combined and start <= combined[-1][1]:
                combined[-1] = (combined[-1][0], max(end, combined[-1][1]))
            else:
                combined.append((start,end))
        self.excluded = combined

    def remove_exclusion(self, time: float) -> bool:
        for i, (a,b) in enumerate(self.excluded):
            if a <= time <= b:
                del self.excluded[i]
                return True
        return False

    def _near(self, values: list[float], time: float, tol=EDIT_TOLERANCE):
        if not values:
            return None
        i = bisect.bisect_left(values, time)
        neighbors = values[max(0,i-1):i+1]
        near = min(neighbors, key=lambda x: abs(x-time)) if neighbors else None
        return near if near is not None and abs(near-time) <= tol else None

    def add(self, time: float):
        if self.is_excluded(time):
            return False
        if self._near(self.added, time) is None:
            bisect.insort(self.added, float(time))
        return True

    def confirm(self, time: float):
        if self.is_excluded(time):
            return False
        near = self._near(self.confirmed, time)
        if near is None:
            bisect.insort(self.confirmed, float(time))
        reject = self._near(self.rejected, time)
        if reject is not None:
            self.rejected.remove(reject)
        return True

    def confirm_existing(self, time: float, kind: str) -> bool:
        """Confirm a visible marker without leaving two human markers at one time."""
        if kind == 'manual':
            old = self._near(self.added, time)
            if old is None:
                return False
            self.added.remove(old)
            return self.confirm(old)
        if kind == 'computed':
            return self.confirm(time)
        return kind == 'confirmed'

    def relocate(self, original: float, destination: float, kind: str) -> bool:
        """Move a displayed marker without mutating generated graph output.

        A generated prediction is rejected at its old position and a human
        candidate is created at the new one. Moving a confirmed prediction
        preserves its confirmed status. All moves survive graph reruns and
        serialize using the existing annotation fields.
        """
        original, destination = float(original), float(destination)
        if not (math.isfinite(original) and math.isfinite(destination)):
            return False
        if original < 0 or destination < 0 or self.is_excluded(destination):
            return False
        if abs(destination - original) < 1e-9:
            return False

        if kind == 'manual':
            old = self._near(self.added, original)
            if old is None:
                return False
            self.added.remove(old)
            bisect.insort(self.added, destination)
        elif kind == 'confirmed':
            old = self._near(self.confirmed, original)
            if old is None:
                return False
            self.confirmed.remove(old)
            bisect.insort(self.confirmed, destination)
            # Suppress the previous raw detection if the graph is rerun.
            if self._near(self.rejected, old) is None:
                bisect.insort(self.rejected, old)
        elif kind == 'computed':
            if self._near(self.rejected, original) is None:
                bisect.insort(self.rejected, original)
            bisect.insort(self.added, destination)
        else:
            return False
        return True

    def replace_nearby(self, source_kind: str, source_time: float,
                       destination: float, target_kind: str, target_time: float) -> bool:
        """Replace a nearby candidate with a new or dragged human correction.

        `source_time` and `target_time` are RAW detector times for computed
        candidates and song times otherwise. Global display offset conversion
        happens once in the UI, before this method. The operation is atomic so
        a failed move never destroys its target. Undo stores one prior snapshot.
        """
        before = self.to_dict()
        if not self.remove(float(target_time), target_kind):
            return False
        if source_kind == 'new':
            accepted = self.add(float(destination))
        else:
            accepted = self.relocate(float(source_time), float(destination), source_kind)
        if not accepted:
            restored = Annotations.from_dict(before)
            self.added = restored.added
            self.confirmed = restored.confirmed
            self.rejected = restored.rejected
            self.excluded = restored.excluded
        return bool(accepted)

    def remove(self, time: float, kind: str):
        if kind == "manual":
            near = self._near(self.added, time)
            if near is not None:
                self.added.remove(near)
                return True
            return False
        if kind == "confirmed":
            near = self._near(self.confirmed, time)
            if near is not None:
                self.confirmed.remove(near)
        if self._near(self.rejected, time) is None:
            bisect.insort(self.rejected, float(time))
        return True

    def display_candidates(self, raw: CandidateSet, offset_s: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
        """Render raw detections at raw+offset; retain manual timestamps unchanged.

        Exclusions occupy song-time coordinates, while rejected predictions refer
        to their original detector timestamps. Hence an offset adjustment never
        resurrects a rejected or moved candidate. Legacy projects default to 0.
        """
        if not math.isfinite(offset_s):
            raise ValueError('Offset must be finite')
        source = raw.times  # original analysis coordinates
        shifted = source + offset_s
        keep = (shifted >= 0.0)
        for a,b in self.excluded:
            keep &= ~((shifted >= a) & (shifted <= b))
        for t in self.rejected:
            keep &= np.abs(source-t) > EDIT_TOLERANCE
        for t in (*self.added, *self.confirmed):
            # Maintain legacy confirmed markers and suppress coincident previews.
            keep &= (np.abs(shifted-t) > EDIT_TOLERANCE)
            if t in self.confirmed:
                keep &= (np.abs(source-t) > EDIT_TOLERANCE)
        ts = [shifted[keep]]
        kinds = [np.zeros(int(np.count_nonzero(keep)), dtype=np.uint8)]
        for values, kind in ((self.added, 1), (self.confirmed, 2)):
            a = np.array([t for t in values if not self.is_excluded(t)], dtype=np.float64)
            ts.append(a)
            kinds.append(np.full(len(a), kind, dtype=np.uint8))
        all_times = np.concatenate(ts)
        all_kinds = np.concatenate(kinds)
        order = np.argsort(all_times, kind="stable")
        return all_times[order], all_kinds[order]
