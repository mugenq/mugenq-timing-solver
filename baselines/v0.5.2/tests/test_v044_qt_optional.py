"""Qt-only synchronization and persisted UI control checks."""
import os,sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
pytest.importorskip('PySide6')
pytest.importorskip('miniaudio')
import numpy as np
from PySide6.QtWidgets import QApplication
from audio_stream_qt import AudioStreamController,PCMIODevice
from streaming_pcm import PCMBufferMixer
from beatsolver_timeline import BeatSolverWindow


def test_pcm_read_tracks_actual_audio_buffers_and_speed_boundary():
    m=PCMBufferMixer(np.zeros((48000,2),dtype=np.float32),48000)
    from played_frame_clock import PlayedFrameClock
    ledger=PlayedFrameClock(48000,0)
    io=PCMIODevice(m,clock=ledger)
    try:
        assert len(io.readData(4000))==4000
        m.set_speed(.5)
        assert len(io.readData(4000))==4000
        assert ledger.source_at_frames(1000)==pytest.approx(1000)
        assert ledger.source_at_frames(2000)==pytest.approx(1500)
    finally:
        io.close()


def test_visual_sync_is_independent_of_audio_position_and_candidate_offset():
    app=QApplication.instance() or QApplication([])
    w=BeatSolverWindow()
    try:
        w.cursor_sync_spin.setValue(-30)
        assert w.cursor_sync_ms==-30
        assert w.global_offset_ms==0
        assert w.playback_clock.displayed()==pytest.approx(0)
    finally:
        w.close()
        app.processEvents()


def test_controller_audio_position_tracks_processed_frames_not_current_rate():
    class MockSink:
        def __init__(self): self.usecs=0
        def processedUSecs(self):return self.usecs
    c=AudioStreamController()
    c.mixer=PCMBufferMixer(np.zeros((48000,2),dtype=np.float32),48000)
    from played_frame_clock import PlayedFrameClock
    clock=PlayedFrameClock(48000)
    clock.append(4800,0,4800)
    clock.append(4800,4800,7200)
    c._frame_clock=clock
    c.sink=MockSink()
    c.playing=True
    c._rate=.5  # current speed is half, but first queued buffer is still 1x
    c.sink.usecs=100000
    assert c.position()==pytest.approx(.1)
    c.sink.usecs=150000
    assert c.position()==pytest.approx(.125)
    c.sink.usecs=200000
    assert c.position()==pytest.approx(.15)
    c.sink=None
