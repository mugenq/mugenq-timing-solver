"""Windows Qt regressions: latest graph request wins, explicit merged STFT viewing.

No generated files, audio playback or lasting background QThreads.
"""
import os
from pathlib import Path
import sys
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal, Qt
import numpy as np
import beatsolver_timeline as gui
from audio_resolution import WaveformCache
from test_v0410_graph_consistency import two_branch_graph,fixture_audio


class WorkerStub(QObject):
    finished=Signal()
    produced=Signal(object,object,object)
    failed=Signal(str)

    def __init__(self, *args):
        parent=args[-1] if args else None
        super().__init__(parent)
        self.running=False
        self.deleted=False

    def start(self):
        self.running=True

    def isRunning(self):
        return self.running

    def deleteLater(self):
        self.deleted=True


def window_with_audio():
    app=QApplication.instance() or QApplication([])
    win=gui.BeatSolverWindow()
    pcm=fixture_audio().pcm
    native=WaveformCache(48000,len(pcm),np.array([0.],np.float32),
                         np.array([0.],np.float32),len(pcm),pcm)
    win.timeline.set_audio(native.coarse_min,native.coarse_max,native.duration,native)
    return app,win


def pump(app):
    for _ in range(5):app.processEvents()


def test_deferred_output_cannot_override_newer_explicit_run(monkeypatch):
    app,win=window_with_audio()
    try:
        out_x=win.graph_panel.output_selector.currentData()
        old=WorkerStub(win)
        win.candidate_worker=old
        win._graph_request_generation=1
        win._queued_graph_output=out_x
        win._queued_graph_generation=1
        old.finished.connect(win.graph_thread_finished,Qt.ConnectionType.QueuedConnection)
        deferred=[]
        class CapturingTimer:
            @staticmethod
            def singleShot(delay, callback):
                assert delay == 0
                deferred.append(callback)
        monkeypatch.setattr(gui, 'QTimer', CapturingTimer)
        old.finished.emit()
        pump(app)
        assert win.candidate_worker is None
        assert len(deferred)==1
        monkeypatch.setattr(gui,'CandidateThread',WorkerStub)
        graph=win.graph
        source=next(e for e in graph.connections if e.target==out_x and e.input=='candidates')
        new_output=graph.add('candidates.output')
        graph.connect(source.source,source.output,new_output.id,'candidates')
        win.graph_panel.set_graph(graph)
        win.run_graph(new_output.id)
        assert win.candidate_worker is not None
        newest=win.candidate_worker
        assert win._queued_graph_output is None
        deferred.pop()()  # old X request fires after manual new_output
        assert win.candidate_worker is newest
        assert win._queued_graph_output is None
        assert win._graph_request_generation==2
        assert win._active_graph_output_id==new_output.id
    finally:
        win.candidate_worker=None
        win.mark_clean();win.close();pump(app)


@pytest.mark.parametrize('reverse',[False,True])
def test_merged_spectral_viewer_uses_selected_source_and_supports_switch(reverse, tmp_path):
    app,win=window_with_audio()
    try:
        graph,out,branches=two_branch_graph(reverse)
        win.graph=graph
        win.graph_panel.set_graph(graph)
        idx=win.graph_panel.output_selector.findData(out.id)
        win.graph_panel.output_selector.setCurrentIndex(idx)
        win._refresh_spectrogram_sources()
        assert win.spectrogram_source_combo.count()==3  # auto, legacy, exact
        song=fixture_audio()
        result,metrics,intermediates=graph.run_with_intermediates(song,out.id)
        win._graph_request_generation=1
        win._active_graph_generation=1
        win._active_graph_snapshot=graph
        win._active_graph_output_id=out.id
        win.graph_produced(result,metrics,intermediates)
        legacy=intermediates[branches[0][0].id]['spectrogram'].features
        exact=intermediates[branches[1][0].id]['spectrogram'].features
        assert win.spectrogram_viewer.features is legacy
        assert win.timeline.audio_cache.spectral_features is legacy
        win.spectrogram_source_combo.setCurrentIndex(win.spectrogram_source_combo.findData(branches[1][0].id))
        assert win.spectrogram_viewer.features is exact
        assert win.timeline.audio_cache.spectral_features is exact
        # Optional source selection is persisted without modifying the graph schema.
        import json
        win.file_path = str(tmp_path/'source.wav')
        win.session_path = str(tmp_path/'chosen.beatsolver.json')
        assert win.save_project()
        saved = json.loads((tmp_path/'chosen.beatsolver.json').read_text(encoding='utf-8'))
        assert saved['spectrogram_source_id'] == branches[1][0].id
        # Switching evidence only changes visualization. Candidate timestamps stay identical.
        np.testing.assert_array_equal(win.candidate_output.times,result.times)
        win.spectrogram_source_combo.setCurrentIndex(win.spectrogram_source_combo.findData(branches[0][0].id))
        assert win.spectrogram_viewer.features is legacy
    finally:
        win.candidate_worker=None
        win.mark_clean();win.close();pump(app)
