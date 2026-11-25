"""Sample-aligned click auditions baked into a short mono PCM WAV excerpt."""
from __future__ import annotations
import io
import math
import wave
import numpy as np


def audible_excerpt(pcm, sample_rate: int, start: float, end: float, candidates,
                    *, audio_gain=.65, click_gain=.60):
    """Return (WAV bytes, actual_start, click_sample_indices) for offline playback.

    Clicks are mixed into the *same decoded sample buffer*, so their relative
    sample positions don't depend on a live QTimer or the audio output latency.
    """
    samples = np.asarray(pcm, dtype=np.float32)
    if samples.ndim != 1 or not len(samples) or sample_rate <= 0:
        raise ValueError('A decoded mono PCM cache is needed for click auditions')
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError('Invalid audition range')
    start_frame = min(len(samples), max(0, round(start*sample_rate)))
    end_frame = min(len(samples), max(start_frame+1, round(end*sample_rate)))
    if end_frame <= start_frame:
        raise ValueError('Audition range exceeds the song')
    first = start_frame / sample_rate
    section = np.array(samples[start_frame:end_frame], dtype=np.float32, copy=True)
    section *= audio_gain
    n = max(1, round(sample_rate*.017))
    x = np.arange(n, dtype=np.float32) / sample_rate
    # An audible but short double-tone click with a fast attack and decay.
    click = (np.sin(2*np.pi*1800*x) * .65 + np.sin(2*np.pi*950*x)*.35)
    click *= np.exp(-x*285) * click_gain
    fired = []
    for t in candidates:
        frame = int(round(float(t)*sample_rate)) - start_frame
        if frame < 0 or frame >= len(section):
            continue
        length = min(n, len(section)-frame)
        section[frame:frame+length] += click[:length]
        fired.append(frame)
    if not fired:
        raise ValueError('No candidate clicks fall inside this excerpt')
    section = np.clip(section, -1.0, 1.0)
    data = (section*32767).astype('<i2').tobytes()
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(data)
    return output.getvalue(), first, fired
