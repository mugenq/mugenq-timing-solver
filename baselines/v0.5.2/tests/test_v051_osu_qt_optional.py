"""GUI workflow for v0.5.1 safe audio.mp3 and playable density option."""
import os
from pathlib import Path
import sys
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
from test_v050_osu_qt_optional import make_gui,teardown
from osu_io import parse_osu


def test_gui_default_lightweight_mp3_reference_export(tmp_path,monkeypatch):
    app,win,_=make_gui(tmp_path)
    source=tmp_path/'orig.mp3'; source.write_bytes(b'fixture MP3 bytes')
    win.file_path=str(source)
    try:
        win.working.paste([.100+i*.012 for i in range(120)],replace=True)
        win.timeline.set_working(win.working)
        target=tmp_path/'new export.osu'
        monkeypatch.setattr(QInputDialog,'getItem',lambda *args,**kwargs:('Lightweight 80 ms minimum gap | Placeholder 120 BPM',True))
        monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args,**kwargs:(str(target),'osu'))
        monkeypatch.setattr(QMessageBox,'information',lambda *args,**kwargs:QMessageBox.StandardButton.Ok)
        win.export_osu_onsets()
        ref=parse_osu(target)
        assert ref.audio_filename=='audio.mp3' and len(ref.hitobject_times)<40
        assert (tmp_path/'audio.mp3').read_bytes()==source.read_bytes()
        assert 'StackLeniency: 0' in target.read_text(encoding='utf-8-sig')
        assert all(line.startswith('256,192,') for line in target.read_text(encoding='utf-8-sig').split('[HitObjects]\n')[1].splitlines() if line.strip())
        assert len(win.working.times)==120
    finally:
        teardown(app,win)
