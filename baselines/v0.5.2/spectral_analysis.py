"""Band-resolved spectral attacks and an inspectable harmonic/attack spectrogram.

All timestamps refer to CENTERED analysis windows on decoded mono PCM. The
result is onset *evidence*: spectral changes can also come from vocals or
instruments, and a stable spectrum does not prove that no reattack occurred.

Pure NumPy: no neural model, hidden file output, or GUI dependency.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
from threading import Condition, RLock
from collections import OrderedDict
import numpy as np


SPECTRAL_REPRESENTATION_VERSION = 2


@dataclass(frozen=True)
class SpectralResult:
    harmonic: np.ndarray       # (log-frequency band, time), log magnitude
    attack: np.ndarray         # (log-frequency band, time), positive band novelty
    novelty: np.ndarray        # (time,), top-band spectral novelty
    frequency_hz: np.ndarray
    step_s: float
    rate: int
    frames: int
    window_ms: float          # Requested window, for legacy readers
    window_samples: int = 0   # The *actual* number of windowed samples
    fft_samples: int = 0      # Possibly zero-padded FFT length
    hop_samples: int = 0
    window_mode: str = 'legacy_fft'
    band_edges_hz: np.ndarray | None = None
    center_samples: np.ndarray | None = None
    representation_version: int = SPECTRAL_REPRESENTATION_VERSION

    @property
    def duration(self):
        return self.frames / self.rate

    @property
    def actual_window_ms(self):
        return 1000. * self.window_samples / self.rate if self.window_samples else self.window_ms


class SpectralCache:
    """Per-song bounded LRU with single-flight FFT work across Qt workers.

    The cache is owned by one decoded WaveformCache, so two different audio
    files cannot alias even when their frame counts match. In-flight workers
    coordinate instead of clearing each other's entries. Failed/cancelled
    computations are not cached.
    """
    def __init__(self, max_bytes: int = 192 * 1024 * 1024):
        self.max_bytes = int(max_bytes)
        self._items = OrderedDict()
        self._inflight = set()
        self._condition = Condition(RLock())
        self._bytes = 0

    @staticmethod
    def _bytes_of(item):
        return sum(getattr(value, 'nbytes', 0) for value in
                   (item.harmonic, item.attack, item.novelty, item.frequency_hz,
                    item.band_edges_hz, item.center_samples))

    def get(self, key, default=None):
        with self._condition:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return default if value is None else value

    def get_or_compute(self, key, factory, cancelled=lambda: False):
        with self._condition:
            while True:
                result = self._items.get(key)
                if result is not None:
                    self._items.move_to_end(key)
                    return result
                if cancelled():
                    raise InterruptedError('Spectrogram calculation cancelled')
                if key not in self._inflight:
                    self._inflight.add(key)
                    break
                self._condition.wait(timeout=.05)
        try:
            computed = factory()
        except BaseException:
            with self._condition:
                self._inflight.discard(key)
                self._condition.notify_all()
            raise
        with self._condition:
            self._inflight.discard(key)
            size = self._bytes_of(computed)
            if size <= self.max_bytes:
                while self._items and self._bytes + size > self.max_bytes:
                    _, evicted = self._items.popitem(last=False)
                    self._bytes -= self._bytes_of(evicted)
                self._items[key] = computed
                self._bytes += size
            self._condition.notify_all()
        return computed

    def __len__(self):
        with self._condition:
            return len(self._items)


def spectral_cache_key(pcm, rate: int, hop_ms: float, window_ms: float,
                       *, bands: int = 96, window_mode: str = 'legacy_fft'):
    """A key scoped to the native PCM identity plus *all* analysis settings."""
    return (SPECTRAL_REPRESENTATION_VERSION, id(pcm), len(pcm), int(rate),
            round(float(hop_ms), 5), round(float(window_ms), 5),
            int(bands), str(window_mode), 'hann-centered-v2')


def shared_spectral(pcm, rate: int, *, cache=None, hop_ms=6.0,
                    window_ms=46.0, bands=96, window_mode='legacy_fft',
                    cancelled=lambda: False):
    key = spectral_cache_key(pcm, rate, hop_ms, window_ms,
                             bands=bands, window_mode=window_mode)
    def calculate():
        return compute_spectral(pcm, rate, hop_ms=hop_ms, window_ms=window_ms,
                                bands=bands, window_mode=window_mode,
                                cancelled=cancelled)
    if isinstance(cache, SpectralCache):
        return cache.get_or_compute(key, calculate, cancelled)
    # Standalone/plugin callers without the shared cache remain supported.
    if isinstance(cache, dict):
        found = cache.get(key)
        if found is not None:
            return found
    result = calculate()
    if isinstance(cache, dict):
        cache[key] = result
    return result


def compute_spectral(pcm: np.ndarray, rate: int, *, hop_ms=6.0,
                     window_ms=46.0, bands=96, window_mode='legacy_fft',
                     cancelled: Callable[[], bool] = lambda: False) -> SpectralResult:
    """Bounded chunked centered STFT with *explicit* physical window length.

    legacy_fft reproduces v0.4.6's Hann window over the full next-power-of-2
    FFT size. exact uses the requested number of samples for the Hann window,
    then zero-pads to that FFT size. Neither is automatically onset-aligned.
    """
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.ndim != 1 or not pcm.size or not np.all(np.isfinite(pcm)):
        raise ValueError('Spectral analysis needs finite, nonempty mono PCM')
    if not 8000 <= int(rate) <= 192000:
        raise ValueError('Unsupported sample rate')
    if not (np.isfinite(hop_ms) and 0 < float(hop_ms) <= 100 and
            np.isfinite(window_ms) and 0 < float(window_ms) <= 500):
        raise ValueError('Invalid hop or spectral window')
    if window_mode not in ('legacy_fft', 'exact'):
        raise ValueError('window_mode must be legacy_fft or exact')
    hop = max(1, round(rate * float(hop_ms) / 1000))
    target = max(128, round(rate * float(window_ms) / 1000))
    n_fft = 1 << (target-1).bit_length()
    if n_fft > 16384:
        raise ValueError('Spectral FFT exceeds 16384 samples')
    n_window = n_fft if window_mode == 'legacy_fft' else target
    centers = np.arange(0, len(pcm), hop, dtype=np.int64)
    pad_left = n_window // 2
    pad_right = n_window - pad_left
    padded = np.pad(pcm, (pad_left, pad_right), mode='constant')
    bins_hz = np.fft.rfftfreq(n_fft, 1.0/rate)
    requested = np.geomspace(max(35.,rate/n_fft), min(12000.,rate*.48), int(bands)+1)
    indices = np.unique(np.clip(np.searchsorted(bins_hz, requested), 1, len(bins_hz)-1))
    starts = np.r_[1, indices[(indices>1)&(indices<len(bins_hz)-1)]]
    starts = np.unique(starts)
    stops = np.r_[starts[1:], len(bins_hz)]
    mid_hz = np.sqrt(np.maximum(1, bins_hz[starts])*bins_hz[np.minimum(stops-1,len(bins_hz)-1)])
    edges = np.column_stack((bins_hz[starts], bins_hz[np.minimum(stops, len(bins_hz)-1)]))
    output = np.empty((len(starts), len(centers)), dtype=np.float32)
    sample_offsets = np.arange(n_window, dtype=np.int64)
    window = np.hanning(n_window).astype(np.float32)
    norm = max(1., float(window.sum()))
    widths = (stops - starts).astype(np.float32)
    for first in range(0, len(centers), 128):
        if cancelled():
            raise InterruptedError('Spectrogram calculation cancelled')
        end = min(len(centers), first+128)
        frame_indices = centers[first:end, None] + sample_offsets[None, :]
        chunk = padded[frame_indices] * window
        magnitude = np.abs(np.fft.rfft(chunk, n=n_fft, axis=1)).astype(np.float32) / norm
        band_mag = np.add.reduceat(magnitude, starts, axis=1) / widths[None,:]
        output[:, first:end] = np.log1p(1600. * band_mag).T
    delta = np.zeros_like(output)
    lag = max(1, round(12. / (hop*1000/rate)))
    delta[:, lag:] = np.maximum(0., output[:, lag:] - output[:, :-lag])
    k = max(3, round(output.shape[0]*.20))
    strongest = np.partition(delta, delta.shape[0]-k, axis=0)[-k:]
    novelty = strongest.mean(axis=0).astype(np.float32)
    result = SpectralResult(output, delta, novelty, mid_hz.astype(np.float32),
                            hop / rate, int(rate), len(pcm), float(window_ms),
                            n_window, n_fft, hop, window_mode,
                            edges.astype(np.float32), centers)
    # Shared numerical evidence may be consumed by two background Qt threads.
    # Never mutate an already-produced spectral array in place.
    for data in (result.harmonic, result.attack, result.novelty,
                 result.frequency_hz, result.band_edges_hz, result.center_samples):
        data.setflags(write=False)
    return result


def evidence_cleanup(times, scores, curve_values, curve_start_s, curve_step_s,
                     *, neighbor_gap_ms=35., max_burst_ms=110.,
                     weak_retention=.65, prominence=.35, search_ms=12.):
    """Non-destructive burst pruning with independent spectral reattack rescue.

    A candidate remains when it is sufficiently strong OR is supported by a
    distinct spectral rise with a local valley. Conservatively keep ambiguous
    candidates if the spectral signal has no meaningful activity; such events
    are for human inspection, not silent destruction.
    """
    t = np.asarray(times,dtype=np.float64)
    s = np.asarray(scores,dtype=np.float32)
    curve = np.asarray(curve_values,dtype=np.float32)
    if not len(t) or len(t)==1 or weak_retention<=0:
        return np.ones(len(t),dtype=bool)
    keep = np.ones(len(t),dtype=bool)
    gap=max(0.,neighbor_gap_ms)/1000.
    span=max(gap,max_burst_ms/1000.)
    search=max(0.,search_ms)/1000.
    centers = np.round((t-curve_start_s)/curve_step_s).astype(int)
    radius = max(1,round(search/curve_step_s))
    spectral = np.zeros(len(t),dtype=np.float32)
    peak_indices=np.full(len(t),-1,dtype=int)
    for j,c in enumerate(centers):
        lo=max(0,c-radius); hi=min(len(curve),c+radius+1)
        if hi>lo:
            peak_indices[j]=lo+int(np.argmax(curve[lo:hi]))
            spectral[j]=max(0.,float(curve[peak_indices[j]]))

    def finish(a,b):
        if b-a<2:
            return
        mx=max(0.,float(np.max(s[a:b])))
        if mx<=0 or float(np.max(spectral[a:b]))<=1e-8:
            # No independent spectral evidence to decide: retain ambiguous
            # candidates for human review rather than deleting them blindly.
            return
        # Never shift candidate timestamps, even when the strongest spectral
        # flux lags a human-observed onset.
        for j in range(a,b):
            if s[j] >= mx*weak_retention:
                continue
            pj=peak_indices[j]
            if pj<0 or spectral[j]<=1e-9:
                keep[j]=False
                continue
            # Distinct reattack: sufficiently deep spectral valley on BOTH
            # sides of the local maximum relative to another nearby candidate.
            # This avoids 'rescuing' every small fluctuation on a broad plateau.
            independent=False
            for other in range(a,b):
                if other==j or peak_indices[other]<0:
                    continue
                po=peak_indices[other]
                if abs(po-pj)<max(2,round(0.008/curve_step_s)):
                    continue
                l,h=sorted((pj,po))
                valley=float(np.min(curve[l:h+1])) if h>l else spectral[j]
                contrast=1-valley/max(1e-9,min(float(spectral[j]),float(spectral[other])))
                if contrast>=prominence and spectral[j]>=.14*max(1e-9,float(np.max(spectral[a:b]))):
                    independent=True
                    break
            if not independent:
                keep[j]=False
    start=0
    for i in range(1,len(t)):
        if t[i]-t[i-1]>gap or t[i]-t[start]>span:
            finish(start,i); start=i
    finish(start,len(t))
    return keep
