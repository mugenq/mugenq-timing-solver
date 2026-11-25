"""Burst Cleanup has typed candidates-only edges and reversible source data."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pytest

from node_engine import (AudioData, CandidateSet, Graph, GraphError,
                         default_registry, starter_graph)
from played_frame_clock import PlayedFrameClock


def audio():
    return AudioData('synthetic.mp3', 10., np.zeros(2000, np.float32),
                     np.zeros(2000, np.float32))


def cleanup(times, scores, **params):
    d = default_registry().defs['candidates.burst_cleanup']
    values = {p.name: p.default for p in d.parameters}
    values.update(params)
    original = CandidateSet(np.asarray(times, np.float64), np.asarray(scores, np.float32))
    out = d.process({'candidates': original}, values, audio())['candidates']
    return original, out


def test_six_weak_subpeaks_followed_by_a_dominant_event():
    positions = [3.32, 3.34, 3.36, 3.38, 3.40, 3.42]
    source, result = cleanup(positions, [.12, .2, .18, .99, .17, .15],
                             neighbor_gap_ms=25, max_burst_ms=120,
                             weak_retention=.6)
    assert result.times.tolist() == pytest.approx([3.38])
    assert len(source.times) == 6
    assert source.times.tolist() == positions  # upstream remains untouched


def test_preserve_comparably_strong_close_notes_even_12ms_apart():
    _, result = cleanup([1., 1.012, 1.024, 1.036], [.96, .87, .94, .92],
                        neighbor_gap_ms=30, max_burst_ms=120,
                        weak_retention=.6)
    assert len(result.times) == 4


def test_disable_cleanup_by_zero_retention():
    source, result = cleanup([1.,1.01,1.02], [.1,.9,.1],weak_retention=0)
    np.testing.assert_array_equal(result.times,source.times)


def test_bursts_never_chain_through_a_whole_fast_passage():
    positions = np.arange(1., 2.01, .02)
    # The 110 ms maximum span means a group can contain only about 6 points.
    scores = np.array([1. if i % 6 == 3 else .1 for i in range(len(positions))])
    source, result = cleanup(positions, scores, neighbor_gap_ms=30,
                             max_burst_ms=110, weak_retention=.6)
    assert len(result.times) >= 8
    assert len(result.times) < len(source.times)
    assert np.all(np.diff(result.times) > 0)


def test_zero_confidence_keeps_all_timestamps():
    _, result = cleanup([.2,.21,.23], [0.,0.,0.])
    assert result.times.tolist()==pytest.approx([.2,.21,.23])


def test_burst_filter_is_candidates_to_candidates_in_serialized_graph():
    registry=default_registry()
    node=registry.defs['candidates.burst_cleanup']
    assert [p.kind for p in node.inputs] == ['candidates']
    assert [p.kind for p in node.outputs] == ['candidates']
    g=starter_graph(registry)
    src=next(n.id for n in g.nodes.values() if n.type_id=='signal.peaks')
    end=next(n.id for n in g.nodes.values() if n.type_id=='candidates.output')
    cleanup_node=g.add('candidates.burst_cleanup', 760, 75)
    g.connect(src,'candidates',cleanup_node.id,'candidates')
    g.connect(cleanup_node.id,'candidates',end,'candidates')
    restored=Graph.from_dict(registry,g.to_dict())
    assert restored.nodes[cleanup_node.id].type_id == 'candidates.burst_cleanup'
    # Filter does not accept curves: no hidden dependency on one detector.
    energy=next(n.id for n in g.nodes.values() if n.type_id=='wave.energy')
    with pytest.raises(GraphError,match='Incompatible'):
        g.connect(energy, 'curve', cleanup_node.id, 'candidates')


def test_buffer_clock_tracks_fast_to_slow_boundary_correctly():
    clock=PlayedFrameClock(48000)
    clock.append(2400,0,2400)
    clock.append(2400,2400,3000)
    assert clock.source_at_processed_us(50000)==pytest.approx(2400)
    assert clock.source_at_processed_us(75000)==pytest.approx(2700)


def test_cursor_calibration_scales_with_playback_speed():
    from playback_clock import calibrated_visual_position
    assert calibrated_visual_position(1., -40.,100,5)==pytest.approx(.96)
    assert calibrated_visual_position(1., -40.,25,5)==pytest.approx(.99)
    assert calibrated_visual_position(1., -40.,50,5)==pytest.approx(.98)
    assert calibrated_visual_position(1., -40.,0,5)==pytest.approx(1.)
    assert calibrated_visual_position(4.99,40,100,5)==pytest.approx(5.)


def test_waveform_pcm_and_playback_pcm_must_share_timebase():
    from streaming_pcm import analysis_and_playback_share_timebase
    assert analysis_and_playback_share_timebase(44100, 1_000_000, 44100, 1_000_000)
    assert analysis_and_playback_share_timebase(44100, 1_000_000, 44100, 1_000_001)
    assert not analysis_and_playback_share_timebase(44100, 1_000_000, 48000, 1_000_000)
    assert not analysis_and_playback_share_timebase(44100, 1_000_000, 44100, 1_001_000)
