"""Sample-timed waveform cache and resolution-selectable analysis envelopes.

This module deliberately has no Qt or decoder dependency so its timebase and
waveform aggregation can be tested independently of the Windows UI.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

DEFAULT_ENVELOPE_MS = 2.0
PCM_CACHE_LIMIT_BYTES = 192 * 1024 * 1024


def stream_peaks_and_pcm(chunks, bin_samples: int, *, max_pcm_bytes: int = PCM_CACHE_LIMIT_BYTES,
                         cancelled=lambda: False):
    """Stream exact-aligned min/max bins; optionally retain mono float PCM.

    On memory cap, discard retained PCM and carry on producing coarse min/max,
    so long songs can still load and be inspected (without sample-level detail).
    Returns minima, maxima, frame_count, PCM or None.
    """
    if bin_samples < 1:
        raise ValueError('bin_samples must be positive')
    lows, highs = [], []
    pieces = []
    retained_bytes = 0
    keep_pcm = max_pcm_bytes > 0
    remainder = np.empty(0, np.float32)
    total = 0
    for chunk in chunks:
        if cancelled():
            raise InterruptedError('Audio decoding interrupted')
        sample = np.asarray(chunk, dtype=np.float32)
        if sample.ndim != 1:
            raise ValueError('Mono PCM chunks must be 1-dimensional')
        if sample.size == 0:
            continue
        total += len(sample)
        if keep_pcm:
            if retained_bytes + sample.nbytes <= max_pcm_bytes:
                pieces.append(sample.copy())
                retained_bytes += sample.nbytes
            else:
                pieces.clear()
                keep_pcm = False
        if remainder.size:
            sample = np.concatenate((remainder, sample))
        count = sample.size // bin_samples
        if count:
            chunk_bins = sample[:count * bin_samples].reshape(count, bin_samples)
            lows.append(chunk_bins.min(axis=1))
            highs.append(chunk_bins.max(axis=1))
        remainder = sample[count * bin_samples:].copy()
    if remainder.size:
        lows.append(np.array([remainder.min()], dtype=np.float32))
        highs.append(np.array([remainder.max()], dtype=np.float32))
    if not lows:
        raise ValueError('The audio file contains no samples')
    raw = np.concatenate(pieces) if keep_pcm else None
    return np.concatenate(lows), np.concatenate(highs), total, raw


def exact_envelope(samples: np.ndarray, bin_samples: int):
    """Compute aligned extrema, retaining the last incomplete bin."""
    if bin_samples < 1:
        raise ValueError('bin_samples must be positive')
    values = np.asarray(samples, np.float32)
    if values.ndim != 1 or not len(values):
        raise ValueError('A non-empty mono PCM array is required')
    count = len(values) // bin_samples
    mins = []; maxs = []
    if count:
        block = values[:count * bin_samples].reshape(count, bin_samples)
        mins.append(block.min(axis=1))
        maxs.append(block.max(axis=1))
    if count * bin_samples < len(values):
        tail = values[count * bin_samples:]
        mins.append(np.asarray([tail.min()], np.float32))
        maxs.append(np.asarray([tail.max()], np.float32))
    return np.concatenate(mins), np.concatenate(maxs)


@dataclass
class WaveformCache:
    rate: int
    frames: int
    coarse_min: np.ndarray
    coarse_max: np.ndarray
    coarse_bin_frames: int
    pcm: np.ndarray | None = None

    def __post_init__(self):
        if self.rate <= 0 or self.frames <= 0 or self.coarse_bin_frames < 1:
            raise ValueError('Invalid waveform sample timing')
        if len(self.coarse_min) != len(self.coarse_max):
            raise ValueError('Mismatched waveform envelopes')
        if self.pcm is not None and len(self.pcm) != self.frames:
            raise ValueError('Retained PCM must match decoded frame count')
        self.spectral_features = None
        from spectral_analysis import SpectralCache
        self.spectral_store = SpectralCache()  # per-song bounded single-flight cache
        self._tiers: dict[int, tuple[np.ndarray, np.ndarray]] = {
            self.coarse_bin_frames: (self.coarse_min, self.coarse_max)
        }
        if self.pcm is not None:
            self._tiers[1] = (self.pcm, self.pcm)
            # Cache several coarse min/max levels once. Each screen pixel then
            # aggregates only a handful of cached bins rather than millions of samples.
            for frames_per_bin in (16, 64, 256, 1024, 4096, 16384):
                if frames_per_bin != self.coarse_bin_frames and frames_per_bin < self.frames:
                    self._tiers[frames_per_bin] = exact_envelope(self.pcm, frames_per_bin)

    @property
    def duration(self):
        return self.frames / self.rate

    @property
    def native_step_ms(self):
        return 1000.0 / self.rate

    @property
    def has_pcm(self):
        return self.pcm is not None

    def analysis_envelope(self, interval_ms: float):
        """Select true PCM extrema at a requested bin size; never interpolate."""
        wanted = max(1, round(float(interval_ms) * self.rate / 1000.0))
        if self.pcm is None:
            # Coarse fallback does not pretend to improve data resolution.
            return self.coarse_min, self.coarse_max, self.coarse_bin_frames / self.rate
        if wanted in self._tiers:
            low, high = self._tiers[wanted]
        else:
            low, high = exact_envelope(self.pcm, wanted)
        return low, high, wanted / self.rate

    def level_for_pixel(self, pixels_span_s: float):
        """Choose bins narrower than a pixel to avoid obvious block artifacts."""
        frames_per_pixel = self.rate * pixels_span_s
        possible = sorted(self._tiers)
        # Prefer >= 3 bins per pixel, while minimizing CPU for overview mode.
        ideal = max(1.0, frames_per_pixel / 3.0)
        candidates = [n for n in possible if n <= ideal]
        chosen = max(candidates) if candidates else min(possible)
        low, high = self._tiers[chosen]
        return chosen, low, high

    def extrema_for_seconds(self, start_s: float, end_s: float, *, tier_frames: int):
        """At least one bin, clamped to the audio bounds."""
        low, high = self._tiers[tier_frames]
        first = max(0, min(len(low) - 1, math.floor(start_s * self.rate / tier_frames + 1e-9)))
        end = max(first + 1, min(len(low), math.ceil(end_s * self.rate / tier_frames - 1e-9)))
        return float(np.min(low[first:end])), float(np.max(high[first:end]))
