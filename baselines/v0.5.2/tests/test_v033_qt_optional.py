"""Optional smoke test for the Qt controls, run on a GUI-capable Windows machine."""
import os
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

qt = pytest.importorskip('PySide6')
pytest.importorskip('miniaudio')
from PySide6.QtWidgets import QApplication
from beatsolver_timeline import BeatSolverWindow


def test_speed_and_radius_controls_keep_state():
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    app = QApplication.instance() or QApplication([])
    window = BeatSolverWindow()
    try:
        window.set_playback_speed(0.0)
        assert window.playback_speed_pct == 0.0
        window.set_playback_speed(150.0)
        assert window.playback_speed_pct == 135.0  # v0.5.2 clamps old out-of-range calls
        window.replace_spin.setValue(17.0)
        assert window.timeline.replace_radius_ms == 17.0
    finally:
        window.mark_clean()
        window.close()
