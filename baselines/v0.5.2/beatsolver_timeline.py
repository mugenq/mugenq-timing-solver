"""BeatSolver Timeline v0.5.0 - osu! references, evaluation and safe onset export.

Requirements: PySide6, numpy, miniaudio.
The graph uses envelope-derived prototype features; no claim of perfect beat detection.
"""

from __future__ import annotations

import json
import csv
import tempfile
import math
import os
import sys
from pathlib import Path

import miniaudio
import numpy as np
from PySide6.QtCore import QPointF, QSettings, QThread, Qt, QUrl, Signal, Slot, QTimer, QEvent
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPainterPath, QPen, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDockWidget,
    QFrame,
    QScrollArea,
    QColorDialog,
    QFileDialog,
    QInputDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QSlider,
    QDoubleSpinBox,
    QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox,
    QCheckBox,
    QVBoxLayout,
    QWidget,
)

from node_engine import (AudioData, CandidateSet, SpectrogramData, Graph, GraphError, default_registry, starter_graph, default_spectral_graph)
from diagnostic_curves import diagnostic_energy_curves, DIAGNOSTIC_WAVE_ID, DIAGNOSTIC_CHANGE_ID
from speed_controls import (MAX_SPEED_PERCENT, SLIDER_UNITS_PER_PERCENT,
                            clamp_speed_percent, speed_to_slider, slider_to_speed)
from annotations import Annotations
from merge_logic import nearby_candidate
from node_graph import GraphPanel
from timeline_layers import LayerInspector, CurveViewer
from spectrogram_viewer import SpectrogramViewer
from spectral_analysis import shared_spectral
from graph_consistency import (choose_spectrum, contributing_spectra, deferred_run_is_current)
from audio_resolution import (WaveformCache, PCM_CACHE_LIMIT_BYTES,
                              stream_peaks_and_pcm)
from playback_clock import PlaybackClock, calibrated_visual_position
from audition_audio import audible_excerpt
from timing_workspace import TimingGuides, WorkingCandidates, BPMRange
from timing_ui import BPMRangesDialog
from candidate_priority import rank_candidates, prune_candidates
from live_mix import click_events, render_file
from timing_diagnostics import TimingMonitor
from timing_ui_diag import DiagnosticsPanel
from audio_stream_qt import AudioStreamController, StereoDecodeThread
from streaming_pcm import analysis_and_playback_share_timebase
from osu_io import (OsuReference, Comparison, parse_osu, match_onsets,
                    export_comparison_csv, export_onset_map)

APP_NAME = "BeatSolver Timeline"
APP_VERSION = "0.5.2"
SUPPORTED_AUDIO = {".mp3", ".wav", ".flac", ".ogg"}
PEAK_SECONDS = 0.002  # Coarse envelope fallback; native PCM is optionally retained.

THEMES: dict[str, dict[str, str]] = {
    "Deep Ocean": {
        "background": "#111923", "panel": "#172330", "waveform": "#75c6d9",
        "grid": "#314152", "text": "#dce8f3", "muted": "#90a4b8",
        "playhead": "#ffca79", "border": "#385063",
        "candidate": "#f1a454", "current": "#f663c4", "manual": "#63d6ee", "confirmed": "#60ca93", "exclusion": "#b55d6e", "ref_hit": "#a5e9fb", "ref_red": "#ff7373", "ref_green": "#a3d98e",
    },
    "Violet Night": {
        "background": "#191522", "panel": "#261e33", "waveform": "#c39df3",
        "grid": "#4a3b5c", "text": "#f0e7fa", "muted": "#b7a6c9",
        "playhead": "#ffbd79", "border": "#675179",
        "candidate": "#f0aa62", "current": "#ff70c9", "manual": "#87cfe8", "confirmed": "#8bdeae", "exclusion": "#d56c85", "ref_hit": "#a5e9fb", "ref_red": "#f48e8e", "ref_green": "#a2dfad",
    },
    "Pale Studio": {
        "background": "#f4f6f9", "panel": "#e6edf4", "waveform": "#237c99",
        "grid": "#bacbd8", "text": "#203346", "muted": "#64798d",
        "playhead": "#dd7840", "border": "#bacbd8",
        "candidate": "#c78629", "current": "#bf1e83", "manual": "#087db0", "confirmed": "#148951", "exclusion": "#d76b7b", "ref_hit": "#13769d", "ref_red": "#bb4040", "ref_green": "#16834f",
    },
}

COLOR_LABELS = {
    "background": "Timeline background",
    "panel": "Toolbar / panels",
    "waveform": "Waveform",
    "grid": "Time grid",
    "text": "Primary text",
    "muted": "Secondary text",
    "playhead": "Playhead",
    "border": "Borders",
    "candidate": "Computed candidates",
    "current": "Committed candidate output",
    "manual": "Manually added candidates",
    "confirmed": "Confirmed candidates",
    "exclusion": "Exclusion regions",
    "ref_hit": "osu! mapped hit-object heads",
    "ref_red": "osu! red BPM timing points",
    "ref_green": "osu! green inherited timing points",
}


def pretty_time(seconds: float, decimals: int = 2) -> str:
    """Format seconds as MM:SS.xx or HH:MM:SS.xx."""
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    sec = seconds % 60
    width = 3 + decimals if decimals else 2
    if hrs:
        return f"{hrs:02d}:{mins:02d}:{sec:0{width}.{decimals}f}"
    return f"{mins:02d}:{sec:0{width}.{decimals}f}"


def nice_tick(raw_seconds: float) -> float:
    """Return a readable 1/2/5/10-style time-grid interval."""
    if raw_seconds <= 0:
        return 1.0
    scale = 10 ** math.floor(math.log10(raw_seconds))
    for multiplier in (1, 2, 5, 10):
        tick = multiplier * scale
        if tick >= raw_seconds:
            return tick
    return 10 * scale


def build_peaks(chunks, bin_size: int, cancelled=lambda: False):
    """Streaming min/max envelope with correct bin alignment between chunks.

    Returns (minimums, maximums, total_frames). Keeps only the peak envelope in
    memory, so it needn't allocate an entire decoded multi-minute audio file.
    """
    if bin_size < 1:
        raise ValueError("bin_size must be >= 1")
    mins: list[np.ndarray] = []
    maxs: list[np.ndarray] = []
    carry = np.empty(0, dtype=np.float32)
    total = 0
    for chunk in chunks:
        if cancelled():
            raise InterruptedError("Audio decoding interrupted")
        values = np.asarray(chunk, dtype=np.float32)
        if values.size == 0:
            continue
        total += values.size
        if carry.size:
            values = np.concatenate((carry, values))
        count = values.size // bin_size
        if count:
            section = values[: count * bin_size].reshape(count, bin_size)
            mins.append(section.min(axis=1))
            maxs.append(section.max(axis=1))
        carry = values[count * bin_size :].copy()
    if carry.size:
        mins.append(np.array([carry.min()], dtype=np.float32))
        maxs.append(np.array([carry.max()], dtype=np.float32))
    if not mins:
        raise ValueError("The audio file contains no samples.")
    return np.concatenate(mins), np.concatenate(maxs), total


class DecodeThread(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, filename: str, parent=None):
        super().__init__(parent)
        self.filename = filename

    def run(self):
        try:
            # Decode at the file's reported rate instead of the previous fixed
            # 22,050 Hz. This preserves native-rate mono PCM for visual detail.
            # Some unicode paths aren't supported by native get_file_info on
            # Windows; use 44.1 kHz as the known resampling target if so.
            try:
                sample_rate = int(miniaudio.get_file_info(self.filename).sample_rate)
            except Exception:
                sample_rate = 44100
            if sample_rate < 8000 or sample_rate > 192000:
                sample_rate = 44100
            options = dict(output_format=miniaudio.SampleFormat.FLOAT32,
                           nchannels=1, sample_rate=sample_rate,
                           frames_to_read=32768)
            if os.name == "nt" and not self.filename.isascii():
                stream = miniaudio.stream_memory(Path(self.filename).read_bytes(), **options)
            else:
                stream = miniaudio.stream_file(self.filename, **options)
            coarse_bin = max(1, round(sample_rate * PEAK_SECONDS))
            low, high, frame_count, raw = stream_peaks_and_pcm(
                stream, coarse_bin, max_pcm_bytes=PCM_CACHE_LIMIT_BYTES,
                cancelled=self.isInterruptionRequested)
            cache = WaveformCache(sample_rate, frame_count, low, high, coarse_bin, raw)
            if not self.isInterruptionRequested():
                self.loaded.emit(cache)
        except InterruptedError:
            pass
        except Exception as exc:
            self.failed.emit(f"Could not decode this audio file:\n\n{exc}")


class CandidateThread(QThread):
    produced = Signal(object, object, object)
    failed = Signal(str)

    def __init__(self, graph, audio, output_id, parent=None):
        super().__init__(parent)
        self.graph, self.audio, self.output_id = graph, audio, output_id

    def run(self):
        try:
            result, metrics, intermediate = self.graph.run_with_intermediates(self.audio, self.output_id)
            self.produced.emit(result, metrics, intermediate)
        except Exception as exc:
            self.failed.emit(str(exc))


class SpectrogramThread(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, cache, parent=None):
        super().__init__(parent)
        self.cache=cache

    def run(self):
        try:
            if self.cache.pcm is None:
                raise ValueError('Native PCM unavailable (song exceeds 192 MiB cache).')
            data = shared_spectral(self.cache.pcm, self.cache.rate,
                                   cache=self.cache.spectral_store,
                                   hop_ms=6., window_ms=46., window_mode='legacy_fft',
                                   cancelled=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.ready.emit(data)
        except InterruptedError:
            pass
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))


class ContinuousMixThread(QThread):
    """Build a sample-aligned song + clicks file without blocking the Qt thread."""
    ready = Signal(str)
    error = Signal(str)

    def __init__(self, source, destination, events, music_gain, click_gain, parent=None):
        super().__init__(parent)
        self.source, self.destination = source, destination
        self.events = events
        self.music_gain, self.click_gain = music_gain, click_gain

    def run(self):
        try:
            render_file(self.source, self.destination, self.events,
                        music_gain=self.music_gain, click_gain=self.click_gain,
                        cancelled=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.ready.emit(self.destination)
            else:
                Path(self.destination).unlink(missing_ok=True)
        except InterruptedError:
            Path(self.destination).unlink(missing_ok=True)
        except Exception as exc:
            Path(self.destination).unlink(missing_ok=True)
            self.error.emit(str(exc))


class Timeline(QWidget):
    candidateEdited = Signal(str, float, float)
    candidateReplaced = Signal(str, float, float, str, float)  # source kind/time, destination, target kind/time
    fileDropped = Signal(str)
    timeClicked = Signal(float)
    viewportChanged = Signal()
    manualNavigation = Signal()  # User chose to explore a different part of the audio.
    hoverTimeChanged = Signal(float)
    auditionRequested = Signal(float, str)

    LEFT = 74
    RIGHT = 17
    RULER = 41

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.colors = THEMES["Deep Ocean"].copy()
        self.low = None
        self.high = None
        self.audio_cache = None
        self.duration = 0.0
        self.view_start = 0.0
        self.view_span = 1.0
        self.playhead = 0.0
        self.global_offset_ms = 0.0
        self.max_level = 1.0
        self.drop_hover = False
        self.pointer_x = -1
        self.edit_mode = 'pan'  # Navigate/seek; middle button always pans.
        self._middle_press_x = None
        self._middle_start = 0.0
        self._middle_detached = False
        self._seek_press_x = None
        self._drag_candidate = None  # (original timestamp, candidate kind)
        self._drag_press_x = None
        self._drag_time = None
        self._drag_moved = False
        self._drag_detached = False
        self.replace_radius_ms = 12.0
        self._merge_preview = None
        self.raw_candidates = CandidateSet(np.array([],dtype=np.float64), np.array([],dtype=np.float32))
        self.priority_threshold = 0.0
        self.priority_radius_ms = 0.0
        self.priority_result = None
        self.preview_scores = np.array([], dtype=np.float32)
        self.ghost_times = np.array([], dtype=np.float64)
        self.annotations = Annotations()
        self.working = WorkingCandidates()
        self.guides = TimingGuides()
        self.show_preview = True
        self.show_working = True
        self.preview_times = np.array([], dtype=np.float64)
        self.draw_times = np.array([],dtype=np.float64)
        self.draw_kinds = np.array([],dtype=np.uint8)
        self._region_start = None
        self._region_end = None
        self.osu_reference = None
        self.ref_hit_times = np.empty(0, dtype=np.float64)
        self.ref_red_times = np.empty(0, dtype=np.float64)
        self.ref_green_times = np.empty(0, dtype=np.float64)
        self.show_ref_hits = True
        self.show_ref_timing = True

    @property
    def has_audio(self) -> bool:
        return self.low is not None and self.duration > 0

    def set_osu_reference(self, reference: OsuReference | None):
        self.osu_reference = reference
        self.ref_hit_times = np.asarray(reference.hitobject_times if reference else (), dtype=np.float64)
        self.ref_red_times = np.asarray(sorted(reference.red_times) if reference else (), dtype=np.float64)
        self.ref_green_times = np.asarray(sorted(reference.green_times) if reference else (), dtype=np.float64)
        self.update()

    def set_reference_visibility(self, *, hits=None, timing=None):
        if hits is not None:
            self.show_ref_hits = bool(hits)
        if timing is not None:
            self.show_ref_timing = bool(timing)
        self.update()

    def set_colors(self, colors: dict[str, str]):
        self.colors = colors.copy()
        self.update()

    def set_annotations(self, annotations: Annotations):
        self.annotations = annotations
        self.refresh_candidates()

    def set_candidates(self, candidates: CandidateSet):
        self.raw_candidates = candidates
        self.refresh_candidates()

    def set_priority(self, threshold: float, radius_ms: float):
        self.priority_threshold = float(threshold)
        self.priority_radius_ms = float(radius_ms)
        self.refresh_candidates()

    def refresh_candidates(self):
        self.priority_result = rank_candidates(
            self.raw_candidates, threshold=self.priority_threshold,
            duplicate_radius_ms=self.priority_radius_ms)
        visible_raw = prune_candidates(self.raw_candidates, self.priority_result)
        ghost_raw=self.raw_candidates.times[~self.priority_result.kept]
        ghost_display=ghost_raw+self.global_offset_ms/1000.0
        mask=ghost_display>=0
        for rejected in self.annotations.rejected:
            mask &= np.abs(ghost_raw-rejected)>0.00005
        for edit in (*self.annotations.added,*self.annotations.confirmed):
            mask &= np.abs(ghost_display-edit)>0.00005
        for a,b in self.annotations.excluded:
            mask &= ~((ghost_display>=a)&(ghost_display<=b))
        self.ghost_times=ghost_display[mask]
        self.preview_times, preview_kinds = self.annotations.display_candidates(
            visible_raw, self.global_offset_ms / 1000.0)
        # Manual and confirmed annotations always get full visibility.
        search_time = self.raw_candidates.times + self.global_offset_ms/1000.0
        orig = np.searchsorted(search_time, self.preview_times)
        if len(search_time):
            orig = np.clip(orig, 0, len(search_time)-1)
            scores = self.priority_result.confidence[orig]
        else:
            scores = np.ones(len(self.preview_times), dtype=np.float32)
        self.preview_scores = np.where(preview_kinds == 0, scores, 1.0)
        times = []
        kinds = []
        confidences = []
        if self.show_preview:
            times.append(self.preview_times)
            kinds.append(preview_kinds)
            confidences.append(self.preview_scores)
        if self.show_working and self.working.times:
            times.append(np.asarray(self.working.times, dtype=np.float64))
            kinds.append(np.full(len(self.working.times), 3, dtype=np.uint8))
            confidences.append(np.ones(len(self.working.times), dtype=np.float32))
        if times:
            self.draw_times = np.concatenate(times)
            self.draw_kinds = np.concatenate(kinds)
            order = np.argsort(self.draw_times, kind='stable')
            self.draw_times = self.draw_times[order]
            self.draw_kinds = self.draw_kinds[order]
            self.draw_confidence = np.concatenate(confidences)[order]
        else:
            self.draw_times = np.array([], dtype=np.float64)
            self.draw_kinds = np.array([], dtype=np.uint8)
            self.draw_confidence = np.array([], dtype=np.float32)
        self._merge_preview = None
        self.update()

    def set_working(self, working: WorkingCandidates):
        self.working = working
        self.refresh_candidates()

    def set_guides(self, guides: TimingGuides):
        self.guides = guides
        self.update()

    def set_visibility(self, *, preview=None, working=None):
        if preview is not None:
            self.show_preview = bool(preview)
        if working is not None:
            self.show_working = bool(working)
        self.refresh_candidates()

    def set_global_offset(self, milliseconds: float):
        self.global_offset_ms = float(milliseconds)
        self.refresh_candidates()

    def set_replace_radius(self, milliseconds: float):
        """Merge radius is in *time*, not pixels: zoom never changes the rule."""
        self.replace_radius_ms = max(0.0, float(milliseconds))
        self._merge_preview = None
        self.update()

    def nearby_replacement(self, seconds: float, *, skip=None):
        layer = 'current' if skip is None or skip[1] in ('new','current') else 'preview'
        target = nearby_candidate(self.draw_times, self.draw_kinds, seconds,
                                  self.replace_radius_ms / 1000.0, skip=skip,
                                  layer=layer)
        # Never silently replace a candidate outside the current visible range.
        if target is not None and not (self.view_start <= target[0] <= self.view_start+self.view_span):
            return None
        return target

    def update_merge_preview(self, seconds: float | None, *, skip=None):
        if seconds is None or self.replace_radius_ms <= 0:
            self._merge_preview = None
        else:
            # Preview extends to 1.6x the actual replacement radius, but only
            # the inner 1.0x triggers replacement on release.
            candidate = nearby_candidate(
                self.draw_times, self.draw_kinds, seconds,
                self.replace_radius_ms / 1000.0 * 1.6, skip=skip,
                layer=('preview' if skip is not None and skip[1] not in ('new','current')
                       else 'current'))
            self._merge_preview = (candidate if candidate is not None and
                                   self.view_start <= candidate[0] <= self.view_start+self.view_span
                                   else None)
        self.update()

    def set_external_hover(self, seconds: float):
        self.pointer_x = (round(self.x_at(seconds)) if seconds >= 0 else -1)
        self.update()

    def reset_audio(self):
        self.raw_candidates = CandidateSet(np.array([], dtype=np.float64), np.array([],dtype=np.float32))
        self.annotations = Annotations()
        self.refresh_candidates()
        self.low = self.high = None
        self.audio_cache = None
        self.duration = 0.0
        self.view_start = 0.0
        self.view_span = 1.0
        self.playhead = 0.0
        self._drag_candidate = None
        self._drag_time = None
        self._merge_preview = None
        self._middle_press_x = None
        self._seek_press_x = None
        self._region_start = self._region_end = None
        self.update()
        self.viewportChanged.emit()

    def set_audio(self, low: np.ndarray, high: np.ndarray, duration: float,
                  cache: WaveformCache | None = None):
        self.low = low
        self.high = high
        self.audio_cache = cache
        self.duration = max(0.001, float(duration))
        self.max_level = max(0.001, float(max(np.max(np.abs(low)), np.max(np.abs(high)))))
        self.view_start = 0.0
        self.view_span = self.duration
        self.playhead = 0.0
        self.update()
        self.viewportChanged.emit()

    def plot_width(self) -> int:
        return max(1, self.width() - self.LEFT - self.RIGHT)

    def time_at(self, x: float) -> float:
        proportion = max(0.0, min(1.0, (x - self.LEFT) / self.plot_width()))
        return self.view_start + proportion * self.view_span

    def x_at(self, t: float) -> float:
        return self.LEFT + (t - self.view_start) / self.view_span * self.plot_width()

    def pan_to(self, new_start: float):
        if not self.has_audio:
            return
        limit = max(0.0, self.duration - self.view_span)
        next_start = max(0.0, min(limit, new_start))
        if abs(next_start - self.view_start) > 1e-9:
            self.view_start = next_start
            self.update()
            self.viewportChanged.emit()

    def zoom(self, factor: float, anchor: float = 0.5):
        """Zoom horizontally, keeping the specified visible fraction fixed."""
        if not self.has_audio:
            return
        anchor = max(0.0, min(1.0, anchor))
        # At high zoom, PCM samples should become individually visible.
        min_span = min(self.duration, 0.005 if self.audio_cache and self.audio_cache.has_pcm else 0.10)
        next_span = max(min_span, min(self.duration, self.view_span * factor))
        reference = self.view_start + anchor * self.view_span
        self.view_span = next_span
        self.view_start = max(0.0, min(self.duration - next_span, reference - anchor * next_span))
        self.update()
        self.viewportChanged.emit()

    def fit(self):
        if self.has_audio:
            self.view_start = 0.0
            self.view_span = self.duration
            self.update()
            self.viewportChanged.emit()

    def set_playhead(self, seconds: float, follow: bool = False):
        old_x = self.x_at(self.playhead) if self.has_audio else -1
        self.playhead = min(max(0.0, seconds), self.duration)
        if follow and self.has_audio and self.playhead > self.view_start + self.view_span:
            self.pan_to(self.playhead - 0.12 * self.view_span)
        elif follow and self.has_audio and self.playhead < self.view_start:
            self.pan_to(self.playhead - 0.12 * self.view_span)
        new_x = self.x_at(self.playhead) if self.has_audio else -1
        if self.has_audio and int(new_x)==int(old_x):
            return
        if self.has_audio and abs(new_x-old_x) < self.width()/2:
            for x in (old_x,new_x):
                if self.LEFT-6 <= x <= self.width()-self.RIGHT+6:
                    self.update(max(0,int(x)-6),0,14,self.height())
        else:
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        c = self.colors
        p.fillRect(self.rect(), QColor(c["background"]))
        if self.drop_hover:
            p.setPen(QPen(QColor(c["waveform"]), 3, Qt.PenStyle.DashLine))
            p.drawRect(self.rect().adjusted(3, 3, -4, -4))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self.has_audio:
            p.setPen(QPen(QColor(c["border"]), 1.5, Qt.PenStyle.DashLine))
            margin = 26
            p.drawRoundedRect(self.rect().adjusted(margin, margin, -margin, -margin), 16, 16)
            p.setPen(QColor(c["text"]))
            font = QFont("Segoe UI", 18, QFont.Weight.DemiBold)
            p.setFont(font)
            p.drawText(self.rect().adjusted(0, -33, 0, 0), Qt.AlignmentFlag.AlignCenter, "Drop an MP3 here")
            p.setPen(QColor(c["muted"]))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(self.rect().adjusted(0, 34, 0, 0), Qt.AlignmentFlag.AlignCenter,
                       "or use File  >  Open Audio    •    MP3 / WAV / FLAC / OGG")
            return

        x0 = self.LEFT
        x1 = self.width() - self.RIGHT
        y0 = self.RULER
        y1 = self.height() - 22
        cy = (y0 + y1) / 2
        amp = max(1.0, (y1 - y0) * 0.42)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Ruler & grid. Use pleasing 1/2/5 ticks and finer grid subdivisions.
        seconds_per_100px = self.view_span * 110 / self.plot_width()
        major = nice_tick(seconds_per_100px)
        grid_col = QColor(c["grid"])
        text_col = QColor(c["muted"])
        first = math.ceil((self.view_start - major * 1e-7) / major)
        last = math.floor((self.view_start + self.view_span + major * 1e-7) / major)
        max_ticks = 2000
        if last - first < max_ticks:
            for tick in range(first, last + 1):
                t = max(0.0, tick * major)
                x = round(self.x_at(t))
                p.setPen(QPen(grid_col, 1))
                p.drawLine(x, y0, x, y1)
                p.setPen(text_col)
                decimals = 0 if major >= 1 else max(1, min(6, int(math.ceil(-math.log10(major)))))
                p.drawText(x + 4, 11, 96, 23, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           pretty_time(t, decimals))

        # Optional musical reference grid. Bar/beat/subdivision lines are
        # strictly visual and leave computed/committed candidates untouched.
        if self.guides.visible:
            for region in self.guides.ranges:
                if region.end < self.view_start or region.start > self.view_start+self.view_span:
                    continue
                left_t = self.time_at(max(x0,event.rect().left()-2))
                right_t = self.time_at(min(x1,event.rect().right()+2))
                for t, kind in region.ticks(left_t,right_t):
                    x = round(self.x_at(t))
                    color = QColor(c['current'] if kind == 'bar' else c['grid'])
                    color.setAlpha(105 if kind == 'bar' else 66 if kind == 'beat' else 27)
                    p.setPen(QPen(color, 1.4 if kind == 'bar' else 1))
                    p.drawLine(x, y0, x, y1)
                    if kind == 'bar' and self.view_span < 14:
                        p.setPen(QColor(c['muted']))
                        p.drawText(x+2, 25, 58, 12, Qt.AlignmentFlag.AlignLeft,
                                   f'{region.bpm:g} BPM')

        p.setPen(QPen(grid_col, 1))
        p.drawLine(x0, round(cy), x1, round(cy))
        p.drawLine(x0, y0 - 1, x1, y0 - 1)
        p.drawLine(x0, y1 + 1, x1, y1 + 1)

        # Adaptive display: draw true decoded PCM samples as a polyline when
        # sufficiently zoomed, otherwise draw per-pixel min/max from a cache
        # level narrower than the pixel interval. This *never* interpolates the
        # underlying analysis values or pretends that coarse bins are samples.
        cache = self.audio_cache
        p.save()
        p.setClipRect(x0, y0, x1-x0, y1-y0)
        p.setPen(QPen(QColor(c['waveform']), 1))
        if cache is not None and cache.pcm is not None and (
                self.view_span * cache.rate / self.plot_width() <= 1.5):
            clip_min = max(x0, event.rect().left()-3)
            clip_max = min(x1, event.rect().right()+3)
            start = max(0, int(math.floor(self.time_at(clip_min) * cache.rate)) - 1)
            end = min(cache.frames, int(math.ceil(self.time_at(clip_max)*cache.rate)) + 2)
            if end > start:
                path = QPainterPath()
                x_first = self.x_at(start / cache.rate)
                path.moveTo(x_first, cy-float(cache.pcm[start])/self.max_level*amp)
                for i in range(start+1, end):
                    path.lineTo(self.x_at(i / cache.rate),
                                cy-float(cache.pcm[i])/self.max_level*amp)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.drawPath(path)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                # Reveal individual sample positions at extreme zoom.
                if self.plot_width() * 1.0 / max(1,end-start) >= 7:
                    for i in range(start, end):
                        p.drawEllipse(QPointF(self.x_at(i / cache.rate),
                                             cy-float(cache.pcm[i])/self.max_level*amp), 1.5, 1.5)
        elif cache is not None:
            tier, _low, _high = cache.level_for_pixel(self.view_span/self.plot_width())
            for x in range(max(x0,event.rect().left()), min(x1,event.rect().right()+1)):
                t0 = self.view_start + (x-x0)/self.plot_width()*self.view_span
                t1 = self.view_start + (x+1-x0)/self.plot_width()*self.view_span
                lo, hi = cache.extrema_for_seconds(t0,t1,tier_frames=tier)
                ya = round(cy-hi/self.max_level*amp)
                yb = round(cy-lo/self.max_level*amp)
                p.drawLine(x, ya, x, max(ya+1,yb))
        else:
            n = len(self.low)
            bin_duration = self.duration/n
            for x in range(max(x0,event.rect().left()), min(x1,event.rect().right()+1)):
                t0 = self.view_start+(x-x0)/self.plot_width()*self.view_span
                t1 = self.view_start+(x+1-x0)/self.plot_width()*self.view_span
                a = max(0,min(n-1,int(t0/bin_duration)))
                b = max(a+1,min(n,int(math.ceil(t1/bin_duration))))
                lo = float(self.low[a:b].min())/self.max_level
                hi = float(self.high[a:b].max())/self.max_level
                ya = round(cy-hi*amp); yb = round(cy-lo*amp)
                p.drawLine(x,ya,x,max(ya+1,yb))
        p.restore()

        # Imported osu! references are read-only and painted separately from
        # both graph Preview and editable Current. Draw only the invalidated
        # viewport strip, even on maps with thousands of timing points.
        if self.osu_reference is not None:
            dirty_lo = self.time_at(max(x0, event.rect().left()-3))
            dirty_hi = self.time_at(min(x1, event.rect().right()+3))
            def draw_reference_marks(times, color, top, bottom, max_count=5000):
                first = int(np.searchsorted(times, dirty_lo, side='left'))
                last = int(np.searchsorted(times, dirty_hi, side='right'))
                if last <= first:
                    return
                p.setPen(QPen(QColor(color), 1.6))
                # Aggregate dense references visually rather than blocking Qt.
                stride = max(1, math.ceil((last-first)/max_count))
                for i in range(first, last, stride):
                    x = round(self.x_at(float(times[i])))
                    if x0 <= x <= x1:
                        p.drawLine(x, top, x, bottom)
            if self.show_ref_timing:
                draw_reference_marks(self.ref_red_times, c['ref_red'], y0-4, y0+17)
                draw_reference_marks(self.ref_green_times, c['ref_green'], y0-4, y0+10)
            if self.show_ref_hits:
                draw_reference_marks(self.ref_hit_times, c['ref_hit'], y1-20, y1+2)

        # Human-excluded regions cover the entire waveform. They are hard masks
        # on preview candidates, never destructive edits to plugin results.
        for a,b in self.annotations.excluded:
            if b < self.view_start or a > self.view_start + self.view_span:
                continue
            left = max(x0, self.x_at(a))
            right = min(x1, self.x_at(b))
            fill = QColor(c['exclusion']); fill.setAlpha(65)
            p.fillRect(int(left), y0, max(1,int(right-left)), y1-y0, fill)
            p.setPen(QPen(QColor(c['exclusion']), 1, Qt.PenStyle.DashLine))
            p.drawLine(int(left), y0, int(left), y1)
            p.drawLine(int(right), y0, int(right), y1)
        if self._region_start is not None and self._region_end is not None:
            a,b = sorted((self._region_start, self._region_end))
            col = QColor(c['exclusion']); col.setAlpha(90)
            left=max(x0,self.x_at(a)); right=min(x1,self.x_at(b))
            p.fillRect(int(left), y0, max(1,int(right-left)), y1-y0, col)

        # Replacement-zone feedback: a time-based horizontal range around the
        # nearest candidate. Opacity increases as the pointer approaches it;
        # its extent is exactly +/- replace_radius_ms (not a fixed pixel size).
        # Faint previews appear when approaching; only the inner range merges.
        if self._merge_preview is not None and self.replace_radius_ms > 0:
            target_t, target_kind, _ = self._merge_preview
            pointer_t = (self._drag_time if self._drag_candidate is not None
                         else self.time_at(self.pointer_x) if x0 <= self.pointer_x <= x1
                         else None)
            if pointer_t is not None:
                radius_s = self.replace_radius_ms / 1000.0
                distance = abs(pointer_t-target_t)
                proximity = max(0.0, 1.0-distance/radius_s)
                extent = radius_s / self.view_span * self.plot_width()
                mid = self.x_at(target_t)
                lo = max(x0, round(mid-extent))
                hi = min(x1, round(mid+extent))
                base = QColor(c['current'] if target_kind == 'current' else c['confirmed'] if target_kind == 'confirmed' else c['candidate'])
                # The zone appears softly near its edge and becomes opaque on approach.
                base.setAlpha(round(20 + 115 * proximity))
                if hi > lo:
                    p.fillRect(lo, y0, hi-lo, y1-y0, base)
                    border = QColor(base); border.setAlpha(min(230,80+round(130*proximity)))
                    p.setPen(QPen(border, 1, Qt.PenStyle.DashLine))
                    p.drawLine(lo, y0, lo, y1)
                    p.drawLine(hi, y0, hi, y1)
                if distance <= radius_s:
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    outer = QColor(base); outer.setAlpha(round(80+150*proximity))
                    p.setPen(QPen(outer, 2.2))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(QPointF(mid, y0+7), 7+8*proximity, 7+8*proximity)
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                    p.setPen(QColor(c['text']))
                    p.drawText(max(x0,min(x1-130, round(mid)+11)), y0+20,
                               130, 19, Qt.AlignmentFlag.AlignLeft,
                               'REPLACE on release' if self._drag_candidate is not None
                               else 'REPLACE on create')

        # Draw candidates with a viewport-indexed search, keeping pan and zoom
        # responsive even when a detector generates thousands of events.
        full_first = int(np.searchsorted(self.draw_times, self.view_start, side='left'))
        full_last = int(np.searchsorted(self.draw_times, self.view_start + self.view_span, side='right'))
        dense = (full_last-full_first) > self.plot_width()*1.4
        # The 240 Hz cursor timer only invalidates narrow strips. Limit the
        # expensive Python loop over candidate markers to those strips.
        dirty_left=self.time_at(max(x0,event.rect().left()-9))
        dirty_right=self.time_at(min(x1,event.rect().right()+9))
        first = int(np.searchsorted(self.draw_times, dirty_left, side='left'))
        last = int(np.searchsorted(self.draw_times, dirty_right, side='right'))
        visible_count = last-first
        if dense:
            # Song-level view: summary strip for generated data, but retain
            # individually draggable handles on explicit human annotations.
            visible_times = self.draw_times[first:last]
            pixels = np.clip(np.floor((visible_times-self.view_start) /
                        self.view_span * self.plot_width()).astype(int),
                        0, max(0,self.plot_width()-1))
            density = np.bincount(pixels, minlength=self.plot_width())
            maximum = max(1,int(density.max()))
            faint=QColor(c['candidate']); faint.setAlpha(120)
            p.setPen(QPen(faint, 1))
            for ix in np.flatnonzero(density):
                height = 3 + int(21 * np.log1p(density[ix]) / np.log1p(maximum))
                p.drawLine(x0+int(ix), y0+2, x0+int(ix), y0+height)
            indices = (first+int(local) for local in
                       np.flatnonzero(self.draw_kinds[first:last] > 0)[:2000])
        else:
            indices = range(first, min(last, first + 12000))
        # Suppressed predictions remain inspectable as faint, non-interactive ghosts.
        if self.show_preview and len(self.ghost_times):
            first_ghost=int(np.searchsorted(self.ghost_times, dirty_left, side='left'))
            last_ghost=int(np.searchsorted(self.ghost_times, dirty_right, side='right'))
            ghost_pen=QColor(c['candidate']); ghost_pen.setAlpha(22)
            p.setPen(QPen(ghost_pen,1))
            for ts in self.ghost_times[first_ghost:min(last_ghost,first_ghost+4000)]:
                gx=round(self.x_at(float(ts)))
                p.drawLine(gx,y0+8,gx,y0+24)
        # Every visible editable candidate gets a ROUND DRAG HANDLE at the top.
        # These handles have no need to decode audio or alter graph output.
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for index in indices:
            xpos = round(self.x_at(float(self.draw_times[index])))
            kind = int(self.draw_kinds[index])
            hue = c['candidate'] if kind == 0 else c['manual'] if kind == 1 else c['confirmed'] if kind == 2 else c['current']
            if not dense:
                line_color=QColor(hue)
                if kind == 0: line_color.setAlpha(round(48+140*float(self.draw_confidence[index])))
                p.setPen(QPen(line_color, 1.4 if kind==0 else 2.2))
                p.drawLine(xpos, y0 + 7, xpos, y1 - 3)
            elif kind:
                p.setPen(QPen(QColor(hue), 2.2))
                p.drawLine(xpos, y0 + 7, xpos, y1 - 3)
            p.setPen(QPen(QColor(c['background']), 1.2))
            ball=QColor(hue)
            if kind == 0: ball.setAlpha(round(80+175*float(self.draw_confidence[index])))
            p.setBrush(ball)
            p.drawEllipse(xpos - 5, y0 + 2, 10, 10)

        # Live preview while dragging a marker. Don't mutate annotations until
        # mouse release, so cancellation / accidental clicks stay harmless.
        if self._drag_candidate is not None and self._drag_time is not None:
            xpos = round(self.x_at(self._drag_time))
            xpos = max(x0, min(x1, xpos))
            hue = QColor(c['current'] if self._drag_candidate[1]=='current' else c['confirmed'] if self._drag_candidate[1]=='confirmed' else c['manual'])
            p.setPen(QPen(hue, 2, Qt.PenStyle.DashLine))
            p.drawLine(xpos, y0 + 7, xpos, y1 - 3)
            p.setPen(QPen(QColor(c['background']), 1.2))
            p.setBrush(hue)
            p.drawEllipse(xpos - 7, y0, 14, 14)
            p.setPen(QColor(c['text']))
            p.drawText(max(x0, min(xpos+9, x1-93)), y0+18, 94, 19,
                       Qt.AlignmentFlag.AlignLeft, pretty_time(self._drag_time, 4))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # Playhead and hovered timestamp.
        if self.view_start <= self.playhead <= self.view_start + self.view_span:
            x = round(self.x_at(self.playhead))
            p.setPen(QPen(QColor(c["playhead"]), 2))
            p.drawLine(x, 0, x, y1 + 8)
            p.setBrush(QColor(c["playhead"]))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(x - 4, y0 - 5, 8, 8)

        if (x0 <= self.pointer_x <= x1 and self._middle_press_x is None
                and self._drag_candidate is None):
            x = self.pointer_x
            p.setPen(QPen(QColor(c["muted"]), 1, Qt.PenStyle.DotLine))
            p.drawLine(x, y0, x, y1)
            display = pretty_time(self.time_at(x), 6 if self.view_span < 0.5 else 3)
            p.setPen(QColor(c["text"]))
            pos = min(max(x + 8, x0 + 3), x1 - 95)
            p.drawText(pos, y1 + 4, 95, 17, Qt.AlignmentFlag.AlignLeft, display)

        p.setPen(QColor(c["muted"]))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(11, round(cy) + 5, "MONO")
        p.end()

    def candidate_handle_at(self, x: float, y: float):
        """Hit-test only top-row ball handles so the rest of the waveform is
        always free for right-click creation and left-click seeking."""
        if not self.has_audio or not len(self.draw_times):
            return None
        if not (self.LEFT <= x <= self.width()-self.RIGHT):
            return None
        if abs(y - (self.RULER + 7)) > 13:
            return None
        time = self.time_at(x)
        at = int(np.searchsorted(self.draw_times, time))
        nearest = None
        distance = 10.0  # Easy-to-grab handle with a 10 px hit radius.
        visible_count = (int(np.searchsorted(self.draw_times, self.view_start+self.view_span, side='right'))
                         - int(np.searchsorted(self.draw_times, self.view_start, side='left')))
        dense = visible_count > self.plot_width()*1.4
        for idx in range(max(0, at-12), min(len(self.draw_times), at+13)):
            if dense and self.draw_kinds[idx] == 0:
                continue  # Invisible computed markers in the density summary.
            d = abs(self.x_at(float(self.draw_times[idx])) - x)
            if d <= distance:
                distance = d
                nearest = idx
        if nearest is None:
            return None
        kind = ('computed', 'manual', 'confirmed', 'current')[int(self.draw_kinds[nearest])]
        return float(self.draw_times[nearest]), kind

    def mousePressEvent(self, event):
        if not self.has_audio:
            event.ignore()
            return
        x, y = event.position().x(), event.position().y()
        inside = self.LEFT <= x <= self.width()-self.RIGHT
        if event.button() == Qt.MouseButton.MiddleButton:
            self._middle_press_x = x
            self._middle_start = self.view_start
            self._middle_detached = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            if not inside:
                event.ignore()
                return
            t = self.time_at(x)
            target = self.candidate_handle_at(x, y)
            if target is not None:
                old, kind = target
                menu = QMenu(self)
                audition = menu.addAction('Audition clicks near this marker')
                promote = (menu.addAction('Copy this marker to Current output')
                           if kind != 'current' else None)
                label = ('Confirm / accept preview' if kind != 'confirmed'
                         else 'Already confirmed')
                confirm = menu.addAction(label) if kind != 'current' else None
                if confirm is not None:
                    confirm.setEnabled(kind != 'confirmed')
                exact = menu.addAction('Set exact timestamp…')
                menu.addSeparator()
                reject = menu.addAction('Reject candidate' if kind == 'computed' else 'Delete candidate')
                chosen = menu.exec(event.globalPosition().toPoint())
                if chosen == audition:
                    self.auditionRequested.emit(old, 'current' if kind == 'current' else 'preview')
                elif promote is not None and chosen == promote:
                    self.candidateEdited.emit('promote:' + kind, old, old)
                elif confirm is not None and chosen == confirm:
                    self.candidateEdited.emit('confirm:' + kind, old, old)
                elif chosen == reject:
                    self.candidateEdited.emit('delete:' + kind, old, old)
                elif chosen == exact:
                    new_time, accepted = QInputDialog.getDouble(
                        self, 'Move candidate', 'Timestamp in seconds:', old,
                        0.0, self.duration, 6)
                    if accepted:
                        self.candidateEdited.emit('move:' + kind, old, new_time)
                event.accept()
                return
            if self.annotations.is_excluded(t):
                menu = QMenu(self)
                unmask = menu.addAction('Remove exclusion region here')
                if menu.exec(event.globalPosition().toPoint()) == unmask:
                    self.candidateEdited.emit('unmask', t, t)
            elif 0 <= y <= self.height():
                nearby = self.nearby_replacement(t)
                if nearby is not None:
                    target_t, target_kind, _ = nearby
                    self.candidateReplaced.emit('new', t, t, target_kind, target_t)
                else:
                    self.candidateEdited.emit('add-current', t, t)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and inside:
            if self.edit_mode == 'exclude':
                self._region_start = self._region_end = self.time_at(x)
                event.accept()
                return
            target = self.candidate_handle_at(x, y)
            if target is not None:
                self._drag_candidate = target
                self._drag_press_x = x
                self._drag_time = target[0]
                self._drag_moved = False
                self._drag_detached = False
                self.update_merge_preview(self._drag_time, skip=target)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            else:
                self._seek_press_x = x
            event.accept()
            return
        event.ignore()

    def mouseMoveEvent(self, event):
        x, y = event.position().x(), event.position().y()
        self.pointer_x = int(x)
        if self.has_audio and self.LEFT <= x <= self.width()-self.RIGHT:
            self.hoverTimeChanged.emit(self.time_at(x))
        if self._region_start is not None:
            self._region_end = self.time_at(x)
            self.update()
            event.accept()
            return
        if self._middle_press_x is not None and self.has_audio:
            if abs(x-self._middle_press_x) > 3 and not self._middle_detached:
                self._middle_detached = True
                self.manualNavigation.emit()
            if self._middle_detached:
                self.pan_to(self._middle_start +
                            (self._middle_press_x-x)/self.plot_width()*self.view_span)
            self.update()
            event.accept()
            return
        if self._drag_candidate is not None and self.has_audio:
            if abs(x-self._drag_press_x) > 3:
                self._drag_moved = True
            if self._drag_moved:
                if not self._drag_detached:
                    self._drag_detached = True
                    self.manualNavigation.emit()
                new_time = (self._drag_candidate[0] +
                            (x-self._drag_press_x)/self.plot_width()*self.view_span)
                self._drag_time = min(self.duration, max(0.0, new_time))
                self.update_merge_preview(self._drag_time, skip=self._drag_candidate)
            self.update()
            event.accept()
            return
        if self._middle_press_x is None and self._drag_candidate is None:
            if (self.has_audio and self.edit_mode != 'exclude'
                    and self.LEFT <= x <= self.width()-self.RIGHT):
                self.update_merge_preview(self.time_at(x))
            else:
                self.update_merge_preview(None)
            if self.edit_mode == 'exclude':
                self.setCursor(Qt.CursorShape.CrossCursor)
            elif self.candidate_handle_at(x, y) is not None:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._middle_press_x is not None:
            was_panning = self._middle_detached
            self._middle_press_x = None
            self._middle_detached = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if not was_panning and self.LEFT <= event.position().x() <= self.width()-self.RIGHT:
                # Middle click seeks; middle drag pans, regardless of edit mode.
                self.timeClicked.emit(self.time_at(event.position().x()))
            self.update()
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if self._region_start is not None:
            end = self.time_at(event.position().x())
            start = self._region_start
            self._region_start = self._region_end = None
            if abs(end-start) >= .002:
                self.candidateEdited.emit('exclude', start, end)
            self.update()
            event.accept()
            return
        if self._drag_candidate is not None:
            old, kind = self._drag_candidate
            new_time = self._drag_time
            moved = self._drag_moved
            target = (self.nearby_replacement(new_time, skip=(old, kind))
                      if moved and new_time is not None else None)
            self._drag_candidate = None
            self._drag_time = None
            self._merge_preview = None
            self._drag_press_x = None
            self._drag_moved = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if moved and new_time is not None:
                if target is not None:
                    target_t, target_kind, _ = target
                    self.candidateReplaced.emit(kind, old, new_time, target_kind, target_t)
                else:
                    self.candidateEdited.emit('move:' + kind, old, new_time)
            self.update()
            event.accept()
            return
        if self._seek_press_x is not None:
            if abs(event.position().x()-self._seek_press_x) <= 4:
                self.timeClicked.emit(self.time_at(event.position().x()))
            self._seek_press_x = None
            event.accept()
            return
        event.ignore()

    def leaveEvent(self, event):
        self.pointer_x = -1
        self._merge_preview = None
        self.hoverTimeChanged.emit(-1.0)
        self.update()

    def wheelEvent(self, event):
        if not self.has_audio:
            return
        horizontal = event.angleDelta().x()
        vertical = event.angleDelta().y()
        if not (horizontal or vertical):
            return
        # A real horizontal wheel/touchpad gesture pans. Shift + vertical
        # wheel also pans; the regular vertical wheel zooms around the cursor.
        # All are manual navigation, so playback must stop pulling the view.
        self.manualNavigation.emit()
        if horizontal:
            self.pan_to(self.view_start - (horizontal / 120) * self.view_span * 0.10)
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.pan_to(self.view_start - (vertical / 120) * self.view_span * 0.10)
        else:
            anchor = (event.position().x() - self.LEFT) / self.plot_width()
            self.zoom(0.80 ** (vertical / 120), anchor)
        event.accept()

    def dragEnterEvent(self, event):
        if valid_drop(event.mimeData()):
            event.acceptProposedAction()
            self.drop_hover = True
            self.update()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_hover = False
        self.update()

    def dropEvent(self, event):
        self.drop_hover = False
        path = first_audio_path(event.mimeData())
        if path:
            self.fileDropped.emit(path)
            event.acceptProposedAction()
        else:
            event.ignore()


def first_audio_path(mime) -> str | None:
    if not mime.hasUrls():
        return None
    for url in mime.urls():
        if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in SUPPORTED_AUDIO:
            return url.toLocalFile()
    return None


def valid_drop(mime) -> bool:
    return first_audio_path(mime) is not None


class BeatSolverWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("mugenq", "BeatSolverTimeline")
        self.theme_name = str(self.settings.value("theme_name", "Deep Ocean"))
        saved_custom = self.settings.value("custom_colors", "")
        self.custom_colors = THEMES["Deep Ocean"].copy()
        if saved_custom:
            try:
                data = json.loads(saved_custom)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if k in COLOR_LABELS and QColor(str(v)).isValid():
                            self.custom_colors[k] = str(v)
            except (TypeError, ValueError):
                pass
        self.colors = (self.custom_colors if self.theme_name == "Custom"
                       else THEMES.get(self.theme_name, THEMES["Deep Ocean"])).copy()
        self.worker = None
        self.file_path = None
        self.loading = False
        self.follow_playhead = True
        self.registry = default_registry()
        self.graph = default_spectral_graph(self.registry)
        self.candidate_worker = None
        self._queued_graph_output = None  # Most recent Run request while Qt cleanup is queued.
        self._queued_graph_generation = None
        self._graph_request_generation = 0
        self._active_graph_generation = None
        self._active_graph_output_id = None
        self._active_graph_snapshot = None
        self._last_graph_intermediates = None
        self._last_graph_output_id = None
        self._shutting_down = False
        self._closing = False  # Prevent pending QTimer reruns after window shutdown.
        self.candidate_output = CandidateSet(np.array([],dtype=np.float64), np.array([],dtype=np.float32))
        self.annotations = Annotations()
        self.working = WorkingCandidates()
        self.guides = TimingGuides()
        self.osu_reference: OsuReference | None = None
        self._last_comparison: Comparison | None = None
        self._last_hover_time = 0.0
        self.cursor_fps = 240
        self.playback_clock = PlaybackClock()
        self.audition_clock = PlaybackClock()
        self.audition_active = False
        self.audition_start = 0.0
        self.audition_window_s = 6.0
        self._audition_temp = tempfile.TemporaryDirectory(prefix='beatsolver_audition_')
        self._audition_file = None
        self.priority_threshold_pct = 0
        self.priority_dedup_ms = 0.0
        self.music_gain = 65
        self.click_gain = 75
        self.continuous_source = 'current'
        self._mix_temp = tempfile.TemporaryDirectory(prefix='beatsolver_live_mix_')
        self._mix_worker = None
        self._mix_running_path = None
        self._mix_queued = False
        self._mix_generation = 0
        self._mix_file = None
        self._mix_pending_transport = None
        self._mix_files = []
        self.timing_monitor = TimingMonitor()
        self._stream_controller = AudioStreamController(self)
        self._stream_controller.finished.connect(self._on_stream_finished)
        self._stream_loader = None
        self._stream_cached_path = None
        self._stream_active = False
        self._stream_start_was_playing = False
        self.session_path = None
        self.session_dirty = False
        self._undos = []
        self.analysis_outdated = True
        self.graph_revision = 0
        self.run_revision = -1
        self.global_offset_ms = 0.0
        self.analysis_hop_ms = 2.0
        self.curve_lines = True
        self.spectrogram_enabled = True
        self.spectrogram_mode = 'attack'
        self._spectrogram_worker = None
        self._spectrogram_pending = False
        self._selected_spectrum_request = None
        self._spectrogram_source_id = None  # None = first contributing typed source.
        self.playback_speed_pct = 100.0
        self._diagnostic_curve_cache = None
        self.cursor_sync_ms = 0.0  # visual cursor correction; does not move audio/annotations
        self._resume_after_zero = False
        self._resume_audition_after_zero = False
        self.replace_radius_ms = 12.0
        # Remember which docks were visible before the window became compact.
        # User-initiated dock toggles remain available at any window size.
        self._compact_layout = None
        self._auto_hidden_docks = set()
        self._adapting_docks = False

        self.setWindowTitle(APP_NAME + "  •  v" + APP_VERSION)
        self.resize(1130, 680)
        # Below this size even the main timeline and its toolbars cannot be
        # meaningfully used. Dock panels auto-collapse well before that point.
        self.setMinimumSize(760, 575)
        old_geometry = self.settings.value("geometry")
        if old_geometry:
            self.restoreGeometry(old_geometry)
        self.setAcceptDrops(True)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.65)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self.on_position)
        self.player.playbackStateChanged.connect(self.on_play_state)
        self.player.errorOccurred.connect(self.on_media_error)
        self.player.mediaStatusChanged.connect(self._on_mix_media_status)
        self.audition_player = QMediaPlayer(self)
        self.audition_output = QAudioOutput(self)
        self.audition_output.setVolume(.80)
        self.audition_player.setAudioOutput(self.audition_output)
        self.audition_player.positionChanged.connect(self.on_audition_position)
        self.audition_player.playbackStateChanged.connect(self.on_audition_state)
        self.audition_player.mediaStatusChanged.connect(self.on_audition_status)
        self.audition_player.errorOccurred.connect(
            lambda err,desc: self.statusBar().showMessage('Audition error: '+desc,7000))
        self.cursor_timer = QTimer(self)
        self.cursor_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.cursor_timer.setInterval(4)
        self.cursor_timer.timeout.connect(self._cursor_tick)
        self._mix_debounce = QTimer(self)
        self._mix_debounce.setSingleShot(True)
        self._mix_debounce.setInterval(550)
        self._mix_debounce.timeout.connect(self._begin_mix_render)

        self.setup_ui()
        self.setup_menus()
        self.diag_timer = QTimer(self)
        self.diag_timer.setInterval(750)
        self.diag_timer.timeout.connect(self._refresh_diagnostics)
        self.diag_timer.start()
        # A restored compact window is only laid out after Qt shows the docks.
        QTimer.singleShot(0, self._update_compact_layout)
        self.apply_colors()
        self.set_controls_enabled(False)
        self.statusBar().showMessage("Ready. Drop a song into the window.")

    def setup_ui(self):
        central = QWidget(self)
        central.setObjectName("central")
        self.setCentralWidget(central)
        body = QVBoxLayout(central)
        body.setContentsMargins(12, 9, 12, 8)
        body.setSpacing(7)

        top = QHBoxLayout()
        headings = QVBoxLayout()
        headings.setSpacing(2)
        title = QLabel("BEATSOLVER")
        title.setObjectName("heading")
        headings.addWidget(title)
        subtitle = QLabel("TIMELINE  /  waveform workspace")
        subtitle.setObjectName("subtitle")
        headings.addWidget(subtitle)
        top.addLayout(headings)
        top.addStretch(1)

        self.open_btn = QPushButton("Open MP3")
        self.open_btn.clicked.connect(self.open_dialog)
        self.open_btn.setMinimumWidth(100)
        self.nodes_btn = QPushButton('Nodes')
        self.nodes_btn.setCheckable(True)
        self.nodes_btn.setChecked(True)
        self.nodes_btn.setToolTip('Show or hide the processing-node editor')
        self.layers_btn = QPushButton('Layers')
        self.layers_btn.setCheckable(True)
        self.layers_btn.setChecked(True)
        self.layers_btn.setToolTip('Show or hide the layer inspector')
        self.diag_btn = QPushButton('Diagnostics')
        self.diag_btn.setCheckable(True)
        self.diag_btn.setChecked(False)
        self.diag_btn.setToolTip('Application repaint and cursor timing, plus candidate score histogram')
        top.addWidget(self.diag_btn)
        top.addWidget(self.nodes_btn)
        top.addWidget(self.layers_btn)
        top.addWidget(self.open_btn)
        body.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(9)
        self.track_name = QLabel("No audio loaded")
        self.track_name.setObjectName("trackName")
        self.track_name.setMinimumWidth(0)
        row.addWidget(self.track_name, stretch=1)
        self.reference_label = QLabel('No osu! reference')
        self.reference_label.setObjectName('subtitle')
        self.reference_label.setToolTip('Import a .osu file to inspect mapped heads and red/green timing points.')
        row.addWidget(self.reference_label)
        self.position_label = QLabel("00:00.00 / 00:00.00")
        self.position_label.setObjectName("timer")
        row.addWidget(self.position_label)
        body.addLayout(row)

        self.timeline = Timeline()
        self.timeline.installEventFilter(self)
        self.timeline.fileDropped.connect(self.load_audio)
        self.timeline.timeClicked.connect(self.seek_to)
        self.timeline.viewportChanged.connect(self.on_view_changed)
        self.timeline.manualNavigation.connect(self.detach_from_playhead)
        self.timeline.candidateEdited.connect(self.on_candidate_edit)
        self.timeline.candidateReplaced.connect(self.on_candidate_replaced)
        self.timeline.auditionRequested.connect(self.audition_at)
        self.timeline.set_replace_radius(self.replace_radius_ms)
        self.timeline.hoverTimeChanged.connect(self.cursor_from_timeline)
        self.curve_viewer = CurveViewer(self.timeline)
        self.spectrogram_viewer = SpectrogramViewer(self.timeline)
        self.spectrogram_viewer.seekRequested.connect(self.seek_to)
        self.spectrogram_viewer.navigationStarted.connect(self.detach_from_playhead)
        self.spectrogram_viewer.hoverChanged.connect(self.cursor_from_curves)
        self.curve_viewer.seekRequested.connect(self.seek_to)
        self.curve_viewer.navigationStarted.connect(self.detach_from_playhead)
        self.curve_viewer.hoverChanged.connect(self.cursor_from_curves)
        self.timeline_split = QSplitter(Qt.Orientation.Vertical)
        self.timeline_split.setChildrenCollapsible(False)
        # Keep space for the ruler and waveform without forcing the bottom
        # transport bar outside a compact window.
        self.timeline.setMinimumHeight(155)
        self.curve_viewer.setMinimumHeight(85)
        self.timeline_split.addWidget(self.timeline)
        self.timeline_split.addWidget(self.curve_viewer)
        self.timeline_split.addWidget(self.spectrogram_viewer)
        self.spectrogram_viewer.setVisible(True)
        self.timeline_split.setStretchFactor(2, 1)
        self.timeline_split.setStretchFactor(0, 3)
        self.timeline_split.setStretchFactor(1, 2)
        body.addWidget(self.timeline_split, stretch=1)

        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.setFixedHeight(16)
        self.scrollbar.valueChanged.connect(self.on_scrollbar_changed)
        body.addWidget(self.scrollbar)

        # The transport is horizontally scrollable when the timeline is narrow.
        # Its controls retain their intrinsic sizes instead of crushing labels
        # down to individual letters (seen in the v0.3.1 recording).
        self.transport_host = QWidget()
        self.transport_host.setObjectName('transportHost')
        control_row = QHBoxLayout(self.transport_host)
        control_row.setContentsMargins(3, 3, 3, 3)
        control_row.setSpacing(8)
        self.play_btn = QPushButton("Play")
        self.play_btn.setMinimumWidth(78)
        self.play_btn.clicked.connect(self.toggle_play)
        control_row.addWidget(self.play_btn)
        control_row.addWidget(QLabel('Speed'))
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(0, speed_to_slider(MAX_SPEED_PERCENT))
        self.speed_slider.setSingleStep(1)  # One slider unit = 0.1 percent.
        self.speed_slider.setPageStep(10)  # Page key = 1.0 percent.
        self.speed_slider.setValue(speed_to_slider(100.0))
        self.speed_slider.setFixedWidth(118)
        self.speed_slider.setToolTip('Playback speed 0.0–135.0%, adjustable in 0.1% increments.')
        self.speed_slider.valueChanged.connect(self.on_speed_slider_changed)
        control_row.addWidget(self.speed_slider)
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.0, MAX_SPEED_PERCENT)
        self.speed_spin.setDecimals(1)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setSuffix(' %')
        self.speed_spin.setValue(100.0)
        self.speed_spin.setFixedWidth(91)
        self.speed_spin.setToolTip('Type any value from 0.0 to 135.0% in 0.1% steps. '
                                   '0% pauses; playback-device limits still apply.')
        self.speed_spin.valueChanged.connect(self.set_playback_speed)
        control_row.addWidget(self.speed_spin)
        speed_reset = QPushButton('1×')
        speed_reset.setToolTip('Reset playback speed to 100%')
        speed_reset.clicked.connect(lambda: self.speed_spin.setValue(100.0))
        control_row.addWidget(speed_reset)
        self.sample_clock_check = QCheckBox('PCM sync')
        self.sample_clock_check.setChecked(True)
        self.sample_clock_check.setToolTip(
            'Use decoded PCM for both waveform timing and native song playback, '
            'even without hitsounds. The playhead follows processed output frames. '
            'Falls back to the media player when native stereo output is unavailable. '
            'Physical headphone latency remains device-dependent.')
        self.sample_clock_check.toggled.connect(self.on_sample_clock_toggle)
        control_row.addWidget(self.sample_clock_check)
        control_row.addWidget(QLabel('Cursor Hz'))
        self.cursor_fps_combo = QComboBox()
        for hz in (60,120,240):
            self.cursor_fps_combo.addItem(str(hz),hz)
        self.cursor_fps_combo.setCurrentIndex(2)
        self.cursor_fps_combo.setToolTip('Target redraw rate. Actual refresh depends on the screen, Qt and CPU.')
        self.cursor_fps_combo.currentIndexChanged.connect(self.change_cursor_fps)
        control_row.addWidget(self.cursor_fps_combo)
        control_row.addWidget(QLabel('Cursor sync'))
        self.cursor_sync_spin = QDoubleSpinBox()
        self.cursor_sync_spin.setRange(-300.0, 300.0)
        self.cursor_sync_spin.setDecimals(1)
        self.cursor_sync_spin.setSingleStep(1.0)
        self.cursor_sync_spin.setSuffix(' ms')
        self.cursor_sync_spin.setFixedWidth(100)
        self.cursor_sync_spin.setToolTip(
            'Visual-only wall-clock cursor calibration. Positive moves the playhead later, '
            'negative earlier. Leaves all candidates and the decoded waveform unchanged. '
            'Scales with playback speed (e.g. 40 ms at 25% shifts 10 ms of song time). ')
        self.cursor_sync_spin.valueChanged.connect(self.on_cursor_sync_changed)
        control_row.addWidget(self.cursor_sync_spin)
        cursor_sync_reset = QPushButton('Sync 0')
        cursor_sync_reset.clicked.connect(lambda: self.cursor_sync_spin.setValue(0.0))
        control_row.addWidget(cursor_sync_reset)
        self.pitch_check=QCheckBox('Preserve pitch')
        self.pitch_check.setToolTip('Use Qt 6.10+ pitch compensation when the playback backend supports it. '
                                     'May increase CPU cost; disabled on older backends.')
        self.pitch_check.setEnabled(hasattr(self.player,'setPitchCompensation'))
        if self.pitch_check.isEnabled():
            try:
                availability=str(self.player.pitchCompensationAvailability())
                if 'Unavailable' in availability:
                    self.pitch_check.setEnabled(False)
                else:
                    self.pitch_check.setChecked(bool(self.player.pitchCompensation()))
            except Exception:
                self.pitch_check.setEnabled(False)
        self.pitch_check.toggled.connect(self.toggle_pitch_compensation)
        control_row.addWidget(self.pitch_check)
        self.zoom_in_btn = QPushButton("Zoom +")
        self.zoom_in_btn.clicked.connect(lambda: self.zoom_manually(0.80))
        control_row.addWidget(self.zoom_in_btn)
        self.zoom_out_btn = QPushButton("Zoom −")
        self.zoom_out_btn.clicked.connect(lambda: self.zoom_manually(1.25))
        control_row.addWidget(self.zoom_out_btn)
        self.fit_btn = QPushButton("Fit all")
        self.fit_btn.clicked.connect(self.fit_manually)
        control_row.addWidget(self.fit_btn)
        self.follow_btn = QPushButton("Follow: ON")
        self.follow_btn.setCheckable(True)
        self.follow_btn.setChecked(True)
        self.follow_btn.setToolTip("Follow playback automatically. Panning or zooming switches this OFF. Press F to reattach.")
        self.follow_btn.toggled.connect(self.set_follow_playhead)
        control_row.addWidget(self.follow_btn)
        control_row.addSpacing(8)
        control_row.addWidget(QLabel('Global offset'))
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-5000.0, 5000.0)
        self.offset_spin.setDecimals(3)
        self.offset_spin.setSingleStep(1.0)
        self.offset_spin.setSuffix(' ms')
        self.offset_spin.setToolTip('Shift all generated curves and candidates relative to the unchanged song. '
                                    'Applied once, only for display, not inside each node.')
        self.offset_spin.setFixedWidth(115)
        self.offset_spin.valueChanged.connect(self.on_offset_changed)
        control_row.addWidget(self.offset_spin)
        self.offset_step = QComboBox()
        for increment in ('0.1', '1', '10'):
            self.offset_step.addItem(increment + ' ms step', float(increment))
        self.offset_step.setCurrentIndex(1)
        self.offset_step.currentIndexChanged.connect(
            lambda _: self.offset_spin.setSingleStep(self.offset_step.currentData()))
        control_row.addWidget(self.offset_step)
        control_row.addSpacing(9)
        control_row.addWidget(QLabel('Replace ±'))
        self.replace_spin = QDoubleSpinBox()
        self.replace_spin.setRange(0.0, 100.0)
        self.replace_spin.setDecimals(1)
        self.replace_spin.setSingleStep(1.0)
        self.replace_spin.setSuffix(' ms')
        self.replace_spin.setValue(self.replace_radius_ms)
        self.replace_spin.setToolTip('When placing or dragging a candidate within this temporal radius '
                                     'of another, the nearby one is replaced. Set 0 to disable.')
        self.replace_spin.setFixedWidth(92)
        self.replace_spin.valueChanged.connect(self.on_replace_radius_changed)
        control_row.addWidget(self.replace_spin)
        control_row.addStretch(1)
        self.zoom_label = QLabel("100% of song")
        self.zoom_label.setObjectName("subtitle")
        control_row.addWidget(self.zoom_label)
        self.transport_host.setMinimumWidth(control_row.sizeHint().width() + 28)
        self.transport_scroll = QScrollArea()
        self.transport_scroll.setObjectName('transportScroll')
        self.transport_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.transport_scroll.setWidgetResizable(True)
        self.transport_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transport_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.transport_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.transport_scroll.setFixedHeight(58)
        self.transport_scroll.setWidget(self.transport_host)
        body.addWidget(self.transport_scroll)

        analysis_row = QHBoxLayout()
        analysis_row.setSpacing(8)
        analysis_row.addWidget(QLabel('Analysis interval'))
        self.analysis_resolution = QComboBox()
        for value in (0.25, 0.5, 1.0, 2.0, 4.0):
            self.analysis_resolution.addItem(f'{value:g} ms', value)
        self.analysis_resolution.setCurrentIndex(self.analysis_resolution.findData(2.0))
        self.analysis_resolution.setToolTip('Recompute the graph on PCM min/max bins at this interval. '
                                            'This affects analysis, but does not interpolate samples.')
        self.analysis_resolution.currentIndexChanged.connect(self.on_analysis_resolution_changed)
        analysis_row.addWidget(self.analysis_resolution)
        self.curve_line_check = QCheckBox('Connect curve samples')
        self.curve_line_check.setChecked(True)
        self.curve_line_check.setToolTip('Visual-only line segments between genuine curve samples. '
                                        'Does not alter detection or measured values.')
        self.curve_line_check.toggled.connect(self.on_curve_display_changed)
        analysis_row.addWidget(self.curve_line_check)
        self.spectrogram_check=QCheckBox('Spectrogram')
        self.spectrogram_check.setChecked(True)
        self.spectrogram_check.setToolTip('Enabled by default. Synchronized harmonic/attack spectrogram uses decoded PCM; saved projects can override visibility.')
        self.spectrogram_check.toggled.connect(self.toggle_spectrogram)
        analysis_row.addWidget(self.spectrogram_check)
        self.spectrogram_mode_combo=QComboBox()
        self.spectrogram_mode_combo.addItem('Attack bands','attack')
        self.spectrogram_mode_combo.addItem('Harmonics / sustain','harmonic')
        self.spectrogram_mode_combo.setToolTip('Switch between spectral rearticulations and sustained harmonic energy')
        self.spectrogram_mode_combo.currentIndexChanged.connect(self.change_spectrogram_mode)
        analysis_row.addWidget(self.spectrogram_mode_combo)
        analysis_row.addWidget(QLabel('Spectral detail'))
        self.spectrogram_detail_combo = QComboBox()
        for factor, hop in ((1,6.0),(2,3.0),(4,1.5)):
            self.spectrogram_detail_combo.addItem(f'{factor}× ({hop:g} ms)', factor)
        self.spectrogram_detail_combo.setToolTip(
            'Numerical STFT time detail: 1× = 6 ms hop, 2× = 3 ms, 4× = 1.5 ms. '
            'Higher detail recomputes the selected spectral graph and may change '
            'Preview candidates. The FFT frequency window is unchanged. '
            '2×/4× may take much longer and use substantially more memory.')
        self.spectrogram_detail_combo.currentIndexChanged.connect(self.change_spectrogram_detail)
        analysis_row.addWidget(self.spectrogram_detail_combo)
        analysis_row.addWidget(QLabel('Evidence'))
        self.spectrogram_source_combo=QComboBox()
        self.spectrogram_source_combo.setMinimumWidth(150)
        self.spectrogram_source_combo.setToolTip(
            'Choose which contributing Spectrogram node appears in the viewer. '
            'Merged branches may use different FFT windows.')
        self.spectrogram_source_combo.addItem('Auto (first branch)', None)
        self.spectrogram_source_combo.currentIndexChanged.connect(self.change_spectrogram_source)
        analysis_row.addWidget(self.spectrogram_source_combo)
        analysis_row.addStretch(1)
        self.resolution_label = QLabel('Waveform: waiting for audio')
        self.resolution_label.setObjectName('subtitle')
        self.resolution_label.setMinimumWidth(0)
        self.resolution_label.setToolTip('Current waveform and analysis precision')
        analysis_row.addWidget(self.resolution_label)
        analysis_host=QWidget()
        analysis_host.setLayout(analysis_row)
        analysis_host.setMinimumWidth(analysis_row.sizeHint().width()+16)
        self.analysis_scroll=QScrollArea()
        self.analysis_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.analysis_scroll.setWidgetResizable(True)
        self.analysis_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.analysis_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.analysis_scroll.setFixedHeight(53)
        self.analysis_scroll.setWidget(analysis_host)
        body.addWidget(self.analysis_scroll)

        self.candidate_bar = QHBoxLayout()
        self.candidate_bar.setSpacing(5)
        self.mode_buttons = QButtonGroup(self)
        self.mode_buttons.setExclusive(True)
        for mode, label in [('pan','Navigate / Seek'),('exclude','Exclude Region')]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(mode=='pan')
            btn.setToolTip('Timeline edit mode: ' + mode)
            btn.clicked.connect(lambda checked=False, mode=mode: self.set_edit_mode(mode))
            self.mode_buttons.addButton(btn)
            self.candidate_bar.addWidget(btn)
        self.candidate_bar.addStretch(1)
        self.count_label = QLabel('0 computed / 0 shown')
        self.candidate_bar.addWidget(self.count_label)
        self.candidate_bar.addWidget(QLabel('Min score'))
        self.priority_slider = QSlider(Qt.Orientation.Horizontal)
        self.priority_slider.setRange(0,100)
        self.priority_slider.setFixedWidth(100)
        self.priority_slider.setToolTip('Keep higher-priority Preview candidates. Human edits and Current are never filtered.')
        self.priority_slider.valueChanged.connect(self.on_priority_changed)
        self.candidate_bar.addWidget(self.priority_slider)
        self.priority_label = QLabel('0%')
        self.candidate_bar.addWidget(self.priority_label)
        self.candidate_bar.addWidget(QLabel('Dedup'))
        self.priority_radius_spin = QDoubleSpinBox()
        self.priority_radius_spin.setRange(0,100)
        self.priority_radius_spin.setDecimals(1)
        self.priority_radius_spin.setSuffix(' ms')
        self.priority_radius_spin.setToolTip('Suppress lower-scoring neighbors within this radius. Zero disables deduplication.')
        self.priority_radius_spin.valueChanged.connect(self.on_priority_changed)
        self.candidate_bar.addWidget(self.priority_radius_spin)
        candidate_host=QWidget()
        candidate_host.setLayout(self.candidate_bar)
        candidate_host.setMinimumWidth(self.candidate_bar.sizeHint().width()+20)
        self.candidate_scroll=QScrollArea()
        self.candidate_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.candidate_scroll.setWidgetResizable(True)
        self.candidate_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.candidate_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.candidate_scroll.setFixedHeight(43)
        self.candidate_scroll.setWidget(candidate_host)
        body.addWidget(self.candidate_scroll)

        workspace_row = QHBoxLayout()
        workspace_row.setSpacing(7)
        self.preview_check = QCheckBox('Preview')
        self.preview_check.setChecked(True)
        self.preview_check.setToolTip('Show transient algorithm output; reruns replace this layer.')
        self.preview_check.toggled.connect(lambda b: self.set_layer_visibility('preview',b))
        workspace_row.addWidget(self.preview_check)
        self.current_check = QCheckBox('Current output')
        self.current_check.setChecked(True)
        self.current_check.setToolTip('Show the committed final candidate product, independent of graph reruns.')
        self.current_check.toggled.connect(lambda b: self.set_layer_visibility('current',b))
        workspace_row.addWidget(self.current_check)
        commit_button = QPushButton('Paste preview → Current…')
        commit_button.setToolTip('Copy the entire displayed preview candidate layer to the committed output.')
        commit_button.clicked.connect(self.paste_preview)
        workspace_row.addWidget(commit_button)
        audition_button = QPushButton('Audition clicks')
        audition_button.clicked.connect(lambda: self.audition_at(self._last_hover_time or self.timeline.playhead,
                                                                 self.audition_source.currentData()))
        workspace_row.addWidget(audition_button)
        self.audition_source = QComboBox()
        self.audition_source.addItem('Current', 'current')
        self.audition_source.addItem('Preview', 'preview')
        self.audition_source.setCurrentIndex(1)
        workspace_row.addWidget(self.audition_source)
        self.audition_seconds = QDoubleSpinBox()
        self.audition_seconds.setRange(1,12)
        self.audition_seconds.setSingleStep(1)
        self.audition_seconds.setDecimals(1)
        self.audition_seconds.setValue(6)
        self.audition_seconds.setSuffix(' s')
        self.audition_seconds.setFixedWidth(73)
        workspace_row.addWidget(self.audition_seconds)
        self.guides_check = QCheckBox('BPM guides')
        self.guides_check.setChecked(True)
        self.guides_check.toggled.connect(self.set_guide_visible)
        workspace_row.addWidget(self.guides_check)
        guides_button=QPushButton('BPM ranges…')
        guides_button.clicked.connect(self.edit_bpm_ranges)
        workspace_row.addWidget(guides_button)
        workspace_row.addStretch(1)
        workspace_host=QWidget()
        workspace_host.setLayout(workspace_row)
        # Compact mode scrolls rather than squashing buttons into unreadable text.
        workspace_host.setMinimumWidth(workspace_row.sizeHint().width()+20)
        self.workspace_scroll=QScrollArea()
        self.workspace_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.workspace_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.workspace_scroll.setFixedHeight(47)
        self.workspace_scroll.setWidget(workspace_host)
        body.addWidget(self.workspace_scroll)

        mixer_row=QHBoxLayout()
        mixer_row.setSpacing(7)
        self.continuous_hits = QCheckBox('Continuous clicks')
        self.continuous_hits.setToolTip('Mix clicks and song on the same audio sample clock. A new mix is generated after edits.')
        self.continuous_hits.toggled.connect(self.on_continuous_toggle)
        mixer_row.addWidget(self.continuous_hits)
        self.stream_mode = QCheckBox('Streaming (beta)')
        self.stream_mode.setToolTip('Optional bounded-memory QAudioSink mixer. No full-song mix files after edits. Fallback remains available.')
        self.stream_mode.toggled.connect(self.on_stream_mode_changed)
        mixer_row.addWidget(self.stream_mode)
        self.live_source = QComboBox()
        for label, source in (('Current','current'),('Preview','preview'),
                              ('Both','both'),('BPM grid','grid')):
            self.live_source.addItem(label,source)
        self.live_source.currentIndexChanged.connect(self.on_mixer_changed)
        mixer_row.addWidget(self.live_source)
        for text, field, initial in [('Music','music_slider',65),('Hitsound','click_slider',75)]:
            mixer_row.addWidget(QLabel(text))
            slider=QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0,100)
            slider.setValue(initial)
            slider.setFixedWidth(102)
            slider.setToolTip('Audio gain 0–100%. Continuous mix refreshes after changes.')
            slider.valueChanged.connect(self.on_mixer_changed)
            setattr(self,field,slider)
            mixer_row.addWidget(slider)
        self.mix_status=QLabel('Live clicks off')
        mixer_row.addWidget(self.mix_status)
        mixer_row.addStretch(1)
        mix_widget=QWidget()
        mix_widget.setLayout(mixer_row)
        mix_widget.setMinimumWidth(mixer_row.sizeHint().width()+14)
        self.mixer_scroll=QScrollArea()
        self.mixer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.mixer_scroll.setWidgetResizable(True)
        self.mixer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.mixer_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.mixer_scroll.setFixedHeight(43)
        self.mixer_scroll.setWidget(mix_widget)
        body.addWidget(self.mixer_scroll)

        # v0.4.3: group controls in one compact tab bar instead of stacking
        # four permanently visible scrollable strips on top of the waveform.
        # Keep the old widgets/attributes intact for projects and QA tools.
        control_strips = (
            ('Playback', self.transport_scroll),
            ('Analysis', self.analysis_scroll),
            ('Candidates', self.candidate_scroll),
            ('Current / BPM', self.workspace_scroll),
            ('Audio', self.mixer_scroll),
        )
        self.controls_tabs = QTabWidget(self)
        self.controls_tabs.setObjectName('controlTabs')
        self.controls_tabs.setDocumentMode(True)
        self.controls_tabs.setUsesScrollButtons(False)
        self.controls_tabs.setFixedHeight(99)
        self.controls_tabs.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Fixed)
        for label, strip in control_strips:
            body.removeWidget(strip)
            strip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            strip.setMinimumHeight(48)
            strip.setMaximumHeight(64)
            self.controls_tabs.addTab(strip, label)
        body.addWidget(self.controls_tabs)

        self.graph_dock = QDockWidget('Candidate Processing Graph', self)
        self.graph_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.graph_panel = GraphPanel(self.graph)
        self.graph_panel.runRequested.connect(self.run_graph)
        self.graph_panel.modified.connect(self.on_graph_modified)
        self.graph_panel.nodeSelected.connect(self.on_node_selected)
        self.graph_dock.setWidget(self.graph_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.graph_dock)
        self.graph_dock.setMinimumWidth(330)
        self.graph_dock.resize(380, 600)
        self.resizeDocks([self.graph_dock], [380], Qt.Orientation.Horizontal)
        self.nodes_btn.toggled.connect(self.graph_dock.setVisible)
        self.graph_dock.visibilityChanged.connect(self.nodes_btn.setChecked)
        self.graph_dock.visibilityChanged.connect(
            lambda visible: self._on_dock_visibility_changed(self.graph_dock, visible))
        self.layer_dock = QDockWidget('Layer Inspector', self)
        self.layer_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.layer_inspector = LayerInspector()
        self.layer_inspector.changed.connect(self.on_layer_style_changed)
        self.layer_dock.setWidget(self.layer_inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.layer_dock)
        self.splitDockWidget(self.graph_dock, self.layer_dock, Qt.Orientation.Vertical)
        self.layer_dock.setMinimumHeight(155)
        self.resizeDocks([self.graph_dock, self.layer_dock], [430, 205], Qt.Orientation.Vertical)
        self.layers_btn.toggled.connect(self._toggle_layer_inspector)
        self.layer_dock.visibilityChanged.connect(self._sync_layer_button)
        self.layer_dock.visibilityChanged.connect(
            lambda visible: self._on_dock_visibility_changed(self.layer_dock, visible))
        self.diag_dock = QDockWidget('Timing Diagnostics', self)
        self.diagnostics_panel = DiagnosticsPanel()
        self.diag_dock.setWidget(self.diagnostics_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,self.diag_dock)
        self.tabifyDockWidget(self.layer_dock,self.diag_dock)
        self.diag_dock.hide()
        self.diag_btn.toggled.connect(self._toggle_diagnostics)
        self.diag_dock.visibilityChanged.connect(self._sync_diagnostics_button)

        self.mouse_help = (
            'Middle-drag: pan timeline / graph\nMiddle-click: seek\n'
            'Left-drag candidate ball: move\nRight-click ball: confirm / delete / exact time\n'
            'Right-click waveform: add candidate to Current output\nLeft-click empty: seek\n'
            'Wheel: timeline zoom\nF: follow playback\n'
            'Speed: 0.0–135.0% in 0.1% steps; 0% pauses playback\n'
            'Replace +/- radius: nearby drag/drop or right-click replaces candidate\n'
            'Exclude mode: left-drag region\n'
            'Current output: right-click empty, or copy/paste preview predictions\n'
            'Audition: short PCM-mixed clicks for candidate timing\n'
            'BPM guides: range start is bar downbeat; purely visual')
        footer = QLabel('Middle-drag: pan  •  Wheel: zoom  •  Right-click: candidates  •  F: follow  •  Ctrl+S: save')
        footer.setObjectName('footer')
        footer.setWordWrap(False)
        footer.setToolTip(self.mouse_help)
        body.addWidget(footer)

    def setup_menus(self):
        file_menu = self.menuBar().addMenu("&File")
        self.open_action = QAction("Open audio…", self)
        self.open_action.setShortcut(QKeySequence("Ctrl+O"))
        self.open_action.triggered.connect(self.open_dialog)
        file_menu.addAction(self.open_action)
        file_menu.addAction('Import osu! reference…', self.import_osu_dialog)
        file_menu.addAction('Remove osu! reference', self.remove_osu_reference)
        file_menu.addSeparator()
        open_session = QAction('Open BeatSolver project…', self)
        open_session.setShortcut(QKeySequence('Ctrl+Shift+O'))
        open_session.triggered.connect(self.open_project)
        file_menu.addAction(open_session)
        save_session = QAction('Save project', self)
        save_session.setShortcut(QKeySequence('Ctrl+S'))
        save_session.triggered.connect(self.save_project)
        file_menu.addAction(save_session)
        save_as = QAction('Save project as…', self)
        save_as.setShortcut(QKeySequence('Ctrl+Shift+S'))
        save_as.triggered.connect(lambda: self.save_project(save_as=True))
        file_menu.addAction(save_as)
        file_menu.addSeparator()
        quit_action = QAction("Exit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view = self.menuBar().addMenu("&View")
        zoom_in = QAction("Zoom in", self)
        zoom_in.setShortcut(QKeySequence("Ctrl++"))
        zoom_in.triggered.connect(lambda: self.zoom_manually(0.8))
        view.addAction(zoom_in)
        zoom_out = QAction("Zoom out", self)
        zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        zoom_out.triggered.connect(lambda: self.zoom_manually(1.25))
        view.addAction(zoom_out)
        fit = QAction("Fit entire song", self)
        fit.setShortcut(QKeySequence("Ctrl+0"))
        fit.triggered.connect(self.fit_manually)
        view.addAction(fit)
        view.addSeparator()
        self.follow_action = QAction("Follow playhead", self)
        self.follow_action.setCheckable(True)
        self.follow_action.setChecked(True)
        self.follow_action.setShortcut(QKeySequence("F"))
        self.follow_action.triggered.connect(self.set_follow_playhead)
        view.addAction(self.follow_action)
        view.addSeparator()
        play = QAction("Play / pause", self)
        play.setShortcut(QKeySequence("Space"))
        play.triggered.connect(self.toggle_play)
        view.addAction(play)
        reset_speed = QAction('Reset playback speed to 100%', self)
        reset_speed.setShortcut(QKeySequence('Ctrl+1'))
        reset_speed.triggered.connect(lambda: self.speed_spin.setValue(100.0))
        view.addAction(reset_speed)
        view.addSeparator()
        view.addAction(self.layer_dock.toggleViewAction())
        view.addAction('Show / hide Timing Diagnostics',
                       lambda: self.diag_btn.setChecked(not self.diag_btn.isChecked()))
        view.addAction('Show / hide Spectrogram',
                       lambda: self.spectrogram_check.setChecked(not self.spectrogram_check.isChecked()))
        self.show_osu_hits_action = view.addAction('Show osu! hit-object heads')
        self.show_osu_hits_action.setCheckable(True)
        self.show_osu_hits_action.setChecked(True)
        self.show_osu_hits_action.toggled.connect(
            lambda on: self._set_reference_visibility(hits=on))
        self.show_osu_timing_action = view.addAction('Show osu! red / green timing points')
        self.show_osu_timing_action.setCheckable(True)
        self.show_osu_timing_action.setChecked(True)
        self.show_osu_timing_action.toggled.connect(
            lambda on: self._set_reference_visibility(timing=on))

        graph_menu = self.menuBar().addMenu('&Graph')
        graph_menu.addAction('Run current graph', lambda: self.run_graph(self.graph_panel.output_selector.currentData()))
        graph_menu.addAction('Show / hide node graph', lambda: self.graph_dock.setVisible(not self.graph_dock.isVisible()))
        graph_menu.addAction('Show / hide Layer Inspector', lambda: self.layer_dock.setVisible(not self.layer_dock.isVisible()))
        graph_menu.addAction('Load Python plugin…', self.load_plugin)
        graph_menu.addAction('Insert Burst Cleanup before Preview output',
                             self.insert_burst_cleanup)
        graph_menu.addAction('Create Spectral Attack comparison branch', self.add_spectral_branch)
        graph_menu.addAction('Create typed Spectrogram comparison branch', self.add_typed_spectral_branch)
        graph_menu.addAction('Insert Evidence-Aware Cleanup before Preview output',
                             self.insert_evidence_cleanup)
        graph_menu.addAction('Reset starter graph…', self.reset_graph)
        annotations_menu = self.menuBar().addMenu('&Annotations')
        undo = QAction('Undo last annotation edit', self)
        undo.setShortcut(QKeySequence('Ctrl+Z'))
        undo.triggered.connect(self.undo_annotation)
        annotations_menu.addAction(undo)
        annotations_menu.addAction('Clear annotation edits…', self.clear_annotations)
        timing_menu = self.menuBar().addMenu('&Timing')
        timing_menu.addAction('Manage BPM guide ranges…', self.edit_bpm_ranges)
        timing_menu.addAction('Paste preview into Current output…', self.paste_preview)
        timing_menu.addAction('Clear Current output…', self.clear_current)
        timing_menu.addAction('Export Current output to CSV…', self.export_current)
        timing_menu.addAction('Export Preview evidence to CSV…', self.export_preview_evidence)
        timing_menu.addSeparator()
        timing_menu.addAction('Compare to imported osu! map…', self.compare_osu_dialog)
        timing_menu.addAction('Export osu! comparison CSV…', self.export_osu_comparison)
        timing_menu.addAction('Export Current as osu! onset-reference map…', self.export_osu_onsets)
        timing_menu.addSeparator()
        timing_menu.addAction('Audition clicks near playhead',
                              lambda: self.audition_at(self.timeline.playhead,self.audition_source.currentData()))
        timing_menu.addAction('Stop audition', self.stop_audition)

        colors = self.menuBar().addMenu("&Colors")
        for preset in THEMES:
            act = QAction(preset, self)
            act.triggered.connect(lambda checked=False, name=preset: self.set_theme(name))
            colors.addAction(act)
        colors.addSeparator()
        edit_colors = colors.addMenu("Customize individual colors")
        for key, label in COLOR_LABELS.items():
            action = QAction(label + "…", self)
            action.triggered.connect(lambda checked=False, key=key: self.choose_color(key))
            edit_colors.addAction(action)
        reset = QAction("Reset to Deep Ocean", self)
        reset.triggered.connect(lambda: self.set_theme("Deep Ocean"))
        colors.addAction(reset)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction('Mouse controls…', lambda: QMessageBox.information(
            self, 'Timeline mouse controls', self.mouse_help))
        help_menu.addSeparator()
        about = QAction("About this prototype", self)
        about.triggered.connect(lambda: QMessageBox.information(
            self, "BeatSolver Timeline v" + APP_VERSION,
            "First step toward a human-in-the-loop timing editor.\n\n"
            "v0.4 adds a smooth visual cursor, sample-aligned click auditions, BPM guides, and Current output.\n"
            "Generated candidates are hypotheses, not verified musical beats."))
        help_menu.addAction(about)

    def set_controls_enabled(self, enabled: bool):
        for widget in (self.play_btn, self.speed_slider, self.speed_spin,
                       self.zoom_in_btn, self.zoom_out_btn, self.fit_btn,
                       self.scrollbar, self.follow_btn, self.offset_spin,
                       self.analysis_resolution):
            widget.setEnabled(enabled)
        self.follow_action.setEnabled(enabled)

    def apply_colors(self):
        c = self.colors
        self.timeline.set_colors(c)
        self.curve_viewer.set_colors(c)
        self.spectrogram_viewer.set_colors(c)
        self.setStyleSheet(f"""
        QMainWindow, QWidget#central {{ background: {c['background']}; color: {c['text']}; }}
        QLabel {{ color: {c['text']}; background: transparent; }}
        QLabel#heading {{ font: 750 20px 'Segoe UI'; letter-spacing: 2px; }}
        QLabel#subtitle, QLabel#footer {{ color: {c['muted']}; font: 11px 'Segoe UI'; }}
        QLabel#trackName {{ font: 600 14px 'Segoe UI'; }}
        QLabel#timer {{ font: 12px Consolas; color: {c['muted']}; }}
        QPushButton {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']};
                       padding: 9px 14px; border-radius: 7px; font: 11px 'Segoe UI'; }}
        QPushButton:hover {{ border: 1px solid {c['waveform']}; }}
        QPushButton:checked {{ background: {c['grid']}; border: 1px solid {c['waveform']}; }}
        QPushButton:disabled {{ color: {c['muted']}; border-color: {c['grid']}; }}
        QDockWidget {{ background: {c['panel']}; color: {c['text']}; }}
        QComboBox, QDoubleSpinBox, QPlainTextEdit {{ background: {c['panel']}; color: {c['text']};
                       border: 1px solid {c['border']}; padding: 3px; }}
        QScrollArea {{ border: 1px solid {c['border']}; }}
        QTabWidget#controlTabs::pane {{ border: 1px solid {c['border']}; border-radius: 5px;
                                          background: {c['background']}; }}
        QTabBar::tab {{ background: {c['panel']}; color: {c['muted']};
                        padding: 5px 11px; min-width: 100px; border: 1px solid {c['border']}; }}
        QTabBar::tab:selected {{ background: {c['border']}; color: {c['text']}; }}
        QScrollArea#transportScroll {{ border: none; background: {c['background']}; }}
        QWidget#transportHost {{ background: {c['background']}; }}
        QMenuBar, QMenu {{ background: {c['panel']}; color: {c['text']}; }}
        QMenuBar::item:selected, QMenu::item:selected {{ background: {c['grid']}; }}
        QStatusBar {{ background: {c['panel']}; color: {c['muted']}; }}
        QScrollBar:horizontal {{ background: {c['panel']}; height: 16px; border: 0px; border-radius: 6px; }}
        QScrollBar::handle:horizontal {{ background: {c['border']}; min-width: 16px; border-radius: 5px; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        """)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if (self.isVisible() and hasattr(self, 'graph_dock')
                and hasattr(self, 'layer_dock')):
            self._update_compact_layout()

    def _toggle_diagnostics(self, checked: bool):
        # Raising a tabified dock requires more than setVisible(True).
        # Never wire visibilityChanged -> button.setChecked directly: Qt can
        # emit visibilityChanged(False) while switching between dock tabs,
        # causing a recursive toggle that hides the newly selected panel.
        if checked:
            self.diag_dock.show()
            self.diag_dock.raise_()
            self._refresh_diagnostics()
        else:
            self.diag_dock.hide()

    def _sync_diagnostics_button(self, visible: bool):
        # A tabified dock may be not visible even though it has not been
        # explicitly hidden. Only a genuine close should clear the button.
        if not visible and self.diag_dock.isHidden() and self.diag_btn.isChecked():
            self.diag_btn.blockSignals(True)
            self.diag_btn.setChecked(False)
            self.diag_btn.blockSignals(False)

    def _toggle_layer_inspector(self, checked: bool):
        if checked:
            self.layer_dock.show()
            self.layer_dock.raise_()
        else:
            self.layer_dock.hide()

    def _sync_layer_button(self, visible: bool):
        if not visible and self.layer_dock.isHidden() and self.layers_btn.isChecked():
            self.layers_btn.blockSignals(True)
            self.layers_btn.setChecked(False)
            self.layers_btn.blockSignals(False)

    def _on_dock_visibility_changed(self, dock, visible: bool):
        # A manual close/open while compact overrides the automatic restore.
        if self._compact_layout and not self._adapting_docks:
            self._auto_hidden_docks.discard(dock)

    def _update_compact_layout(self):
        """Give the waveform space when side panels cannot fit comfortably.

        Changes happen only when crossing the breakpoint; manual dock toggles
        inside compact mode are permitted and do not get undone on resize.
        """
        compact = self.width() < 1000 or self.height() < 605
        if self._compact_layout == compact:
            return
        self._compact_layout = compact
        self._adapting_docks = True
        try:
            if compact:
                self._auto_hidden_docks = {
                    dock for dock in (self.graph_dock, self.layer_dock)
                    if dock.isVisible() and not dock.isFloating()
                }
                for dock in self._auto_hidden_docks:
                    dock.hide()
            else:
                for dock in tuple(self._auto_hidden_docks):
                    dock.show()
                self._auto_hidden_docks.clear()
            self.resolution_label.setVisible(not compact)
        finally:
            self._adapting_docks = False
        if compact:
            self.statusBar().showMessage(
                'Compact layout: use Nodes / Layers in the top bar to open side panels.', 5000)

    def set_theme(self, theme: str):
        self.theme_name = theme
        self.colors = THEMES[theme].copy()
        self.settings.setValue("theme_name", theme)
        self.apply_colors()

    def choose_color(self, key: str):
        selection = QColorDialog.getColor(QColor(self.colors[key]), self, COLOR_LABELS[key])
        if selection.isValid():
            self.colors[key] = selection.name()
            self.custom_colors = self.colors.copy()
            self.theme_name = "Custom"
            self.settings.setValue("theme_name", "Custom")
            self.settings.setValue("custom_colors", json.dumps(self.custom_colors))
            self.apply_colors()

    def open_dialog(self):
        if self.loading:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open song", str(Path.home()),
            "Audio files (*.mp3 *.wav *.flac *.ogg);;MP3 (*.mp3);;All files (*)")
        if path:
            self.load_audio(path)

    def load_audio(self, path: str, *, from_project=False):
        # A stopped QThread may still own queued results/completion callbacks.
        if self.candidate_worker is not None:
            self.statusBar().showMessage('Finish the current graph run before opening another song.', 5000)
            return
        if self.session_dirty and not from_project and not self.maybe_discard_changes():
            return
        if self.loading:
            self.statusBar().showMessage("Finish loading the current song before opening another.", 5000)
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "File not found", "The selected file doesn't exist.")
            return
        if Path(path).suffix.lower() not in SUPPORTED_AUDIO:
            QMessageBox.warning(self, "Unsupported file", "Please select an MP3, WAV, FLAC, or OGG file.")
            return
        self.loading = True
        self._graph_request_generation += 1  # Cancel timers for the prior song.
        self._queued_graph_output = None
        self._queued_graph_generation = None
        self._last_graph_intermediates = None
        self._last_graph_output_id = None
        if not from_project:
            self.analysis_hop_ms = 2.0
            self.analysis_resolution.blockSignals(True)
            self.analysis_resolution.setCurrentIndex(self.analysis_resolution.findData(2.0))
            self.analysis_resolution.blockSignals(False)
            self.global_offset_ms = 0.0
            self.offset_spin.blockSignals(True)
            self.offset_spin.setValue(0.0)
            self.offset_spin.blockSignals(False)
            self.layer_inspector.set_saved_styles({})
            self.annotations = Annotations()
            self.working = WorkingCandidates()
            self.guides = TimingGuides()
            self.osu_reference = None
            self._last_comparison = None
            self.reference_label.setText('No osu! reference')
            self.guides_check.setChecked(True)
            self._undos.clear()
            self.session_path = None
            self.session_dirty = False
            self.candidate_output = CandidateSet(np.array([],dtype=np.float64), np.array([],dtype=np.float32))
        self._stop_stream(restore=False)
        self._stream_cached_path=None
        if self._stream_loader is not None and self._stream_loader.isRunning():
            self._stream_loader.requestInterruption()
        self._mix_generation+=1
        self._mix_debounce.stop()
        if self._mix_worker and self._mix_worker.isRunning():
            self._mix_worker.requestInterruption()
        self._mix_pending_transport=None
        self.file_path = path
        self.stop_audition()
        self.player.stop()
        self.playback_clock.reset(0)
        self.player.setSource(QUrl())
        self.timeline.reset_audio()
        self._diagnostic_curve_cache = None
        self.timeline.set_osu_reference(self.osu_reference)
        self.timeline.set_working(self.working)
        self.timeline.set_guides(self.guides)
        self.resolution_label.setText('Waveform: decoding…')
        self.timeline.set_global_offset(self.global_offset_ms)
        self.layer_inspector.reset_data()
        self.curve_viewer.set_layers({}, {}, {}, self.global_offset_ms / 1000.0)
        self.spectrogram_viewer.set_spectrum(None)
        self._selected_spectrum_request = None
        if self._spectrogram_worker is not None and self._spectrogram_worker.isRunning():
            self._spectrogram_worker.requestInterruption()
        self.curve_viewer.set_playhead(0.0)
        self.set_follow_playhead(True, snap=False)  # A new song starts in follow mode.
        self.track_name.setText(f"Loading: {Path(path).name}")
        self.track_name.setToolTip(path)
        self.set_controls_enabled(False)
        self.open_btn.setEnabled(False)
        self.open_action.setEnabled(False)
        self.statusBar().showMessage("Decoding waveform…")

        self.worker = DecodeThread(path, self)
        self.worker.loaded.connect(self.audio_loaded)
        self.worker.failed.connect(self.audio_failed)
        self.worker.finished.connect(self.thread_finished)
        self.worker.start()

    def audio_loaded(self, cache: WaveformCache):
        self.loading = False
        self.timeline.set_audio(cache.coarse_min, cache.coarse_max, cache.duration, cache)
        low, duration = cache.coarse_min, cache.duration
        self.timeline_split.setSizes([320, 180, 145] if self.spectrogram_enabled else [320,180,0])
        if self.spectrogram_enabled:
            self._begin_spectrogram()
        self.timeline.set_annotations(self.annotations)
        self.timeline.set_candidates(self.candidate_output)
        self.timeline.set_working(self.working)
        self.timeline.set_guides(self.guides)
        self.update_candidate_count()
        self.analysis_outdated = True
        self.track_name.setText(Path(self.file_path).name)
        self._refresh_osu_reference_label()
        self.position_label.setText(f"{pretty_time(0)} / {pretty_time(duration)}")
        self.player.setSource(QUrl.fromLocalFile(self.file_path))
        self.playback_clock.reset(0.0,duration=duration)
        self.player.setPlaybackRate(max(0.01, self.playback_speed_pct / 100.0))
        self._resume_after_zero = False
        self._resume_audition_after_zero = False
        self.set_controls_enabled(True)
        self.on_play_state(self.player.playbackState())
        self.update_resolution_label()
        self.open_btn.setEnabled(True)
        self.open_action.setEnabled(True)
        self.statusBar().showMessage(
            f"Loaded {Path(self.file_path).name} • {pretty_time(duration)} • "
            f"{cache.rate:,} Hz mono PCM" if cache.has_pcm else
            f"Loaded {Path(self.file_path).name} • coarse waveform only (memory limit)")
        if self._stream_requested() or self.continuous_hits.isChecked():
            self._schedule_mix_render()
        # Start the default chain immediately, without blocking GUI playback.
        current_output = self.graph_panel.output_selector.currentData()
        if current_output:
            self.run_graph(current_output)

    def audio_failed(self, message: str):
        self.loading = False
        self.open_btn.setEnabled(True)
        self.open_action.setEnabled(True)
        self.track_name.setText("No audio loaded")
        self.resolution_label.setText('Waveform: decode failed')
        self.statusBar().showMessage("Unable to load audio", 8000)
        QMessageBox.warning(self, "Audio decoding failed", message)

    @Slot()
    def thread_finished(self):
        # A late queued completion belongs to its original decoder, even if
        # another decoder has subsequently become active.
        completed = self.sender()
        if completed is None:
            return
        if completed is self.worker:
            self.worker = None
        completed.deleteLater()

    def on_media_error(self, error, description: str):
        if error != QMediaPlayer.Error.NoError:
            self.statusBar().showMessage(f"Playback error: {description}", 10000)

    def on_speed_slider_changed(self, value: int):
        self.speed_spin.setValue(slider_to_speed(value))

    def set_playback_speed(self, percent: float):
        """Apply 0.0–135.0% speed at one-decimal-percent UI precision.

        The precise UI step is a control granularity, not guaranteed device
        timing resolution. 0% is a real pause rather than a zero playback rate.
        """
        percent = clamp_speed_percent(percent)
        if abs(self.speed_spin.value() - percent) > 0.0001:
            self.speed_spin.blockSignals(True)
            self.speed_spin.setValue(percent)
            self.speed_spin.blockSignals(False)
        before = self.playback_speed_pct
        was_playing = (self._stream_controller.playing if self._stream_active else
                       self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        audition_was_playing = (self.audition_active and
            self.audition_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        self.playback_speed_pct = percent
        self.speed_slider.blockSignals(True)
        self.speed_slider.setValue(speed_to_slider(percent))
        self.speed_slider.blockSignals(False)
        if percent == 0.0:
            if before > 0:
                self._resume_after_zero = was_playing
                self._resume_audition_after_zero = audition_was_playing
            if was_playing:
                if self._stream_active:self._stream_controller.pause()
                else:self.player.pause()
            if audition_was_playing:
                self.audition_player.pause()
            self.play_btn.setText('Play (0%)')
            self.statusBar().showMessage('Playback speed 0%: paused; raise speed to resume.', 4500)
        else:
            if self._stream_active:
                self._stream_controller.set_rate(percent/100.0)
            else:
                self.player.setPlaybackRate(percent / 100.0)
            self.audition_player.setPlaybackRate(percent / 100.0)
            self.playback_clock.set_rate(percent / 100.0)
            self.audition_clock.set_rate(percent / 100.0)
            if before == 0.0 and self._resume_after_zero and self.timeline.has_audio:
                self._resume_after_zero = False
                if self._stream_active:self._stream_controller.play()
                else:self.player.play()
            if before == 0.0 and self._resume_audition_after_zero and self.audition_active:
                self._resume_audition_after_zero = False
                self.audition_player.play()
            self.on_play_state(self.player.playbackState())

    def on_replace_radius_changed(self, milliseconds: float):
        self.replace_radius_ms = float(milliseconds)
        self.timeline.set_replace_radius(milliseconds)
        if self.timeline.has_audio:
            self.mark_dirty()

    def toggle_play(self):
        if not self.timeline.has_audio:
            return
        if self.playback_speed_pct == 0:
            self._resume_after_zero = False
            self.statusBar().showMessage('Speed is 0% (paused). Increase speed to start playback.', 4500)
            return
        if self.audition_active:
            self.stop_audition()
            return
        if self._stream_active:
            if self._stream_controller.playing:
                self._stream_controller.pause()
            else:
                try:
                    self._stream_controller.play()
                except Exception as exc:
                    self._stream_failed(str(exc))
                    return
            self.on_play_state(self.player.playbackState())
        elif self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def on_play_state(self, state):
        if self._stream_active:
            is_playing=self._stream_controller.playing
        else:
            is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.playback_clock.set_playing(is_playing)
        if is_playing:
            self.cursor_timer.start()
        elif not self.audition_active:
            self.cursor_timer.stop()
        self.play_btn.setText('Play (0%)' if self.playback_speed_pct == 0 else
                              ('Pause' if is_playing else 'Play'))

    def on_position(self, pos: int):
        if self._stream_active:
            return
        self.timing_monitor.record_backend(self.playback_clock.displayed(),pos/1000.0)
        self.playback_clock.sync(pos/1000.0)
        if not self.audition_active:
            self.animate_cursor()

    def seek_to(self, t: float):
        if not self.timeline.has_audio:
            return
        if self.audition_active:
            self.stop_audition()
        t = max(0.0, min(self.timeline.duration,float(t)))
        self.playback_clock.reset(t)
        if self._stream_active:
            try:
                self._stream_controller.seek(t)
            except Exception as exc:
                self._stream_failed(str(exc))
            self.animate_cursor()
            return
        self.player.setPosition(round(t*1000))
        self.animate_cursor()

    def _cursor_tick(self):
        self.timing_monitor.record_timer()
        # Fallback for audio backends that miss QAudioSink's EOF transition.
        if self._stream_active:
            self._stream_controller.poll_completion()
        self.animate_cursor()

    def eventFilter(self, watched, event):
        if watched is getattr(self,'timeline',None) and event.type()==QEvent.Type.Paint:
            self.timing_monitor.record_paint()
        return super().eventFilter(watched,event)

    def _refresh_diagnostics(self):
        if hasattr(self,'diagnostics_panel') and self.diag_dock.isVisible():
            self.diagnostics_panel.update_snapshot(self.timing_monitor.snapshot())

    def animate_cursor(self):
        if not self.timeline.has_audio:
            return
        if self.audition_active:
            seconds = self.audition_start + self.audition_clock.displayed()
        elif self._stream_active:
            seconds = self._stream_controller.position()
            self.playback_clock.reset(seconds)
        else:
            seconds = self.playback_clock.displayed()
        audio_seconds = max(0.0,min(self.timeline.duration,seconds))
        # Cursor sync is specified in wall milliseconds. Slow playback covers
        # fewer song milliseconds per wall millisecond, so scale the visual
        # adjustment by the active playback speed.
        visual_seconds = calibrated_visual_position(
            audio_seconds, self.cursor_sync_ms, self.playback_speed_pct,
            self.timeline.duration)
        self.curve_viewer.set_playhead(visual_seconds)
        if self.spectrogram_enabled:
            self.spectrogram_viewer.set_playhead(visual_seconds)
        self.timeline.set_playhead(visual_seconds, follow=self.follow_playhead and (
            self.audition_active or self.playback_clock.playing))
        self.position_label.setText(f'{pretty_time(audio_seconds,3)} / {pretty_time(self.timeline.duration)}')

    def change_cursor_fps(self, index):
        self.cursor_fps = int(self.cursor_fps_combo.currentData())
        self.cursor_timer.setInterval(max(1,round(1000/self.cursor_fps)))

    def on_cursor_sync_changed(self, milliseconds):
        self.cursor_sync_ms = float(milliseconds)
        self.animate_cursor()
        if self.timeline.has_audio:
            self.mark_dirty()

    def toggle_pitch_compensation(self, enabled):
        if not self.pitch_check.isEnabled():
            return
        try:
            self.player.setPitchCompensation(bool(enabled))
            if hasattr(self.audition_player,'setPitchCompensation'):
                self.audition_player.setPitchCompensation(bool(enabled))
        except Exception as exc:
            self.statusBar().showMessage('Pitch compensation unavailable: '+str(exc),5000)


    def set_follow_playhead(self, enabled: bool, *, snap: bool = True):
        """Keep the toolbar button and View-menu checkbox in sync.

        When reattaching, jump back to the playhead immediately even if the
        playhead has continued offscreen while the user was inspecting audio.
        """
        self.follow_playhead = bool(enabled)
        self.follow_btn.blockSignals(True)
        self.follow_btn.setChecked(self.follow_playhead)
        self.follow_btn.setText("Follow: ON" if self.follow_playhead else "Follow: OFF")
        self.follow_btn.blockSignals(False)
        self.follow_action.blockSignals(True)
        self.follow_action.setChecked(self.follow_playhead)
        self.follow_action.blockSignals(False)
        if self.follow_playhead and snap and self.timeline.has_audio:
            self.timeline.set_playhead(self.timeline.playhead, follow=True)
        self.follow_btn.setToolTip(
            "Following playback. Drag, scroll, or zoom to detach."
            if self.follow_playhead else
            "Detached: the playback position won't move this view. Click to reattach, or press F."
        )

    def detach_from_playhead(self):
        if self.follow_playhead:
            self.set_follow_playhead(False)
            self.statusBar().showMessage("View detached from playback. Press F or click Follow: OFF to reattach.", 5000)

    def on_scrollbar_changed(self, value: int):
        # Programmatic scrollbar updates are signal-blocked in on_view_changed.
        # This slot therefore handles deliberate scrollbar movement only.
        self.detach_from_playhead()
        self.timeline.pan_to(value / 1000.0)

    def zoom_manually(self, factor: float):
        if self.timeline.has_audio:
            self.detach_from_playhead()
            self.timeline.zoom(factor)

    def fit_manually(self):
        if self.timeline.has_audio:
            self.detach_from_playhead()
            self.timeline.fit()

    def on_view_changed(self):
        canvas = self.timeline
        if not canvas.has_audio:
            self.scrollbar.setEnabled(False)
            self.zoom_label.setText("100% of song")
            return
        self.scrollbar.blockSignals(True)
        self.scrollbar.setRange(0, round(max(0, canvas.duration - canvas.view_span) * 1000))
        self.scrollbar.setPageStep(max(1, round(canvas.view_span * 1000)))
        self.scrollbar.setValue(round(canvas.view_start * 1000))
        self.scrollbar.blockSignals(False)
        multiplier = canvas.duration / canvas.view_span
        self.zoom_label.setText(f"{multiplier:.1f}× zoom")
        self.update_resolution_label()
        self.curve_viewer.update()
        if self.spectrogram_enabled:
            self.spectrogram_viewer.update()

    def update_resolution_label(self):
        cache = self.timeline.audio_cache
        if cache is None:
            self.resolution_label.setText('Waveform: waiting for audio')
            return
        if cache.has_pcm:
            samples_per_pixel = cache.rate * self.timeline.view_span/self.timeline.plot_width()
            mode = ('PCM samples' if samples_per_pixel <= 1.5 else 'adaptive envelope')
            self.resolution_label.setText(
                f'{cache.rate / 1000:g} kHz • {mode} • '
                f'analysis {round(self.analysis_hop_ms*cache.rate/1000)/cache.rate*1000:.3f} ms')
        else:
            self.resolution_label.setText(
                f'Coarse {cache.coarse_bin_frames / cache.rate*1000:.2f} ms waveform '
                '(PCM memory cap)')

    def toggle_spectrogram(self, checked: bool):
        self.spectrogram_enabled=bool(checked)
        self.spectrogram_viewer.setVisible(self.spectrogram_enabled)
        if self.spectrogram_enabled:
            self.timeline_split.setSizes([300,145,130])
            self._begin_spectrogram()
        else:
            self.timeline_split.setSizes([320,180,0])
        if self.timeline.has_audio:
            self.mark_dirty()

    def change_spectrogram_mode(self, index: int):
        mode=self.spectrogram_mode_combo.currentData()
        if mode:
            self.spectrogram_mode=str(mode)
            self.spectrogram_viewer.set_mode(self.spectrogram_mode)
            if self.timeline.has_audio:
                self.mark_dirty()

    @staticmethod
    def _spectral_signature(features):
        """Comparable physical-analysis signature (not filename or RGB pixels)."""
        return (str(features.window_mode), round(float(features.window_ms), 4),
                int(features.hop_samples))

    @staticmethod
    def _selected_graph_spectrum(graph, output_id, rate, preferred_id=None):
        """Return the user's contributing typed STFT signature, if present."""
        selected = choose_spectrum(graph, output_id, rate, preferred_id)
        return selected.signature if selected is not None else None

    def _refresh_spectrogram_sources(self):
        """Keep the Analysis picker scoped to the selected Candidate Output."""
        if not hasattr(self, 'spectrogram_source_combo') or not hasattr(self, 'graph_panel'):
            return
        cache = self.timeline.audio_cache
        rate = cache.rate if cache is not None else 48000
        output_id = self.graph_panel.output_selector.currentData()
        available = contributing_spectra(self.graph, output_id, rate)
        combo = self.spectrogram_source_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem('Auto (first branch)', None)
        for source in available:
            combo.addItem(source.label, source.node_id)
        # Preserve explicit selection only if it still contributes to this output.
        preferred = self._spectrogram_source_id
        index = combo.findData(preferred) if preferred is not None else 0
        if index < 0:
            preferred = None
            index = 0
        combo.setCurrentIndex(index)
        combo.setEnabled(len(available) > 1)
        combo.setToolTip(('Multiple upstream spectra: choose the evidence to display.'
                          if len(available) > 1 else
                          'Viewer follows the sole contributing spectrogram.'))
        combo.blockSignals(False)
        self._spectrogram_source_id = preferred
        self._sync_spectrogram_detail()

    def _sync_spectrogram_detail(self):
        """Display the selected contributing graph node's *actual* hop setting."""
        if not hasattr(self, 'spectrogram_detail_combo') or not hasattr(self, 'graph_panel'):
            return
        combo = self.spectrogram_detail_combo
        output_id = self.graph_panel.output_selector.currentData()
        cache = self.timeline.audio_cache
        selected = choose_spectrum(self.graph, output_id,
                                   cache.rate if cache is not None else 48000,
                                   self._spectrogram_source_id)
        combo.blockSignals(True)
        if selected is None:
            combo.setEnabled(False)
            combo.setCurrentIndex(combo.findData(1))
        else:
            combo.setEnabled(True)
            hop = float(self.graph.nodes[selected.node_id].params.get('hop_ms',6.0))
            index = min(range(combo.count()),
                        key=lambda i: abs(6.0 / float(combo.itemData(i)) - hop))
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def change_spectrogram_detail(self, index: int):
        """Opt-in 2×/4× real STFT time sampling for the selected graph source.

        Changing the graph also changes its spectral Preview candidates. The
        original audio, Current annotations and alternate branches are untouched.
        """
        factor = self.spectrogram_detail_combo.itemData(index)
        if not factor or not hasattr(self, 'graph_panel'):
            return
        output_id = self.graph_panel.output_selector.currentData()
        cache = self.timeline.audio_cache
        selected = choose_spectrum(self.graph, output_id,
                                   cache.rate if cache is not None else 48000,
                                   self._spectrogram_source_id)
        if selected is None:
            self._sync_spectrogram_detail()
            self.statusBar().showMessage('Select a typed Spectrogram graph branch first.', 5000)
            return
        node = self.graph.nodes[selected.node_id]
        wanted = 6.0 / float(factor)
        if abs(float(node.params.get('hop_ms',6.0))-wanted) < 1e-6:
            return
        node.params['hop_ms'] = wanted
        self.graph_panel.show_selected()
        self.on_graph_modified()
        self.statusBar().showMessage(
            f'Spectral time detail {factor}× ({wanted:g} ms hop): '
            'rerunning graph; higher detail uses more memory.', 6500)
        if self.timeline.has_audio:
            self.run_graph(output_id)

    def change_spectrogram_source(self, index: int):
        """Switch evidence view without recalculating or changing Preview."""
        if index < 0 or not hasattr(self, 'graph_panel'):
            return
        self._spectrogram_source_id = self.spectrogram_source_combo.currentData()
        self._sync_spectrogram_detail()
        cache = self.timeline.audio_cache
        if cache is None:
            return
        output_id = self.graph_panel.output_selector.currentData()
        selected = choose_spectrum(self.graph, output_id, cache.rate,
                                   self._spectrogram_source_id)
        self._selected_spectrum_request = selected.signature if selected else None
        evidence = None
        if (selected is not None and self._last_graph_output_id == output_id
                and self._last_graph_intermediates is not None):
            evidence = self._last_graph_intermediates.get(selected.node_id, {}).get('spectrogram')
        if isinstance(evidence, SpectrogramData):
            cache.spectral_features = evidence.features
            if self.spectrogram_enabled:
                self.spectrogram_viewer.set_spectrum(evidence.features)
        else:
            # Never show another branch's results as though this source produced them.
            if self.spectrogram_enabled:
                self.spectrogram_viewer.set_spectrum(None)
            if selected is None:
                self._begin_spectrogram()
            elif self.candidate_worker is None:
                self.statusBar().showMessage('Run Graph to display this spectral source.', 4500)
        self.mark_dirty()

    def _begin_spectrogram(self):
        cache = self.timeline.audio_cache
        if not self.spectrogram_enabled or cache is None:
            return
        if cache.pcm is None:
            self.statusBar().showMessage('Spectrogram unavailable: PCM exceeded the memory cap.',6000)
            return
        stored = getattr(cache, 'spectral_features', None)
        expected = (self._selected_spectrum_request if self._selected_spectrum_request is not None
                    else ('legacy_fft', 46., max(1, round(cache.rate * .006))))
        if stored is not None and self._spectral_signature(stored) == expected:
            self.spectrogram_viewer.set_spectrum(stored)
            return
        if self._selected_spectrum_request is not None:
            # An explicit typed graph will supply its own selected spectrum.
            # Do not schedule an unrelated legacy calculation as its substitute.
            return
        worker = self._spectrogram_worker
        if worker is not None:
            # Even after isRunning() turns false, its queued finished signal
            # must be processed before installing a replacement thread.
            self._spectrogram_pending = True
            if worker.isRunning():
                worker.requestInterruption()
            return
        worker = SpectrogramThread(cache, self)
        self._spectrogram_worker = worker
        worker.ready.connect(lambda features,c=cache: self._spectrogram_ready(c,features))
        worker.failed.connect(lambda message:self.statusBar().showMessage('Spectrogram: '+message,8000))
        worker.finished.connect(self._spectrogram_finished)
        worker.start()

    def _spectrogram_ready(self, cache, features):
        if cache is not self.timeline.audio_cache:
            return
        selected = self._selected_spectrum_request
        if selected is not None and self._spectral_signature(features) != selected:
            # Late legacy worker finished after a typed graph selected another
            # STFT mode. Never replace the graph's evidence or its cache pointer.
            return
        if selected is None and self._spectral_signature(features) != (
                'legacy_fft', 46., max(1, round(cache.rate * .006))):
            return
        cache.spectral_features = features
        if self.spectrogram_enabled:
            self.spectrogram_viewer.set_spectrum(features)
            self.statusBar().showMessage('Spectrogram ready: switch Attack / Harmonics in Analysis.',5500)

    @Slot()
    def _spectrogram_finished(self):
        completed = self.sender()
        if completed is None:
            return
        if completed is not self._spectrogram_worker:
            completed.deleteLater()
            return  # Stale completion must never clear the new active worker.
        self._spectrogram_worker = None
        completed.deleteLater()
        if self._spectrogram_pending:
            self._spectrogram_pending = False
            self._begin_spectrogram()

    def on_curve_display_changed(self, checked: bool):
        self.curve_lines = bool(checked)
        self.curve_viewer.connect_samples = self.curve_lines
        self.curve_viewer.update()
        if self.timeline.has_audio:
            self.mark_dirty()

    def on_analysis_resolution_changed(self, index: int):
        value = self.analysis_resolution.itemData(index)
        if value is None:
            return
        value = float(value)
        if abs(value-self.analysis_hop_ms) < 1e-8:
            return
        if self.timeline.has_audio and self.candidate_worker and self.candidate_worker.isRunning():
            self.analysis_resolution.blockSignals(True)
            self.analysis_resolution.setCurrentIndex(self.analysis_resolution.findData(self.analysis_hop_ms))
            self.analysis_resolution.blockSignals(False)
            self.statusBar().showMessage('Graph is running; change the analysis interval after it finishes.', 6000)
            return
        cache = self.timeline.audio_cache
        if cache and not cache.has_pcm and value < cache.coarse_bin_frames/cache.rate*1000 - 1e-5:
            self.analysis_resolution.blockSignals(True)
            self.analysis_resolution.setCurrentIndex(self.analysis_resolution.findData(self.analysis_hop_ms))
            self.analysis_resolution.blockSignals(False)
            self.statusBar().showMessage('High-resolution PCM unavailable: this song exceeded the 192 MiB cache limit.', 6000)
            return
        self.analysis_hop_ms = value
        self._diagnostic_curve_cache = None
        self.update_resolution_label()
        if self.timeline.has_audio:
            self.graph_revision += 1
            self.analysis_outdated = True
            self.mark_dirty()
            self.run_graph(self.graph_panel.output_selector.currentData())

    def on_offset_changed(self, milliseconds: float):
        """Apply a *single* display-time shift; the analysis cache remains untouched."""
        if abs(self.global_offset_ms - milliseconds) < 1e-10:
            return
        self.global_offset_ms = float(milliseconds)
        self.timeline.set_global_offset(milliseconds)
        self.sync_curve_layers()
        self.update_candidate_count()
        self.layer_inspector.set_cursor(self.layer_inspector.cursor_time, milliseconds / 1000.0,
                                        self.layer_inspector.sample_index)
        if self.timeline.has_audio:
            self.mark_dirty()

    def sync_curve_layers(self):
        inspector = self.layer_inspector
        self.curve_viewer.set_layers(inspector.curves, inspector.styles,
                                     inspector.labels, self.global_offset_ms / 1000.0)

    def on_layer_style_changed(self):
        self.sync_curve_layers()
        if self.timeline.has_audio:
            self.mark_dirty()

    def on_node_selected(self, uid: str):
        self.layer_inspector.select_node(uid)
        if self.layer_inspector.curves:
            self.layer_dock.show()
        self.sync_curve_layers()

    def pcm_sample_at(self, seconds: float):
        """Nearest actual decoded PCM sample to a cursor timestamp."""
        cache = self.timeline.audio_cache
        if seconds < 0 or cache is None or not cache.has_pcm:
            return seconds, None
        index = max(0, min(cache.frames-1, round(seconds*cache.rate)))
        return index/cache.rate, index

    def cursor_from_timeline(self, seconds: float):
        self._last_hover_time = seconds
        display_seconds, index = self.pcm_sample_at(seconds)
        self.curve_viewer.set_hover(display_seconds)
        if self.spectrogram_enabled:
            self.spectrogram_viewer.set_hover(display_seconds)
        self.layer_inspector.set_cursor(display_seconds,
                                        self.global_offset_ms / 1000.0, index)

    def cursor_from_curves(self, seconds: float):
        self._last_hover_time = seconds
        display_seconds, index = self.pcm_sample_at(seconds)
        self.timeline.set_external_hover(display_seconds)
        self.curve_viewer.set_hover(display_seconds)
        if self.spectrogram_enabled:
            self.spectrogram_viewer.set_hover(display_seconds)
        self.layer_inspector.set_cursor(display_seconds,
                                        self.global_offset_ms / 1000.0, index)

    def set_edit_mode(self, mode: str):
        self.timeline.edit_mode = mode
        self.statusBar().showMessage(f'Timeline mode: {mode}', 3000)

    def on_priority_changed(self, *args):
        self.priority_threshold_pct=self.priority_slider.value()
        self.priority_dedup_ms=self.priority_radius_spin.value()
        self.priority_label.setText(f'{self.priority_threshold_pct}%')
        self.timeline.set_priority(self.priority_threshold_pct/100.0,self.priority_dedup_ms)
        self.update_candidate_count()
        if self.timeline.has_audio:
            self.mark_dirty()

    def on_mixer_changed(self, *args):
        self.music_gain=self.music_slider.value()
        self.click_gain=self.click_slider.value()
        self.continuous_source=self.live_source.currentData()
        if self._stream_active:
            self._update_stream_events()
        elif not self.continuous_hits.isChecked():
            self.audio_output.setVolume(self.music_gain/100.0)
        else:
            self._schedule_mix_render()
        if self.timeline.has_audio:
            self.mark_dirty()

    def _stream_requested(self):
        return (self.sample_clock_check.isChecked() or
                (self.continuous_hits.isChecked() and self.stream_mode.isChecked()))

    def on_sample_clock_toggle(self, enabled):
        if not self.file_path or not self.timeline.has_audio:
            return
        if enabled:
            self._mix_generation += 1
            self._mix_debounce.stop()
            if self._mix_worker and self._mix_worker.isRunning():
                self._mix_worker.requestInterruption()
            self._begin_stream_decode()
        elif not self._stream_requested():
            self._stop_stream(restore=True)
            if self.continuous_hits.isChecked():
                self._schedule_mix_render()
        if self.timeline.has_audio:
            self.mark_dirty()

    def on_stream_mode_changed(self, enabled):
        if not self.file_path or not self.timeline.has_audio:
            return
        if self._stream_requested():
            self._mix_debounce.stop()
            self._mix_generation += 1
            if self._mix_worker is not None and self._mix_worker.isRunning():
                self._mix_worker.requestInterruption()
            if self._stream_active:
                self._update_stream_events()
            else:
                self._begin_stream_decode()
        else:
            self._stop_stream(restore=True)
            self._schedule_mix_render()

    def _begin_stream_decode(self):
        if self._stream_cached_path==self.file_path and self._stream_controller.mixer is not None:
            if not self._stream_active:
                try:
                    self._activate_stream()
                except Exception as exc:
                    self._stream_failed(str(exc))
            else:
                self._update_stream_events()
            return
        if self._stream_loader is not None and self._stream_loader.isRunning():
            return
        if not self._stream_requested():
            return
        self.mix_status.setText('Decoding stereo for PCM playback…')
        loader=StereoDecodeThread(self.file_path,self)
        self._stream_loader=loader
        loader.ready.connect(self._stream_decoded)
        loader.failed.connect(self._stream_decode_failed)
        loader.finished.connect(lambda obj=loader: self._stream_loader_finished(obj))
        loader.finished.connect(loader.deleteLater)
        loader.start()

    def _stream_loader_finished(self,loader):
        if self._stream_loader is loader:
            self._stream_loader=None
        if (self._stream_requested() and self.timeline.has_audio
                and not self._stream_active and self._stream_cached_path != self.file_path):
            # A cancelled decode of an older song must not permanently block
            # streaming on the newly loaded song.
            self._begin_stream_decode()

    def _stream_decode_failed(self, message, source):
        if source==self.file_path and self._stream_requested():
            self._stream_failed('PCM playback unavailable: '+message)

    def _stream_decoded(self,rate,pcm,source):
        if source != self.file_path or not self._stream_requested():
            return
        try:
            cache=self.timeline.audio_cache
            if (cache is not None and not analysis_and_playback_share_timebase(
                    cache.rate, cache.frames, rate, len(pcm))):
                raise RuntimeError('PCM decoder timelines differ; cannot guarantee '
                                   'alignment between waveform and streamed audio')
            self._stream_controller.load(rate,pcm)
            self._stream_cached_path=source
            self._activate_stream()
        except Exception as exc:
            self._stream_failed(str(exc))

    def _activate_stream(self):
        if self._stream_active:return
        pos=self.player.position()/1000.0
        was_playing=self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        self._stream_start_was_playing=was_playing
        self._mix_debounce.stop()
        self._mix_generation+=1  # invalidate any stale WAV render callbacks
        if self._mix_worker is not None and self._mix_worker.isRunning():
            self._mix_worker.requestInterruption()
        self._stream_controller.seek(pos)
        self._stream_controller.set_rate(self.playback_speed_pct/100.0)
        self._stream_active=True
        self.player.stop()
        self._update_stream_events()
        if not self._stream_active:
            return  # An invalid source fell back to the legacy mixer.
        if was_playing and self.playback_speed_pct>0:
            self._stream_controller.play()  # caller routes errors to fallback
        self._stream_start_was_playing=False
        self.playback_clock.reset(pos)
        self.on_play_state(self.player.playbackState())
        self.mix_status.setText('PCM clock: live buffers (no mix WAV)')

    def _update_stream_events(self):
        if not self._stream_active:return
        try:
            events=(click_events(self.timeline.preview_times,self.working.times,
                                self.guides,self.continuous_source,self.timeline.duration)
                    if self.continuous_hits.isChecked() else [])
            self._stream_controller.update_mix(events,self.music_gain/100.,
                                               self.click_gain/100. if events else 0.0)
            self.mix_status.setText(f'PCM clock: {len(events):,} hitsounds')
        except Exception as exc:
            self._stream_failed(str(exc))

    def _stop_stream(self,restore=False):
        if not self._stream_active:return
        pos=self._stream_controller.position()
        was_playing=self._stream_controller.playing or self._stream_start_was_playing
        self._stream_start_was_playing=False
        self._stream_active=False
        self._stream_controller.stop()
        if restore and self.file_path:
            self._switch_audio_source(self.file_path)
            # When the native source was already selected, _switch_audio_source
            # returns early. Direct position/playing restore covers that case.
            if self._mix_pending_transport is None:
                self.player.setPosition(round(pos*1000))
                if was_playing and self.playback_speed_pct>0:self.player.play()
            else:
                self._mix_pending_transport=(round(pos*1000),was_playing)
        if restore:
            self.audio_output.setVolume(self.music_gain/100.0)
        self.playback_clock.reset(pos)
        self.on_play_state(self.player.playbackState())

    def _on_stream_finished(self):
        # The QAudioSink has drained the last PCM frame. Synchronize the
        # transport button and cursor with the stopped streaming controller.
        if not self._stream_active:
            return
        self.playback_clock.reset(self._stream_controller.position())
        self.on_play_state(self.player.playbackState())
        self.animate_cursor()

    def _stream_failed(self,message):
        self.statusBar().showMessage('PCM playback fallback: '+str(message),9000)
        self.mix_status.setText('PCM unavailable; using media-player fallback')
        self._stop_stream(restore=True)
        # Disable both requests before retrying: otherwise a failed decoder
        # can endlessly start another worker on the same unsupported file.
        for checkbox in (self.sample_clock_check, self.stream_mode):
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        if self.continuous_hits.isChecked():
            self._schedule_mix_render()

    def on_continuous_toggle(self, enabled: bool):
        if enabled:
            if not self.file_path or not self.timeline.has_audio:
                self.continuous_hits.blockSignals(True)
                self.continuous_hits.setChecked(False)
                self.continuous_hits.blockSignals(False)
                self.statusBar().showMessage('Load a song before enabling continuous clicks.', 4500)
                return
            self.mix_status.setText('Preparing mix…')
            self._schedule_mix_render()
        else:
            self._mix_debounce.stop()
            self._mix_generation += 1
            if self._mix_worker and self._mix_worker.isRunning():
                self._mix_worker.requestInterruption()
            if self._stream_requested():
                # PCM remains the audio master; simply turn off the clicks.
                if self._stream_active:
                    self._update_stream_events()
                else:
                    self._begin_stream_decode()
            else:
                self._stop_stream(restore=True)
                self._switch_audio_source(self.file_path)
                self.audio_output.setVolume(self.music_gain/100.0)
            self._mix_file=None
            QTimer.singleShot(2500, self._cleanup_mix_files)
            if not self._stream_active:
                self.mix_status.setText('Live clicks off')
        if self.timeline.has_audio:
            self.mark_dirty()

    def _schedule_mix_render(self):
        if not self.file_path or not self.timeline.has_audio:
            return
        if self._stream_requested():
            if self._stream_active:
                self._update_stream_events()
            else:
                self._begin_stream_decode()
            return
        if not self.continuous_hits.isChecked():
            return
        self._mix_generation+=1
        self._mix_debounce.start()

    def _begin_mix_render(self):
        if not self.continuous_hits.isChecked() or not self.file_path or not self.timeline.has_audio:
            return
        if self._mix_worker is not None:
            if self._mix_worker.isRunning():
                self._mix_queued=True
                self._mix_worker.requestInterruption()
                return
            # The finished signal should normally have removed the worker.
            self._mix_worker.deleteLater()
            self._mix_worker=None
        self._mix_queued=False
        try:
            events=click_events(self.timeline.preview_times,self.working.times,
                                self.guides,self.continuous_source,self.timeline.duration)
        except Exception as exc:
            self.mix_status.setText('Mix unavailable')
            self.statusBar().showMessage('Cannot create continuous clicks: '+str(exc),7000)
            return
        import uuid
        target=str(Path(self._mix_temp.name)/('mix_'+uuid.uuid4().hex+'.wav'))
        self._mix_running_path=target
        revision=self._mix_generation
        source_path=self.file_path
        worker=ContinuousMixThread(source_path,target,events,self.music_gain/100.,self.click_gain/100.,self)
        self._mix_worker=worker
        worker.ready.connect(lambda path,r=revision,src=source_path:self._mix_ready(path,r,src))
        worker.error.connect(self._mix_error)
        worker.finished.connect(self._mix_finished)
        self.mix_status.setText(f'Mixing {len(events):,} clicks…')
        worker.start()

    def _mix_error(self, message):
        if self._stream_active:return
        self.mix_status.setText('Mix failed')
        self.statusBar().showMessage('Continuous hitsound mix failed: '+message,9000)

    def _mix_ready(self, path, revision, source_path):
        if (revision != self._mix_generation or not self.continuous_hits.isChecked()
                or self._stream_active or source_path != self.file_path):
            Path(path).unlink(missing_ok=True)
            return
        self._mix_file=path
        self._mix_files.append(path)
        self._switch_audio_source(path)
        QTimer.singleShot(2500, self._cleanup_mix_files)
        self.mix_status.setText('Clicks synchronized')

    def _cleanup_mix_files(self):
        # Only unlink tracks the multimedia backend has already released.
        active=str(self.player.source().toLocalFile())
        writing=self._mix_running_path if self._mix_worker and self._mix_worker.isRunning() else None
        keep=[]
        for path in self._mix_files:
            if path in (active,writing,self._mix_file):
                keep.append(path)
                continue
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                keep.append(path)
        self._mix_files=keep

    def _mix_finished(self):
        worker=self._mix_worker
        self._mix_worker=None
        if worker is not None:
            worker.deleteLater()
        if self._mix_queued and self.continuous_hits.isChecked():
            self._mix_queued=False
            self._mix_debounce.start(0)

    def _switch_audio_source(self, path):
        if not path:
            return
        desired=QUrl.fromLocalFile(str(path))
        if self.player.source()==desired:
            self.audio_output.setVolume(1.0 if self.continuous_hits.isChecked() else self.music_gain/100.0)
            return
        pos=self.player.position()
        playing=self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        self.playback_clock.set_playing(False)
        self.player.stop()
        self._mix_pending_transport=(pos,playing)
        self.audio_output.setVolume(1.0 if self.continuous_hits.isChecked() else self.music_gain/100.0)
        self.player.setSource(desired)

    def _on_mix_media_status(self, status):
        if self._mix_pending_transport is None:
            return
        if status not in (QMediaPlayer.MediaStatus.LoadedMedia,
                          QMediaPlayer.MediaStatus.BufferedMedia):
            return
        pos,playing=self._mix_pending_transport
        self._mix_pending_transport=None
        self.player.setPosition(pos)
        self.playback_clock.reset(pos/1000.0)
        if self.playback_speed_pct>0:
            self.player.setPlaybackRate(self.playback_speed_pct/100.)
            if playing:
                self.player.play()

    def update_candidate_count(self):
        raw = len(self.candidate_output.times)
        shown = len(self.timeline.preview_times)
        current = len(self.working.times)
        self.count_label.setText(f'{raw:,} graph / {shown:,} preview / {current:,} current')
        self.count_label.setToolTip('Preview uses a reversible score threshold and optional duplicate pruning. Current is untouched.')
        if hasattr(self,'diagnostics_panel'):
            self.diagnostics_panel.update_candidates(self.timeline.priority_result)

    def on_candidate_edit(self, action: str, start: float, end: float):
        # Computed markers are displayed at raw_time + global_offset. Convert
        # only their ORIGINAL position back before writing persistent rejections.
        kind = action.split(':', 1)[1] if ':' in action else ''
        original_raw = start - self.global_offset_ms / 1000.0 if kind == 'computed' else start
        if action == 'miss':
            self.statusBar().showMessage('Zoom in to inspect individual candidate handles.', 4500)
            return
        self.push_undo()
        if action == 'add-current':
            if not self.working.add(start):
                self._undos.pop(); return
        elif action.startswith('promote:'):
            if not self.working.add(start):
                self._undos.pop(); return
        elif action == 'add':
            if not self.annotations.add(start):
                self.statusBar().showMessage('Cannot add inside an excluded region; remove the mask first.', 5000)
                self._undos.pop(); return
        elif action == 'exclude':
            self.annotations.add_exclusion(start,end)
        elif action == 'unmask':
            self.annotations.remove_exclusion(start)
        elif action == 'delete:current':
            if not self.working.remove(start):
                self._undos.pop(); return
        elif action.startswith('delete:'):
            if not self.annotations.remove(original_raw, kind):
                self._undos.pop(); return
        elif action.startswith('confirm:'):
            if kind == 'computed':
                # Lock the accepted timestamp on the waveform, while suppressing
                # the original raw detection after future global-offset changes.
                valid = self.annotations.confirm(start)
                if valid:
                    self.annotations.remove(original_raw, 'computed')
            else:
                valid = self.annotations.confirm_existing(start, kind)
            if not valid:
                self._undos.pop(); return
        elif action == 'move:current':
            if not self.working.move(start,end):
                self._undos.pop(); return
        elif action.startswith('move:'):
            if not self.annotations.relocate(original_raw,end,kind):
                self.statusBar().showMessage('Cannot move marker here (check exclusion region or destination).', 4500)
                self._undos.pop(); return
        else:
            self._undos.pop(); return
        self.timeline.refresh_candidates()
        self.update_candidate_count()
        self.mark_dirty()

    def on_candidate_replaced(self, source_kind: str, source_time: float,
                              destination: float, target_kind: str, target_time: float):
        """Commit a visual replacement as ONE undoable, persistent annotation.

        Never mutate graph predictions. Computed timestamps are converted from
        displayed (global-offset) time to their raw detector time exactly once.
        """
        if source_kind in ('current','new'):
            self.push_undo()
            # Replacement zones only target the Current layer, never mutate previews.
            if target_kind != 'current' or not self.working.remove(target_time):
                self._undos.pop(); return
            ok = (self.working.add(destination) if source_kind == 'new'
                  else self.working.move(source_time,destination))
            if not ok:
                self.restore_undo()
                return
            self.timeline.refresh_candidates()
            self.update_candidate_count()
            self.mark_dirty()
            return
        source_raw = (source_time - self.global_offset_ms / 1000.0
                      if source_kind == 'computed' else source_time)
        target_raw = (target_time - self.global_offset_ms / 1000.0
                      if target_kind == 'computed' else target_time)
        snapshot = self.annotations.to_dict()
        # Atomic operation: a bad destination cannot erase a target.
        good = self.annotations.replace_nearby(source_kind, source_raw, destination,
                                               target_kind, target_raw)
        if not good:
            self.timeline.set_annotations(self.annotations)
            self.statusBar().showMessage('Replacement cancelled (exclusion or invalid move).', 4500)
            return
        self._undos.append({'annotations':snapshot,'working':self.working.to_dict()})
        self._undos = self._undos[-50:]
        self.timeline.refresh_candidates()
        self.update_candidate_count()
        self.mark_dirty()
        self.statusBar().showMessage(
            f'Replaced nearby {target_kind} candidate at {target_time:.6f}s. Ctrl+Z to undo.', 5000)

    def push_undo(self):
        self._undos.append({'annotations':self.annotations.to_dict(),
                            'working':self.working.to_dict()})
        self._undos = self._undos[-50:]

    def restore_undo(self):
        if not self._undos:
            return
        saved = self._undos.pop()
        self.annotations = Annotations.from_dict(saved.get('annotations',saved))
        self.working = WorkingCandidates.from_dict(saved.get('working',{}))
        self.timeline.set_annotations(self.annotations)
        self.timeline.set_working(self.working)
        self.update_candidate_count()

    def undo_annotation(self):
        if self._undos:
            self.restore_undo()
            self.update_candidate_count()
            self.mark_dirty()

    def clear_annotations(self):
        if QMessageBox.question(self, 'Clear annotations',
                'Remove all manually added, confirmed and rejected candidates and exclusion regions?') != QMessageBox.StandardButton.Yes:
            return
        self.push_undo()
        self.annotations = Annotations()
        self.timeline.set_annotations(self.annotations)
        self.update_candidate_count()
        self.mark_dirty()

    def set_layer_visibility(self, which, shown):
        if which == 'preview':
            self.timeline.set_visibility(preview=shown)
        else:
            self.timeline.set_visibility(working=shown)

    def paste_preview(self):
        if not self.timeline.has_audio:
            return
        items = self.timeline.preview_times
        if not len(items):
            self.statusBar().showMessage('No preview candidates to paste. Run Graph first.',4500)
            return
        box=QMessageBox(self)
        box.setWindowTitle('Paste preview → Current')
        box.setText(f'Paste {len(items):,} preview candidates into the committed Current output?')
        box.setInformativeText('Current output is independent of future graph reruns. '
                               'A replacement can be undone with Ctrl+Z.')
        merge_button=box.addButton('Add to Current', QMessageBox.ButtonRole.AcceptRole)
        replace_button=box.addButton('Replace Current', QMessageBox.ButtonRole.DestructiveRole)
        cancel_button=box.addButton('Cancel', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(merge_button)
        box.exec()
        chosen=box.clickedButton()
        if chosen is None or chosen == cancel_button:
            return
        self.push_undo()
        count=self.working.paste(items, replace=chosen == replace_button)
        self.timeline.set_working(self.working)
        self.update_candidate_count()
        self.mark_dirty()
        self.statusBar().showMessage(f'Current output: {len(self.working.times):,} candidates (added {count:,}).',6000)

    def clear_current(self):
        if not self.working.times:
            return
        if QMessageBox.question(self,'Clear Current output',
              'Delete all committed Current candidates? Preview graph data stays untouched.') != QMessageBox.StandardButton.Yes:
            return
        self.push_undo()
        self.working = WorkingCandidates()
        self.timeline.set_working(self.working)
        self.update_candidate_count()
        self.mark_dirty()

    def _set_reference_visibility(self, *, hits=None, timing=None):
        self.timeline.set_reference_visibility(hits=hits, timing=timing)
        if self.osu_reference is not None:
            self.mark_dirty()

    def _refresh_osu_reference_label(self):
        ref = self.osu_reference
        if ref is None:
            self.reference_label.setText('No osu! reference')
            self.reference_label.setToolTip('Import a .osu file to compare mapped onsets.')
            return
        self.reference_label.setText(
            f'osu! {len(ref.hitobject_times)} heads / {len(ref.red_times)} red / {len(ref.green_times)} green')
        self.reference_label.setToolTip(
            ref.source_path + '\nAudioFilename: ' + ref.audio_filename +
            ('\nWARNING: source audio filename differs from the loaded song.'
             if self.file_path and not ref.matches_audio_name(self.file_path) else ''))

    def import_osu_dialog(self):
        if not self.timeline.has_audio or not self.file_path:
            QMessageBox.information(self, 'Import osu! references', 'Load an audio file first.')
            return
        path, _ = QFileDialog.getOpenFileName(
            self, 'Import osu! map as read-only reference', str(Path(self.file_path).parent),
            'osu! beatmaps (*.osu)')
        if path:
            self.import_osu_reference(path)

    def import_osu_reference(self, path: str, *, confirm_mismatch=True) -> bool:
        if not self.file_path or not self.timeline.has_audio:
            return False
        try:
            reference = parse_osu(path)
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot read osu! beatmap', str(exc))
            return False
        if confirm_mismatch and not reference.matches_audio_name(self.file_path):
            answer = QMessageBox.question(
                self, 'Reference audio filename differs',
                f'The map expects {reference.audio_filename or "an unspecified audio file"}, but BeatSolver '
                f'has loaded {Path(self.file_path).name}.\n\n'
                'No alignment or offset correction has been verified. '
                'Use this map as a reference anyway?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self.osu_reference = reference
        self._last_comparison = None
        self.timeline.set_osu_reference(reference)
        self._refresh_osu_reference_label()
        self.mark_dirty()
        self.statusBar().showMessage(
            f'Imported {len(reference.hitobject_times)} unique mapped starts and '
            f'{len(reference.timing_points)} timing points (read-only).', 8000)
        return True

    def remove_osu_reference(self):
        if self.osu_reference is None:
            return
        self.osu_reference = None
        self._last_comparison = None
        self.timeline.set_osu_reference(None)
        self._refresh_osu_reference_label()
        self.mark_dirty()

    def _compare_candidates(self, source: str, tolerance_ms: float) -> Comparison:
        if self.osu_reference is None:
            raise ValueError('Import an osu! map first')
        if source == 'preview':
            times = self.timeline.preview_times
        elif source == 'current':
            times = self.working.times
        else:
            raise ValueError('Invalid candidate comparison source')
        return match_onsets(self.osu_reference.hitobject_times, times, tolerance_ms)

    def compare_osu_dialog(self):
        if self.osu_reference is None:
            QMessageBox.information(self, 'Compare to osu!', 'Import an osu! reference map first.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('osu! mapped onset comparison (partial-positive references)')
        dialog.resize(780, 540)
        layout = QVBoxLayout(dialog)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('Compare'))
        source = QComboBox()
        source.addItem('Preview (shown candidates)', 'preview')
        source.addItem('Current (committed)', 'current')
        controls.addWidget(source)
        controls.addWidget(QLabel('Tolerance'))
        tolerance = QDoubleSpinBox()
        tolerance.setRange(.1, 200)
        tolerance.setDecimals(1)
        tolerance.setValue(30)
        tolerance.setSuffix(' ms')
        controls.addWidget(tolerance)
        controls.addStretch(1)
        layout.addLayout(controls)
        headline = QLabel()
        headline.setWordWrap(True)
        layout.addWidget(headline)
        histogram = QLabel()
        histogram.setFont(QFont('Consolas', 9))
        layout.addWidget(histogram)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(['Reference (s)', 'Candidate (s)', 'Signed error (ms)', 'Status'])
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(table)
        controls2 = QHBoxLayout()
        export_btn = QPushButton('Export full comparison CSV…')
        controls2.addWidget(export_btn)
        controls2.addStretch(1)
        close_btn = QPushButton('Close')
        close_btn.clicked.connect(dialog.accept)
        controls2.addWidget(close_btn)
        layout.addLayout(controls2)
        comparison = [None]

        def refresh():
            result = self._compare_candidates(source.currentData(), tolerance.value())
            comparison[0] = result
            pct = f'{result.recall*100:.1f}%' if result.recall is not None else 'n/a'
            median_ms = f'{result.median_abs_ms:.2f} ms' if result.median_abs_ms is not None else 'n/a'
            headline.setText(
                f'Matched: {len(result.matches)}/{result.reference_count} ({pct} reference recall)  |  '
                f'Median absolute timing error: {median_ms}  |  '
                f'Unlabelled extra detections: {len(result.additional)}\n'
                'Unmatched detections are NOT proven false positives. '
                'Audio alignment is assumed; no offset is fitted.')
            half = tolerance.value()
            edges = np.linspace(-half,half,9)
            counts,_ = np.histogram([m.deviation_ms for m in result.matches], bins=edges)
            peak = max(1,int(counts.max()))
            parts = ['Signed timing error histogram (ms):']
            for index,count in enumerate(counts):
                parts.append(f'{edges[index]:+6.1f} to {edges[index+1]:+6.1f}: '
                             + '|'*int(round(22*count/peak)) + f'  {count}')
            histogram.setText('\n'.join(parts))
            rows = [(m.reference_s, m.candidate_s, m.deviation_ms, 'matched') for m in result.matches]
            rows += [(t,None,None,'missing reference') for t in result.missed]
            rows = sorted(rows, key=lambda x:x[0])[:500]
            table.setRowCount(len(rows))
            for i, (ref, cand, delta, label) in enumerate(rows):
                for j, value in enumerate((f'{ref:.6f}',f'{cand:.6f}' if cand is not None else '',
                                          f'{delta:+.2f}' if delta is not None else '',label)):
                    table.setItem(i,j,QTableWidgetItem(value))
        source.currentIndexChanged.connect(refresh)
        tolerance.valueChanged.connect(refresh)
        def export_dialog():
            self._export_osu_comparison_dialog(comparison[0])
        export_btn.clicked.connect(export_dialog)
        refresh()
        dialog.exec()

    def _export_osu_comparison_dialog(self, comparison: Comparison):
        if self.osu_reference is None or comparison is None:
            return
        default = str(Path(self.file_path).with_suffix('.beatsolver-comparison.csv'))
        target,_ = QFileDialog.getSaveFileName(self, 'Export osu! comparison',default,'CSV (*.csv)')
        if not target:
            return
        try:
            export_comparison_csv(target,comparison)
            self.statusBar().showMessage('Exported comparison CSV (unlabelled detections preserved).',6000)
        except Exception as exc:
            QMessageBox.warning(self, 'Could not export comparison',str(exc))

    def export_osu_comparison(self):
        if self.osu_reference is None:
            QMessageBox.information(self,'Export osu! comparison','Import an osu! reference map first.')
            return
        comparison = self._compare_candidates('preview',30)
        self._export_osu_comparison_dialog(comparison)

    def export_osu_onsets(self):
        if not self.timeline.has_audio or not self.file_path:
            QMessageBox.information(self,'Export osu! onsets','Load a song first.')
            return
        if not self.working.times:
            QMessageBox.information(self, 'Current output is empty',
                'Paste or commit candidates into Current before exporting a new osu! difficulty. '
                'Preview predictions are never silently promoted.')
            return
        choices = [
            'Lightweight 80 ms minimum gap | Placeholder 120 BPM',
            'Moderate 40 ms minimum gap | Placeholder 120 BPM',
            'Full fidelity (all Current) | Placeholder 120 BPM',
        ]
        if self.osu_reference and self.osu_reference.red_times:
            choices += [
                'Reuse imported timing points | Lightweight 80 ms gap',
                'Reuse imported timing points | Full fidelity',
            ]
        choice, ok = QInputDialog.getItem(self,'osu! timing for reference map',
            'Choose timing representation for the new difficulty:',choices,0,False)
        if not ok:
            return
        mode = 'imported' if choice.startswith('Reuse') else 'placeholder'
        minimum_gap = 80 if '80 ms' in choice else (40 if '40 ms' in choice else 0)
        if (mode == 'imported' and self.osu_reference is not None
                and not self.osu_reference.matches_audio_name(self.file_path)):
            confirmation = QMessageBox.question(
                self, 'Reusing timing from a different audio filename',
                'The imported map names another audio file. Reusing its timing points '
                'assumes sample-level alignment, which BeatSolver has not checked. '
                'Continue with this unverified timing?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if confirmation != QMessageBox.StandardButton.Yes:
                return
        audio = Path(self.file_path)
        default = audio.parent/(audio.stem+(' [BeatSolver Onset Reference Light].osu' if minimum_gap else ' [BeatSolver Onset Reference Full].osu'))
        target,_ = QFileDialog.getSaveFileName(self, 'Create a NEW osu! onset reference difficulty',
                                               str(default),'osu! beatmaps (*.osu)')
        if not target:
            return
        try:
            result = export_onset_map(target,self.file_path,self.working.times,
                imported=self.osu_reference,timing_mode=mode,duration_s=self.timeline.duration,
                min_circle_spacing_ms=minimum_gap)
            self.statusBar().showMessage(f'Created {result.exported_circles} circles at {result.path}',9000)
            QMessageBox.information(self,'New osu! difficulty created',
                f'Created {result.exported_circles} circles as a NEW file.\n'
                f'Timing: {"placeholder 120 BPM (not estimated)" if mode=="placeholder" else "imported timing points"}.\n'
                f'Rounded millisecond duplicates: {result.rounded_duplicates}.\n'
                f'Density reduction skipped: {result.density_skipped} candidates.\n'
                f'Audio: {result.referenced_audio}'
                f'{" (copied safely)" if result.audio_copied else " (existing file)"}.\n'
                f'Every exported circle is at (256,192); StackLeniency = 0.\n'
                f'File: {result.path}\n\nThe original song and map were not modified.')
        except Exception as exc:
            QMessageBox.warning(self,'Could not export osu! difficulty',str(exc))

    def export_current(self):
        if not self.working.times:
            self.statusBar().showMessage('Current output is empty; paste candidates into it first.',5000)
            return
        target,_=QFileDialog.getSaveFileName(self,'Export Current timestamps',
            str(Path(self.file_path).with_suffix('.beatsolver-candidates.csv')) if self.file_path else '',
            'CSV (*.csv)')
        if not target: return
        try:
            with open(target,'w',newline='',encoding='utf-8') as fh:
                writer=csv.writer(fh)
                writer.writerow(['time_seconds','type'])
                for t in self.working.times:
                    writer.writerow([f'{t:.9f}','current'])
            self.statusBar().showMessage(f'Exported {len(self.working.times):,} Current candidates.',6000)
        except Exception as exc:
            QMessageBox.warning(self,'CSV export failed',str(exc))

    def export_preview_evidence(self):
        """Optional experiment export; raw candidates and Current stay untouched."""
        if not len(self.candidate_output.times):
            self.statusBar().showMessage('Run Graph before exporting Preview evidence.', 5000)
            return
        target, _ = QFileDialog.getSaveFileName(
            self, 'Export raw Preview candidate evidence',
            str(Path(self.file_path).with_suffix('.beatsolver-preview-evidence.csv')) if self.file_path else '',
            'CSV (*.csv)')
        if not target:
            return
        try:
            from node_engine import export_candidate_evidence_csv
            export_candidate_evidence_csv(self.candidate_output, target)
            self.statusBar().showMessage(
                f'Exported {len(self.candidate_output.times):,} Preview events with provenance.', 6000)
        except Exception as exc:
            QMessageBox.warning(self, 'Evidence export failed', str(exc))

    def edit_bpm_ranges(self):
        start = self.timeline.view_start if self.timeline.has_audio else 0.0
        end = start + self.timeline.view_span if self.timeline.has_audio else 5.0
        if end-start < .1: end=start+.1
        dialog=BPMRangesDialog(self,self.guides,view_start=start,view_end=end)
        if dialog.exec():
            self.guides.ranges=dialog.items
            self.timeline.set_guides(self.guides)
            self.mark_dirty()

    def set_guide_visible(self, visible):
        self.guides.visible=bool(visible)
        self.timeline.set_guides(self.guides)
        if self.timeline.has_audio:
            self.mark_dirty()

    def audition_at(self, seconds:float, layer:str='current'):
        if self.playback_speed_pct <= 0:
            self.statusBar().showMessage('Set playback speed above 0% to audition.',5000)
            return
        cache=self.timeline.audio_cache
        if cache is None or cache.pcm is None:
            self.statusBar().showMessage('Audition requires retained PCM. This song exceeded the 192 MiB audio cache.',8000)
            return
        source = self.working.times if layer=='current' else self.timeline.preview_times
        if not len(source):
            self.statusBar().showMessage(f'No {layer} candidates to audition.',5000)
            return
        seconds=float(seconds)
        span=self.audition_seconds.value()
        start=max(0.0,seconds-span/2)
        end=min(cache.duration,start+span)
        start=max(0.0,end-span)
        # Include only the chosen layer's candidates, with no BPM guide clicks.
        try:
            data,actual_start,clicks=audible_excerpt(cache.pcm,cache.rate,start,end,source,
                audio_gain=self.music_gain/100.0,click_gain=self.click_gain/100.0)
        except Exception as exc:
            self.statusBar().showMessage('Audition unavailable: '+str(exc),6000)
            return
        self.stop_audition()
        # When decoded PCM is the song master, pausing QMediaPlayer alone
        # would leave the QAudioSink streaming music over the audition.
        if self._stream_active:
            self._stream_controller.pause()
            self.on_play_state(self.player.playbackState())
        else:
            self.player.pause()
        self.audition_active=True
        self.audition_start=actual_start
        self.audition_clock.reset(0,duration=end-actual_start)
        self.audition_clock.set_rate(max(.01,self.playback_speed_pct/100.0))
        # A unique filename avoids Windows media backends holding an earlier WAV open.
        import uuid
        dest=Path(self._audition_temp.name)/('audition_'+uuid.uuid4().hex+'.wav')
        dest.write_bytes(data)
        self._audition_file = dest
        self.audition_player.setSource(QUrl.fromLocalFile(str(dest)))
        self.audition_player.setPlaybackRate(max(.01,self.playback_speed_pct/100.0))
        if self.playback_speed_pct <= 0:
            self.audition_active=False
            self.statusBar().showMessage('Set playback speed above 0% to audition.',5000)
            return
        self.audition_player.play()
        self.cursor_timer.start()
        self.statusBar().showMessage(f'Auditioning {len(clicks)} {layer} candidate clicks in {end-start:.1f}s (mono PCM).',7000)

    def stop_audition(self):
        # File lifecycle must not depend on whether playback was active.
        self._resume_audition_after_zero = False
        self.audition_active = False
        self.audition_player.stop()
        self.audition_player.setSource(QUrl())
        old=self._audition_file
        self._audition_file=None
        if old is not None:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                # A Windows backend may temporarily retain a handle. The
                # enclosing TemporaryDirectory is cleaned during shutdown.
                pass
        self.audition_clock.set_playing(False)
        if not self.playback_clock.playing:
            self.cursor_timer.stop()

    def on_audition_state(self,state):
        active=state == QMediaPlayer.PlaybackState.PlayingState
        self.audition_clock.set_playing(active)
        if active:
            self.cursor_timer.start()
        elif not self.playback_clock.playing:
            self.cursor_timer.stop()

    def on_audition_position(self,position):
        self.audition_clock.sync(position/1000.0)
        if self.audition_active: self.animate_cursor()

    def on_audition_status(self,status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.stop_audition()

    def on_graph_modified(self):
        self._refresh_spectrogram_sources()
        # Selecting a different output without running it should never leave
        # the previous output's spectral image presented as current evidence.
        if (self._last_graph_output_id is not None and hasattr(self, 'graph_panel')
                and self.graph_panel.output_selector.currentData() != self._last_graph_output_id
                and self.spectrogram_enabled):
            self.spectrogram_viewer.set_spectrum(None)
        self.analysis_outdated = True
        self.graph_revision += 1
        self.mark_dirty()
        self.statusBar().showMessage('Graph changed. Press Run Graph to refresh candidates.', 4500)

    def mark_dirty(self):
        if self.continuous_hits.isChecked():
            self._schedule_mix_render()
        self.session_dirty = True
        self.setWindowTitle(f'{APP_NAME}  •  v{APP_VERSION}  * unsaved')

    def mark_clean(self):
        self.session_dirty = False
        self.setWindowTitle(f'{APP_NAME}  •  v{APP_VERSION}')

    def run_graph(self, output_id=None, *, _generation=None):
        """Latest-request-wins, including across deferred Qt completion callbacks."""
        if self._closing:
            return
        if not self.timeline.has_audio:
            self.statusBar().showMessage('Load audio before running the processing graph.', 4500)
            return
        if not output_id or output_id not in self.graph.nodes:
            self.statusBar().showMessage('Add a Candidate Output node and select it first.', 4500)
            return
        if _generation is None:
            self._graph_request_generation += 1
            generation = self._graph_request_generation
        else:
            generation = _generation
            if not deferred_run_is_current(generation, self._graph_request_generation,
                                           self.timeline.has_audio):
                return  # A newer explicit Run request superseded this queued timer.
        if self.candidate_worker is not None:
            # The finished signal can still be queued when isRunning() is False.
            self._queued_graph_output = output_id
            self._queued_graph_generation = generation
            self.statusBar().showMessage('Graph is finishing; latest Run request queued.', 3500)
            return
        self._queued_graph_output = None
        self._queued_graph_generation = None
        try:
            snapshot = Graph.from_dict(self.registry, self.graph.to_dict())
        except Exception as exc:
            QMessageBox.warning(self, 'Invalid processing graph', str(exc))
            return
        cache = self.timeline.audio_cache
        # The combo is scoped to the selected Candidate Output and follows node IDs.
        selected = choose_spectrum(snapshot, output_id,
                    cache.rate if cache is not None else None,
                    self._spectrogram_source_id)
        requested = selected.signature if selected is not None else None
        self._selected_spectrum_request = requested
        if (requested is not None and self.spectrogram_viewer.features is not None and
                self._spectral_signature(self.spectrogram_viewer.features) != requested):
            self.spectrogram_viewer.set_spectrum(None)
        if cache is not None:
            low, high, step_s = cache.analysis_envelope(self.analysis_hop_ms)
            audio = AudioData(self.file_path, cache.duration, low, high,
                              bin_seconds=step_s, sample_rate=cache.rate,
                              pcm=cache.pcm, spectral_features=getattr(cache,'spectral_features',None),
                              spectral_cache=cache.spectral_store)
        else:
            audio = AudioData(self.file_path, self.timeline.duration,
                              self.timeline.low, self.timeline.high)
        self.run_revision = self.graph_revision
        self._active_graph_generation = generation
        self._active_graph_output_id = output_id
        self._active_graph_snapshot = snapshot
        self.candidate_worker = CandidateThread(snapshot, audio, output_id, self)
        self.candidate_worker.produced.connect(self.graph_produced)
        self.candidate_worker.failed.connect(self.graph_failed)
        self.candidate_worker.finished.connect(self.graph_thread_finished)
        self.graph_panel.set_busy(True)
        self.statusBar().showMessage('Processing candidate graph…')
        self.candidate_worker.start()

    @Slot(object, object, object)
    def graph_produced(self, candidates: CandidateSet, metrics, intermediates):
        origin = self.sender()
        if origin is not None and origin is not self.candidate_worker:
            return  # Ignore outputs emitted by a superseded analysis worker.
        if (self._active_graph_generation is not None and
                self._active_graph_generation != self._graph_request_generation):
            return  # User queued a newer graph; don't briefly show stale Preview.
        self._last_graph_intermediates = intermediates
        self._last_graph_output_id = self._active_graph_output_id
        self.candidate_output = candidates
        self.timeline.set_candidates(candidates)
        labels = {}
        for uid, results in intermediates.items():
            node = self.graph.nodes.get(uid)
            if node is None:
                continue
            definition = self.registry.defs.get(node.type_id)
            if definition is not None:
                for name in results:
                    labels[f'{uid}:{name}'] = f'{definition.label} / {name}'
        # Energy Changes belongs to the old starter graph; it is absent
        # from the active typed Spectrogram branch. Compute *visual-only*
        # reference curves from the same loaded envelope, keeping Preview
        # candidate calculations and the user's graph untouched.
        display_layers = dict(intermediates)
        display_labels = dict(labels)
        cache = self.timeline.audio_cache
        if cache is not None:
            reference_key = (id(cache), self.analysis_hop_ms)
            if self._diagnostic_curve_cache is None or self._diagnostic_curve_cache[0] != reference_key:
                low, high, step_s = cache.analysis_envelope(self.analysis_hop_ms)
                reference_audio = AudioData(self.file_path, cache.duration, low, high,
                                            bin_seconds=step_s)
                layers, descriptions = diagnostic_energy_curves(reference_audio)
                self._diagnostic_curve_cache = (reference_key, layers, descriptions)
            _, layers, descriptions = self._diagnostic_curve_cache
            display_layers.update(layers)
            display_labels.update(descriptions)
            self.layer_inspector.styles.setdefault(f'{DIAGNOSTIC_WAVE_ID}:curve', {
                'visible': True, 'color': '#b19bf5', 'opacity': 80,
            })
            self.layer_inspector.styles.setdefault(f'{DIAGNOSTIC_CHANGE_ID}:curve', {
                'visible': True, 'color': '#74d7a0', 'opacity': 90,
            })
        self.layer_inspector.set_data(display_layers, display_labels)
        # Choose evidence by its *contributing graph node ID*. A merge may
        # evaluate two typed spectra; dictionary/evaluation order is not a
        # meaningful viewer selection and must never decide the displayed STFT.
        cache = self.timeline.audio_cache
        snapshot = self._active_graph_snapshot
        selected = (choose_spectrum(snapshot, self._active_graph_output_id,
                    cache.rate, self._spectrogram_source_id)
                    if snapshot is not None and cache is not None else None)
        self._selected_spectrum_request = selected.signature if selected else None
        evidence = (intermediates.get(selected.node_id, {}).get('spectrogram')
                    if selected is not None else None)
        if isinstance(evidence, SpectrogramData) and cache is not None:
            cache.spectral_features = evidence.features
            if self.spectrogram_enabled:
                self.spectrogram_viewer.set_spectrum(evidence.features)
        elif selected is None and cache is not None and self.spectrogram_enabled:
            # Non-typed graphs display the legacy viewer, never a random
            # typed source left behind by an earlier output selection.
            if (cache.spectral_features is not None and
                    self._spectral_signature(cache.spectral_features) !=
                    ('legacy_fft', 46., max(1, round(cache.rate * .006)))):
                cache.spectral_features = None
                self.spectrogram_viewer.set_spectrum(None)
            self._begin_spectrogram()
        elif selected is not None and self.spectrogram_enabled:
            self.spectrogram_viewer.set_spectrum(None)
            self.statusBar().showMessage('Selected spectrogram data missing; rerun this branch.', 6000)
        self.sync_curve_layers()
        self.graph_panel.show_metrics(metrics)
        self.graph_panel.show_message(f'Preview updated: {len(candidates.times):,} candidate events.')
        self.analysis_outdated = self.run_revision != self.graph_revision
        if self.analysis_outdated:
            self.graph_panel.show_message('Output belongs to an earlier graph version. Run Graph again.')
        self.update_candidate_count()
        self._schedule_mix_render()
        self.statusBar().showMessage(f'Graph finished: {len(candidates.times):,} generated, '
                                     f'{len(self.timeline.draw_times):,} after human annotations.', 8000)

    @Slot(str)
    def graph_failed(self, message):
        origin = self.sender()
        if origin is not None and origin is not self.candidate_worker:
            return
        if (self._active_graph_generation is not None and
                self._active_graph_generation != self._graph_request_generation):
            return  # Error from an obsolete run should not interrupt the newer one.
        self.graph_panel.show_message(message)
        QMessageBox.warning(self, 'Candidate graph failed', message)
        self.statusBar().showMessage('Processing graph failed.', 5000)

    @Slot()
    def graph_thread_finished(self):
        # QThread.finished is queued to the GUI event loop. Looking at
        # self.candidate_worker here and deleting it unconditionally can kill
        # a *newer, still-running* thread. Qt then aborts the process.
        completed = self.sender()
        if completed is None:
            return  # Never guess which thread finished.
        if completed is not self.candidate_worker:
            # Old completion, delivered after a newer run was installed.
            # Its QObject is still ours to retire; the active run is untouched.
            completed.deleteLater()
            return
        self.candidate_worker = None
        self.graph_panel.set_busy(False)
        completed.deleteLater()
        next_output = self._queued_graph_output
        next_generation = self._queued_graph_generation
        self._queued_graph_output = None
        self._queued_graph_generation = None
        self._active_graph_generation = None
        self._active_graph_snapshot = None
        self._active_graph_output_id = None
        if (next_output is not None and
                deferred_run_is_current(next_generation, self._graph_request_generation,
                                        self.timeline.has_audio) and not self._closing):
            # Capture the *original* request generation. A later manual run
            # invalidates this timer even if it started another worker first.
            QTimer.singleShot(0, lambda output=next_output, generation=next_generation:
                              self.run_graph(output, _generation=generation))

    def load_plugin(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select trusted node plugin',
                     str(Path(__file__).parent / 'plugins'), 'Python plugins (*.py)')
        if not path:
            return
        if QMessageBox.warning(self, 'Execute Python plugin?',
                    'This will EXECUTE the selected Python code with your user privileges. '
                    'Only import plugins you trust.\n\n'+path,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        try:
            added = self.registry.load_plugin(path)
            self.graph_panel.update_types()
            QMessageBox.information(self,'Plugin loaded', 'Registered types: '+(', '.join(added) or '(none)'))
        except Exception as exc:
            QMessageBox.warning(self,'Plugin error',str(exc))

    def insert_burst_cleanup(self):
        """Insert an optional Candidates->Candidates filter without deleting a branch.

        Existing projects are untouched unless the user chooses this action.
        Upstream candidates remain available for A/B comparison via another
        Candidate Output node. The selection is switched to the filtered chain.
        """
        out_id = self.graph_panel.output_selector.currentData()
        if not out_id:
            self.statusBar().showMessage('Select a Preview Candidate Output first.', 4500)
            return
        edge = next((e for e in self.graph.connections
                     if e.target == out_id and e.input == 'candidates'), None)
        if edge is None:
            self.statusBar().showMessage('The Preview output needs a candidate input first.', 4500)
            return
        if self.graph.nodes[edge.source].type_id == 'candidates.burst_cleanup':
            self.statusBar().showMessage('This output already has Burst Cleanup.', 4500)
            return
        try:
            # Work on a copy so a failed connection never damages the graph.
            graph = Graph.from_dict(self.registry, self.graph.to_dict())
            downstream = graph.nodes[out_id]
            upstream = graph.nodes[edge.source]
            node = graph.add('candidates.burst_cleanup',
                             (upstream.x+downstream.x)*.5,
                             (upstream.y+downstream.y)*.5 + 70)
            graph.connect(edge.source, edge.output, node.id, 'candidates')
            graph.connect(node.id, 'candidates', out_id, 'candidates')
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot insert filter', str(exc))
            return
        self.graph = graph
        self.graph_panel.set_graph(graph)
        self.on_graph_modified()
        self.graph_dock.setVisible(True)
        self.statusBar().showMessage('Burst Cleanup added. Press Run Graph to compare.', 6000)

    def _find_audio_source(self, graph):
        matches=[n for n in graph.nodes.values() if n.type_id=='audio.source']
        return matches[0] if matches else graph.add('audio.source',0,0)

    def add_spectral_branch(self):
        """Build a parallel Preview output, preserving the original graph."""
        try:
            graph=Graph.from_dict(self.registry,self.graph.to_dict())
            source=self._find_audio_source(graph)
            existing=max((n.y for n in graph.nodes.values()),default=0)
            y=existing+160
            spec=graph.add('signal.spectral_attack',source.x+230,y)
            peaks=graph.add('signal.peaks',source.x+470,y)
            output=graph.add('candidates.output',source.x+700,y)
            graph.connect(source.id,'audio',spec.id,'audio')
            graph.connect(spec.id,'curve',peaks.id,'curve')
            graph.connect(peaks.id,'candidates',output.id,'candidates')
            peaks.params['min_spacing_ms']=12.0
            peaks.params['percentile']=60.0
        except Exception as exc:
            QMessageBox.warning(self,'Cannot create spectral branch',str(exc));return
        self.graph=graph
        self.graph_panel.set_graph(graph)
        self.graph_panel.output_selector.setCurrentIndex(
            self.graph_panel.output_selector.findData(output.id))
        self.on_graph_modified()
        self.graph_dock.show()
        self.statusBar().showMessage('Spectral branch added. Run Graph to compare with the original output.',6000)

    def add_typed_spectral_branch(self):
        """A/B compare a typed STFT source without migrating existing projects."""
        try:
            graph = Graph.from_dict(self.registry, self.graph.to_dict())
            source = self._find_audio_source(graph)
            y = max((n.y for n in graph.nodes.values()), default=0) + 190
            spec = graph.add('signal.spectrogram', source.x + 210, y)
            novelty = graph.add('signal.spectral_novelty', source.x + 420, y)
            peaks = graph.add('signal.peaks', source.x + 630, y)
            output = graph.add('candidates.output', source.x + 850, y)
            graph.connect(source.id, 'audio', spec.id, 'audio')
            graph.connect(spec.id, 'spectrogram', novelty.id, 'spectrogram')
            graph.connect(novelty.id, 'curve', peaks.id, 'curve')
            graph.connect(peaks.id, 'candidates', output.id, 'candidates')
            peaks.params['min_spacing_ms'] = 12.
            peaks.params['percentile'] = 60.
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot create typed spectral branch', str(exc))
            return
        self.graph = graph
        self.graph_panel.set_graph(graph)
        self.graph_panel.output_selector.setCurrentIndex(
            self.graph_panel.output_selector.findData(output.id))
        self.on_graph_modified()
        self.graph_dock.show()
        self.statusBar().showMessage('Typed spectrogram branch added. Run Graph to compare.', 6000)

    def insert_evidence_cleanup(self):
        """Add Candidates+Curve cleanup, sourcing spectral evidence from shared PCM."""
        out_id=self.graph_panel.output_selector.currentData()
        edge=next((e for e in self.graph.connections
                   if e.target==out_id and e.input=='candidates'),None)
        if edge is None:
            self.statusBar().showMessage('Select a connected Preview Candidate Output.',6000)
            return
        if self.graph.nodes[edge.source].type_id=='candidates.evidence_cleanup':
            self.statusBar().showMessage('Evidence-Aware Cleanup already present.',5000)
            return
        try:
            graph=Graph.from_dict(self.registry,self.graph.to_dict())
            audio_source=self._find_audio_source(graph)
            upstream=graph.nodes[edge.source]
            output=graph.nodes[out_id]
            spectral=next((n for n in graph.nodes.values()
                           if n.type_id=='signal.spectral_attack' and any(
                               e.source==audio_source.id and e.target==n.id and e.input=='audio'
                               for e in graph.connections)),None)
            if spectral is None:
                spectral=graph.add('signal.spectral_attack',upstream.x+65,upstream.y+225)
                graph.connect(audio_source.id,'audio',spectral.id,'audio')
            cleanup=graph.add('candidates.evidence_cleanup',
                              (upstream.x+output.x)/2,output.y+70)
            graph.connect(edge.source,edge.output,cleanup.id,'candidates')
            graph.connect(spectral.id,'curve',cleanup.id,'attack_curve')
            graph.connect(cleanup.id,'candidates',out_id,'candidates')
        except Exception as exc:
            QMessageBox.warning(self,'Cannot insert evidence cleanup',str(exc));return
        self.graph=graph
        self.graph_panel.set_graph(graph)
        self.on_graph_modified()
        self.graph_dock.show()
        self.statusBar().showMessage('Evidence-Aware Cleanup inserted. Run Graph to inspect the attack curve.',6000)

    def reset_graph(self):
        if QMessageBox.question(self,'Reset graph?',
                'Replace the current graph with the default spectral pipeline and Burst Cleanup?') != QMessageBox.StandardButton.Yes:
            return
        self.graph = default_spectral_graph(self.registry)
        self.graph_panel.set_graph(self.graph)
        self.on_graph_modified()

    def maybe_discard_changes(self):
        if not self.session_dirty:
            return True
        result = QMessageBox.question(self, 'Unsaved BeatSolver project',
                     'Current project has unsaved graph or annotation edits. Discard them?',
                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                     QMessageBox.StandardButton.No)
        return result == QMessageBox.StandardButton.Yes

    def save_project(self, save_as=False):
        if not self.file_path or not self.timeline.has_audio:
            QMessageBox.information(self,'Save project','Open an audio file first.')
            return False
        path = self.session_path if not save_as else None
        if not path:
            default = str(Path(self.file_path).with_suffix('.beatsolver.json'))
            path, _ = QFileDialog.getSaveFileName(self,'Save BeatSolver project',default,
                                                  'BeatSolver projects (*.beatsolver.json);;JSON (*.json)')
        if not path: return False
        data = {'schema':1, 'audio_path':os.path.abspath(self.file_path),
                'graph':self.graph.to_dict(), 'annotations':self.annotations.to_dict(),
                'current_output':self.working.to_dict(),
                'timing_guides':self.guides.to_dict(),
                'osu_reference_path':self.osu_reference.source_path if self.osu_reference else None,
                'osu_show_hitobjects':self.show_osu_hits_action.isChecked(),
                'osu_show_timing':self.show_osu_timing_action.isChecked(),
                'preview_output_id':self.graph_panel.output_selector.currentData(),
                'plugin_paths':self.registry.plugin_paths[:],
                'global_offset_ms': self.global_offset_ms,
                'replace_radius_ms': self.replace_radius_ms,
                'playback_speed_pct': self.playback_speed_pct,
                'cursor_sync_ms':self.cursor_sync_ms,
                'priority_threshold_pct':self.priority_threshold_pct,
                'priority_dedup_ms':self.priority_dedup_ms,
                'continuous_clicks':self.continuous_hits.isChecked(),
                'stream_mixer':self.stream_mode.isChecked(),
                'sample_clock_playback':self.sample_clock_check.isChecked(),
                'continuous_source':self.continuous_source,
                'music_gain':self.music_gain,
                'click_gain':self.click_gain,
                'analysis_hop_ms': self.analysis_hop_ms,
                'curve_display_lines': self.curve_lines,
                'spectrogram_visible':self.spectrogram_enabled,
                'spectrogram_mode':self.spectrogram_mode,
                'spectrogram_source_id':self._spectrogram_source_id,
                'layer_styles': self.layer_inspector.export_styles(),
                'note':'Generated candidate predictions are recalculated when the graph runs.'}
        # Plugin source files are references, never silently executed on reopen.
        known = set(self.registry.defs)
        from node_engine import default_registry
        custom_types = known - set(default_registry().defs)
        if custom_types:
            data['note'] += ' Reload custom Python plugins before opening this project.'
        try:
            target = Path(path)
            temp = target.with_suffix(target.suffix+'.tmp')
            temp.write_text(json.dumps(data,indent=2),encoding='utf-8')
            temp.replace(target)
            self.session_path = str(target)
            self.mark_clean()
            self.statusBar().showMessage('Project saved: '+path, 6000)
            return True
        except Exception as exc:
            QMessageBox.warning(self,'Project not saved',str(exc))
            return False

    def open_project(self):
        if self.loading or self.candidate_worker and self.candidate_worker.isRunning():
            self.statusBar().showMessage('Finish processing before opening a project.', 5000)
            return
        if not self.maybe_discard_changes(): return
        path,_ = QFileDialog.getOpenFileName(self,'Open BeatSolver project',str(Path.home()),
                                             'BeatSolver projects (*.beatsolver.json *.json)')
        if not path: return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            if data.get('schema') != 1: raise ValueError('Unsupported project schema')
            plugins = [x for x in data.get('plugin_paths',[]) if x not in self.registry.plugin_paths]
            if plugins:
                listing = '\n'.join(str(x) for x in plugins)
                allow = QMessageBox.warning(self, 'Project includes external Python plugins',
                    'Opening this project requires EXECUTING these Python files. '
                    'Do so only if you trust them:\n\n'+listing,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel)
                if allow != QMessageBox.StandardButton.Yes: return
                for plugin_path in plugins:
                    self.registry.load_plugin(plugin_path)
                self.graph_panel.update_types()
            new_graph = Graph.from_dict(self.registry,data['graph'])
            annotations = Annotations.from_dict(data['annotations'])
            loaded_working = WorkingCandidates.from_dict(data.get('current_output',{}))
            loaded_guides = TimingGuides.from_dict(data.get('timing_guides',{}))
            reference_path = data.get('osu_reference_path')
            loaded_reference = parse_osu(reference_path) if reference_path and Path(reference_path).is_file() else None
            saved_offset = float(data.get('global_offset_ms', 0.0))
            if not math.isfinite(saved_offset) or not -5000.0 <= saved_offset <= 5000.0:
                raise ValueError('Invalid global timing offset')
            saved_replace_radius = float(data.get('replace_radius_ms', 12.0))
            if not math.isfinite(saved_replace_radius) or not 0 <= saved_replace_radius <= 100:
                raise ValueError('Invalid replacement radius')
            saved_speed = float(data.get('playback_speed_pct', 100.0))
            if not math.isfinite(saved_speed) or not 0 <= saved_speed <= 1000:
                raise ValueError('Invalid playback speed')
            saved_cursor_sync = float(data.get('cursor_sync_ms', 0.0))
            if not math.isfinite(saved_cursor_sync) or not -300 <= saved_cursor_sync <= 300:
                raise ValueError('Invalid cursor sync compensation')
            saved_interval = float(data.get('analysis_hop_ms', 2.0))
            if saved_interval not in (0.25, 0.5, 1.0, 2.0, 4.0):
                raise ValueError('Invalid analysis interval')
            saved_priority=int(data.get('priority_threshold_pct',0))
            saved_dedup=float(data.get('priority_dedup_ms',0))
            saved_music=int(data.get('music_gain',65))
            saved_click=int(data.get('click_gain',75))
            saved_live=bool(data.get('continuous_clicks',False))
            saved_stream=bool(data.get('stream_mixer',False))
            saved_source=str(data.get('continuous_source','current'))
            if not (0<=saved_priority<=100 and 0<=saved_dedup<=100 and
                    0<=saved_music<=100 and 0<=saved_click<=100 and
                    saved_source in {'preview','current','both','grid'}):
                raise ValueError('Invalid priority/audio settings')
            saved_curve_lines = bool(data.get('curve_display_lines', True))
            saved_spectrogram_visible=bool(data.get('spectrogram_visible',True))
            saved_spectrogram_mode=data.get('spectrogram_mode','attack')
            saved_spectrogram_source=data.get('spectrogram_source_id')
            saved_styles = data.get('layer_styles', {})
            audio_path = data['audio_path']
            if not os.path.isfile(audio_path):
                alternative,_ = QFileDialog.getOpenFileName(self,
                    'Find the song referenced by this project',str(Path(path).parent),
                    'Audio files (*.mp3 *.wav *.flac *.ogg)')
                if not alternative: return
                audio_path = alternative
        except Exception as exc:
            QMessageBox.warning(self,'Invalid project',str(exc))
            return
        self.graph = new_graph
        self.graph_panel.set_graph(self.graph)
        idx=self.graph_panel.output_selector.findData(data.get('preview_output_id'))
        if idx >= 0: self.graph_panel.output_selector.setCurrentIndex(idx)
        self._spectrogram_source_id = saved_spectrogram_source
        self._refresh_spectrogram_sources()
        self.annotations=annotations
        self.working=loaded_working
        self.guides=loaded_guides
        self.osu_reference=loaded_reference
        self._last_comparison=None
        self.show_osu_hits_action.setChecked(bool(data.get('osu_show_hitobjects',True)))
        self.show_osu_timing_action.setChecked(bool(data.get('osu_show_timing',True)))
        self._refresh_osu_reference_label()
        self.guides_check.blockSignals(True)
        self.guides_check.setChecked(loaded_guides.visible)
        self.guides_check.blockSignals(False)
        self.replace_radius_ms = saved_replace_radius
        self.replace_spin.blockSignals(True)
        self.replace_spin.setValue(saved_replace_radius)
        self.replace_spin.blockSignals(False)
        self.timeline.set_replace_radius(saved_replace_radius)
        self.priority_slider.setValue(saved_priority)
        self.priority_radius_spin.setValue(saved_dedup)
        self.music_slider.setValue(saved_music)
        self.click_slider.setValue(saved_click)
        self.live_source.setCurrentIndex(self.live_source.findData(saved_source))
        self.stream_mode.blockSignals(True)
        self.stream_mode.setChecked(saved_stream)
        self.stream_mode.blockSignals(False)
        self.sample_clock_check.blockSignals(True)
        self.sample_clock_check.setChecked(bool(data.get('sample_clock_playback', True)))
        self.sample_clock_check.blockSignals(False)
        self.continuous_hits.blockSignals(True)
        self.continuous_hits.setChecked(saved_live)
        self.continuous_hits.blockSignals(False)
        self.speed_spin.setValue(saved_speed)
        self.cursor_sync_ms = saved_cursor_sync
        self.cursor_sync_spin.blockSignals(True)
        self.cursor_sync_spin.setValue(saved_cursor_sync)
        self.cursor_sync_spin.blockSignals(False)
        self.global_offset_ms = saved_offset
        self.offset_spin.blockSignals(True)
        self.offset_spin.setValue(saved_offset)
        self.offset_spin.blockSignals(False)
        self.analysis_hop_ms = saved_interval
        self.analysis_resolution.blockSignals(True)
        self.analysis_resolution.setCurrentIndex(self.analysis_resolution.findData(saved_interval))
        self.analysis_resolution.blockSignals(False)
        self.curve_lines = saved_curve_lines
        self.curve_line_check.blockSignals(True)
        self.curve_line_check.setChecked(saved_curve_lines)
        self.curve_line_check.blockSignals(False)
        self.curve_viewer.connect_samples = saved_curve_lines
        self.spectrogram_mode_combo.setCurrentIndex(0 if saved_spectrogram_mode!='harmonic' else 1)
        self.spectrogram_check.setChecked(saved_spectrogram_visible)
        self.layer_inspector.set_saved_styles(saved_styles)
        self._undos.clear()
        self.candidate_output = CandidateSet(np.array([],dtype=np.float64), np.array([],dtype=np.float32))
        self.session_path=path
        self.mark_clean()
        self.load_audio(audio_path, from_project=True)
        self.statusBar().showMessage(
            'Project opened. Missing osu! reference: import it again.'
            if reference_path and loaded_reference is None else
            'Project opened. Graph analysis is starting.', 7000)

    def dragEnterEvent(self, event):
        if valid_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        path = first_audio_path(event.mimeData())
        if path:
            self.load_audio(path)
            event.acceptProposedAction()
        else:
            event.ignore()

    def closeEvent(self, event):
        if not self.maybe_discard_changes():
            event.ignore(); return
        if self.candidate_worker is not None:
            if self.candidate_worker.isRunning():
                QMessageBox.information(self,'Analysis running',
                                        'Please let the current graph run finish before closing.')
                event.ignore()
                return
            # The thread has stopped, though Qt's completion may still be
            # queued. It is safe to dispose now; do not launch a queued rerun
            # after the window has closed.
            self._queued_graph_output = None
            self._queued_graph_generation = None
            self._graph_request_generation += 1
            completed = self.candidate_worker
            self.candidate_worker = None
            completed.deleteLater()
        self.settings.setValue("geometry", self.saveGeometry())
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            if not self.worker.wait(4000):
                event.ignore()
                self.statusBar().showMessage("Audio decoding is still stopping…", 4000)
                return
        self._mix_debounce.stop()
        if self._spectrogram_worker is not None and self._spectrogram_worker.isRunning():
            self._spectrogram_worker.requestInterruption()
            if not self._spectrogram_worker.wait(8000):
                event.ignore()
                self.statusBar().showMessage('Waiting for spectrogram analysis to finish…',4000)
                return
        if self._stream_loader is not None and self._stream_loader.isRunning():
            self._stream_loader.requestInterruption()
            if not self._stream_loader.wait(8000):
                event.ignore()
                self.statusBar().showMessage('Waiting for stereo decoding to stop…',4000)
                return
        self._stop_stream(restore=False)
        self.diag_timer.stop()
        if self._mix_worker is not None and self._mix_worker.isRunning():
            self._mix_worker.requestInterruption()
            if not self._mix_worker.wait(8000):
                event.ignore()
                self.statusBar().showMessage('Waiting for the audio mix worker to stop…',4000)
                return
        self._spectrogram_pending = False
        self._queued_graph_output = None
        self._queued_graph_generation = None
        self._graph_request_generation += 1
        self._closing = True
        self.stop_audition()
        self.cursor_timer.stop()
        self.audition_player.stop()
        self.player.stop()
        self.player.setSource(QUrl())
        self._mix_pending_transport=None
        try:
            self._mix_temp.cleanup()
        except OSError:
            pass
        try:
            self._audition_temp.cleanup()
        except OSError:
            # The app must still close if a Windows multimedia backend holds a
            # temporary WAV open for a moment.
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("mugenq")
    window = BeatSolverWindow()
    window.show()
    print("[BeatSolver] Main window shown. Qt event loop starting.", flush=True)
    if len(sys.argv) > 1 and Path(sys.argv[1]).suffix.lower() in SUPPORTED_AUDIO:
        window.load_audio(sys.argv[1])
    return_code = app.exec()
    print(f"[BeatSolver] Qt event loop returned {return_code}.", flush=True)
    sys.exit(return_code)


if __name__ == "__main__":
    import traceback

    def _uncaught_exception(kind, value, trace):
        traceback.print_exception(kind, value, trace)
        sys.stderr.flush()
        # Report unexpected exceptions in Qt signal callbacks instead of
        # silently letting the application become unresponsive.
        try:
            app = QApplication.instance()
            if app is not None:
                QMessageBox.critical(
                    None, "BeatSolver Timeline encountered an error",
                    f"{kind.__name__}: {value}\n\n"
                    "More details are in the startup log."
                )
        except Exception:
            pass

    sys.excepthook = _uncaught_exception
    print("[BeatSolver] Python entrypoint reached.", flush=True)
    main()
