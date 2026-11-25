"""Independent, non-destructive diagnostic curves for the Layer Inspector.

These are visual references from the existing Wave Energy and Energy Changes
implementations. They NEVER enter the selected Candidate Output branch or
change its detector settings, scores or output.
"""
from __future__ import annotations

from node_engine import AudioData, Curve, default_registry

DIAGNOSTIC_WAVE_ID = 'diagnostic-wave-energy'
DIAGNOSTIC_CHANGE_ID = 'diagnostic-energy-changes'


def diagnostic_energy_curves(audio: AudioData) -> tuple[dict, dict]:
    registry = default_registry()
    energy_def = registry.defs['wave.energy']
    changes_def = registry.defs['signal.transients']
    energy = energy_def.process({'audio': audio}, {'smoothing_ms': 8.0}, audio)['curve']
    changes = changes_def.process({'curve': energy},
                                  {'smoothing_ms': 5.0, 'lag_ms': 18.0}, audio)['curve']
    assert isinstance(energy, Curve) and isinstance(changes, Curve)
    return (
        {DIAGNOSTIC_WAVE_ID: {'curve': energy}, DIAGNOSTIC_CHANGE_ID: {'curve': changes}},
        {f'{DIAGNOSTIC_WAVE_ID}:curve': 'Wave Energy (reference) / curve',
         f'{DIAGNOSTIC_CHANGE_ID}:curve': 'Energy Changes (reference) / curve'},
    )
