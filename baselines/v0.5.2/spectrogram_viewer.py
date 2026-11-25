"""Time-locked spectrogram renderer with no disk cache or external assets."""
from __future__ import annotations
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QColor, QPen
from timeline_layers import CurveViewer


class SpectrogramViewer(CurveViewer):
    """Same cursor/pan/seek/zoom gestures as CurveViewer; shares Timeline x-axis."""

    def __init__(self,timeline,parent=None):
        super().__init__(timeline,parent)
        self.features=None
        self.mode='attack'
        self._prepared={}
        self._render_key=None
        self._image=None
        self.setMinimumHeight(85)
        self.setToolTip('Spectrogram: top = high frequencies; bright columns = strong band activity. '
                        'Middle-drag to pan; wheel to zoom; left-click to seek.')

    def set_spectrum(self,features):
        self.features=features
        self._prepared.clear()
        self._render_key=None
        self._image=None
        if features is not None:
            for mode,matrix in [('harmonic',features.harmonic),('attack',features.attack)]:
                # Robust band-wise contrast reveals quiet harmonics under pedal sustain.
                # Compute contrast thresholds from at most ~8192 time slices,
                # then normalize the full data. Without subsampling, a 4×
                # full-song spectrogram may spend seconds sorting millions of
                # spectral samples on the GUI thread when the worker finishes.
                stride=max(1, (matrix.shape[1]+8191)//8192)
                sampled=matrix[:,::stride]
                lo=np.percentile(sampled,15,axis=1).astype(np.float32)[:,None]
                hi=np.percentile(sampled,98,axis=1).astype(np.float32)[:,None]
                self._prepared[mode]=np.clip((matrix-lo)/np.maximum(.00001,hi-lo),0.,1.)
        self.update()

    def set_mode(self,mode):
        if mode not in ('harmonic','attack'):
            raise ValueError('Invalid spectrogram view mode')
        if mode!=self.mode:
            self.mode=mode
            self._render_key=None
            self.update()

    def paintEvent(self,event):
        p=QPainter(self)
        c=self.colors or {'background':'#111923','muted':'#90a4b8','playhead':'#ffca79'}
        p.fillRect(self.rect(),QColor(c['background']))
        x0=self.timeline.LEFT
        x1=self.width()-self.timeline.RIGHT
        if not self.timeline.has_audio or self.features is None:
            p.setPen(QColor(c['muted']))
            p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,
                       'Spectrogram: enable in Analysis; calculating from decoded PCM…')
            return
        width=max(1,x1-x0)
        span=self.timeline.view_span
        start=self.timeline.view_start
        key=(self.mode,round(start,7),round(span,7),width)
        if key!=self._render_key:
            data=self._prepared[self.mode]
            frames=data.shape[1]
            step=self.features.step_s
            pixels=np.arange(width+1,dtype=np.float64)
            index=np.clip(np.floor((start+span*pixels/width)/step).astype(int),0,frames)
            band_count=data.shape[0]
            out=np.empty((band_count,width),dtype=np.float32)
            for j in range(width):
                a=int(min(frames-1,index[j])); b=int(max(a+1,min(frames,index[j+1])))
                if self.mode=='attack':
                    out[:,j]=data[:,a:b].max(axis=1)
                else:
                    out[:,j]=data[:,a:b].mean(axis=1)
            # High-frequency bands appear at the top. Deep navy -> teal -> gold.
            levels=np.clip(np.flipud(out),0.,1.)
            rgb=np.empty((band_count,width,3),dtype=np.uint8)
            rgb[:,:,0]=(8+240*np.clip((levels-.38)/.62,0,1)).astype(np.uint8)
            rgb[:,:,1]=(15+206*np.clip(levels/.80,0,1)).astype(np.uint8)
            rgb[:,:,2]=(27+140*np.clip(levels/.65,0,1)*(1-np.clip((levels-.82)/.18,0,1))).astype(np.uint8)
            rgb=np.ascontiguousarray(rgb)
            self._image=QImage(rgb.data,width,band_count,3*width,QImage.Format.Format_RGB888).copy()
            self._render_key=key
        p.drawImage(QRectF(float(x0),4.,float(width),float(max(1,self.height()-8))),self._image)
        p.setPen(QColor(c['muted']))
        p.drawText(3,14,'ATTACK' if self.mode=='attack' else f'{int(self.features.frequency_hz[-1]):,} Hz')
        p.drawText(3,self.height()-8,f'{int(self.features.frequency_hz[0]):,} Hz')
        px=self.x_at(self.playhead)
        if x0<=px<=x1:
            p.setPen(QPen(QColor(c['playhead']),1.5))
            p.drawLine(round(px),0,round(px),self.height())
        hx=self.x_at(self.hover_time) if self.hover_time>=0 else -1
        if x0<=hx<=x1:
            p.setPen(QPen(QColor(c['muted']),1,Qt.PenStyle.DotLine))
            p.drawLine(round(hx),0,round(hx),self.height())
