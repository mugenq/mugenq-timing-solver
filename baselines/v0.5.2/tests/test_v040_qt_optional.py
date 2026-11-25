"""Optional integration smoke, enabled when PySide6 is installed (e.g. Windows)."""
import os
import sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
qt = pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from beatsolver_timeline import BeatSolverWindow
from timing_workspace import BPMRange, WorkingCandidates


def test_window_has_workspace_guides_audition_and_high_frequency_cursor():
    app=QApplication.instance() or QApplication([])
    win=BeatSolverWindow()
    try:
        assert win.cursor_timer.timerType() == qt.QtCore.Qt.TimerType.PreciseTimer
        assert win.cursor_timer.interval() == 4
        assert win.audition_source.currentData() == 'preview'
        assert win.working.times == []
        win.guides.add(BPMRange(1,4,180,4,4,3))
        win.timeline.set_guides(win.guides)
        win.working.paste([1,1.333333333],replace=True)
        win.timeline.set_working(win.working)
        assert len(win.timeline.draw_times) == 2
        win.cursor_fps_combo.setCurrentIndex(win.cursor_fps_combo.findData(120))
        assert win.cursor_timer.interval() == 8
    finally:
        # Do not depend on opening an actual audio device for this smoke test.
        win.close()
        app.processEvents()
