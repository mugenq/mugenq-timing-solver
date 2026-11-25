"""Band-resolved spectral attacks and an inspectable harmonic/attack spectrogram.

All timestamps refer to CENTERED analysis windows on decoded mono PCM. The
result is onset *evidence*: spectral changes can also come from vocals or
instruments, and a stable spectrum does not prove that no reattack occurred.

Pure NumPy: no neural model, hidden file output, or GUI dependency.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
import numpy as np


@dataclass(frozen=True)
class SpectralResult:
    harmonic: np.ndarray  # (log-frequency band, time), log-compressed magnitude
    attack: np.ndarray    # (log-frequency band, time), positive band novelty
    novelty: np.ndarray   # (time,), robust top-band spectral novelty
    frequency_hz: np.ndarray
    step_s: float
    rate: int
    frames: int
    window_ms: float

    @property
    def duration(self):
        return self.frames / self.rate


def compute_spectral(pcm: np.ndarray, rate: int, *, hop_ms=6.0,
                     window_ms=46.0, bands=96,
                     cancelled: Callable[[], bool] = lambda: False) -> SpectralResult:
    """Chunked, bounded-memory STFT, including independent-band positive flux.

    The window is centered on the reported timestamp; the first and last
    windows are symmetrically zero-padded. This must NOT be silently interpreted
    as a sample-exact note-onset timestamp: FFT windowing spreads evidence.
    """
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.ndim != 1 or not pcm.size or not np.all(np.isfinite(pcm)):
        raise ValueError('Spectral analysis needs finite, nonempty mono PCM')
    if not 8000 <= int(rate) <= 192000:
        raise ValueError('Unsupported sample rate')
    hop = max(1, round(rate * float(hop_ms) / 1000))
    target = max(128, round(rate * float(window_ms) / 1000))
    n_fft = 1 << (target-1).bit_length()
    if n_fft > 16384:
        raise ValueError('Spectral window exceeds 16384 FFT samples')
    centers = np.arange(0, len(pcm), hop, dtype=np.int64)
    pad_left = n_fft // 2
    pad_right = n_fft - pad_left
    padded = np.pad(pcm, (pad_left, pad_right), mode='constant')
    bins_hz = np.fft.rfftfreq(n_fft, 1.0/rate)
    # Every band must contain at least one FFT bin; low-frequency duplicates
    # are collapsed instead of inventing measurements between FFT bins.
    requested = np.geomspace(max(35.,rate/n_fft), min(12000.,rate*.48), int(bands)+1)
    indices = np.unique(np.clip(np.searchsorted(bins_hz, requested), 1, len(bins_hz)-1))
    starts = np.r_[1, indices[(indices>1)&(indices<len(bins_hz)-1)]]
    starts = np.unique(starts)
    stops = np.r_[starts[1:], len(bins_hz)]
    mid_hz = np.sqrt(np.maximum(1, bins_hz[starts])*bins_hz[np.minimum(stops-1,len(bins_hz)-1)])
    output = np.empty((len(starts), len(centers)), dtype=np.float32)
    sample_offsets = np.arange(n_fft, dtype=np.int64)
    window = np.hanning(n_fft).astype(np.float32)
    norm = max(1.,float(window.sum()))
    widths = (stops - starts).astype(np.float32)
    for first in range(0, len(centers), 128):
        if cancelled():
            raise InterruptedError('Spectrogram calculation cancelled')
        end = min(len(centers), first+128)
        frame_indices = centers[first:end,None] + sample_offsets[None,:]
        chunk = padded[frame_indices] * window
        magnitude = np.abs(np.fft.rfft(chunk, axis=1)).astype(np.float32) / norm
        band_mag = np.add.reduceat(magnitude, starts, axis=1) / widths[None,:]
        output[:,first:end] = np.log1p(1600.*band_mag).T
    # Positive changes in *individual* bands, unlike an all-band volume curve.
    # Two-hops comparison keeps local piano rearticulations without conflating
    # them with the ~46 ms FFT window itself.
    delta = np.zeros_like(output)
    lag = max(1, round(12./(hop*1000/rate)))
    delta[:,lag:] = np.maximum(0., output[:,lag:] - output[:,:-lag])
    # Use only the most changed ~20% of bands.  Weak localized harmonics can
    # contribute even when the full audio waveform is almost constant.
    k = max(3, round(output.shape[0]*.20))
    strongest = np.partition(delta, delta.shape[0]-k, axis=0)[-k:]
    novelty = strongest.mean(axis=0).astype(np.float32)
    return SpectralResult(output, delta, novelty, mid_hz.astype(np.float32),
                          hop/rate, int(rate), len(pcm), float(window_ms))


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
