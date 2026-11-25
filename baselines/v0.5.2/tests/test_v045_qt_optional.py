"""Additional Qt regression coverage for independent PCM sync."""
from pathlib import Path
import sys, os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
pytest.importorskip('PySide6')
pytest.importorskip('miniaudio')
from PySide6.QtWidgets import QApplication
from beatsolver_timeline import BeatSolverWindow


def test_pcm_sync_can_play_without_continuous_clicks():
    app=QApplication.instance() or QApplication([])
    window=BeatSolverWindow()
    try:
        assert window.sample_clock_check.isChecked()
        assert not window.continuous_hits.isChecked()
        assert window._stream_requested()
        window.sample_clock_check.setChecked(False)
        assert not window._stream_requested()
        window.continuous_hits.setChecked(True)  # Audio not loaded; self-reverts.
        assert not window.continuous_hits.isChecked()
        window.stream_mode.setChecked(True)
        assert not window._stream_requested()
    finally:
        # Don't let an unsaved-project modal dialog hang the offscreen suite.
        window.mark_clean()
        window.close()
        app.processEvents()


def test_project_setting_is_saved_independently_of_hitsounds():
    app=QApplication.instance() or QApplication([])
    window=BeatSolverWindow()
    try:
        assert window.sample_clock_check.isChecked()
        window.sample_clock_check.setChecked(False)
        assert not window._stream_requested()
        assert 'candidates.burst_cleanup' in window.registry.defs
    finally:
        # Don't let an unsaved-project modal dialog hang the offscreen suite.
        window.mark_clean()
        window.close()
        app.processEvents()


def test_burst_cleanup_menu_preserves_original_branch_data():
    app=QApplication.instance() or QApplication([])
    window=BeatSolverWindow()
    try:
        selected=window.graph_panel.output_selector.currentData()
        # New sessions already use the spectral pipeline and Burst Cleanup.
        assert len(window.graph.nodes)==6
        assert sum(n.type_id=='candidates.burst_cleanup' for n in window.graph.nodes.values())==1
        window.insert_burst_cleanup()  # Already present: should be idempotent.
        assert len(window.graph.nodes)==6
        assert window.graph_panel.output_selector.currentData()==selected
        node=next(n for n in window.graph.nodes.values()
                  if n.type_id=='candidates.burst_cleanup')
        assert any(e.source==node.id and e.target==selected for e in window.graph.connections)
        assert any(e.target==node.id for e in window.graph.connections)
    finally:
        # Don't let an unsaved-project modal dialog hang the offscreen suite.
        window.mark_clean()
        window.close()
        app.processEvents()
