"""Optional real Qt regressions; run on Windows via the parent qa/ runner."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
qt=pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from beatsolver_timeline import BeatSolverWindow


def test_zero_speed_audition_never_creates_unused_wav(tmp_path):
    app=QApplication.instance() or QApplication([])
    win=BeatSolverWindow()
    try:
        # The precondition is enforced before decoding, generating or writing.
        win.playback_speed_pct=0
        win.audition_at(3,'current')
        win.audition_at(3,'preview')
        assert win._audition_file is None
        assert not list(Path(win._audition_temp.name).glob('*.wav'))
    finally:
        win.close(); app.processEvents()


def test_hierarchy_controls_update_preview_not_current():
    app=QApplication.instance() or QApplication([])
    win=BeatSolverWindow()
    try:
        import numpy as np
        from node_engine import CandidateSet
        win.timeline.set_candidates(CandidateSet(np.array([1.,1.003,2.]),np.array([.9,.1,.5])))
        win.working.paste([1.003],replace=True)
        win.timeline.set_working(win.working)
        win.priority_radius_spin.setValue(10)
        assert len(win.timeline.preview_times)==2
        assert win.working.times==[1.003]
        assert any(k==3 for k in win.timeline.draw_kinds)
    finally:
        win.close(); app.processEvents()
