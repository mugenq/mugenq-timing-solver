"""v0.4.3 regression coverage for Windows-only bugs found by Codex QA.

Run using the REAL Qt event loop for final audio-device verification. These
unit-level tests check widgets and transitions without touching external audio.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
pytest.importorskip('PySide6')
pytest.importorskip('miniaudio')

import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QAudio
from beatsolver_timeline import BeatSolverWindow
from audio_stream_qt import AudioStreamController
from streaming_pcm import PCMBufferMixer


def test_controls_are_tabbed_and_diagnostics_raises():
    app = QApplication.instance() or QApplication([])
    window = BeatSolverWindow()
    try:
        window.resize(1250, 810)
        window.show()
        app.processEvents()
        assert window.controls_tabs.count() == 5
        assert [window.controls_tabs.tabText(i) for i in range(5)] == [
            'Playback', 'Analysis', 'Candidates', 'Current / BPM', 'Audio']
        assert window.transport_scroll.parent() is not window.centralWidget()
        assert window.diag_dock.isVisible() is False
        window.diag_btn.click()
        app.processEvents()
        assert window.diag_btn.isChecked()
        assert window.diag_dock.isVisible()
        assert not window.diag_dock.isHidden()
        window.layers_btn.click() if not window.layers_btn.isChecked() else None
        app.processEvents()
        window.diag_btn.setChecked(False)
        app.processEvents()
        window.diag_btn.setChecked(True)
        app.processEvents()
        assert window.diag_dock.isVisible()
        assert window.diag_btn.isChecked()
        window.controls_tabs.setCurrentIndex(4)
        app.processEvents()
        assert window.mixer_scroll.isVisible()
    finally:
        window.close()
        app.processEvents()


def test_audio_eof_stops_and_replays_without_physical_device(monkeypatch):
    app = QApplication.instance() or QApplication([])
    controller = AudioStreamController()
    controller.mixer = PCMBufferMixer(np.zeros((2400, 2), dtype=np.float32), 48000)
    controller._rate = 1.0
    finished = []
    controller.finished.connect(lambda: finished.append(True))

    # Simulate the same IdleState observed by Windows QA after the mixer
    # consumes its last frame. No global device/audio settings are modified.
    controller.mixer.seek(controller.mixer.duration)
    controller.playing = True
    monkeypatch.setattr(controller, '_release_sink',
                        lambda: setattr(controller, 'playing', False))
    controller._on_sink_state(QAudio.State.IdleState)
    assert not controller.playing
    assert controller.position() == pytest.approx(controller.mixer.duration)
    assert finished == [True]

    restarted = []
    monkeypatch.setattr(controller, '_start_sink', lambda: restarted.append(controller._paused))
    controller.play()
    assert restarted == [0.0]
    assert controller.mixer.seconds == pytest.approx(0.0)


def test_cached_stream_activation_error_runs_fallback(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = BeatSolverWindow()
    try:
        window.file_path = 'qa_nonexistent_song.wav'
        window._stream_cached_path = window.file_path
        window._stream_controller.mixer = PCMBufferMixer(
            np.zeros((200, 2), dtype=np.float32), 48000)
        window.continuous_hits.blockSignals(True)
        window.continuous_hits.setChecked(True)
        window.continuous_hits.blockSignals(False)
        window.stream_mode.blockSignals(True)
        window.stream_mode.setChecked(True)
        window.stream_mode.blockSignals(False)
        def fail_start():
            raise RuntimeError('Injected device startup failure')
        monkeypatch.setattr(window, '_activate_stream', fail_start)
        window._begin_stream_decode()
        assert not window.stream_mode.isChecked()
        assert not window._stream_active
        assert 'fallback' in window.mix_status.text().lower()
    finally:
        # Keep cleanup isolated from the nonexistent test audio path.
        window.file_path = None
        window.close()
        app.processEvents()
