"""Native GUI startup defaults and deterministic stale-viewer race regression."""
from pathlib import Path
import os, sys
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from beatsolver_timeline import BeatSolverWindow
from audio_resolution import WaveformCache, exact_envelope
from spectral_analysis import compute_spectral
import numpy as np


def test_new_window_defaults_to_visible_spectrum_and_cleaned_spectral_pipeline():
    app=QApplication.instance() or QApplication([])
    win=BeatSolverWindow()
    try:
        assert win.spectrogram_check.isChecked() and win.spectrogram_enabled
        assert not win.spectrogram_viewer.isHidden()
        assert sum(n.type_id=='signal.spectrogram' for n in win.graph.nodes.values())==1
        assert sum(n.type_id=='candidates.burst_cleanup' for n in win.graph.nodes.values())==1
        output=win.graph_panel.output_selector.currentData()
        assert win._selected_graph_spectrum(win.graph,output,48000)==('legacy_fft',46.,288)
        assert not win.session_dirty
        win.add_typed_spectral_branch()
        selected=win.graph_panel.output_selector.currentData()
        assert win._selected_graph_spectrum(win.graph,selected,48000)==('exact',46.,288)
        win.mark_clean()
    finally:
        win.mark_clean()
        win.close()
        app.processEvents()


def test_late_legacy_viewer_result_cannot_replace_selected_exact_spectrum():
    app=QApplication.instance() or QApplication([])
    win=BeatSolverWindow()
    try:
        rate=48000
        pcm=np.sin(2*np.pi*440*np.arange(rate//5)/rate).astype(np.float32)
        low,high=exact_envelope(pcm,96)
        cache=WaveformCache(rate,len(pcm),low,high,96,pcm)
        win.timeline.set_audio(low,high,len(pcm)/rate,cache)
        exact=compute_spectral(pcm,rate,window_ms=46,window_mode='exact')
        legacy=compute_spectral(pcm,rate,window_ms=46,window_mode='legacy_fft')
        win._selected_spectrum_request=win._spectral_signature(exact)
        cache.spectral_features=exact
        win.spectrogram_viewer.set_spectrum(exact)
        win._spectrogram_ready(cache,legacy)  # Late signal from previously started QThread
        assert win.spectrogram_viewer.features is exact
        assert cache.spectral_features is exact
        # Acceptance is still possible if the signal has the selected signature.
        win._spectrogram_ready(cache,exact)
        assert win.spectrogram_viewer.features is exact
        win.mark_clean()
    finally:
        win.mark_clean()
        win.close()
        app.processEvents()
