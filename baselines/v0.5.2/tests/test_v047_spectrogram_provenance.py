"""v0.4.7 numerical window semantics, typed graph, provenance and concurrency."""
from __future__ import annotations
import csv
import importlib.util
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audio_resolution import WaveformCache, exact_envelope
from node_engine import (AudioData, CandidateSet, Graph, GraphError, SpectrogramData,
                         default_registry, export_candidate_evidence_csv)
from spectral_analysis import (compute_spectral, shared_spectral, SpectralCache,
                               spectral_cache_key)


def tone(rate=48000, seconds=.55):
    t = np.arange(round(rate*seconds)) / rate
    a = .11*np.sin(2*np.pi*440*t) + .06*np.sin(2*np.pi*660*t)
    if len(a) > round(.19*rate):
        a[round(.19*rate):] += .16*np.sin(2*np.pi*880*t[:len(a)-round(.19*rate)])
    return a.astype(np.float32)


def as_audio(samples, rate=48000):
    lo,hi=exact_envelope(samples,96)
    return AudioData('research.wav',len(samples)/rate,lo,hi,96/rate,rate,samples,
                     spectral_cache=SpectralCache())


def test_fft_requested_vs_actual_window_48khz_and_exact_center_samples():
    x=tone()
    legacy=compute_spectral(x,48000,hop_ms=6,window_ms=46)
    exact=compute_spectral(x,48000,hop_ms=6,window_ms=46,window_mode='exact')
    assert legacy.window_ms == exact.window_ms == 46
    assert legacy.window_samples == 4096
    assert legacy.actual_window_ms == pytest.approx(85.3333333333)
    assert exact.window_samples == round(.046*48000) == 2208
    assert exact.actual_window_ms == pytest.approx(46.)
    assert exact.fft_samples == legacy.fft_samples == 4096
    assert exact.hop_samples == legacy.hop_samples == 288
    np.testing.assert_array_equal(exact.center_samples, np.arange(0,len(x),288))
    assert len(exact.novelty)==len(exact.center_samples)
    assert np.max(np.abs(exact.novelty-legacy.novelty)) > .00001
    assert np.all(np.diff(exact.frequency_hz)>=0)
    assert exact.band_edges_hz.shape==(len(exact.frequency_hz),2)
    for a in (exact.harmonic, exact.attack, exact.novelty, exact.center_samples):
        assert a.flags.writeable is False
        with pytest.raises(ValueError):
            a.flat[0]=1


def test_legacy_fft_output_matches_original_046_numerically():
    original = ROOT / 'tests' / 'ref_spectral_v046.py'
    spec=importlib.util.spec_from_file_location('spectral_v046_test_ref',original)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    raw=tone(22050,.42)
    expected=module.compute_spectral(raw,22050,hop_ms=5.,window_ms=46.)
    actual=compute_spectral(raw,22050,hop_ms=5.,window_ms=46.)
    np.testing.assert_allclose(actual.harmonic,expected.harmonic,rtol=2e-7,atol=2e-7)
    np.testing.assert_allclose(actual.attack,expected.attack,rtol=2e-7,atol=2e-7)
    np.testing.assert_allclose(actual.novelty,expected.novelty,rtol=2e-7,atol=2e-7)


def test_shared_cache_single_flight_and_bounded_replacement():
    cache=SpectralCache(max_bytes=2_000_000)
    x=tone(8000,.31)
    gate=threading.Barrier(5)
    def job():
        gate.wait()
        return shared_spectral(x,8000,cache=cache,hop_ms=8,window_ms=30)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results=list(pool.map(lambda _:job(),range(5)))
    assert all(x is results[0] for x in results)
    assert len(cache)==1
    other=shared_spectral(x,8000,cache=cache,hop_ms=8,window_ms=30,
                          window_mode='exact')
    assert other is not results[0]
    key=spectral_cache_key(x,8000,8,30,window_mode='exact')
    assert cache.get(key) is other
    tiny=SpectralCache(max_bytes=32)
    ignored=shared_spectral(x,8000,cache=tiny,hop_ms=8,window_ms=30)
    assert len(tiny)==0 and ignored is not None


def test_shared_cache_failure_unblocks_waiters_and_retries():
    cache=SpectralCache()
    counter=[0]
    def factory():
        counter[0]+=1
        if counter[0]==1:
            time.sleep(.02)
            raise InterruptedError('intentional interruption')
        return compute_spectral(tone(8000,.12),8000)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(cache.get_or_compute,('x',),factory)
        time.sleep(.005)
        b=pool.submit(cache.get_or_compute,('x',),factory)
        with pytest.raises(InterruptedError):a.result(timeout=10)
        assert b.result(timeout=10) is not None
    assert counter[0]==2 and len(cache)==1


def build_typed():
    r=default_registry()
    g=Graph(r)
    a=g.add('audio.source')
    sp=g.add('signal.spectrogram')
    v=g.add('signal.spectral_novelty')
    peak=g.add('signal.peaks')
    out=g.add('candidates.output')
    g.connect(a.id,'audio',sp.id,'audio')
    g.connect(sp.id,'spectrogram',v.id,'spectrogram')
    g.connect(v.id,'curve',peak.id,'curve')
    g.connect(peak.id,'candidates',out.id,'candidates')
    return r,g,(a,sp,v,peak,out)


def test_typed_graph_roundtrip_metadata_and_preview_provenance(tmp_path):
    r,g,ids=build_typed()
    data=as_audio(tone())
    g=Graph.from_dict(r,g.to_dict())
    output,metrics,cache=g.run_with_intermediates(data,ids[-1].id)
    shared=cache[ids[1].id]['spectrogram']
    assert isinstance(shared,SpectrogramData)
    assert shared.metadata['actual_window_ms']==pytest.approx(46.)
    assert shared.metadata['requested_window_ms']==46
    assert shared.metadata['fft_samples']==4096
    assert shared.magnitudes.flags.writeable is False
    assert cache[ids[2].id]['curve'].metadata['window_mode']=='exact'
    assert metrics[1][2]=='spectrogram'
    assert len(output.provenance)==len(output.times)
    assert len(output.provenance)>0
    assert all(row['latest_node_id']==ids[3].id for row in output.provenance)
    assert all('curve_index' in row for row in output.provenance)
    assert all(row['parent_curve']['window_samples']==2208 for row in output.provenance)
    dest=tmp_path/'evidence.csv'
    export_candidate_evidence_csv(output,dest)
    with dest.open(newline='',encoding='utf-8') as file:
        rows=list(csv.DictReader(file))
    assert len(rows)==len(output.times)
    assert 'source' not in rows[0]  # deliberately stable CSV schema
    assert json.loads(rows[0]['provenance_json'])['latest_node_id']==ids[3].id


def test_old_project_graph_unmodified_and_old_spectral_default():
    r=default_registry()
    g=Graph(r)
    src=g.add('audio.source')
    spec=g.add('signal.spectral_attack')
    pk=g.add('signal.peaks')
    out=g.add('candidates.output')
    g.connect(src.id,'audio',spec.id,'audio')
    g.connect(spec.id,'curve',pk.id,'curve')
    g.connect(pk.id,'candidates',out.id,'candidates')
    saved=g.to_dict()
    # Old on-disk graphs omit the newly introduced exact_window parameter.
    for n in saved['nodes']:
        n['params'].pop('exact_window',None)
    old=Graph.from_dict(r,saved)
    assert old.nodes[spec.id].params['exact_window']==0
    produced,_,cache=old.run_with_intermediates(as_audio(tone()),out.id)
    assert cache[spec.id]['curve'].metadata['actual_window_ms']==pytest.approx(85.3333333333)
    assert len(produced.provenance)==len(produced.times)


def test_all_candidate_filters_keep_lineage_and_merge_keeps_both_parents():
    source=CandidateSet(np.array([1.,1.03,1.2]),np.array([.9,.1,.8]),
                        {'detector':'test'},tuple({'id':str(i)} for i in range(3)))
    r=default_registry()
    sample=as_audio(tone(8000),8000)
    spaced=r.defs['candidates.spacing'].process({'candidates':source},
                                   {'min_spacing_ms':40},sample)['candidates']
    assert spaced.provenance[0]['id']=='0'
    assert 'candidates.spacing' in spaced.provenance[0]['transforms']
    clean=r.defs['candidates.burst_cleanup'].process({'candidates':source},
              {'neighbor_gap_ms':45,'max_burst_ms':110,'weak_retention':.6},sample)['candidates']
    assert len(clean.provenance)==len(clean.times)
    if len(clean.provenance):
        assert 'candidates.burst_cleanup' in clean.provenance[0]['transforms']
    from node_engine import Curve
    curve=Curve(np.zeros(2000,dtype=np.float32),3.,sample_step_s=.002)
    kept=r.defs['candidates.evidence_cleanup'].process({'candidates':source,'attack_curve':curve},
         {'neighbor_gap_ms':45,'max_burst_ms':110,'weak_retention':.6,
          'spectral_valley':.35,'search_ms':12},sample)['candidates']
    assert len(kept.provenance)==len(kept.times)
    merged=r.defs['candidates.merge'].process({'a':source,'b':source},
                             {'merge_within_ms':8},sample)['candidates']
    assert len(merged.provenance)==3
    assert all(len(row['parents'])==2 for row in merged.provenance)


def test_legacy_plugin_lineage_recovered_by_graph_run():
    r=default_registry()
    r.load_plugin(str(ROOT/'plugins'/'example_threshold.py'))
    g=Graph(r)
    src=g.add('audio.source')
    energy=g.add('wave.energy')
    peaks=g.add('signal.peaks')
    legacy=g.add('example.threshold')
    out=g.add('candidates.output')
    g.connect(src.id,'audio',energy.id,'audio')
    g.connect(energy.id,'curve',peaks.id,'curve')
    g.connect(peaks.id,'candidates',legacy.id,'candidates')
    g.connect(legacy.id,'candidates',out.id,'candidates')
    # Give envelope actual variation so the legacy threshold sees peaks.
    raw=np.zeros(1200,np.float32)
    for center in (180,450,900):
        raw[center:center+6]=np.linspace(.02,.8,6)
    from audio_resolution import exact_envelope
    lo,hi=exact_envelope(raw,2)
    audio=AudioData('foo',1.2,lo,hi,.002,1000,raw)
    res,_,cache=g.run_with_intermediates(audio,out.id)
    assert len(res.provenance)==len(res.times)
    assert res.provenance
    assert all(row['parents'] for row in res.provenance)
