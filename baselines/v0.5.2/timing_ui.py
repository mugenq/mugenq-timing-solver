"""Qt dialogs for editing persistent BPM range guides."""
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QSpinBox, QComboBox, QVBoxLayout, QHBoxLayout,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QAbstractItemView, QMessageBox, QLabel)
from timing_workspace import BPMRange


class BPMRangeEditor(QDialog):
    def __init__(self, parent=None, *, region=None, default_start=0.0, default_end=5.0):
        super().__init__(parent)
        self.setWindowTitle('BPM guide range')
        form = QFormLayout(self)
        self.start = QDoubleSpinBox(); self.end = QDoubleSpinBox()
        for spin in (self.start, self.end):
            spin.setRange(0, 999999); spin.setDecimals(6); spin.setSingleStep(.1); spin.setSuffix(' s')
        self.start.setValue(region.start if region else default_start)
        self.end.setValue(region.end if region else max(default_start+.2, default_end))
        self.bpm = QDoubleSpinBox(); self.bpm.setRange(20, 1000); self.bpm.setDecimals(3)
        self.bpm.setValue(region.bpm if region else 180.0)
        self.beats = QSpinBox(); self.beats.setRange(1,32)
        self.beats.setValue(region.beats_per_bar if region else 4)
        self.unit = QComboBox()
        for x in (2,4,8,16): self.unit.addItem(str(x),x)
        self.unit.setCurrentIndex(self.unit.findData(region.beat_unit if region else 4))
        self.sub = QComboBox()
        for x in (1,2,3,4,5,6,8,12,16):
            self.sub.addItem(f'1/{x} per beat' if x!=1 else '1/1 (beats only)', x)
        self.sub.setCurrentIndex(self.sub.findData(region.subdivisions if region else 3))
        form.addRow('Range start / first downbeat',self.start)
        form.addRow('Range end (exclusive)',self.end)
        form.addRow('BPM (quarter-note tempo)',self.bpm)
        form.addRow('Beats per bar',self.beats)
        form.addRow('Meter denominator',self.unit)
        form.addRow('Subdivision',self.sub)
        help_label=QLabel('Example: 180 BPM, 4/4, 1/3 produces 12 faint ticks per bar.\n'
                          'The range start is beat 1. Guides never alter candidates.')
        help_label.setWordWrap(True)
        form.addRow(help_label)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate); buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.result_region = None

    def _validate(self):
        try:
            self.result_region = BPMRange(self.start.value(), self.end.value(), self.bpm.value(),
                                          self.beats.value(),self.unit.currentData(),self.sub.currentData())
        except ValueError as exc:
            QMessageBox.warning(self,'Invalid BPM range',str(exc))
            return
        self.accept()


class BPMRangesDialog(QDialog):
    def __init__(self, parent, guides, *, view_start=0.0, view_end=5.0):
        super().__init__(parent)
        self.setWindowTitle('BPM ranges / subdivision guides')
        self.resize(720,360)
        self.items = [BPMRange.from_dict(x.to_dict()) for x in guides.ranges]
        self.view_start,self.view_end = view_start,view_end
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('These are musical-grid references. They never delete or generate beat candidates.'))
        self.table=QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(['Start (s)','End (s)','BPM','Meter','Subdivision'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.edit)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for name,fn in [('Add range…',self.add),('Edit selected…',self.edit),('Delete selected',self.delete)]:
            b=QPushButton(name); b.clicked.connect(fn); row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.table.setRowCount(len(self.items))
        for i,g in enumerate(self.items):
            for j,v in enumerate((f'{g.start:.6f}', f'{g.end:.6f}',f'{g.bpm:g}',
                                  f'{g.beats_per_bar}/{g.beat_unit}', f'1/{g.subdivisions}')):
                self.table.setItem(i,j,QTableWidgetItem(v))

    def selected(self):
        return self.table.currentRow()

    def add(self):
        d=BPMRangeEditor(self,default_start=self.view_start,default_end=self.view_end)
        if d.exec():
            self.items.append(d.result_region)
            self.items.sort(key=lambda r:r.start)
            self.refresh()

    def edit(self,*args):
        i=self.selected()
        if 0 <= i < len(self.items):
            d=BPMRangeEditor(self,region=self.items[i])
            if d.exec():
                self.items[i] = d.result_region
                self.items.sort(key=lambda r:r.start)
                self.refresh()

    def delete(self):
        i=self.selected()
        if 0 <= i < len(self.items):
            self.items.pop(i)
            self.refresh()
