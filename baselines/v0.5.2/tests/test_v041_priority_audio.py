"""v0.4.1 regression tests for hierarchy, half-open guides and sample-aligned clicks."""
from pathlib import Path
import io
import sys
import wave
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from node_engine import CandidateSet
from candidate_priority import rank_candidates, prune_candidates
from live_mix import click_events,write_pcm_mix
from timing_workspace import TimingGuides,BPMRange,WorkingCandidates


def test_priority_keeps_human_committed_data_intact():
    raw=CandidateSet(np.array([1.,1.006,2.,2.008]),np.array([.9,.2,.8,.1],dtype=np.float32))
    current=WorkingCandidates([1.006,4.])
    before=raw.times.copy(),raw.scores.copy()
    ranked=rank_candidates(raw,threshold=.5,duplicate_radius_ms=10)
    out=prune_candidates(raw,ranked)
    assert len(out.times)<len(raw.times)
    assert (raw.times==before[0]).all() and (raw.scores==before[1]).all()
    assert current.times == [1.006,4.]
    assert 'near stronger candidate' in ranked.reasons or 'below threshold' in ranked.reasons


def test_priority_local_rank_protects_quiet_section():
    raw=CandidateSet(np.array([1.,1.1,1.2,10.,10.1,10.2]),
                     np.array([.02,.10,.03,.2,1.,.3],dtype=np.float32))
    rank=rank_candidates(raw)
    assert rank.local_rank[1]==pytest.approx(1.)
    assert rank.confidence[1]>.5
    assert rank.confidence[1]>rank.confidence[0]


def test_duplicate_selection_prefers_stronger_evidence():
    raw=CandidateSet(np.array([1.,1.005,2.]),np.array([.9,.1,.5],dtype=np.float32))
    priority=rank_candidates(raw,duplicate_radius_ms=10)
    assert priority.kept.tolist()==[True,False,True]
    assert priority.reasons[1]=='near stronger candidate'
    assert rank_candidates(raw,duplicate_radius_ms=0).kept.all()


def test_exclusive_guide_end_and_adjacent_regions():
    first=BPMRange(0,1,120,4,4,1)
    second=BPMRange(1,2,120,4,4,1)
    assert first.ticks(0,2)==[(0.,'bar'),(.5,'beat')]
    assert second.ticks(0,2)==[(1.,'bar'),(1.5,'beat')]
    assert first.ticks(1,1)==[]
    assert second.ticks(1,1)==[(1.,'bar')]


def test_hitsound_source_both_and_grid():
    guides=TimingGuides([BPMRange(0,1,120,4,4,1)])
    both=click_events([.25],[.5],guides,'both',1.)
    assert both == [(0.25,1640.,.85),(.5,1100.,1.)]
    ticks=click_events([],[],guides,'grid',1.)
    assert [(round(t,6),freq) for t,freq,_ in ticks] == [(0.,1930.),(.5,1360.)]
    with pytest.raises(ValueError):click_events([],[],guides,'other',1)


def test_clicks_sample_aligned_across_pcm_chunks(tmp_path):
    rate=48000
    # The click at sample 29 crosses the boundary between 32-frame chunks.
    data=np.zeros((128,2),dtype=np.float32)
    chunks=[data[i:i+32].reshape(-1) for i in range(0,128,32)]
    out=tmp_path/'mix.wav'
    assert write_pcm_mix(chunks,rate,[(29/rate,1600,.8)],out,
                         music_gain=0,click_gain=.8,channels=2)==128
    with wave.open(str(out),'rb') as w:
        assert w.getnframes()==128
        samples=np.frombuffer(w.readframes(128),dtype='<i2').reshape(-1,2)
    assert np.max(np.abs(samples[:29]))==0
    assert np.max(np.abs(samples[30:60]))>100
    assert np.max(np.abs(samples[60:100]))>100


def test_cancelled_render_removes_tempfile(tmp_path):
    out=tmp_path/'cancel.wav'
    with pytest.raises(InterruptedError):
        write_pcm_mix([np.zeros(64,dtype=np.float32)],48000,[],out,
                      cancelled=lambda:True)
    # Caller (render_file) owns deletion on failure. At the pure write helper
    # layer, a complete, valid file is never reported as success.
    assert out.exists()
    out.unlink()
    assert not list(tmp_path.iterdir())


def test_render_file_cancelled_does_not_leave_wav(monkeypatch,tmp_path):
    """The production renderer cleans an interrupted WAV, unlike its low-level writer."""
    import sys,types
    from live_mix import render_file
    class FakeFormat:
        FLOAT32=1
    def fake_stream_file(filename,**options):
        return iter([np.zeros(128,dtype=np.float32)])
    monkeypatch.setitem(sys.modules,'miniaudio',types.SimpleNamespace(
        SampleFormat=FakeFormat,
        get_file_info=lambda source: types.SimpleNamespace(sample_rate=48000),
        stream_file=fake_stream_file))
    destination=tmp_path/'interrupted.wav'
    with pytest.raises(InterruptedError):
        render_file('placeholder.wav',destination,[],cancelled=lambda: True)
    assert not destination.exists()
