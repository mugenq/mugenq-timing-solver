"""Continuous song + hitsound mix, rendered on a shared decoded sample clock.

Rendering a full-length *stereo* PCM WAV means clicks and the source audio are
sample-aligned even under playback-speed changes or seeks. The UI builds this on
an asynchronous QThread, never on a 240 Hz repaint timer. This implementation
uses a temporary disk file; the user is told when a mix is being regenerated.
"""
from __future__ import annotations
import math
import os
from pathlib import Path
import wave
import numpy as np

MAX_EVENTS = 200000
MAX_DURATION_SECONDS = 3600


def click_events(preview, current, guides, source: str, duration: float):
    """Return (seconds, tone_hz, amplitude) with distinct layers and grid accents."""
    if not math.isfinite(duration) or not 0 <= duration <= MAX_DURATION_SECONDS:
        raise ValueError('Song is too long for full-track live click rendering')
    if source not in {'preview','current','both','grid'}:
        raise ValueError('Unknown hitsound source')
    result = []
    if source in {'preview','both'}:
        result.extend((float(t), 1640.0, 0.85) for t in preview if 0 <= float(t) < duration)
    if source in {'current','both'}:
        result.extend((float(t), 1100.0, 1.0) for t in current if 0 <= float(t) < duration)
    if source == 'grid':
        for rng in guides.ranges:
            for t, kind in rng.ticks(0,duration,max_count=MAX_EVENTS):
                hz, loudness = {'bar':(1930.,1.),'beat':(1360.,.77),'sub':(890.,.45)}[kind]
                result.append((float(t),hz,loudness))
    if len(result) > MAX_EVENTS:
        raise ValueError('Too many hitsounds for one continuous mix')
    result.sort(key=lambda item: item[0])
    return result


def _click_kernel(rate: int, hz: float):
    length = max(1, round(rate * .016))
    x = np.arange(length, dtype=np.float32) / rate
    return ((np.sin(2*np.pi*hz*x)*.83 + np.sin(2*np.pi*hz*.52*x)*.17) *
            np.exp(-x*300)).astype(np.float32)


def write_pcm_mix(chunks, sample_rate: int, events, output_path,
                  *, music_gain: float = .65, click_gain: float = .75,
                  channels: int = 2, cancelled=lambda: False):
    """Render float32 interleaved source chunks into a 16-bit PCM WAV.

    All timestamps map to round(t*sample_rate), including clicks crossing chunk
    boundaries. Input can be a generator; it is not cached in RAM.
    """
    if not 8000 <= sample_rate <= 192000 or channels not in (1,2):
        raise ValueError('Invalid PCM format')
    for value in (music_gain, click_gain):
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('Gains must be between 0 and 1')
    events = sorted((int(round(float(t)*sample_rate)),float(freq),float(amp))
                    for t,freq,amp in events if float(t)>=0)
    for frame,freq,amp in events:
        if not math.isfinite(freq) or freq <= 0 or not math.isfinite(amp) or not 0 <= amp <= 1:
            raise ValueError('Invalid hitsound parameters')
    last, count = 0, 0
    cache = {}
    path=Path(output_path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for chunk in chunks:
            if cancelled():
                raise InterruptedError('Mix rendering cancelled')
            samples = np.asarray(chunk,dtype=np.float32)
            if samples.ndim != 1 or samples.size % channels:
                raise ValueError('Expected interleaved float32 PCM')
            nframes = samples.size//channels
            if nframes == 0:
                continue
            mix = samples.reshape(nframes,channels).copy()*music_gain
            until = last+nframes
            # Events from the preceding chunk can still have decaying tails.
            while count < len(events) and events[count][0] < until:
                count+=1
            for frame,freq,amp in events[max(0, count-256):count]:
                kernel=cache.setdefault(freq,_click_kernel(sample_rate,freq))
                start=max(last,frame)
                end=min(until,frame+len(kernel))
                if end>start:
                    segment=kernel[start-frame:end-frame]*amp*click_gain
                    mix[start-last:end-last,:] += segment[:,None]
            # For very dense events, 256 can be insufficient for all overlapping
            # clicks; dynamically expand the left edge using the kernel duration.
            if count > 256 and events[count-257][0]+int(sample_rate*.016)>last:
                import bisect
                frames = [event[0] for event in events]
                lower = bisect.bisect_left(frames,last-int(sample_rate*.016))
                # Recompute this chunk from base with the complete event set.
                mix = samples.reshape(nframes,channels).copy()*music_gain
                for frame,freq,amp in events[lower:count]:
                    kernel=cache.setdefault(freq,_click_kernel(sample_rate,freq))
                    start=max(last,frame);end=min(until,frame+len(kernel))
                    if end>start:
                        mix[start-last:end-last,:] += (kernel[start-frame:end-frame]*amp*click_gain)[:,None]
            np.clip(mix,-1,1,out=mix)
            wav.writeframes((mix*32767).astype('<i2').tobytes())
            last=until
    return last


def render_file(source_path, destination, events, *, music_gain=.65,
                click_gain=.75, cancelled=lambda:False):
    """Decode stereo in chunks and render the full song for continuous audition."""
    import miniaudio
    source=str(source_path)
    try:
        rate=int(miniaudio.get_file_info(source).sample_rate)
    except Exception:
        rate=44100
    if not 8000 <= rate <= 192000:
        rate=44100
    options=dict(output_format=miniaudio.SampleFormat.FLOAT32,
                 nchannels=2,sample_rate=rate,frames_to_read=32768)
    if os.name=='nt' and not source.isascii():
        stream=miniaudio.stream_memory(Path(source).read_bytes(),**options)
    else:
        stream=miniaudio.stream_file(source,**options)
    try:
        return write_pcm_mix(stream,rate,events,destination,
                             music_gain=music_gain,click_gain=click_gain,
                             cancelled=cancelled)
    except BaseException:
        Path(destination).unlink(missing_ok=True)
        raise
