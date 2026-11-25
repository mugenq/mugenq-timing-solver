"""Coverage for v0.3 raw outputs, offset alignment and persistent annotations."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from node_engine import AudioData, Curve, CandidateSet, Graph, default_registry, starter_graph
from annotations import Annotations


def fake_audio():
    n = 2500
    x = np.zeros(n, dtype=np.float32)
    for k in (250, 750, 1200, 1800):
        x[k:k+20] = np.linspace(0,1,20)
    return AudioData('example.mp3', 5, -x, x)


def test_graph_intermediate_outputs_and_original_api():
    g = starter_graph(default_registry())
    out = next(n.id for n in g.nodes.values() if n.type_id == 'candidates.output')
    candidates, metrics, values = g.run_with_intermediates(fake_audio(), out)
    simple_candidates, simple_metrics = g.run(fake_audio(), out)
    assert len(metrics) == len(simple_metrics) == 5
    np.testing.assert_array_equal(candidates.times, simple_candidates.times)
    curve_nodes = [n for n in g.nodes.values() if n.type_id in ('wave.energy', 'signal.transients')]
    assert len(curve_nodes) == 2
    for node in curve_nodes:
        curve = values[node.id]['curve']
        assert isinstance(curve, Curve)
        assert len(curve.values) == 2500
        assert curve.time_at(2499) == 5.0
    assert candidates.times.size > 0


def test_global_offset_is_once_and_immutable():
    a = Annotations()
    raw = CandidateSet(np.array([1.,2.]),np.array([.8,.9]))
    a.add(1.5)
    times, kinds = a.display_candidates(raw, offset_s=0.012)
    np.testing.assert_allclose(times, [1.012,1.5,2.012])
    assert list(kinds) == [0,1,0]
    np.testing.assert_array_equal(raw.times,[1.,2.])
    times2, _ = a.display_candidates(raw, offset_s=.012)
    np.testing.assert_array_equal(times,times2)


def test_rejection_survives_offset_changes():
    a = Annotations()
    raw = CandidateSet(np.array([1.,2.,3.]), np.ones(3))
    # Drag generated marker at visible 1.012s to human 1.007s.
    assert a.relocate(1., 1.007, 'computed')
    t0, k0 = a.display_candidates(raw, offset_s=.012)
    np.testing.assert_allclose(t0,[1.007,2.012,3.012])
    assert list(k0) == [1,0,0]
    t1, k1 = a.display_candidates(raw, offset_s=-.02)
    np.testing.assert_allclose(t1,[1.007,1.98,2.98])
    assert list(k1)==[1,0,0]
    restored = Annotations.from_dict(a.to_dict())
    np.testing.assert_array_equal(restored.display_candidates(raw,.012)[0],t0)


def test_masks_use_song_time_not_raw_time():
    a = Annotations()
    a.add_exclusion(1.09,1.11)
    raw = CandidateSet(np.array([1.,1.1,2.]), np.ones(3))
    t, _ = a.display_candidates(raw,.1)
    np.testing.assert_allclose(t,[1.2,2.1])


def test_curve_time_metadata_propagates():
    reg = default_registry()
    c = Curve(np.array([0.,1.,0.],dtype=np.float32), 2.0, start_time=12.0, latency_s=.014)
    result = reg.defs['signal.transients'].process({'curve':c},{'smoothing_ms':0.,'lag_ms':500.},fake_audio())['curve']
    assert result.start_time == 12.0
    assert result.latency_s == .014
    assert result.time_at(1)==13.
