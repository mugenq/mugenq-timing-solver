"""Measurement helpers. These report Qt scheduling, NOT physical display/audio latency."""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
import math
import statistics
import time


def percentile(values, fraction):
    ordered=sorted(float(v) for v in values if math.isfinite(v))
    if not ordered:
        return 0.0
    index=(len(ordered)-1)*fraction
    lo=int(index); hi=min(len(ordered)-1,lo+1)
    return ordered[lo]*(1-(index-lo))+ordered[hi]*(index-lo)


@dataclass(frozen=True)
class TimingSnapshot:
    paint_hz: float
    timer_hz: float
    paint_p95_ms: float
    paint_p99_ms: float
    clock_disagreement_p95_ms: float
    clock_disagreement_mean_ms: float
    paint_samples: int
    backend_samples: int


class TimingMonitor:
    """Uses a bounded moving window, so it cannot grow for long sessions."""
    def __init__(self, now=time.perf_counter, window_s=5.0):
        self.now=now
        self.window_s=window_s
        self.paint=deque(maxlen=2400)
        self.timer=deque(maxlen=2400)
        self.backend=deque(maxlen=1000)

    def _trim(self, samples, instant):
        while samples and instant-samples[0][0]>self.window_s:
            samples.popleft()

    def record_paint(self):
        instant=self.now()
        self.paint.append((instant,0.0))
        self._trim(self.paint,instant)

    def record_timer(self):
        instant=self.now()
        self.timer.append((instant,0.0))
        self._trim(self.timer,instant)

    def record_backend(self, clock_position: float, backend_position: float):
        instant=self.now()
        if math.isfinite(clock_position) and math.isfinite(backend_position):
            self.backend.append((instant,1000.0*(clock_position-backend_position)))
            self._trim(self.backend,instant)

    def snapshot(self):
        instant=self.now()
        for samples in (self.paint,self.timer,self.backend):
            self._trim(samples,instant)
        def hz(samples):
            return (len(samples)-1)/(samples[-1][0]-samples[0][0]) if len(samples)>1 and samples[-1][0]>samples[0][0] else 0.0
        gaps=[1000*(b[0]-a[0]) for a,b in zip(self.paint,list(self.paint)[1:])]
        errors=[abs(v) for _,v in self.backend]
        return TimingSnapshot(hz(self.paint),hz(self.timer),percentile(gaps,.95),percentile(gaps,.99),
                              percentile(errors,.95),statistics.mean(errors) if errors else 0.,
                              len(self.paint),len(self.backend))


def confidence_histogram(scores, bins=20):
    """Distribution of heuristic priorities, never interpreted as probabilities."""
    import numpy as np
    if bins<1 or bins>256:
        raise ValueError('Invalid bin count')
    raw=np.asarray(scores,dtype=np.float64)
    raw=raw[np.isfinite(raw)]
    if raw.size==0:
        return np.zeros(bins,dtype=np.int64)
    return np.histogram(np.clip(raw,0,1),bins=bins,range=(0,1))[0]
