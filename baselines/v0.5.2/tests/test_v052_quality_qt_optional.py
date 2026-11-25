"""Native Qt regressions for v0.5.2. Requires PySide6 on Windows."""
import os,sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from beatsolver_timeline import BeatSolverWindow
from node_engine import AudioData
from diagnostic_curves import diagnostic_energy_curves, DIAGNOSTIC_CHANGE_ID, DIAGNOSTIC_WAVE_ID
import numpy as np


def test_transport_speed_precision_without_audio():
    app=QApplication.instance() or QApplication([])
    win=BeatSolverWindow()
    try:
        assert win.speed_spin.maximum()==135.
        assert win.speed_spin.singleStep()==.1
        assert win.speed_spin.decimals()==1
        assert win.speed_slider.maximum()==1350
        win.speed_slider.setValue(1003)
        assert win.speed_spin.value()==100.3
        win.speed_spin.setValue(125.7)
        assert win.speed_slider.value()==1257
        win.speed_spin.setValue(135.)
        assert win.speed_slider.value()==1350
        win.set_playback_speed(500.)
        assert win.playback_speed_pct==135.
        assert win.speed_spin.value()==135.
        win.speed_spin.setValue(0.0)
        assert win.playback_speed_pct==0.
    finally:
        win.mark_clean();win.close();app.processEvents()


def test_energy_reference_inspector_and_spectral_detail_graph():
    app=QApplication.instance() or QApplication([])
    win=BeatSolverWindow()
    try:
        audio=AudioData('x.wav',1.,np.zeros(500,np.float32),
                        np.r_[np.zeros(100),np.ones(5),np.zeros(395)].astype(np.float32),
                        bin_seconds=.002)
        outputs, labels=diagnostic_energy_curves(audio)
        win.layer_inspector.set_data(outputs, labels)
        assert f'{DIAGNOSTIC_WAVE_ID}:curve' in win.layer_inspector.curves
        assert f'{DIAGNOSTIC_CHANGE_ID}:curve' in win.layer_inspector.curves
        win.layer_inspector.set_visible(f'{DIAGNOSTIC_CHANGE_ID}:curve',False)
        assert not win.layer_inspector.styles[f'{DIAGNOSTIC_CHANGE_ID}:curve']['visible']
        win.layer_inspector.set_visible(f'{DIAGNOSTIC_CHANGE_ID}:curve',True)
        assert win.layer_inspector.styles[f'{DIAGNOSTIC_CHANGE_ID}:curve']['visible']
        node=next(n for n in win.graph.nodes.values() if n.type_id=='signal.spectrogram')
        assert win.spectrogram_detail_combo.currentData()==1
        index=win.spectrogram_detail_combo.findData(4)
        win.spectrogram_detail_combo.setCurrentIndex(index)
        assert node.params['hop_ms']==1.5
        # No song is loaded, so no background graph worker should start.
        assert win.candidate_worker is None
        assert win.spectrogram_detail_combo.currentData()==4
        win.spectrogram_detail_combo.setCurrentIndex(win.spectrogram_detail_combo.findData(1))
        assert node.params['hop_ms']==6.0
    finally:
        win.mark_clean();win.close();app.processEvents()
