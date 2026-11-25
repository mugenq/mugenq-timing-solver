"""Qt regressions for queued late worker completions (Windows P1 in v0.4.8).

Run inside the app's versioned Windows QA harness; does not open audio,
write projects or start long-lived QThreads.
"""
import os
from pathlib import Path
import sys
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication
from beatsolver_timeline import BeatSolverWindow
from audio_resolution import WaveformCache
from node_engine import CandidateSet
import numpy as np


class FinishedEmitter(QObject):
    finished = Signal()
    produced = Signal(object,object,object)
    failed = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.deletion_requested = False
        self.running = False

    def isRunning(self):
        return self.running

    def deleteLater(self):
        self.deletion_requested = True
        # Deliberately keep alive to inspect state; these are safe stand-ins.


def _new_window():
    app=QApplication.instance() or QApplication([])
    return app, BeatSolverWindow()


def _drain(app):
    for _ in range(5):
        app.processEvents()


def test_old_graph_finished_event_cannot_destroy_new_active_worker():
    app,win=_new_window()
    try:
        old=FinishedEmitter(win);current=FinishedEmitter(win)
        current.running=True
        win.candidate_worker=current
        win.graph_panel.set_busy(True)
        old.finished.connect(win.graph_thread_finished,Qt.ConnectionType.QueuedConnection)
        old.finished.emit()
        _drain(app)
        assert old.deletion_requested, 'Old worker must be retired'
        assert win.candidate_worker is current
        assert not current.deletion_requested
        assert not win.graph_panel.run_btn.isEnabled(), 'New run must stay busy'
    finally:
        win.candidate_worker=None;win.mark_clean();win.close();_drain(app)


def test_completed_graph_worker_stays_owned_until_queued_finished_and_rerun_is_coalesced():
    app,win=_new_window()
    try:
        pcm=np.zeros(4800,np.float32)
        cache=WaveformCache(48000,len(pcm),np.array([0.],np.float32),
                           np.array([0.],np.float32),4800,pcm)
        win.timeline.set_audio(cache.coarse_min,cache.coarse_max,cache.duration,cache)
        old=FinishedEmitter(win);old.running=False
        win.candidate_worker=old
        win.graph_panel.set_busy(True)
        out_id=win.graph_panel.output_selector.currentData()
        win.run_graph(out_id)  # Must not start a new QThread yet.
        assert win._queued_graph_output==out_id
        assert win.candidate_worker is old
        assert not old.deletion_requested
        started=[]
        # The deferred callback is expected to reach run_graph only after
        # the completion cleared the reference. Avoid starting actual work.
        win.run_graph=lambda output=None, **kwargs:started.append((output,win.candidate_worker))
        old.finished.connect(win.graph_thread_finished,Qt.ConnectionType.QueuedConnection)
        old.finished.emit();_drain(app)
        assert old.deletion_requested
        assert win.candidate_worker is None
        assert win._queued_graph_output is None
        assert started==[(out_id,None)]
    finally:
        win.candidate_worker=None;win.mark_clean();win.close();_drain(app)


def test_stale_produced_signal_does_not_replace_new_preview():
    app,win=_new_window()
    try:
        old=FinishedEmitter(win);current=FinishedEmitter(win)
        win.candidate_worker=current
        original=win.candidate_output
        old.produced.connect(win.graph_produced,Qt.ConnectionType.QueuedConnection)
        bogus=CandidateSet(np.array([12.],np.float64),np.array([.8],np.float32))
        old.produced.emit(bogus,[],{});_drain(app)
        assert win.candidate_output is original
    finally:
        win.candidate_worker=None;win.mark_clean();win.close();_drain(app)


def test_old_spectrogram_completion_keeps_new_worker():
    app,win=_new_window()
    try:
        old=FinishedEmitter(win);current=FinishedEmitter(win)
        current.running=True
        win._spectrogram_worker=current
        old.finished.connect(win._spectrogram_finished,Qt.ConnectionType.QueuedConnection)
        old.finished.emit();_drain(app)
        assert old.deletion_requested
        assert win._spectrogram_worker is current
        assert not current.deletion_requested
    finally:
        win._spectrogram_worker=None;win.mark_clean();win.close();_drain(app)


def test_close_with_completed_worker_pending_cleanup_cancels_queued_rerun():
    app,win=_new_window()
    try:
        old=FinishedEmitter(win);old.running=False
        win.candidate_worker=old
        win._queued_graph_output='discard-after-close'
        win._queued_graph_generation=1
        win.mark_clean()
        assert win.close(), 'A completed thread should never block clean shutdown'
        assert win._closing
        assert win.candidate_worker is None
        assert win._queued_graph_output is None
        assert old.deletion_requested
    finally:
        win.candidate_worker=None;win.mark_clean();win.close();_drain(app)
