"""Qt timing inspector and histogram of heuristic candidate priorities."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor,QPainter,QPen
from PySide6.QtWidgets import QLabel,QVBoxLayout,QWidget
from timing_diagnostics import confidence_histogram
import numpy as np


class ScoreHistogram(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setMinimumHeight(90)
        self.counts=np.zeros(20,dtype=np.int64)
    def set_scores(self,values):
        self.counts=confidence_histogram(values)
        self.update()
    def paintEvent(self,event):
        p=QPainter(self)
        p.fillRect(self.rect(),QColor('#15232e'))
        height=max(10,self.height()-24)
        width=max(1,self.width()-24)
        top=8;left=12
        scale=max(1,int(max(self.counts)))
        p.setPen(Qt.PenStyle.NoPen)
        for i,count in enumerate(self.counts):
            x=left+round(i*width/len(self.counts))
            w=max(1,round((i+1)*width/len(self.counts))-round(i*width/len(self.counts))-1)
            h=round(height*int(count)/scale)
            p.fillRect(x,top+height-h,w,max(0,h),QColor('#e6ac61'))
        p.setPen(QPen(QColor('#94a9b9')))
        p.drawText(left,self.height()-5,'0.0')
        p.drawText(max(left,self.width()-34),self.height()-5,'1.0')
        p.end()


class DiagnosticsPanel(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        layout=QVBoxLayout(self)
        title=QLabel('TIMING INSTRUMENTS')
        layout.addWidget(title)
        self.render=QLabel('Paint delivery: —')
        self.timer=QLabel('Update callbacks: —')
        self.lag=QLabel('Paint gap p95/p99: —')
        self.diff=QLabel('Clock vs media reports: —')
        for label in (self.render,self.timer,self.lag,self.diff):
            label.setWordWrap(True);layout.addWidget(label)
        caveat=QLabel('These are application-level measurements. They do NOT measure monitor scanout, headphone latency or absolute MP3 onset alignment.')
        caveat.setWordWrap(True)
        layout.addWidget(caveat)
        layout.addWidget(QLabel('PREVIEW CONFIDENCE DISTRIBUTION'))
        self.histogram=ScoreHistogram()
        layout.addWidget(self.histogram)
        self.summary=QLabel('Graph not run yet')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addStretch()

    def update_snapshot(self,stat):
        self.render.setText(f'Qt Paint delivery: {stat.paint_hz:,.1f}/s ({stat.paint_samples} events)')
        self.timer.setText(f'Playhead timer: {stat.timer_hz:,.1f}/s (target configured separately)')
        self.lag.setText(f'Paint gap p95/p99: {stat.paint_p95_ms:.1f} / {stat.paint_p99_ms:.1f} ms')
        self.diff.setText(f'Clock vs media-report discrepancy: mean |{stat.clock_disagreement_mean_ms:.1f}| ms, p95 |{stat.clock_disagreement_p95_ms:.1f}| ms ({stat.backend_samples} reports)')

    def update_candidates(self,result):
        if result is None:
            self.histogram.set_scores([])
            self.summary.setText('Graph not run yet')
            return
        scores=result.confidence
        self.histogram.set_scores(scores)
        if len(scores):
            self.summary.setText(f'{len(scores):,} predictions; median {np.median(scores):.3f}; '
                                 f'{np.count_nonzero(result.kept):,} kept. Scores are heuristics, NOT probabilities.')
        else:
            self.summary.setText('No generated predictions')
