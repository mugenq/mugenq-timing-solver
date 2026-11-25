import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pytest
from node_engine import (AudioData, Curve, CandidateSet, Graph, GraphError,
                         default_registry, starter_graph)
from annotations import Annotations


def make_audio():
    n=6000
    low=np.zeros(n,dtype=np.float32)
    high=np.zeros(n,dtype=np.float32)
    for k in range(100,n,500):
        high[k:k+15] = np.linspace(0,1,15)
        low[k:k+15] = -high[k:k+15]
    return AudioData('dummy.mp3',12.0,low,high)


def test_starter_runs():
    reg=default_registry()
    graph=starter_graph(reg)
    output=next(n.id for n in graph.nodes.values() if n.type_id=='candidates.output')
    result, metrics=graph.run(make_audio(),output)
    assert isinstance(result,CandidateSet)
    assert len(result.times) > 0
    assert np.all(np.diff(result.times)>=0)
    assert 0<=min(result.times)<=max(result.times)<=12
    assert len(metrics)==5


def test_graph_types_and_cycle():
    reg=default_registry(); g=Graph(reg)
    a=g.add('audio.source'); b=g.add('wave.energy'); c=g.add('signal.transients')
    with pytest.raises(GraphError,match='Incompatible'):
        g.connect(a.id,'audio',c.id,'curve')
    g.connect(a.id,'audio',b.id,'audio')
    g.connect(b.id,'curve',c.id,'curve')
    with pytest.raises(GraphError,match='Incompatible'):
        g.connect(c.id,'curve',b.id,'audio')


def test_detect_cycle_with_same_kind():
    g=Graph(default_registry()); a=g.add('signal.transients'); b=g.add('signal.transients')
    g.connect(a.id,'curve',b.id,'curve')
    with pytest.raises(GraphError,match='cycle'):
        g.connect(b.id,'curve',a.id,'curve')


def test_missing_input_and_serialization():
    r=default_registry(); g=starter_graph(r)
    output=next(n.id for n in g.nodes.values() if n.type_id=='candidates.output')
    before,_=g.run(make_audio(),output)
    copy=Graph.from_dict(r,g.to_dict())
    after,_=copy.run(make_audio(),output)
    np.testing.assert_array_equal(before.times,after.times)
    target=next(n.id for n in copy.nodes.values() if n.type_id=='signal.peaks')
    copy.disconnect_input(target,'curve')
    with pytest.raises(GraphError,match='Missing connection'):
        copy.run(make_audio(),output)


def test_merge_and_spacing():
    r=default_registry()
    sample=make_audio()
    c1=CandidateSet(np.array([1.,1.01,2.]),np.array([.7,.9,.5]))
    c2=CandidateSet(np.array([1.015,3.]),np.array([.6,.4]))
    merge=r.defs['candidates.merge'].process({'a':c1,'b':c2},{'merge_within_ms':20.},sample)['candidates']
    assert len(merge.times)==3
    assert any(abs(x-1.01)<1e-8 for x in merge.times)
    spaced=r.defs['candidates.spacing'].process({'candidates':c1},{'min_spacing_ms':30},sample)['candidates']
    assert len(spaced.times)==2
    assert spaced.times[0]==1.01


def test_manual_edits_persist_across_new_candidates():
    edits=Annotations()
    assert edits.add(5.5)
    edits.confirm(1.0)
    edits.remove(2.0,'computed')
    edits.add_exclusion(3,4)
    first=CandidateSet(np.array([1,2,3.5,5.0]),np.ones(4))
    times, kinds=edits.display_candidates(first)
    assert list(np.round(times,3))==[1.,5.,5.5]
    assert list(kinds)==[2,0,1]
    rerun=CandidateSet(np.array([1.01,2.01,3.6,4.7]),np.ones(4))
    times,kinds=edits.display_candidates(rerun)
    assert list(np.round(times,2))==[1.,4.7,5.5]
    restored=Annotations.from_dict(edits.to_dict())
    assert restored.to_dict()==edits.to_dict()
    assert restored.remove_exclusion(3.5)
    assert not restored.is_excluded(3.5)


def test_regions_merge_and_overlap():
    edits=Annotations()
    edits.add_exclusion(4,3)
    edits.add_exclusion(3.9,5)
    edits.add_exclusion(10,11)
    assert edits.excluded == [(3,5),(10,11)]
    assert not edits.add(3.3)
    assert edits.add(8.0)


def test_example_plugin():
    reg=default_registry(); src=Path(__file__).resolve().parents[1]/'plugins'/'example_threshold.py'
    types=reg.load_plugin(str(src))
    assert types==['example.threshold']
    c=CandidateSet(np.array([.5,2.]),np.array([.3,.8]))
    result=reg.defs['example.threshold'].process({'candidates':c},{'minimum_score':.5},make_audio())
    assert list(result['candidates'].times)==[2.]


def test_relocate_computed_persists_across_graph_rerun():
    annotations = Annotations()
    original = 1.200
    adjusted = 1.207  # Even a 7 ms edit must be preserved.
    assert annotations.relocate(original, adjusted, 'computed')
    data = CandidateSet(np.array([original, 3.0]), np.ones(2))
    times, kinds = annotations.display_candidates(data)
    np.testing.assert_allclose(times, [adjusted, 3.0])
    assert list(kinds) == [1, 0]
    rebuilt = Annotations.from_dict(annotations.to_dict())
    times, kinds = rebuilt.display_candidates(data)
    np.testing.assert_allclose(times, [adjusted, 3.0])
    assert list(kinds) == [1, 0]


def test_move_manual_and_confirmed_keep_kind_and_are_undoable():
    a = Annotations()
    a.add(2.0)
    assert a.relocate(2.0, 2.013, 'manual')
    assert a.added == [2.013]
    assert a.confirm_existing(2.013, 'manual')
    assert a.added == []
    assert a.confirmed == [2.013]
    assert a.relocate(2.013, 2.017, 'confirmed')
    assert a.confirmed == [2.017]
    assert any(abs(t - 2.013) < 1e-8 for t in a.rejected)
    original = a.to_dict()
    assert Annotations.from_dict(original).to_dict() == original


def test_relocation_respects_exclusions_without_destroying_marker():
    a = Annotations()
    a.add(1.0)
    a.add_exclusion(2.0, 3.0)
    snapshot = a.to_dict()
    assert not a.relocate(1.0, 2.5, 'manual')
    assert a.to_dict() == snapshot
    assert not a.relocate(1.0, 1.0, 'manual')
    assert a.to_dict() == snapshot
