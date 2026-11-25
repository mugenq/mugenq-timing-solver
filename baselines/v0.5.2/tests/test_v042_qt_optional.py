"""Optional desktop control smoke, to run with the installed Windows Qt stack."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
pytest.importorskip('miniaudio')
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from beatsolver_timeline import BeatSolverWindow


def test_streaming_and_diagnostics_controls_exist_and_close():
    app=QApplication.instance() or QApplication([])
    win=BeatSolverWindow()
    try:
        assert win.stream_mode.isChecked() is False
        assert win.diag_btn.text()=='Diagnostics'
        win.diag_btn.click()
        assert win.diag_dock.isVisible() is True if win.isVisible() else win.diag_btn.isChecked()
        win._refresh_diagnostics()
        assert 'Paint' in win.diagnostics_panel.render.text()
    finally:
        win.close();app.processEvents()


def test_qt_sequential_io_device_returns_stereo_pcm():
    import numpy as np
    from PySide6.QtCore import QIODevice
    from audio_stream_qt import PCMIODevice
    from streaming_pcm import PCMBufferMixer
    app=QApplication.instance() or QApplication([])
    mixer=PCMBufferMixer(np.ones((40,2),dtype=np.float32)*.25,48000,[],1.,0.)
    io=PCMIODevice(mixer)
    try:
        assert io.isOpen() and io.isSequential()
        samples=bytes(io.read(64))
        assert len(samples)==64
        assert np.frombuffer(samples,dtype='<i2').tolist()==[8191]*32
    finally:
        io.close()
