"""Windows Qt integration checks for v0.4.7 typed spectral branch and UI."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
pytest.importorskip('PySide6')
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
from beatsolver_timeline import BeatSolverWindow
from node_engine import PORT_COLORS, SPECTROGRAM, Graph


def test_typed_spectrogram_branch_visible_and_compatible_with_old_projects():
    app = QApplication.instance() or QApplication([])
    win = BeatSolverWindow()
    try:
        assert QColor(PORT_COLORS[SPECTROGRAM]).isValid()
        old = win.graph.to_dict()
        win.add_typed_spectral_branch()
        assert sum(n.type_id=='signal.spectrogram' for n in win.graph.nodes.values())==2
        assert sum(n.type_id=='signal.spectral_novelty' for n in win.graph.nodes.values())==2
        assert win.graph_panel.output_selector.currentData() is not None
        clone = Graph.from_dict(win.registry,win.graph.to_dict())
        assert len(clone.nodes)==len(win.graph.nodes)
        assert sum(n['type']=='signal.spectrogram' for n in old['nodes'])==1
        assert sum(n.type_id=='audio.source' for n in clone.nodes.values())==1
        win.session_dirty=False
    finally:
        win.session_dirty=False
        win.close()
        app.processEvents()


def test_viewer_shows_effective_fft_metadata_in_curve_inspector():
    from node_engine import Curve
    import numpy as np
    from timeline_layers import LayerInspector
    app=QApplication.instance() or QApplication([])
    inspector=LayerInspector()
    try:
        curve=Curve(np.array([.1,.8,.2],np.float32),2.,sample_step_s=.006,
                    metadata=dict(requested_window_ms=46.,actual_window_ms=85.3333,
                                  window_samples=4096,fft_samples=4096,
                                  window_mode='legacy_fft'))
        inspector.set_data({'node':{'curve':curve}},{'node:curve':'Spectral Novelty'})
        for check in inspector.scroll_host.findChildren(__import__('PySide6').QtWidgets.QCheckBox):
            if check.text()=='Spectral Novelty':
                assert '85.333' in check.toolTip()
                assert 'requested 46.000' in check.toolTip()
                break
        else:
            pytest.fail('Missing spectral node curve control')
    finally:
        inspector.close()
        app.processEvents()
