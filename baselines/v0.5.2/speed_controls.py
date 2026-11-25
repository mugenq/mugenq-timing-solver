"""Playback-speed UI conversion without a Qt dependency.

The slider stores tenths of one percent as integers; do not mistake
0.1% UI precision for an audio-device timing guarantee.
"""
from __future__ import annotations
import math

MIN_SPEED_PERCENT = 0.0
MAX_SPEED_PERCENT = 135.0
SLIDER_UNITS_PER_PERCENT = 10


def clamp_speed_percent(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Playback speed must be finite')
    return round(max(MIN_SPEED_PERCENT, min(MAX_SPEED_PERCENT, value)), 1)


def speed_to_slider(percent: float) -> int:
    return round(clamp_speed_percent(percent) * SLIDER_UNITS_PER_PERCENT)


def slider_to_speed(value: int) -> float:
    return clamp_speed_percent(int(value) / SLIDER_UNITS_PER_PERCENT)
