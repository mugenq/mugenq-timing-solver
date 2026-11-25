"""Sample-clock stereo click mixer that produces PCM buffers without WAV files.

The UI may use this via QAudioSink. Stereo decoding is performed once per
song, with a strict memory budget; large tracks fall back to legacy rendering.
No disk files are produced by this module.
"""
from __future__ import annotations
import bisect
import math
from pathlib import Path
import os
import numpy as np
from live_mix import _click_kernel, MAX_EVENTS

STEREO_CACHE_LIMIT_BYTES=256*1024*1024


def analysis_and_playback_share_timebase(analysis_rate, analysis_frames,
                                         stereo_rate, stereo_frames) -> bool:
    """Verify two separately decoded streams use the same sample timeline.

    Both streams should be decoded by miniaudio from the same file. A missing
    sample-rate match or more than one frame of count mismatch is too uncertain
    to call 'PCM synced'. This is a structural guard, not a signal-alignment
    or headphone-latency measurement.
    """
    return (int(analysis_rate) == int(stereo_rate) and
            int(analysis_frames) > 0 and int(stereo_frames) > 0 and
            abs(int(analysis_frames) - int(stereo_frames)) <= 1)


def decode_stereo(source, *, cancelled=lambda:False, max_bytes=STEREO_CACHE_LIMIT_BYTES):
    import miniaudio
    try:
        rate=int(miniaudio.get_file_info(str(source)).sample_rate)
    except Exception:
        rate=44100
    if not 8000<=rate<=192000:
        raise ValueError('Unsupported sample rate')
    options=dict(output_format=miniaudio.SampleFormat.FLOAT32,
                 nchannels=2,sample_rate=rate,frames_to_read=32768)
    if os.name=='nt' and not str(source).isascii():
        stream=miniaudio.stream_memory(Path(source).read_bytes(),**options)
    else:
        stream=miniaudio.stream_file(str(source),**options)
    pieces=[];total=0
    for chunk in stream:
        if cancelled():raise InterruptedError('Stereo decode cancelled')
        pcm=np.asarray(chunk,dtype=np.float32)
        if pcm.size%2:raise ValueError('Malformed stereo PCM')
        total+=pcm.nbytes
        if total>max_bytes:
            raise MemoryError('Stereo stream exceeds cache budget; use file mixer')
        pieces.append(pcm.reshape(-1,2).copy())
    if not pieces:raise ValueError('Empty audio stream')
    return rate,np.concatenate(pieces,axis=0)


class PCMBufferMixer:
    """Buffer-based music+click mixing, source time measured in PCM frames.

    rate multiplies source progression for pitch-changing playback. Qt's native
    preserve-pitch effect is unavailable in this experimental streaming path.
    """
    def __init__(self, audio, sample_rate, events=(), music_gain=.65, click_gain=.75):
        audio=np.asarray(audio,dtype=np.float32)
        if audio.ndim!=2 or audio.shape[1]!=2:
            raise ValueError('Expected stereo PCM array')
        if not 8000<=sample_rate<=192000:raise ValueError('Unsupported sample rate')
        self.audio=audio
        self.sample_rate=int(sample_rate)
        self.source_cursor=0.0
        self.speed=1.0
        self.music_gain=.65
        self.click_gain=.75
        self.events=()
        self.event_frames=[]
        self._event_snapshot=((),())
        self.kernels={}
        self.set_gains(music_gain,click_gain)
        self.set_events(events)

    @property
    def duration(self):return len(self.audio)/self.sample_rate
    @property
    def seconds(self):return min(self.duration,self.source_cursor/self.sample_rate)

    def seek(self,seconds):
        self.source_cursor=max(0.0,min(float(len(self.audio)),float(seconds)*self.sample_rate))

    def set_speed(self,value):
        if not math.isfinite(value) or value<0 or value>10:
            raise ValueError('Speed must be from 0 to 10')
        self.speed=float(value)

    def set_gains(self,music,click):
        if not all(math.isfinite(v) and 0<=v<=1 for v in (music,click)):
            raise ValueError('Gains must be 0..1')
        self.music_gain=float(music)
        self.click_gain=float(click)

    def set_events(self,events):
        ordered=[]
        for t,freq,amplitude in events:
            if not all(math.isfinite(v) for v in (float(t),float(freq),float(amplitude))):
                raise ValueError('Invalid click event')
            if freq<=0 or not 0<=amplitude<=1:raise ValueError('Invalid click frequency or amplitude')
            frame=round(float(t)*self.sample_rate)
            if frame>=0:
                ordered.append((frame,float(freq),float(amplitude)))
        if len(ordered)>MAX_EVENTS:raise ValueError('Too many events')
        ordered.sort(key=lambda e:e[0])
        sorted_events=tuple(ordered)
        frame_indices=tuple(x[0] for x in sorted_events)
        self._event_snapshot=(frame_indices,sorted_events)
        self.events=sorted_events
        self.event_frames=frame_indices

    def render(self,num_frames):
        """Return 16-bit LE stereo bytes, advancing the source cursor."""
        count=int(num_frames)
        if count<0 or count>262144:raise ValueError('Unsafe buffer size')
        # Snapshot speed once: a Qt GUI speed edit must not change the sample
        # positions mid-render while the source cursor advances at another rate.
        speed=self.speed
        source_start=self.source_cursor
        if not count or speed==0 or source_start>=len(self.audio):return b''
        # Avoid emitting a partial audio frame. The last output buffer may end
        # before the requested count at EOF, which signals QAudioSink to stop.
        available=max(0,math.ceil((len(self.audio)-source_start)/speed))
        count=min(count,available)
        positions=source_start + speed*np.arange(count,dtype=np.float64)
        start=np.floor(positions).astype(np.int64)
        after=np.minimum(start+1,len(self.audio)-1)
        alpha=(positions-start).astype(np.float32)
        samples=(self.audio[start]*(1-alpha[:,None])+
                 self.audio[after]*alpha[:,None])*self.music_gain
        frame_indices,events=self._event_snapshot
        if self.click_gain and events:
            lower=bisect.bisect_left(frame_indices,positions[0]-self.sample_rate*.016-1)
            upper=bisect.bisect_right(frame_indices,positions[-1])
            for frame,freq,amp in events[lower:upper]:
                a=int(np.searchsorted(positions,frame,side='left'))
                b=int(np.searchsorted(positions,frame+self.sample_rate*.016,side='left'))
                if b<=a:continue
                kernel=self.kernels.setdefault(freq,_click_kernel(self.sample_rate,freq))
                # Interpolating a 16-ms click over the exact source-frame
                # coordinates also handles speeds other than 1x.
                delta=positions[a:b]-frame
                frac_index=np.clip(delta,0,len(kernel)-1)
                click=np.interp(frac_index,np.arange(len(kernel)),kernel)
                samples[a:b,:]+=(click*amp*self.click_gain)[:,None]
        self.source_cursor=source_start+speed*count
        np.clip(samples,-1,1,out=samples)
        return (samples*32767).astype('<i2').tobytes()
