"""Map decoded source samples to the *processed output frames* of the audio sink.

The audio device can pull ahead into buffers. If playback speed changes, those
pre-rendered buffers keep their old speed. A single elapsed*time*current_speed
formula therefore drifts. This ledger maps each rendered output run to its
actual source-frame range and samples the ledger at QAudioSink.processedUSecs.

The sink's processed clock is the strongest device-side clock exposed by Qt;
it does not measure analog/DAC/headphone latency.
"""
from __future__ import annotations
from bisect import bisect_right
from dataclasses import dataclass
from threading import RLock
import math


@dataclass
class _Run:
    first_output: int
    last_output: int
    first_source: float
    source_step: float


class PlayedFrameClock:
    def __init__(self, sample_rate: int, start_source_frame: float = 0.0):
        if not 8000 <= int(sample_rate) <= 192000:
            raise ValueError('Invalid output sample rate')
        self.sample_rate = int(sample_rate)
        self.source_start = float(start_source_frame)
        self.generated_frames = 0
        self._runs: list[_Run] = []
        self._lock = RLock()

    def append(self, output_frames: int, source_from: float, source_to: float):
        """Record a real rendered buffer, including whatever rate it used."""
        n = int(output_frames)
        start = float(source_from)
        end = float(source_to)
        if n < 0 or not all(map(math.isfinite, (start, end))):
            raise ValueError('Invalid rendered span')
        if n == 0:
            return
        step = (end - start) / n
        if step < 0:
            raise ValueError('Source time may not run backward')
        with self._lock:
            at = self.generated_frames
            if (self._runs and self._runs[-1].last_output == at and
                    math.isclose(self._runs[-1].source_step, step, abs_tol=1e-10) and
                    math.isclose(self._runs[-1].first_source +
                                 (at-self._runs[-1].first_output)*step,
                                 start, abs_tol=1e-6)):
                self._runs[-1].last_output += n
            else:
                self._runs.append(_Run(at, at+n, start, step))
            self.generated_frames += n

    def source_at_frames(self, played_frames: float) -> float:
        """Interpolate source position at processed output frame (not queued bytes)."""
        with self._lock:
            if not self._runs:
                return self.source_start
            frame = max(0.0, min(float(played_frames), float(self.generated_frames)))
            if not math.isfinite(frame):
                return self.source_start
            starts = [x.first_output for x in self._runs]
            idx = max(0, bisect_right(starts, frame)-1)
            run = self._runs[idx]
            return run.first_source + (min(frame, run.last_output) - run.first_output)*run.source_step

    def source_at_processed_us(self, processed_us: int | float) -> float:
        return self.source_at_frames(max(0.0, float(processed_us))*self.sample_rate/1_000_000.0)

    @property
    def queued_output_frames(self) -> int:
        return self.generated_frames

    @property
    def speed_regions(self) -> int:
        return len(self._runs)
