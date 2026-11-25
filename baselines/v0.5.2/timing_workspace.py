"""BeatSolver timing guides and persistent committed candidate workspace.

No Qt dependency. All stored timestamps use original song seconds; an analysis
plugin's global display offset is resolved *before* committing its output.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import bisect
import math


def finite_nonnegative(value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('Timestamp must be finite and nonnegative')
    return value


@dataclass
class BPMRange:
    start: float
    end: float
    bpm: float
    beats_per_bar: int = 4
    beat_unit: int = 4
    subdivisions: int = 3

    def __post_init__(self):
        self.start = finite_nonnegative(self.start)
        self.end = finite_nonnegative(self.end)
        self.bpm = float(self.bpm)
        self.beats_per_bar = int(self.beats_per_bar)
        self.beat_unit = int(self.beat_unit)
        self.subdivisions = int(self.subdivisions)
        if self.end <= self.start or not 20 <= self.bpm <= 1000:
            raise ValueError('Invalid BPM range or tempo')
        if not 1 <= self.beats_per_bar <= 32 or self.beat_unit not in (2, 4, 8, 16):
            raise ValueError('Invalid time signature')
        if self.subdivisions not in (1, 2, 3, 4, 5, 6, 8, 12, 16):
            raise ValueError('Invalid subdivisions')

    @property
    def beat_seconds(self):
        # BPM is quarter notes per minute; e.g. 180 BPM in 4/4 is 0.3333s/beat.
        return 60.0 / self.bpm * 4.0 / self.beat_unit

    @property
    def bar_seconds(self):
        return self.beat_seconds * self.beats_per_bar

    @property
    def tick_seconds(self):
        return self.beat_seconds / self.subdivisions

    def ticks(self, window_start: float, window_end: float, *, max_count=1500):
        """Iterate visible (time, 'bar'|'beat'|'sub') without floating drift.

        At wide zoom, skip fine ticks to avoid blocking the GUI; bar lines retain
        priority. The start timestamp is the phase anchor for the first bar.
        """
        a = max(self.start, window_start)
        b = min(self.end, window_end)
        if b < a:
            return []
        step = self.tick_seconds
        first = max(0, math.ceil((a-self.start) / step - 1e-9))
        last = max(-1, math.floor((b-self.start) / step + 1e-9))
        # Range.end is EXCLUSIVE; window_end itself remains inclusive.
        # Do not draw the last guide tick in two adjacent BPM regions.
        last = min(last, math.ceil((self.end-self.start) / step - 1e-9) - 1)
        count = last - first + 1
        if count <= 0:
            return []
        bar = self.beats_per_bar * self.subdivisions
        # Preserve bars preferentially. Subdivisions only at sufficiently close zoom.
        result = []
        if count <= max_count:
            indices = range(first, last+1)
        else:
            # Prefer bar lines when the view contains too many subdivisions.
            # Iteration remains O(max_count) even for a 2-hour, 1000 BPM song.
            bar_first = (first+bar-1)//bar
            bar_last = last//bar
            bars = max(0,bar_last-bar_first+1)
            stride = max(1,math.ceil(bars/max_count))
            indices = (i*bar for i in range(bar_first,bar_last+1,stride))
        for i in indices:
            kind = 'bar' if i % bar == 0 else 'beat' if i % self.subdivisions == 0 else 'sub'
            t = self.start + i*step
            if a-1e-9 <= t <= b+1e-9 and t < self.end - 1e-10:
                result.append((t,kind))
        return result

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


@dataclass
class TimingGuides:
    ranges: list[BPMRange] = field(default_factory=list)
    visible: bool = True

    def add(self, item: BPMRange):
        self.ranges.append(item)
        self.ranges.sort(key=lambda r: (r.start, r.end))

    def to_dict(self):
        return {'visible': self.visible, 'ranges': [r.to_dict() for r in self.ranges]}

    @classmethod
    def from_dict(cls, obj):
        if not isinstance(obj, dict):
            raise ValueError('Invalid timing guides object')
        return cls([BPMRange.from_dict(x) for x in obj.get('ranges',[])], bool(obj.get('visible',True)))


@dataclass
class WorkingCandidates:
    """Persistent final product, fully separate from each graph's preview output."""
    times: list[float] = field(default_factory=list)
    TOLERANCE = 0.00005

    def __post_init__(self):
        self.times = sorted(set(finite_nonnegative(x) for x in self.times))

    def add(self, t: float):
        t = finite_nonnegative(t)
        at = bisect.bisect_left(self.times, t)
        for j in (at-1, at):
            if 0 <= j < len(self.times) and abs(self.times[j]-t) <= self.TOLERANCE:
                return False
        self.times.insert(at, t)
        return True

    def remove(self, t: float):
        at = bisect.bisect_left(self.times, t)
        neighbor = [j for j in (at-1, at) if 0 <= j < len(self.times)]
        if not neighbor:
            return False
        j = min(neighbor, key=lambda i: abs(self.times[i]-t))
        if abs(self.times[j] - t) > self.TOLERANCE:
            return False
        self.times.pop(j)
        return True

    def move(self, old: float, new: float):
        new = finite_nonnegative(new)
        before = self.times[:]
        if not self.remove(old):
            return False
        if not self.add(new):
            self.times = before
            return False
        return True

    def paste(self, times, *, replace=False):
        new = [finite_nonnegative(x) for x in times]
        if replace:
            self.times = []
        n = 0
        for value in new:
            n += int(self.add(value))
        return n

    def nearest(self, t: float, radius: float):
        if not self.times or radius < 0:
            return None
        at = bisect.bisect_left(self.times, t)
        found = [(abs(self.times[j]-t), self.times[j]) for j in (at-1, at)
                 if 0 <= j < len(self.times)]
        if found:
            distance, value = min(found)
            if distance <= radius:
                return value
        return None

    def to_dict(self):
        return {'times': self.times[:]}

    @classmethod
    def from_dict(cls, data):
        return cls(data.get('times', []))
