"""Pure regressions for graph request order and merged spectral evidence source."""
from pathlib import Path
import sys
import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from graph_consistency import choose_spectrum, contributing_spectra, deferred_run_is_current
from node_engine import AudioData, Graph, default_registry, SpectrogramData
from spectral_analysis import SpectralCache
from audio_resolution import exact_envelope


def two_branch_graph(reverse=False):
    graph = Graph(default_registry())
    audio = graph.add('audio.source')
    branches = []
    for exact in (0, 1):
        spec = graph.add('signal.spectrogram')
        spec.params.update(exact_window=exact, window_ms=46., hop_ms=6.)
        novelty = graph.add('signal.spectral_novelty')
        picker = graph.add('signal.peaks')
        graph.connect(audio.id, 'audio', spec.id, 'audio')
        graph.connect(spec.id, 'spectrogram', novelty.id, 'spectrogram')
        graph.connect(novelty.id, 'curve', picker.id, 'curve')
        branches.append((spec,picker))
    merge = graph.add('candidates.merge')
    output = graph.add('candidates.output')
    a,b = branches[::-1] if reverse else branches
    graph.connect(a[1].id, 'candidates', merge.id, 'a')
    graph.connect(b[1].id, 'candidates', merge.id, 'b')
    graph.connect(merge.id, 'candidates', output.id, 'candidates')
    return graph, output, branches


def fixture_audio():
    rate = 48000
    pcm = .08*np.sin(2*np.pi*440*np.arange(rate//2)/rate)
    pcm[round(.24*rate):round(.24*rate)+120] += np.exp(-np.arange(120)/24)*.6
    pcm=pcm.astype(np.float32)
    lo,hi=exact_envelope(pcm,96)
    return AudioData('synthetic.wav',len(pcm)/rate,lo,hi,96/rate,rate,pcm,
                     spectral_cache=SpectralCache())


def test_two_spectral_sources_are_all_and_only_upstream_contributors():
    for reverse in (False, True):
        graph,out,branches=two_branch_graph(reverse)
        unused=graph.add('signal.spectrogram')
        options=contributing_spectra(graph,out.id,48000)
        assert [s.node_id for s in options]==[s.id for s,_ in branches]
        assert unused.id not in [s.node_id for s in options]
        assert [s.signature[0] for s in options]==['legacy_fft','exact']
        assert choose_spectrum(graph,out.id,48000).node_id==branches[0][0].id
        assert choose_spectrum(graph,out.id,48000,branches[1][0].id).signature==('exact',46.,288)
        assert choose_spectrum(graph,out.id,48000,'invalid').node_id==branches[0][0].id
        assert contributing_spectra(graph,'missing',48000)==()
        restored=Graph.from_dict(graph.registry,graph.to_dict())
        assert [s.node_id for s in contributing_spectra(restored,out.id,48000)]==[s.id for s,_ in branches]


def test_picker_independent_of_graph_evaluation_order():
    audio=fixture_audio()
    for reverse in (False,True):
        graph,out,branches=two_branch_graph(reverse)
        candidates,_,cache=graph.run_with_intermediates(audio,out.id)
        for spec,pk in branches:
            ev=cache[spec.id]['spectrogram']
            assert isinstance(ev,SpectrogramData)
            assert ev.features.window_mode==('exact' if spec.params['exact_window'] else 'legacy_fft')
            assert choose_spectrum(graph,out.id,audio.sample_rate,spec.id).signature[0]==ev.features.window_mode
        assert len(candidates.provenance)==len(candidates.times)


def test_generation_check_prevents_deferred_old_run():
    # A timer for X was enqueued at generation 2; a new explicit Y is gen 3.
    assert deferred_run_is_current(2,2)
    assert not deferred_run_is_current(2,3)
    assert not deferred_run_is_current(2,2,False)
    assert not deferred_run_is_current(None,2)
