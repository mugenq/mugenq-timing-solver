"""Example user plugin. Load explicitly through Graph > Load Python plugin.

Security: Python plugins execute as normal code under your user account.
This plugin accepts candidate positions and returns only stronger candidates.
"""
from node_engine import NodeDefinition, Parameter, Port, CANDIDATES, CandidateSet
import numpy as np


def threshold(inputs, params, audio):
    data: CandidateSet = inputs['candidates']
    keep = data.scores >= float(params['minimum_score'])
    return {'candidates': CandidateSet(data.times[keep].copy(), data.scores[keep].copy())}


def register(registry):
    registry.register(NodeDefinition(
        type_id='example.threshold', label='Score Threshold',
        inputs=(Port('candidates', CANDIDATES),),
        outputs=(Port('candidates', CANDIDATES),),
        parameters=(Parameter('minimum_score', 0.5, 0.0, 1.0, 0.05, 2),),
        process=threshold,
        description='Example plugin: filter candidates by confidence score.',
    ))
