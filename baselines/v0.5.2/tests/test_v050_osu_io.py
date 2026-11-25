"""v0.5 osu! import/export comparison invariants, without Qt or live audio."""
from pathlib import Path
import csv
import itertools
import math
import os
import random
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from osu_io import (parse_osu, match_onsets, export_comparison_csv,
                    export_onset_map)


BEATMAP = '''osu file format v14
[General]
AudioFilename: song.mp3
Mode: 0
[Metadata]
Title:My test
Artist:Tester
Creator:Mapper
Version:Original
[TimingPoints]
0,500,4,1,0,60,1,0
1000,-100,4,1,0,60,0,0
2000,400,4,1,0,60,1,0
[HitObjects]
256,192,1000,1,0,0:0:0:0:
256,192,1040.25,2,0,B|320:192,1,100
256,192,1040.25,1,0,0:0:0:0:
256,192,3000,8,0,3500
'''


def write_map(tmp_path, body=BEATMAP):
    file = tmp_path/'reference.osu'
    file.write_text(body, encoding='utf-8-sig')
    return file


def test_parse_real_supplied_reference_if_available():
    supplied = Path('/mnt/data/Hanatan - Yuuhizaka (Mugenq) [Sunset].osu')
    if not supplied.is_file():
        pytest.skip('Only available in the conversation artifact environment')
    ref = parse_osu(supplied)
    assert len(ref.timing_points) == 453
    assert len(ref.red_times) == 325
    assert len(ref.green_times) == 128
    assert len(ref.hitobject_times) == 46
    assert ref.audio_filename == 'audio.mp3'
    assert ref.hitobject_times[0] == .247


def test_import_head_starts_only_and_timing_types(tmp_path):
    ref = parse_osu(write_map(tmp_path))
    assert ref.hitobject_times == (1., 1.04025)
    assert ref.raw_hitobject_count == 3
    assert ref.spinner_count == 1
    assert len(ref.red_times) == 2
    assert len(ref.green_times) == 1
    assert ref.timing_points[0].bpm == 120
    assert ref.timing_points[1].bpm is None
    assert ref.matches_audio_name(tmp_path/'song.MP3')
    assert not ref.matches_audio_name(tmp_path/'different.mp3')


def test_parser_rejects_bad_formats_and_times(tmp_path):
    with pytest.raises(ValueError, match='Only osu!standard'):
        parse_osu(write_map(tmp_path, BEATMAP.replace('Mode: 0','Mode: 3')))
    with pytest.raises(ValueError, match='timestamp'):
        parse_osu(write_map(tmp_path, BEATMAP.replace('256,192,1000,1','256,192,nan,1')))
    with pytest.raises(ValueError, match='beat length'):
        parse_osu(write_map(tmp_path, BEATMAP.replace('0,500,4,1,0,60,1,0','0,-500,4,1,0,60,1,0')))


def test_match_maximum_cardinality_then_minimum_error():
    refs = [1.,1.009,1.018]
    cands = [1.005,1.014]
    result = match_onsets(refs,cands,12)
    assert len(result.matches) == 2
    assert abs(sum(abs(m.deviation_ms) for m in result.matches)-8) < 1e-6
    assert len(result.missed) == 1 and len(result.additional) == 0
    assert len(match_onsets([1.,1.01],[1.01],12).matches) == 1


def test_optimal_matching_against_bruteforce_small():
    rng = random.Random(125)
    for _ in range(125):
        refs = sorted(rng.uniform(0,.25) for _ in range(rng.randrange(5)))
        cands = sorted(rng.uniform(0,.25) for _ in range(rng.randrange(5)))
        tol_ms = 30
        result = match_onsets(refs,cands,tol_ms)
        best = (0,float('inf'))
        def scan(i,j,n,error):
            nonlocal best
            if i==len(refs) or j==len(cands):
                if n>best[0] or (n==best[0] and error<best[1]):
                    best=(n,error)
                return
            scan(i+1,j,n,error)
            scan(i,j+1,n,error)
            d=abs(refs[i]-cands[j])*1000
            if d<=tol_ms:
                scan(i+1,j+1,n+1,error+d)
        scan(0,0,0,0)
        assert len(result.matches)==best[0]
        assert abs(sum(abs(m.deviation_ms) for m in result.matches)-best[1])<1e-6


def test_sparse_full_song_matching():
    refs = [i*.5 for i in range(3000)]
    candidates = [t+.007 for t in refs]+[i*.5+.22 for i in range(3000)]
    result = match_onsets(refs,candidates,20)
    assert len(result.matches)==3000 and len(result.additional)==3000
    assert 6.9 < result.median_abs_ms < 7.1


def test_export_placeholder_and_round_trip(tmp_path):
    audio=tmp_path/'song.mp3';audio.write_bytes(b'original audio')
    original=write_map(tmp_path);digest=original.read_bytes()
    target=tmp_path/'new [BeatSolver Onset Reference].osu'
    result=export_onset_map(target,audio,[.100,.1004,.102,.750,1.2],duration_s=1.5)
    assert result.exported_circles==4 and result.rounded_duplicates==1
    out=parse_osu(target)
    assert out.hitobject_times == (.1,.102,.75,1.2)
    assert len(out.red_times)==1 and len(out.green_times)==0
    assert out.audio_filename == 'audio.mp3'
    assert (tmp_path / 'audio.mp3').read_bytes() == audio.read_bytes()
    assert out.metadata['Version'] == 'BeatSolver Onset Reference'
    assert original.read_bytes()==digest and audio.read_bytes()==b'original audio'
    with pytest.raises(FileExistsError):
        export_onset_map(target,audio,[1.])
    assert target.read_bytes().count(b'[TimingPoints]')==1


def test_export_imported_timing_preserves_all_rows(tmp_path):
    audio=tmp_path/'song.mp3';audio.write_bytes(b'data')
    ref=parse_osu(write_map(tmp_path))
    target=tmp_path/'new.osu'
    result=export_onset_map(target,audio,[.1,1.],imported=ref,timing_mode='imported')
    generated=parse_osu(target)
    assert result.timing_points==3
    assert [p.line for p in generated.timing_points]==[p.line for p in ref.timing_points]
    assert generated.metadata['BeatmapID']=='0'
    assert generated.metadata['BeatmapSetID']=='-1'
    assert generated.metadata['Title']=='My test'


def test_export_prevents_misplaced_or_empty_files(tmp_path):
    audio=tmp_path/'song.wav';audio.write_bytes(b'data')
    (tmp_path/'elsewhere').mkdir()
    moved=export_onset_map(tmp_path/'elsewhere'/'new.osu',audio,[.1])
    assert Path(moved.path).exists()
    assert (tmp_path/'elsewhere'/'song.wav').read_bytes()==b'data'
    with pytest.raises(ValueError, match='empty'):
        export_onset_map(tmp_path/'new.osu',audio,[])
    with pytest.raises(ValueError, match='past'):
        export_onset_map(tmp_path/'new.osu',audio,[10.],duration_s=1)
    with pytest.raises(ValueError, match='Import'):
        export_onset_map(tmp_path/'new.osu',audio,[.1],timing_mode='imported')
    assert not (tmp_path/'new.osu').exists()


def test_comparison_csv_unmatched_are_not_falsely_labeled(tmp_path):
    result=match_onsets([.1,.2],[.105,.35],10)
    path=tmp_path/'compare.csv'
    export_comparison_csv(path,result)
    with path.open(encoding='utf-8-sig',newline='') as file:
        rows=list(csv.DictReader(file))
    assert {r['status'] for r in rows} == {'matched','unmatched_reference','unlabelled_detection'}
    assert next(r for r in rows if r['status']=='unlabelled_detection')['interpretation']=='NOT verified false positive'
    with pytest.raises(FileExistsError):
        export_comparison_csv(path,result)


def test_duplicate_onsets_same_ms_collapse_and_report(tmp_path):
    audio=tmp_path/'audio.mp3';audio.write_bytes(b'data')
    result=export_onset_map(tmp_path/'new.osu',audio,[.1001,.1004,.10049, .1006])
    assert result.exported_circles == 2 and result.rounded_duplicates==2


def test_export_refuses_flac_audio_not_supported_as_beatmap_audio(tmp_path):
    audio=tmp_path/'demo.flac';audio.write_bytes(b'flac')
    with pytest.raises(ValueError, match='requires MP3'):
        export_onset_map(tmp_path/'export.osu', audio, [.1])
    assert not (tmp_path/'export.osu').exists()
