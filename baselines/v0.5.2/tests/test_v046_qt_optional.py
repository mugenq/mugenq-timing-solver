"""Windows/offscreen Qt smoke: feature controls, graph insertion and rendering."""
from pathlib import Path
import os
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
qt=pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from beatsolver_timeline import BeatSolverWindow
from spectral_analysis import compute_spectral
import numpy as np


def test_spectrogram_control_and_graph_insertion_without_audio():
    app=QApplication.instance() or QApplication([])
    win=BeatSolverWindow()
    try:
        assert win.spectrogram_check.isChecked() is True
        assert not win.spectrogram_viewer.isHidden()
        win.add_spectral_branch()
        assert sum(n.type_id=='signal.spectral_attack' for n in win.graph.nodes.values())==1
        assert sum(n.type_id=='candidates.output' for n in win.graph.nodes.values())==2
        win.insert_evidence_cleanup()
        assert sum(n.type_id=='candidates.evidence_cleanup' for n in win.graph.nodes.values())==1
        out=win.graph_panel.output_selector.currentData()
        assert any(e.target==out and win.graph.nodes[e.source].type_id=='candidates.evidence_cleanup'
                   for e in win.graph.connections)
        win.spectrogram_check.setChecked(True)
        assert not win.spectrogram_viewer.isHidden()
        win.spectrogram_mode_combo.setCurrentIndex(1)
        assert win.spectrogram_viewer.mode=='harmonic'
        win.session_dirty=False
    finally:
        win.session_dirty=False
        win.close()
        app.processEvents()


def test_spectrogram_viewer_offscreen_paint():
    app=QApplication.instance() or QApplication([])
    win=BeatSolverWindow()
    try:
        # Present synthesized data without writing test files.
        from audio_resolution import WaveformCache, exact_envelope
        rate=16000
        samples=(.2*np.sin(2*np.pi*440*np.arange(16000)/rate)).astype(np.float32)
        lo,hi=exact_envelope(samples,32)
        wave=WaveformCache(rate,len(samples),lo,hi,32,samples)
        win.timeline.set_audio(lo,hi,1.,wave)
        data=compute_spectral(samples,rate,hop_ms=8,window_ms=32)
        win.spectrogram_viewer.set_spectrum(data)
        win.spectrogram_viewer.set_colors(win.colors)
        win.spectrogram_check.setChecked(True)
        win.spectrogram_viewer.resize(800,125)
        img=QImage(800,125,QImage.Format.Format_ARGB32)
        win.spectrogram_viewer.render(img)
        assert not img.isNull()
        assert win.spectrogram_viewer._image is not None
        win.session_dirty=False
    finally:
        win.session_dirty=False
        win.close()
        app.processEvents()
