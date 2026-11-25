import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import io
import math
import time
import wave

import numpy as np
import pytest

from timing_workspace import BPMRange, TimingGuides, WorkingCandidates
from playback_clock import PlaybackClock
from audition_audio import audible_excerpt
from merge_logic import nearby_candidate


def test_180_bpm_4_4_one_third_has_twelve_subdivisions_per_bar():
    r=BPMRange(2.0,7.0,180,4,4,3)
    assert r.bar_seconds == pytest.approx(4*60/180)
    ticks=r.ticks(2,2+r.bar_seconds-1e-5)
    assert len(ticks)==12
    assert sum(1 for _,kind in ticks if kind=='bar') == 1
    assert sum(1 for _,kind in ticks if kind=='beat') == 3
    assert sum(1 for _,kind in ticks if kind=='sub') == 8
    assert ticks[0] == (2.0,'bar')
    assert ticks[1][0] == pytest.approx(2.0+1/9)
    assert r.ticks(2+r.bar_seconds,2+r.bar_seconds)[0][1] == 'bar'


def test_meter_denominator_and_phase_anchor():
    r=BPMRange(1.5,6.0,120,6,8,3)
    assert r.beat_seconds == .25
    assert r.bar_seconds == 1.5
    assert r.ticks(3.0,3.0)[0] == pytest.approx((3.0,'bar'))


def test_long_grid_does_not_materialize_every_subdivision():
    r=BPMRange(0,7200,1000,4,4,16)
    before=time.monotonic()
    result=r.ticks(0,7200,max_count=1500)
    assert len(result)<=1500
    assert all(kind=='bar' for _,kind in result)
    assert time.monotonic()-before < .3


def test_guide_persistence_and_no_candidate_coupling():
    guides=TimingGuides([BPMRange(.2,4.2,180,4,4,3)],False)
    assert TimingGuides.from_dict(guides.to_dict()).to_dict() == guides.to_dict()
    with pytest.raises(ValueError):
        BPMRange(2,1,180)
    with pytest.raises(ValueError):
        BPMRange(0,2,180,4,4,0)


def test_committed_set_is_independent_of_graph_previews_and_global_offset():
    workspace=WorkingCandidates()
    assert workspace.paste([1.004,2.0], replace=True)==2
    assert workspace.paste([2.0,3.0],replace=False)==1
    assert workspace.times == [1.004,2.0,3.0]
    assert WorkingCandidates.from_dict(workspace.to_dict()).times == workspace.times
    # A graph rerun can replace its own array without affecting Current output.
    other_preview=np.array([1.2,2.3,5.0])
    assert workspace.times == [1.004,2.0,3.0]
    assert len(other_preview)==3


def test_current_replacement_and_atomic_move():
    workspace=WorkingCandidates([1.0,1.010])
    assert workspace.remove(1.010)
    assert workspace.move(1.0,1.009)
    assert workspace.times == [1.009]
    assert not workspace.move(4,5)
    assert workspace.times == [1.009]
    assert workspace.nearest(1.014,.010)==pytest.approx(1.009)
    assert workspace.nearest(1.05,.010) is None


def test_merging_respects_current_vs_preview_layers():
    times=np.array([1.0,1.002,1.01],dtype=float)
    kinds=np.array([0,3,0],dtype=np.uint8)
    assert nearby_candidate(times,kinds,1.001,.01,layer='current')[:2] == (1.002,'current')
    assert nearby_candidate(times,kinds,1.001,.01,layer='preview')[:2] == (1.,'computed')


def test_click_audition_is_sample_aligned_inside_the_audio_wav():
    rate=48000
    pcm=np.zeros(rate*3,dtype=np.float32)
    blob,first,frames=audible_excerpt(pcm,rate,.5,2.5,[.75,1.25,2.35])
    assert first==.5
    assert frames==[12000,36000,88800]
    with wave.open(io.BytesIO(blob),'rb') as wav:
        assert wav.getnchannels()==1
        assert wav.getframerate()==rate
        actual=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
    assert actual[:11990].max()==0
    assert np.max(np.abs(actual[frames[0]:frames[0]+400]))>100
    assert np.max(np.abs(actual[frames[1]:frames[1]+400]))>100
    assert np.max(np.abs(actual[frames[2]:frames[2]+400]))>100


def test_audition_rejects_missing_candidates_and_ranges_outside_audio():
    pcm=np.zeros(4000,dtype=np.float32)
    with pytest.raises(ValueError):
        audible_excerpt(pcm,4000,0,.5,[])
    with pytest.raises(ValueError):
        audible_excerpt(pcm,4000,5,6,[5.5])


def test_playback_clock_smooth_interpolation_and_seek_reset():
    ts=[0.0]
    clk=PlaybackClock(now=lambda:ts[0])
    clk.reset(12,duration=50)
    clk.set_rate(1.0)
    clk.set_playing(True)
    ts[0]+=.004
    assert clk.displayed()==pytest.approx(12.004)
    ts[0]+=.026
    clk.sync(12.029)
    assert abs(clk.displayed()-12.03)<.002
    clk.reset(20)
    assert clk.displayed()==pytest.approx(20.)
    clk.set_rate(.5)
    ts[0]+=.004
    assert clk.displayed()==pytest.approx(20.002)
    clk.set_playing(False)
    t=clk.displayed()
    ts[0]+=.5
    assert clk.displayed()==pytest.approx(t)


def test_legacy_projects_have_empty_current_and_guides():
    assert WorkingCandidates.from_dict({}).times==[]
    assert TimingGuides.from_dict({}).ranges==[]
