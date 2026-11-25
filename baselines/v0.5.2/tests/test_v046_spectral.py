"""Synthetic sustain/rearticulation and graph tests, independent of Windows Qt."""
import numpy as np
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spectral_analysis import compute_spectral, evidence_cleanup
from node_engine import AudioData, CandidateSet, Curve, Graph, GraphError, default_registry, starter_graph


def piano(rate=22050, duration=2.):
    x=np.zeros(round(rate*duration),np.float32)
    t=np.arange(len(x))/rate
    # Sustained first attack plus two quiet re-articulations; volume is already
    # high when latter notes begin, so the absolute audio envelope stays active.
    for onset,amplitude,freq in [(0.3,.6,330),(0.67,.13,440),(.72,.12,550)]:
        j=round(onset*rate)
        time=np.arange(len(x)-j)/rate
        shape=np.minimum(time/.003,1.)*np.exp(-time*2.8)
        x[j:]+=amplitude*shape*np.sin(2*np.pi*freq*time)
    return x


def test_spectral_attack_timebase_rearticulation_and_stable_sustain():
    raw=piano()
    features=compute_spectral(raw,22050,hop_ms=5,window_ms=34,bands=72)
    assert features.harmonic.ndim==2
    assert features.harmonic.shape==features.attack.shape
    assert len(features.novelty)==features.harmonic.shape[1]
    assert features.step_s==pytest.approx(round(.005*22050)/22050)
    for onset in (.3,.67,.72):
        where=(np.arange(len(features.novelty))*features.step_s>onset-.028)&(np.arange(len(features.novelty))*features.step_s<onset+.045)
        assert features.novelty[where].max()>.008
    assert np.all(features.novelty>=0)
    assert np.all(np.isfinite(features.harmonic))


def test_spectral_computation_is_non_destructive_and_cancelable():
    pcm=piano(); copy=pcm.copy()
    with pytest.raises(InterruptedError):
        compute_spectral(pcm,22050,cancelled=lambda:True)
    out=compute_spectral(pcm,22050,hop_ms=5)
    np.testing.assert_array_equal(pcm,copy)
    assert out.harmonic.dtype==np.float32


def test_evidence_cleanup_preserves_rearticulated_weak_notes():
    t=np.array([1.,1.030,1.064],np.float64)
    scores=np.array([1.,.12,.16],np.float32)
    step=.002
    c=np.zeros(700,np.float32)
    for center,height in [(1.,1.),(1.030,.7),(1.064,.8)]:
        sample=round(center/step)
        c[sample-2:sample+3]=[0,height*.4,height,height*.4,0]
    mask=evidence_cleanup(t,scores,c,0,step,neighbor_gap_ms=40,
                          max_burst_ms=110,weak_retention=.65,prominence=.35)
    assert mask.tolist()==[True,True,True]
    # Without independent valleys weak events should disappear.
    flat=np.zeros_like(c);flat[492:538]=.5
    mask=evidence_cleanup(t,scores,flat,0,step,neighbor_gap_ms=40,
                          max_burst_ms=110,weak_retention=.65,prominence=.35)
    assert mask.tolist()==[True,False,False]


def test_typed_spectral_branch_and_evidence_cleanup_run():
    reg=default_registry()
    assert [p.kind for p in reg.defs['signal.spectral_attack'].inputs]==['audio']
    assert [p.kind for p in reg.defs['candidates.evidence_cleanup'].inputs]==['candidates','curve']
    a=AudioData('synthetic.wav',2.,np.zeros(1000,np.float32),np.zeros(1000,np.float32),
                bin_seconds=.002,sample_rate=22050,pcm=piano())
    g=starter_graph(reg)
    source=next(n for n in g.nodes.values() if n.type_id=='audio.source')
    peaks=next(n for n in g.nodes.values() if n.type_id=='signal.peaks')
    output=next(n for n in g.nodes.values() if n.type_id=='candidates.output')
    spec=g.add('signal.spectral_attack',450,200)
    cleanup=g.add('candidates.evidence_cleanup',780,200)
    g.connect(source.id,'audio',spec.id,'audio')
    g.connect(peaks.id,'candidates',cleanup.id,'candidates')
    g.connect(spec.id,'curve',cleanup.id,'attack_curve')
    g.connect(cleanup.id,'candidates',output.id,'candidates')
    restored=Graph.from_dict(reg,g.to_dict())
    produced,metrics,cache=restored.run_with_intermediates(a,output.id)
    assert isinstance(cache[spec.id]['curve'],Curve)
    assert isinstance(produced,CandidateSet)
    assert len(produced.times)<=len(cache[peaks.id]['candidates'].times)
    assert len(metrics)>=7


def test_no_pcm_raises_actionable_message():
    d=default_registry().defs['signal.spectral_attack']
    a=AudioData('x',1,np.zeros(200),np.zeros(200),sample_rate=22050)
    with pytest.raises(ValueError,match='PCM'):
        d.process({'audio':a},{p.name:p.default for p in d.parameters},a)


def test_evidence_filter_does_not_delete_without_any_spectral_information():
    times=np.array([1.,1.025,1.055])
    scores=np.array([.9,.1,.05])
    keep=evidence_cleanup(times,scores,np.zeros(1000),0,.002)
    assert keep.tolist()==[True,True,True]
