"""v0.4.8: default spectral+burst pipeline and exact legacy merge compatibility."""
from pathlib import Path
import importlib.util
import sys
import numpy as np
import pytest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from node_engine import (AudioData, CandidateSet, Graph, default_registry,
                         default_spectral_graph, starter_graph)
from spectral_analysis import SpectralCache
from audio_resolution import exact_envelope


def fixture_audio(rate=48000, seconds=.44):
    t = np.arange(round(seconds * rate), dtype=np.float64) / rate
    x = (.06 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    for start in (.12, .18, .28):
        idx = round(start * rate)
        pulse = np.exp(-np.arange(140, dtype=np.float32) / 24.) * .65
        x[idx:idx+len(pulse)] += pulse
    lo, hi = exact_envelope(x, 96)
    return AudioData('fixture.wav', len(x)/rate, lo, hi, 96/rate,
                     rate, x, spectral_cache=SpectralCache())


def test_default_spectral_pipeline_is_complete_and_non_destructive():
    registry = default_registry()
    graph = default_spectral_graph(registry)
    nodes = list(graph.nodes.values())
    assert [n.type_id for n in nodes] == [
        'audio.source', 'signal.spectrogram', 'signal.spectral_novelty',
        'signal.peaks', 'candidates.burst_cleanup', 'candidates.output']
    assert nodes[1].params['exact_window'] == 0  # measured stronger Hanatan baseline
    assert nodes[3].params['percentile'] == 60.
    assert nodes[3].params['min_spacing_ms'] == 12.
    assert all(any(e.target==n.id for e in graph.connections) for n in nodes[1:])
    audio = fixture_audio()
    output, metrics, cache = graph.run_with_intermediates(audio, nodes[-1].id)
    assert len(output.times) and len(output.provenance)==len(output.times)
    assert cache[nodes[1].id]['spectrogram'].features.window_mode == 'legacy_fft'
    assert 'candidates.burst_cleanup' in output.provenance[0]['transforms']
    # Core human annotations are not part of the generated graph pipeline.
    assert not any(n.type_id=='current.output' for n in nodes)
    restored=Graph.from_dict(registry, graph.to_dict())
    other,_,_=restored.run_with_intermediates(audio, nodes[-1].id)
    np.testing.assert_array_equal(output.times, other.times)
    np.testing.assert_array_equal(output.scores, other.scores)
    assert len(other.provenance)==len(other.times)


def test_legacy_starter_is_still_available_for_historical_project_graphs():
    registry=default_registry()
    old=starter_graph(registry)
    assert len(old.nodes)==5
    assert all(n.type_id != 'signal.spectrogram' for n in old.nodes.values())
    old2=Graph.from_dict(registry,old.to_dict())
    assert old2.to_dict()==old.to_dict()


def test_v046_merge_chain_anchor_and_parent_lineage():
    reg=default_registry()
    merge=reg.defs['candidates.merge'].process
    audio=fixture_audio()
    a=CandidateSet(np.array([1.,1.009,1.018]),np.array([.1,.5,.9],np.float32),
                   provenance=tuple({'source': f'event-{i}'} for i in range(3)))
    b=CandidateSet(np.array([],dtype=np.float64),np.array([],dtype=np.float32))
    result=merge({'a':a,'b':b},{'merge_within_ms':10},audio)['candidates']
    np.testing.assert_array_equal(result.times, np.array([1.018]))
    assert result.provenance[0]['source']=='event-2'
    assert [e['source'] for e in result.provenance[0]['parents']]==['event-0','event-1','event-2']
    assert result.provenance[0]['transforms']==['candidates.merge']
    # Inverse-strength chain, unlike an anchored group, retains two starts.
    inverse=CandidateSet(np.array([1.,1.009,1.018]),np.array([.9,.5,.1],np.float32))
    reduced=merge({'a':inverse,'b':b},{'merge_within_ms':10},audio)['candidates']
    np.testing.assert_array_equal(reduced.times,np.array([1.,1.018]))
    assert len(reduced.provenance[0]['parents'])==2
    assert len(reduced.provenance[1]['parents'])==1


def test_merge_matches_v046_reference_selection_for_seeded_chains():
    # Historical comparison must remain exact for all positive test-score ties.
    reg=default_registry()
    merge=reg.defs['candidates.merge'].process
    audio=fixture_audio()
    rng=np.random.default_rng(48)
    empty=CandidateSet(np.array([],np.float64),np.array([],np.float32))
    for _ in range(200):
        times=np.cumsum(rng.integers(0,19,size=25)).astype(float)*.001+1
        scores=rng.integers(0,10,size=len(times)).astype(np.float32)/10.
        c=CandidateSet(times,scores)
        for radius in (0,4,10,20):
            actual=merge({'a':c,'b':empty},{'merge_within_ms':radius},audio)['candidates']
            order=np.lexsort((-scores,times))
            ts,sc=times[order],scores[order]
            chosen=[0]
            for index in range(1,len(ts)):
                if ts[index]-ts[chosen[-1]]<=radius/1000.:
                    if sc[index]>sc[chosen[-1]]:
                        chosen[-1]=index
                else:
                    chosen.append(index)
            np.testing.assert_array_equal(actual.times,ts[chosen])
            np.testing.assert_array_equal(actual.scores,sc[chosen])
            assert len(actual.provenance)==len(actual.times)
            assert sum(len(e['parents']) for e in actual.provenance)==len(c.times)


def test_merge_keeps_lineage_across_two_sources():
    reg=default_registry()
    process=reg.defs['candidates.merge'].process
    first=CandidateSet(np.array([1.]),np.array([.2],np.float32),
                       provenance=({'source':'first'},))
    second=CandidateSet(np.array([1.008]),np.array([.9],np.float32),
                        provenance=({'source':'second'},))
    output=process({'a':first,'b':second},{'merge_within_ms':10},fixture_audio())['candidates']
    assert len(output.times)==1
    assert output.provenance[0]['source']=='second'
    assert {x['source'] for x in output.provenance[0]['parents']}=={'first','second'}
