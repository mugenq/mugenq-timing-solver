"""Interpolation of sparse media backend timestamps for a smooth *visual* cursor.

Audio device timing is not exposed by QMediaPlayer; this is an estimate, and
can never by itself guarantee audio-sample synchronization.
"""
import math
import time


class PlaybackClock:
    def __init__(self, now=time.perf_counter):
        self._now = now
        self._anchor = now()
        self._seconds = 0.0
        self._last_backend = None
        self.rate = 1.0
        self.playing = False
        self.duration = float('inf')

    def displayed(self):
        elapsed = (self._now() - self._anchor)*self.rate if self.playing else 0.0
        return min(self.duration, max(0.0, self._seconds + max(0.0, elapsed)))

    def reset(self, seconds, *, duration=None):
        if duration is not None:
            self.duration = max(0.0, float(duration))
        self._seconds = max(0.0, min(self.duration, float(seconds)))
        self._anchor = self._now()
        self._last_backend = self._seconds

    def set_playing(self, playing):
        prior = self.displayed()
        self._seconds = prior
        self._anchor = self._now()
        self.playing = bool(playing)

    def set_rate(self, rate):
        prior = self.displayed()
        self._seconds = prior
        self._anchor = self._now()
        self.rate = max(0.0, float(rate))

    def sync(self, reported_seconds):
        reported_seconds = float(reported_seconds)
        if not math.isfinite(reported_seconds):
            return
        expected = self.displayed()
        difference = reported_seconds - expected
        # Audio is the master. On each media backend notification, anchor near
        # the reported transport time rather than accumulating lag with an
        # 18%-gain low-pass filter (especially audible below 1x playback).
        # Extrapolation between notifications remains smooth.
        if not self.playing or abs(difference) > .020:
            corrected = reported_seconds
        else:
            corrected = expected + .85 * difference
        self._seconds = max(0.0, min(self.duration, corrected))
        self._anchor = self._now()
        self._last_backend = reported_seconds


def calibrated_visual_position(source_seconds: float, wall_offset_ms: float,
                               speed_percent: float, duration: float) -> float:
    """Convert wall-clock AV latency to source-timeline displacement.

    At 25% playback, 40 ms of *wall* latency corresponds to just 10 ms of
    source music. Applying the unscaled offset at every speed made calibration
    wrong as soon as a mapper slowed down the song. This is a visual-only
    transform: the actual audio PCM and candidate timestamps never move.
    """
    if not all(math.isfinite(float(v)) for v in
               (source_seconds, wall_offset_ms, speed_percent, duration)):
        raise ValueError('Cursor calibration values must be finite')
    if duration < 0 or speed_percent < 0:
        raise ValueError('Negative duration or playback rate')
    source_shift = float(wall_offset_ms) * .001 * float(speed_percent) / 100.0
    return min(float(duration), max(0.0, float(source_seconds) + source_shift))
