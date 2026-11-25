"""Regression coverage for requested osu! reference export corrections."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from osu_io import export_onset_map, parse_osu


def test_mp3_copy_center_stack_zero_and_no_source_modification(tmp_path):
    source_folder=tmp_path/'songs';source_folder.mkdir()
    audio=source_folder/'long piano title.mp3'; audio.write_bytes(b'FAKE-MP3-CONTENT')
    output_folder=tmp_path/'osu_folder';output_folder.mkdir()
    target=output_folder/'new [BeatSolver].osu'
    r=export_onset_map(target,audio,[.12,.132,.17,.50],duration_s=1)
    assert r.exported_circles==4 and r.audio_copied and r.referenced_audio=='audio.mp3'
    assert (output_folder/'audio.mp3').read_bytes()==audio.read_bytes()
    assert sorted(source_folder.iterdir())==[audio]
    data=target.read_text(encoding='utf-8-sig')
    assert 'AudioFilename: audio.mp3' in data
    assert 'StackLeniency: 0\n' in data
    assert '192,192' not in data and '320,192' not in data
    assert all(line.startswith('256,192,') for line in data.split('[HitObjects]\n')[1].splitlines() if line.strip())
    assert parse_osu(target).hitobject_times == (.12,.132,.17,.5)
    with pytest.raises(FileExistsError):export_onset_map(target,audio,[.2])


def test_density_preview_reduces_only_export_not_current(tmp_path):
    audio=tmp_path/'audio.mp3';audio.write_bytes(b'RECORDING')
    times=[.1,.112,.13,.142,.19,.205,.29,.300,.401]
    keep=times.copy()
    r=export_onset_map(tmp_path/'light.osu',audio,times,min_circle_spacing_ms=80)
    assert times==keep and r.exported_circles==4 and r.density_skipped==5
    assert not r.audio_copied
    text=(tmp_path/'light.osu').read_text(encoding='utf-8-sig')
    assert 'ApproachRate:9' in text
    assert parse_osu(tmp_path/'light.osu').metadata['Version'].endswith('Light 80ms')
    assert parse_osu(tmp_path/'light.osu').hitobject_times == (.1,.19,.29,.401)
    f=export_onset_map(tmp_path/'full.osu',audio,times,min_circle_spacing_ms=0)
    assert f.exported_circles==9 and f.density_skipped==0
    assert 'ApproachRate:5' in (tmp_path/'full.osu').read_text(encoding='utf-8-sig')


def test_different_existing_audio_mp3_refused_without_output(tmp_path):
    song=tmp_path/'orig.mp3';song.write_bytes(b'RIGHT AUDIO')
    other=tmp_path/'output';other.mkdir()
    target=other/'map.osu';(other/'audio.mp3').write_bytes(b'WRONG AUDIO')
    with pytest.raises(FileExistsError,match='differs'):
        export_onset_map(target,song,[.1])
    assert not target.exists() and (other/'audio.mp3').read_bytes()==b'WRONG AUDIO'
    (other/'audio.mp3').write_bytes(b'RIGHT AUDIO')
    r=export_onset_map(target,song,[.1])
    assert not r.audio_copied
    assert (other/'audio.mp3').read_bytes()==b'RIGHT AUDIO'


def test_failed_or_invalid_export_never_creates_copy(tmp_path):
    song=tmp_path/'orig.mp3';song.write_bytes(b'RIGHT AUDIO')
    other=tmp_path/'output';other.mkdir()
    with pytest.raises(ValueError,match='empty'):
        export_onset_map(other/'map.osu',song,[])
    assert not (other/'audio.mp3').exists()
    with pytest.raises(ValueError,match='gap'):
        export_onset_map(other/'map.osu',song,[.1],min_circle_spacing_ms=-1)
    assert not (other/'audio.mp3').exists()
    with pytest.raises(ValueError,match='gap'):
        export_onset_map(other/'map.osu',song,[.1],min_circle_spacing_ms=float('nan'))
    assert not (other/'audio.mp3').exists()
    (other/'map.osu').write_text('EXISTING')
    with pytest.raises(FileExistsError):
        export_onset_map(other/'map.osu',song,[.1])
    assert (other/'map.osu').read_text()=='EXISTING'
    assert not (other/'audio.mp3').exists()


def test_full_song_density_memory_without_creating_audio(tmp_path):
    audio=tmp_path/'audio.mp3'; audio.write_bytes(b'RECORDING')
    times=[.1+i*.012 for i in range(6373)]
    r=export_onset_map(tmp_path/'light.osu',audio,times,min_circle_spacing_ms=80)
    assert r.exported_circles<1000 and r.density_skipped>5000
    text=(tmp_path/'light.osu').read_text(encoding='utf-8-sig')
    assert all(line.startswith('256,192,') for line in text.split('[HitObjects]\n')[1].splitlines() if line.strip())
