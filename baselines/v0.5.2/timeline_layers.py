"""Visual debugging panel for curve-producing BeatSolver nodes (v0.3).

Curves are rendered only; global offset is applied at visualization time.
Waveform data and user annotations remain anchored to the original song time.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from node_engine import Curve

PALETTE = ("#b19bf5", "#74d7a0", "#eda66c", "#e18eb8", "#84cbe8", "#d4d878")


def visible_curve_value(curve: Curve, time_s: float, offset_s: float) -> float | None:
    """Sample a *raw* curve at the displayed song time (linear interpolation)."""
    data = curve.values
    if not len(data) or curve.duration <= 0:
        return None
    index = curve.index_at(time_s - offset_s)
    if index < 0 or index > len(data)-1:
        return None
    i = int(index)
    f = index - i
    if i + 1 >= len(data):
        value = float(data[i])
    else:
        value = float(data[i]) * (1-f) + float(data[i+1]) * f
    return value if math.isfinite(value) else None


class LayerInspector(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.curves: dict[str, Curve] = {}
        self.labels: dict[str, str] = {}
        self.styles: dict[str, dict] = {}
        self.value_labels = {}
        self.cursor_time = -1.0
        self.sample_index = None
        self.offset_s = 0.0
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(9, 9, 9, 9)
        root.setSpacing(6)
        caption = QLabel("Graph curves + Wave Energy / Energy Changes references")
        caption.setWordWrap(True)
        root.addWidget(caption)
        self.cursor_label = QLabel("Cursor: move over the waveform or curve viewer")
        root.addWidget(self.cursor_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(62)
        self.scroll_host = QWidget()
        self.items = QVBoxLayout(self.scroll_host)
        self.items.setContentsMargins(3, 2, 3, 2)
        self.items.setSpacing(5)
        self.scroll.setWidget(self.scroll_host)
        root.addWidget(self.scroll, stretch=1)
        self.notice = QLabel("Load a song and run the processing graph to show intermediate curves.")
        self.notice.setWordWrap(True)
        root.addWidget(self.notice)

    def set_saved_styles(self, styles: dict):
        self.styles = {}
        if not isinstance(styles, dict):
            return
        for key, value in styles.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            color = str(value.get('color', PALETTE[0]))
            if not QColor(color).isValid():
                continue
            self.styles[key] = {
                'visible': bool(value.get('visible', True)),
                'color': color,
                'opacity': max(15, min(100, int(value.get('opacity', 85)))),
            }

    def export_styles(self):
        return {k: dict(v) for k, v in self.styles.items()}

    def reset_data(self):
        self.set_data({}, {})

    def set_data(self, outputs: dict, labels: dict):
        self.curves = {
            f'{uid}:{name}': value
            for uid, values in outputs.items()
            for name, value in values.items()
            if isinstance(value, Curve)
        }
        self.labels = {k: labels.get(k, k) for k in self.curves}
        self._loading = True
        try:
            while self.items.count():
                item = self.items.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            self.value_labels.clear()
            for index, (key, curve) in enumerate(self.curves.items()):
                style = self.styles.setdefault(key, {
                    'visible': True, 'color': PALETTE[index % len(PALETTE)], 'opacity': 85,
                })
                row = QWidget()
                layout = QHBoxLayout(row)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(5)
                check = QCheckBox(self.labels[key])
                check.setChecked(bool(style['visible']))
                step_ms = (curve.time_at(1) - curve.time_at(0)) * 1000 if len(curve.values) > 1 else 0
                meta = getattr(curve, 'metadata', {})
                fft_detail = ('; spectral Hann {actual_window_ms:.3f}ms '
                              '(requested {requested_window_ms:.3f}ms; '
                              '{window_samples} samples, FFT {fft_samples}; {window_mode})'.format(**meta)
                              if all(k in meta for k in
                                     ('actual_window_ms','requested_window_ms',
                                      'window_samples','fft_samples','window_mode')) else '')
                check.setToolTip(f'{len(curve.values):,} bins; actual step {step_ms:.4f}ms; '
                                 f'start {curve.start_time:.6f}s; '
                                 f'duration {curve.duration:.4f}s; '
                                 f'processing latency {curve.latency_s * 1000:.3f}ms'
                                 f'{fft_detail}')
                check.toggled.connect(lambda on, k=key: self.set_visible(k, on))
                layout.addWidget(check, stretch=1)
                color_button = QPushButton('●')
                color_button.setFixedWidth(28)
                color_button.setToolTip('Choose this layer color')
                color_button.setStyleSheet(f'color: {style["color"]}; font-size: 16px; padding: 2px')
                color_button.clicked.connect(lambda checked=False, k=key: self.pick_color(k))
                layout.addWidget(color_button)
                transparency = QSlider(Qt.Orientation.Horizontal)
                transparency.setRange(15, 100)
                transparency.setValue(style['opacity'])
                transparency.setFixedWidth(64)
                transparency.setToolTip('Opacity')
                transparency.valueChanged.connect(lambda v, k=key: self.set_opacity(k, v))
                layout.addWidget(transparency)
                value_label = QLabel('—')
                value_label.setMinimumWidth(65)
                value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                layout.addWidget(value_label)
                self.value_labels[key] = value_label
                self.items.addWidget(row)
            self.items.addStretch(1)
        finally:
            self._loading = False
        self.notice.setText(f'{len(self.curves)} time-aligned curve layer(s) available.' if self.curves
                            else 'No curves yet; load a song and run the graph.')
        self.refresh_values()

    def set_visible(self, key: str, on: bool):
        self.styles[key]['visible'] = bool(on)
        if not self._loading:
            self.changed.emit()

    def set_opacity(self, key: str, value: int):
        self.styles[key]['opacity'] = int(value)
        if not self._loading:
            self.changed.emit()

    def pick_color(self, key: str):
        choice = QColorDialog.getColor(QColor(self.styles[key]['color']), self, 'Choose curve color')
        if choice.isValid():
            self.styles[key]['color'] = choice.name()
            # Refresh small color picker without affecting current node results.
            self._rebuild_existing()
            self.changed.emit()

    def _rebuild_existing(self):
        outputs: dict[str, dict] = {}
        for key, value in self.curves.items():
            uid, name = key.split(':', 1)
            outputs.setdefault(uid, {})[name] = value
        self.set_data(outputs, self.labels)

    def select_node(self, uid: str):
        matching = [key for key in self.curves if key.startswith(uid + ':')]
        if not matching:
            self.notice.setText('Selected node has no curve result from this run. '
                                'Connect it upstream of the selected Candidate Output and rerun.')
            return
        for key in matching:
            self.styles[key]['visible'] = True
        self._rebuild_existing()
        self.changed.emit()
        self.notice.setText('Showing ' + ', '.join(self.labels[k] for k in matching))

    def set_cursor(self, time_s: float, offset_s: float, sample_index=None):
        self.cursor_time = time_s
        self.offset_s = offset_s
        self.sample_index = sample_index
        self.refresh_values()

    def refresh_values(self):
        sample = (f'  |  PCM #{self.sample_index:,}' if self.sample_index is not None else '')
        self.cursor_label.setText(
            f'Cursor {self.cursor_time:.6f} s{sample}  |  Offset {self.offset_s * 1000:+.3f} ms'
            if self.cursor_time >= 0 else 'Cursor: move over the waveform or curve viewer')
        for key, label in self.value_labels.items():
            value = visible_curve_value(self.curves[key], self.cursor_time, self.offset_s)
            label.setText(f'{value:.4g}' if value is not None else '—')


class CurveViewer(QWidget):
    """Per-layer waveform-aligned curve lanes below the original audio view."""
    seekRequested = Signal(float)
    navigationStarted = Signal()
    hoverChanged = Signal(float)

    def __init__(self, timeline, parent=None):
        super().__init__(parent)
        self.timeline = timeline
        self.setMinimumHeight(110)
        self.setMouseTracking(True)
        self.curves = {}
        self.styles = {}
        self.labels = {}
        self.offset_s = 0.0
        self.playhead = 0.0
        self.hover_time = -1.0
        self.colors = {}
        self.connect_samples = True  # Display-only straight segments at high zoom.
        self._curve_stats = {}
        self._pan_x = None
        self._pan_start = 0.0
        self._pan_moved = False
        self._left_x = None

    def set_layers(self, curves, styles, labels, offset_s):
        self.curves = curves.copy()
        self.styles = styles.copy()
        self.labels = labels.copy()
        self.offset_s = offset_s
        self._curve_stats = {}
        for key, curve in self.curves.items():
            arr = np.asarray(curve.values)
            finite = arr[np.isfinite(arr)]
            if finite.size:
                self._curve_stats[key] = (max(1e-9, float(np.max(np.abs(finite)))),
                                          float(np.min(finite)))
        self.update()

    def set_colors(self, colors):
        self.colors = colors.copy()
        self.update()

    def set_playhead(self, value):
        old = self.x_at(self.playhead) if self.timeline.has_audio else -1
        self.playhead = value
        new = self.x_at(value) if self.timeline.has_audio else -1
        if self.timeline.has_audio and int(new)==int(old):
            return
        if self.timeline.has_audio and abs(old-new) < self.width()/2:
            for x in (old,new):
                if self.timeline.LEFT-5 <= x <= self.width()-self.timeline.RIGHT+5:
                    self.update(max(0,int(x)-5),0,12,self.height())
        else:
            self.update()

    def set_hover(self, value):
        self.hover_time = value
        self.update()

    def time_at(self, x):
        return self.timeline.time_at(x)

    def x_at(self, t):
        return self.timeline.x_at(t)

    def paintEvent(self, event):
        if not self.colors:
            return
        p = QPainter(self)
        c = self.colors
        p.fillRect(self.rect(), QColor(c['background']))
        x0 = self.timeline.LEFT
        x1 = self.width() - self.timeline.RIGHT
        if not self.timeline.has_audio:
            p.setPen(QColor(c['muted']))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Intermediate curves appear here after processing')
            return
        visible = [(k, v) for k, v in self.curves.items() if self.styles.get(k, {}).get('visible', True)]
        if not visible:
            p.setPen(QColor(c['muted']))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Enable curves in the Layer Inspector')
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        lane_h = (self.height() - 6) / len(visible)
        for lane, (key, curve) in enumerate(visible):
            ytop = 3 + lane * lane_h
            ybottom = ytop + lane_h - 2
            data = np.asarray(curve.values)
            if not len(data) or curve.duration <= 0:
                continue
            p.setPen(QPen(QColor(c['grid']), 1))
            p.drawLine(x0, round(ybottom), x1, round(ybottom))
            if lane:
                p.drawLine(x0, round(ytop), x1, round(ytop))
            # Robust global normalization. Rendering never edits actual data.
            stats = self._curve_stats.get(key)
            if stats is None:
                continue
            maximum, min_value = stats
            bipolar = min_value < -maximum * 0.03
            height = max(9.0, lane_h - 22.0)
            center = (ytop+ybottom) / 2
            def y_for(value):
                return center - value/maximum * height*0.5 if bipolar else ybottom-3-value/maximum*height
            style = self.styles[key]
            color = QColor(style['color'])
            color.setAlpha(round(255 * style.get('opacity', 85)/100))
            p.setPen(QPen(color, 1))
            length = len(data)
            # At high zoom, drawing the true sample locations as connected
            # straight line segments removes artificial blockiness without
            # inventing new curve samples or smoothing the detector's input.
            index0 = max(0, math.floor(curve.index_at(self.timeline.view_start-self.offset_s))-1)
            index1 = min(length, math.ceil(curve.index_at(
                self.timeline.view_start+self.timeline.view_span-self.offset_s))+2)
            if self.connect_samples and 1 < index1-index0 <= self.width()*2:
                p.save()
                p.setClipRect(x0, round(ytop), x1-x0, max(1,round(ybottom-ytop)))
                path = QPainterPath()
                drawing = False
                # Narrow media-timer repaints only redraw a few horizontal pixels.
                index0 = max(index0, math.floor(curve.index_at(
                    self.time_at(max(x0, event.rect().left()-3))-self.offset_s))-1)
                index1 = min(index1, math.ceil(curve.index_at(
                    self.time_at(min(x1,event.rect().right()+3))-self.offset_s))+2)
                for i in range(index0, index1):
                    if not np.isfinite(data[i]):
                        drawing = False
                        continue
                    x = self.x_at(curve.time_at(i)+self.offset_s)
                    y = y_for(float(data[i]))
                    if not drawing:
                        path.moveTo(x,y)
                        drawing = True
                    else:
                        path.lineTo(x,y)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.drawPath(path)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                p.restore()
            else:
                for x in range(max(x0,event.rect().left()), min(x1,event.rect().right()+1)):
                    t0 = self.time_at(x) - self.offset_s
                    t1 = self.time_at(x+1) - self.offset_s
                    a = max(0, math.floor(curve.index_at(t0)))
                    b = min(length, math.ceil(curve.index_at(t1)) + 1)
                    if b <= a:
                        continue
                    selection = data[a:b]
                    selection = selection[np.isfinite(selection)]
                    if not len(selection):
                        continue
                    y1 = round(y_for(float(np.max(selection))))
                    y2 = round(y_for(float(np.min(selection))))
                    p.drawLine(x, y1, x, max(y1+1, y2))
            p.setPen(QColor(c['text']))
            p.drawText(5, round(ytop)+3, self.timeline.LEFT-9, 18,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       self.labels.get(key, key).split(' / ')[0][:12])
        play_x = self.x_at(self.playhead)
        if x0 <= play_x <= x1:
            p.setPen(QPen(QColor(c['playhead']), 1.6))
            p.drawLine(round(play_x), 0, round(play_x), self.height())
        hover_x = self.x_at(self.hover_time) if self.hover_time >= 0 else -1
        if x0 <= hover_x <= x1:
            p.setPen(QPen(QColor(c['muted']), 1, Qt.PenStyle.DotLine))
            p.drawLine(round(hover_x), 0, round(hover_x), self.height())

    def mousePressEvent(self, event):
        if not self.timeline.has_audio:
            return
        x = event.position().x()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_x = x
            self._pan_start = self.timeline.view_start
            self._pan_moved = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        elif event.button() == Qt.MouseButton.LeftButton:
            self._left_x = x
            event.accept()

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self.timeline.has_audio:
            self.hoverChanged.emit(self.time_at(x))
        if self._pan_x is not None:
            if abs(x-self._pan_x)>3 and not self._pan_moved:
                self._pan_moved = True
                self.navigationStarted.emit()
            if self._pan_moved:
                self.timeline.pan_to(self._pan_start +
                    (self._pan_x-x)/max(1,self.timeline.plot_width())*self.timeline.view_span)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_x is not None:
            moved = self._pan_moved
            self._pan_x = None
            self._pan_moved = False
            self.unsetCursor()
            if not moved:
                self.seekRequested.emit(self.time_at(event.position().x()))
            event.accept()
        elif event.button() == Qt.MouseButton.LeftButton and self._left_x is not None:
            if abs(event.position().x()-self._left_x)<4:
                self.seekRequested.emit(self.time_at(event.position().x()))
            self._left_x = None
            event.accept()

    def wheelEvent(self, event):
        if not self.timeline.has_audio:
            return
        h, v = event.angleDelta().x(), event.angleDelta().y()
        if not (h or v):
            return
        self.navigationStarted.emit()
        if h:
            self.timeline.pan_to(self.timeline.view_start-(h/120)*self.timeline.view_span*.1)
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.timeline.pan_to(self.timeline.view_start-(v/120)*self.timeline.view_span*.1)
        else:
            anchor = (event.position().x()-self.timeline.LEFT)/max(1,self.timeline.plot_width())
            self.timeline.zoom(.8**(v/120), anchor)
        event.accept()

    def leaveEvent(self, event):
        self.hoverChanged.emit(-1.0)
        self.update()
