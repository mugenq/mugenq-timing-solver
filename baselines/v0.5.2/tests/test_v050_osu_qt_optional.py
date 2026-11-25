"""GUI-level osu! references and safe export; requires actual PySide6 on Windows."""
import os
from pathlib import Path
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog, QInputDialog
import numpy as np
from audio_resolution import WaveformCache
from beatsolver_timeline import BeatSolverWindow
from osu_io import parse_osu


def make_gui(tmp_path):
    app=QApplication.instance() or QApplication([])
    win=BeatSolverWindow()
    audio=tmp_path/'demo.wav'
    audio.write_bytes(b'not decoded by UI tests')
    pcm=np.zeros(48000*2,dtype=np.float32)
    cache=WaveformCache(48000,len(pcm),np.array([0.],np.float32),
                        np.array([0.],np.float32),len(pcm),pcm)
    win.file_path=str(audio)
    win.timeline.set_audio(cache.coarse_min,cache.coarse_max,cache.duration,cache)
    return app,win,audio


def write_reference(tmp_path):
    path=tmp_path/'example.osu'
    path.write_text('''osu file format v14
[General]
AudioFilename: demo.wav
Mode: 0
[Metadata]
Title:Example
Artist:Example Artist
Creator:someone
Version:Original
[TimingPoints]
0,500,4,1,0,60,1,0
1000,-100,4,1,0,60,0,0
[HitObjects]
192,192,400,1,0,0:0:0:0:
192,192,700,2,0,B|250:192,1,120
''',encoding='utf-8')
    return path


def teardown(app,win):
    win.mark_clean()
    assert win.close()
    app.processEvents()


def test_gui_import_overlays_and_comparison(tmp_path):
    app,win,audio=make_gui(tmp_path)
    try:
        assert win.import_osu_reference(str(write_reference(tmp_path)))
        assert win.osu_reference is not None
        assert list(win.timeline.ref_hit_times)==[.4,.7]
        assert len(win.timeline.ref_red_times)==1
        assert len(win.timeline.ref_green_times)==1
        assert '2 heads' in win.reference_label.text()
        win.timeline.set_reference_visibility(hits=False,timing=True)
        assert not win.timeline.show_ref_hits and win.timeline.show_ref_timing
        win.working.paste([.407,.705],replace=True)
        win.timeline.set_working(win.working)
        match=win._compare_candidates('current',10)
        assert len(match.matches)==2 and match.median_abs_ms<8
        win.remove_osu_reference()
        assert win.timeline.osu_reference is None
        assert len(win.timeline.ref_hit_times)==0
    finally:
        teardown(app,win)


def test_gui_export_current_to_new_map(tmp_path,monkeypatch):
    app,win,audio=make_gui(tmp_path)
    try:
        win.working.paste([.4,.7],replace=True)
        win.timeline.set_working(win.working)
        target=tmp_path/'new [BeatSolver Onset Reference].osu'
        monkeypatch.setattr(QInputDialog,'getItem',lambda *args,**kwargs:('Placeholder 120 BPM (not estimated)',True))
        monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args,**kwargs:(str(target),'osu'))
        monkeypatch.setattr(QMessageBox,'information',lambda *args,**kwargs: QMessageBox.StandardButton.Ok)
        win.export_osu_onsets()
        out=parse_osu(target)
        assert out.audio_filename==audio.name
        assert len(out.hitobject_times)==2 and len(out.red_times)==1
        assert out.hitobject_times == (.4,.7)
    finally:
        teardown(app,win)


def test_project_persists_reference_and_does_not_replace_default_graph(tmp_path,monkeypatch):
    app,win,audio=make_gui(tmp_path)
    try:
        ref=write_reference(tmp_path)
        assert win.import_osu_reference(str(ref))
        win.show_osu_hits_action.setChecked(False)
        win.session_path=str(tmp_path/'saved.beatsolver.json')
        assert win.save_project()
        import json
        obj=json.loads(Path(win.session_path).read_text(encoding='utf-8'))
        assert obj['schema']==1 and obj['osu_reference_path']==str(ref.resolve())
        assert obj['osu_show_hitobjects'] is False
        assert 'candidates.burst_cleanup' in [n['type'] for n in obj['graph']['nodes']]
    finally:
        teardown(app,win)
