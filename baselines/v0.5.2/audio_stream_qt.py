"""Optional Windows QtAudioSink playback for decoded stereo PCM.

One stereo decode is held in RAM under a strict 256 MiB cap. Hitsounds and
volume changes can be updated without ever writing a rendered WAV. The
previous QMediaPlayer/WAV pipeline remains the supported fallback.
"""
from __future__ import annotations
import math
from PySide6.QtCore import QIODevice,QThread,Signal,QObject
from PySide6.QtMultimedia import QAudio,QAudioFormat,QAudioSink,QMediaDevices
from streaming_pcm import decode_stereo, PCMBufferMixer
from played_frame_clock import PlayedFrameClock


class StereoDecodeThread(QThread):
    ready=Signal(int,object,str)
    failed=Signal(str,str)
    def __init__(self,path,parent=None):
        super().__init__(parent)
        self.path=str(path)
    def run(self):
        try:
            rate,pcm=decode_stereo(self.path,cancelled=self.isInterruptionRequested)
            if not self.isInterruptionRequested():self.ready.emit(rate,pcm,self.path)
        except InterruptedError:
            pass
        except Exception as exc:
            self.failed.emit(str(exc),self.path)


class PCMIODevice(QIODevice):
    """QAudioSink pull device; returns precisely the requested PCM frames."""
    def __init__(self,mixer,parent=None, clock=None):
        super().__init__(parent)
        self.mixer=mixer
        self.clock=clock
        self.open(QIODevice.OpenModeFlag.ReadOnly)
    def isSequential(self):return True
    def bytesAvailable(self):
        # Preserve base class buffer accounting when accessed by Qt.
        frames=max(0,int(math.ceil((len(self.mixer.audio)-self.mixer.source_cursor)/max(.01,self.mixer.speed))))
        return frames*4+super().bytesAvailable()
    def readData(self,maxlen):
        before=self.mixer.source_cursor
        data=self.mixer.render(min(max(0,int(maxlen)//4),262144))
        if self.clock and data:
            self.clock.append(len(data)//4,before,self.mixer.source_cursor)
        return data
    def writeData(self,data):return -1


class AudioStreamController(QObject):
    """Transport with a source-time mapping from QAudioSink.processedUSecs.

    This is an estimate of the audio backend, not a hardware loopback latency
    measurement. Only the PCM click/sound mix is sample-aligned by design.
    """
    failed=Signal(str)
    finished=Signal()  # Emitted once when the stream drains at EOF.
    def __init__(self,parent=None):
        super().__init__(parent)
        self.mixer=None
        self.sink=None
        self.reader=None
        self.playing=False
        self._paused=0.0
        self._processed_anchor=0.0
        self._source_anchor=0.0
        self._rate=1.0
        self._selected_device=None
        self._finishing=False
        self._frame_clock=None

    def load(self,rate,pcm):
        self.stop()
        fmt=QAudioFormat()
        fmt.setSampleRate(int(rate))
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        device=QMediaDevices.defaultAudioOutput()
        if device.isNull() or not device.isFormatSupported(fmt):
            raise RuntimeError('Output device cannot play this native stereo PCM format')
        self._selected_device=device
        self._format=fmt
        self.mixer=PCMBufferMixer(pcm,rate)
        self._paused=0.0

    def _processed(self):
        return max(0.0,self.sink.processedUSecs()/1_000_000) if self.sink else 0.0

    def position(self):
        if not self.sink or not self.playing:return self._paused
        if self._frame_clock is None:return self._paused
        # Output sample count, *not* the read-ahead mixer.source_cursor.
        # Every buffered frame retains the speed it had when rendered.
        played_source_frame=self._frame_clock.source_at_processed_us(
            max(0,self.sink.processedUSecs()))
        return min(self.mixer.duration,max(0.0,played_source_frame/self.mixer.sample_rate))

    def _start_sink(self):
        self.mixer.seek(self._paused)
        self.mixer.set_speed(self._rate)
        self._frame_clock=PlayedFrameClock(self.mixer.sample_rate,self.mixer.source_cursor)
        self.reader=PCMIODevice(self.mixer,self,clock=self._frame_clock)
        self.sink=QAudioSink(self._selected_device,self._format,self)
        self.sink.stateChanged.connect(self._on_sink_state)
        # Audio device buffering is still platform-managed. Small buffers
        # improve feedback but are not hardware latency guarantees.
        # Request ~50ms instead of ~100ms, reducing cursor/audio lead.
        # Device may round or ignore the request; it is not a DAC latency test.
        self.sink.setBufferSize(max(4096,round(self.mixer.sample_rate*.05)*4))
        self._source_anchor=self._paused
        self._processed_anchor=0.0
        self.sink.start(self.reader)
        if getattr(self.sink.error(),'name',None)!='NoError':
            message=str(self.sink.error())
            self._release_sink()
            raise RuntimeError('QAudioSink failed to start: '+message)
        self.playing=True

    def _release_sink(self):
        # Clear references and playback state BEFORE stopping the device;
        # stop() itself can synchronously emit stateChanged.
        sink,reader=self.sink,self.reader
        self.sink=None
        self.reader=None
        self.playing=False
        if sink:
            try:sink.stateChanged.disconnect(self._on_sink_state)
            except (RuntimeError,TypeError):pass
            sink.stop()
            sink.deleteLater()
        if reader:
            reader.close()
            reader.deleteLater()

    def _on_sink_state(self,state):
        # IdleState also occurs for device underflow. Only completed PCM
        # consumption counts as the natural end of the track.
        if (state == QAudio.State.IdleState and self.playing and
                self.mixer is not None and
                self.mixer.source_cursor >= len(self.mixer.audio)):
            self._finish_eof()

    def _finish_eof(self):
        if self._finishing or not self.playing or self.mixer is None:
            return
        self._finishing=True
        try:
            self._paused=self.mixer.duration
            self._release_sink()
        finally:
            self._finishing=False
        self.finished.emit()

    def poll_completion(self):
        # A safety net for backends that do not deliver stateChanged reliably.
        if (self.sink is not None and self.playing and
                self.mixer is not None and
                self.mixer.source_cursor >= len(self.mixer.audio) and
                self.sink.state()==QAudio.State.IdleState):
            self._finish_eof()

    def stop(self):
        self._release_sink()
        self._paused=0.0

    def play(self):
        if self.mixer is None:raise RuntimeError('No decoded audio')
        if self._rate==0:return
        # A completed track should replay from zero on the next Play press.
        if self._paused >= self.mixer.duration - 1/max(1,self.mixer.sample_rate):
            self._paused=0.0
            self.mixer.seek(0.0)
        if self.sink is not None and not self.playing:
            self.sink.resume()
            self._processed_anchor=self._processed()
            self._source_anchor=self._paused
            self.playing=True
        elif self.sink is None:
            self._start_sink()

    def pause(self):
        if self.sink and self.playing:
            self._paused=self.position()
            self.sink.suspend()
            self.playing=False

    def seek(self,t):
        if self.mixer is None:return
        was_playing=self.playing
        self._release_sink()
        self._paused=max(0.0,min(self.mixer.duration,float(t)))
        self.mixer.seek(self._paused)
        if was_playing and self._paused < self.mixer.duration:
            self.play()
        elif was_playing and self._paused >= self.mixer.duration:
            self.finished.emit()

    def set_rate(self,value):
        if self.mixer is None:return
        value=float(value)
        if not math.isfinite(value) or value<0 or value>10:raise ValueError('Invalid rate')
        # Existing sink buffers were rendered at the PREVIOUS speed. Do not
        # re-anchor a single-rate clock here: the frame ledger preserves the
        # exact old/new speed boundary as Qt consumes queued audio frames.
        prior=self.position()
        self._paused=prior
        self._rate=value
        self.mixer.set_speed(value)
        if value==0:self.pause()

    def update_mix(self,events,music,click):
        if self.mixer:
            self.mixer.set_events(events)
            self.mixer.set_gains(music,click)
