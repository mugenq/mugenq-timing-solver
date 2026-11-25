import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from merge_logic import nearby_candidate
from annotations import Annotations
from node_engine import CandidateSet


def test_merge_zone_is_time_based_and_has_fade():
    times = np.array([1.0, 1.04, 2.0])
    kinds = np.array([0, 1, 2], dtype=np.uint8)
    assert nearby_candidate(times,kinds,1.011,.012)[:2] == (1.0,'computed')
    middle = nearby_candidate(times,kinds,1.006,.012)
    assert abs(middle[2] - .5) < 1e-8
    assert nearby_candidate(times,kinds,1.01201,.012) is None
    assert nearby_candidate(times,kinds,1.04,0) is None


def test_drag_skips_the_source_and_finds_nearby_target():
    times = np.array([0.990,1.0,1.008,2.0]); kinds = np.array([0,1,0,2],dtype=np.uint8)
    dest = nearby_candidate(times,kinds,1.007,.012,skip=(1.008,'computed'))
    assert dest[:2] == (1.0,'manual')
    at_source = nearby_candidate(times,kinds,1.008,.0005,skip=(1.008,'computed'))
    assert at_source is None


def test_drag_generated_onto_generated_merges_one_persistent_manual_marker():
    a = Annotations()
    old = 1.0; target=1.1; dest=1.098
    assert a.replace_nearby('computed',old,dest,'computed',target)
    data=CandidateSet(np.array([old,target,2.0]),np.ones(3))
    times,kinds=a.display_candidates(data)
    np.testing.assert_allclose(times,[dest,2.0])
    assert list(kinds)==[1,0]
    reloaded=Annotations.from_dict(a.to_dict())
    times,_=reloaded.display_candidates(data)
    np.testing.assert_allclose(times,[dest,2.0])


def test_new_manual_point_replaces_existing_and_undo_possible():
    a=Annotations()
    assert a.add(3.0)
    before=a.to_dict()
    assert a.replace_nearby('new',3.007,3.007,'manual',3.0)
    assert a.added == [3.007]
    assert Annotations.from_dict(before).added == [3.0]


def test_replacement_preserves_confirmed_move_kind():
    a=Annotations()
    assert a.confirm(1.0)
    assert a.replace_nearby('confirmed',1.0,2.005,'computed',2.0)
    assert a.confirmed == [2.005]
    assert any(abs(x-2.0)<1e-8 for x in a.rejected)


def test_replacement_rolls_back_if_destination_is_excluded():
    a=Annotations()
    a.add(1.0)
    a.add_exclusion(1.9,2.1)
    before=a.to_dict()
    assert not a.replace_nearby('manual',1.0,2.0,'computed',2.3)
    assert a.to_dict()==before
