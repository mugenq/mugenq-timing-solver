"""
Beat Timer v0.6 trajectory-aware audio-only prediction engine.

This module reads audio and predicts BPM/timing sections. It never reads
or imports .osu map data. Evaluation belongs in beattimer_evaluation.py.
"""

__version__ = "0.12"
DENSE_OFFSET_METHOD = "viterbi_unwrapped_segmented"

from pathlib import Path

import librosa
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go
from IPython.display import display
from plotly.subplots import make_subplots


LOCAL_PHASE_CHANGE_THRESHOLD_SECONDS = 0.020

# Audio analysis defaults.
N_FFT = 2048
HOP_LENGTH = 32

# Global BPM search range.
MIN_BPM = 60
MAX_BPM = 240

# 0 = integer, 1 = one decimal, n = n decimals, None = no rounding.
ROUND_INITIAL_BPM = 1

# Rising-onset detector.
MINIMUM_RISE = 0.12
MINIMUM_SLOPE = 0.02
LOOKBACK_FRAMES = 30
MINIMUM_PEAK_DISTANCE_FRAMES = 10

# Global grid phase and matching.
PHASE_STEPS = 2000
PHASE_MATCH_TOLERANCE_SECONDS = 0.040
GRID_MATCH_TOLERANCE_SECONDS = 0.060

# Local tempo tracking.
USE_LOCAL_TEMPO = True
LOCAL_WINDOW_SECONDS = 2.0
LOCAL_STEP_SECONDS = 0.25
LOCAL_MINIMUM_ONSETS = 4
LOCAL_BPM_CHANGE_THRESHOLD = 0.50
LOCAL_MINIMUM_WINDOWS_PER_SECTION = 3
LOCAL_PHASE_MATCH_TOLERANCE_SECONDS = 0.060

LOCAL_MINIMUM_MATCHES = 3
LOCAL_MINIMUM_MATCH_RATIO = 0.45
LOCAL_MINIMUM_COVERED_BEATS = 1.5
LOCAL_MAXIMUM_P95_ERROR_SECONDS = 0.030
LOCAL_MAXIMUM_REFERENCE_DEVIATION = 0.25
LOCAL_MINIMUM_CONFIDENCE = 0.55

# Global timing-section model.
USE_INTEGER_BPM = True
SECTION_TOLERANCE_MS = 6.0
MINIMUM_SECTION_POINTS = 8
ERROR_QUANTILE = 0.95

# Plotting.
PIXELS_PER_SECOND = 45
MINIMUM_FIGURE_WIDTH = 1200

# Dense rhythmic-evidence diagnostics.
DENSE_OFFSET_WINDOW_SECONDS = 4.0
DENSE_OFFSET_STEP_SECONDS = 0.25
DENSE_OFFSET_SEARCH_MS = 45.0
DENSE_OFFSET_RESOLUTION_MS = 1.0
DENSE_OFFSET_SUBDIVISIONS = (1, 2, 4)
DENSE_OFFSET_MINIMUM_GRID_POINTS = 6
DENSE_OFFSET_SMOOTHING_WINDOWS = 5

def grid_offset_at_time(
    *,
    local_phase: float,
    local_period: float,
    reference_phase: float,
    reference_period: float,
    comparison_time: float,
) -> float:
    """
    Compare the local and reference grids at one common time.

    Returns:
        local grid time - reference grid time, in seconds.
    """
    if (
        not np.isfinite(local_phase)
        or not np.isfinite(local_period)
        or local_period <= 0
        or not np.isfinite(reference_phase)
        or not np.isfinite(reference_period)
        or reference_period <= 0
    ):
        return np.nan

    local_index = np.round(
        (comparison_time - local_phase)
        / local_period
    )

    reference_index = np.round(
        (comparison_time - reference_phase)
        / reference_period
    )

    local_grid_time = (
        local_phase
        + local_index * local_period
    )

    reference_grid_time = (
        reference_phase
        + reference_index * reference_period
    )

    raw_offset = (
        local_grid_time
        - reference_grid_time
    )

    # Resolve neighboring-beat ambiguity using the reference period.
    equivalence_period = reference_period / 2.0

    return float(
        (
            raw_offset
            + equivalence_period / 2.0
        )
        % equivalence_period
        - equivalence_period / 2.0
    )


def _score_grid_with_phase(
    peak_times: np.ndarray,
    *,
    start_time: float,
    end_time: float,
    bpm: float,
    phase: float,
    grid_match_tolerance: float,
) -> dict:
    mask = (peak_times >= start_time) & (peak_times < end_time)
    local_times = np.asarray(peak_times[mask], dtype=float)

    if len(local_times) == 0:
        return {
            "matched_count": 0,
            "match_ratio": 0.0,
            "median_error_ms": np.inf,
            "p95_error_ms": np.inf,
            "phase": float(phase),
        }

    period = 60.0 / float(bpm)
    first_grid = phase + np.ceil((start_time - phase) / period) * period
    grid = np.arange(first_grid, end_time + period / 2.0, period)
    matches = match_onsets_to_grid(local_times, grid, tolerance=grid_match_tolerance)
    absolute_errors_ms = np.abs(matches["errors"]) * 1000.0

    return {
        "matched_count": int(len(matches["peak_times"])),
        "match_ratio": float(len(matches["peak_times"]) / max(len(local_times), 1)),
        "median_error_ms": float(np.median(absolute_errors_ms)) if len(absolute_errors_ms) else np.inf,
        "p95_error_ms": float(np.percentile(absolute_errors_ms, 95)) if len(absolute_errors_ms) else np.inf,
        "phase": float(phase),
    }


def score_grid_candidate(
    peak_times: np.ndarray,
    peak_strengths: np.ndarray,
    *,
    start_time: float,
    end_time: float,
    bpm: float,
    phase_steps: int,
    phase_tolerance: float,
    grid_match_tolerance: float,
) -> dict:
    """Score a fresh local grid after optimizing its phase."""
    mask = (peak_times >= start_time) & (peak_times < end_time)
    local_times = np.asarray(peak_times[mask], dtype=float)
    local_strengths = np.asarray(peak_strengths[mask], dtype=float)

    if len(local_times) == 0:
        return _score_grid_with_phase(
            peak_times,
            start_time=start_time,
            end_time=end_time,
            bpm=bpm,
            phase=np.nan,
            grid_match_tolerance=grid_match_tolerance,
        )

    period = 60.0 / float(bpm)
    phase_result = estimate_grid_phase(
        local_times,
        local_strengths,
        period,
        phase_steps=phase_steps,
        tolerance=phase_tolerance,
    )
    return _score_grid_with_phase(
        peak_times,
        start_time=start_time,
        end_time=end_time,
        bpm=bpm,
        phase=float(phase_result["phase"]),
        grid_match_tolerance=grid_match_tolerance,
    )


def score_continuation_grid(
    peak_times: np.ndarray,
    *,
    start_time: float,
    end_time: float,
    bpm: float,
    phase: float,
    grid_match_tolerance: float,
) -> dict:
    """Score continuation of an existing grid without refitting phase."""
    return _score_grid_with_phase(
        peak_times,
        start_time=start_time,
        end_time=end_time,
        bpm=bpm,
        phase=phase,
        grid_match_tolerance=grid_match_tolerance,
    )

def circular_phase_distance(
    phase_a: float,
    phase_b: float,
    period: float,
) -> float:
    """
    Smallest distance between two phases modulo one period.
    """
    return float(
        abs(
            (
                phase_b
                - phase_a
                + period / 2.0
            )
            % period
            - period / 2.0
        )
    )

def parabolic_peak_offset(values: np.ndarray, index: int) -> float:
    if index <= 0 or index >= len(values) - 1:
        return 0.0

    left = float(values[index - 1])
    center = float(values[index])
    right = float(values[index + 1])
    denominator = left - 2 * center + right

    if abs(denominator) < 1e-12:
        return 0.0

    return 0.5 * (left - right) / denominator


def detect_rising_onsets(
    data: np.ndarray,
    minimum_rise: float = 0.20,
    minimum_slope: float = 0.02,
    lookback: int = 30,
    minimum_distance: int = 20,
) -> tuple[list[int], dict[int, dict[str, float | int]]]:
    x = np.asarray(data, dtype=float)
    candidates: list[int] = []
    features: dict[int, dict[str, float | int]] = {}

    for peak in range(1, len(x) - 1):
        is_local_peak = x[peak] >= x[peak - 1] and x[peak] > x[peak + 1]
        if not is_local_peak:
            continue

        start = max(0, peak - lookback)
        if start >= peak:
            continue

        valley = start + int(np.argmin(x[start:peak]))
        rise = float(x[peak] - x[valley])
        rise_time = peak - valley
        slope = rise / max(rise_time, 1)

        if rise >= minimum_rise and slope >= minimum_slope:
            candidates.append(peak)
            features[peak] = {
                "valley": valley,
                "rise": rise,
                "rise_time": rise_time,
                "slope": float(slope),
            }

    selected: list[int] = []
    ranked = sorted(
        candidates,
        key=lambda p: (features[p]["rise"], features[p]["slope"]),
        reverse=True,
    )

    for peak in ranked:
        if all(abs(peak - chosen) >= minimum_distance for chosen in selected):
            selected.append(peak)

    return sorted(selected), features


def estimate_bpm_from_flux(
    spectral_flux: np.ndarray,
    sample_rate: int,
    hop_length: int,
    min_bpm: float,
    max_bpm: float,
) -> dict:
    if min_bpm <= 0 or max_bpm <= min_bpm:
        raise ValueError("Require 0 < min_bpm < max_bpm.")

    onset_signal = spectral_flux - np.mean(spectral_flux)
    autocorrelation = np.correlate(onset_signal, onset_signal, mode="full")
    autocorrelation = autocorrelation[len(autocorrelation) // 2:]

    frames_per_second = sample_rate / hop_length
    minimum_lag = max(1, int(np.floor(frames_per_second * 60 / max_bpm)))
    maximum_lag = min(
        len(autocorrelation) - 2,
        int(np.ceil(frames_per_second * 60 / min_bpm)),
    )

    if maximum_lag <= minimum_lag:
        raise ValueError("Audio is too short for the requested BPM range.")

    lags = np.arange(minimum_lag, maximum_lag + 1)
    search_region = autocorrelation[minimum_lag:maximum_lag + 1]
    best_local_index = int(np.argmax(search_region))
    best_integer_lag = int(lags[best_local_index])
    fractional_offset = parabolic_peak_offset(search_region, best_local_index)
    best_fractional_lag = best_integer_lag + fractional_offset

    period = best_fractional_lag / frames_per_second
    bpm = 60 / period

    return {
        "bpm": float(bpm),
        "period": float(period),
        "integer_lag": best_integer_lag,
        "fractional_lag": float(best_fractional_lag),
        "autocorrelation": autocorrelation,
        "lags": lags,
        "search_region": search_region,
    }



def estimate_grid_phase(
    peak_times: np.ndarray,
    peak_strengths: np.ndarray,
    period: float,
    phase_steps: int = 2000,
    tolerance: float = 0.040,
) -> dict:
    """
    Estimate grid phase from onset-derived candidates.

    Candidate phases come from onset timestamps modulo the period instead of
    blindly testing thousands of uniformly spaced phases in every window.
    ``phase_steps`` remains as a deterministic cap for compatibility.
    """
    peak_times = np.asarray(peak_times, dtype=float)
    peak_strengths = np.asarray(peak_strengths, dtype=float)

    if period <= 0 or not np.isfinite(period):
        raise ValueError("period must be finite and positive.")
    if tolerance <= 0:
        raise ValueError("tolerance must be positive.")
    if peak_times.shape != peak_strengths.shape:
        raise ValueError("peak_times and peak_strengths must have the same shape.")
    if len(peak_times) == 0:
        raise ValueError("At least one onset is required to estimate phase.")

    base_phases = np.mod(peak_times, period)
    offsets = np.asarray([0.0, -tolerance / 2.0, tolerance / 2.0])
    phases = np.mod((base_phases[:, None] + offsets).ravel(), period)
    phases = np.unique(np.round(phases, decimals=12))

    if phase_steps > 0 and len(phases) > phase_steps:
        selected = np.linspace(0, len(phases) - 1, phase_steps, dtype=int)
        phases = phases[selected]

    distances = np.abs(
        ((peak_times[:, None] - phases[None, :] + period / 2.0) % period)
        - period / 2.0
    )
    matches = distances <= tolerance
    timing_weights = np.clip(1.0 - distances / tolerance, 0.0, 1.0)
    phase_scores = np.sum(
        peak_strengths[:, None] * timing_weights * matches,
        axis=0,
    )

    best_index = int(np.argmax(phase_scores))
    return {
        "phase": float(phases[best_index]),
        "score": float(phase_scores[best_index]),
        "phases": phases,
        "phase_scores": phase_scores,
    }


def match_onsets_to_grid(
    peak_times: np.ndarray,
    predicted_beats: np.ndarray,
    tolerance: float,
) -> dict:
    """Greedily match grid lines and onsets one-to-one by smallest error."""
    peak_times = np.asarray(peak_times, dtype=float)
    predicted_beats = np.asarray(predicted_beats, dtype=float)

    if tolerance <= 0:
        raise ValueError("tolerance must be positive.")

    empty = {
        "beat_indices": np.asarray([], dtype=int),
        "grid_times": np.asarray([], dtype=float),
        "peak_times": np.asarray([], dtype=float),
        "errors": np.asarray([], dtype=float),
    }
    if len(peak_times) == 0 or len(predicted_beats) == 0:
        return empty

    candidates: list[tuple[float, int, int]] = []
    for beat_index, grid_time in enumerate(predicted_beats):
        distances = np.abs(peak_times - float(grid_time))
        for peak_index in np.flatnonzero(distances <= tolerance):
            candidates.append((float(distances[peak_index]), beat_index, int(peak_index)))

    candidates.sort()
    used_beats: set[int] = set()
    used_peaks: set[int] = set()
    rows: list[tuple[int, float, float, float]] = []

    for _, beat_index, peak_index in candidates:
        if beat_index in used_beats or peak_index in used_peaks:
            continue
        used_beats.add(beat_index)
        used_peaks.add(peak_index)
        grid_time = float(predicted_beats[beat_index])
        peak_time = float(peak_times[peak_index])
        rows.append((beat_index, grid_time, peak_time, peak_time - grid_time))

    rows.sort(key=lambda row: row[1])
    if not rows:
        return empty

    return {
        "beat_indices": np.asarray([row[0] for row in rows], dtype=int),
        "grid_times": np.asarray([row[1] for row in rows], dtype=float),
        "peak_times": np.asarray([row[2] for row in rows], dtype=float),
        "errors": np.asarray([row[3] for row in rows], dtype=float),
    }

def round_bpm(
    bpm: float,
    decimal_places: int | None,
) -> float | int:
    """Round BPM to a requested number of decimal places."""
    bpm = float(bpm)

    if not np.isfinite(bpm) or bpm <= 0:
        raise ValueError("BPM must be finite and positive.")

    if decimal_places is None:
        return bpm

    if (
        not isinstance(decimal_places, int)
        or isinstance(decimal_places, bool)
        or decimal_places < 0
    ):
        raise ValueError(
            "ROUND_INITIAL_BPM must be None or a nonnegative integer."
        )

    rounded = round(bpm, decimal_places)
    return int(rounded) if decimal_places == 0 else float(rounded)



def normalize_bpm_to_reference(
    bpm: float,
    reference_bpm: float,
    min_bpm: float,
    max_bpm: float,
) -> float:
    """
    Fold octave-related tempo estimates toward a reference BPM.

    Example: with reference 84 BPM, 168 BPM is normalized to 84 BPM.
    """
    candidates = []

    for octave_shift in range(-3, 4):
        candidate = bpm * (2.0 ** octave_shift)

        if min_bpm <= candidate <= max_bpm:
            candidates.append(candidate)

    if not candidates:
        return float(bpm)

    return float(
        min(
            candidates,
            key=lambda candidate: abs(
                np.log2(candidate / reference_bpm)
            ),
        )
    )


def estimate_local_tempo_windows(
    spectral_flux: np.ndarray,
    flux_times: np.ndarray,
    peak_times: np.ndarray,
    peak_strengths: np.ndarray,
    sample_rate: int,
    hop_length: int,
    *,
    duration: float,
    window_seconds: float,
    step_seconds: float,
    minimum_onsets: int,
    min_bpm: float,
    max_bpm: float,
    reference_bpm: float,
    bpm_decimal_places: int | None,
    phase_steps: int,
    phase_tolerance: float,
    minimum_matches: int = 3,
    minimum_match_ratio: float = 0.45,
    minimum_covered_beats: float = 1.5,
    maximum_p95_error_seconds: float = 0.030,
    maximum_reference_deviation: float = 0.25,
    minimum_confidence: float = 0.55,
) -> list[dict]:
    """
    Estimate local BPM candidates in overlapping windows.

    Each window receives an evidence score. Weak or implausible local
    estimates are retained for diagnostics but replaced by the global
    reference BPM for downstream segmentation.

    This prevents sparse windows from creating unsupported tempo aliases.
    """
    if window_seconds <= 0 or step_seconds <= 0:
        raise ValueError(
            "Local window and step sizes must be positive."
        )

    if reference_bpm <= 0:
        raise ValueError(
            "reference_bpm must be positive."
        )

    if minimum_matches < 1:
        raise ValueError(
            "minimum_matches must be at least 1."
        )

    reference_bpm = float(reference_bpm)
    reference_period = 60.0 / reference_bpm

    windows: list[dict] = []

    final_start = max(
        duration - window_seconds,
        0.0,
    )

    start_times = np.arange(
        0.0,
        final_start + step_seconds / 2,
        step_seconds,
    )

    for start_time in start_times:
        end_time = min(
            duration,
            start_time + window_seconds,
        )

        center_time = (
            start_time + end_time
        ) / 2.0

        frame_mask = (
            (flux_times >= start_time)
            & (flux_times < end_time)
        )

        peak_mask = (
            (peak_times >= start_time)
            & (peak_times < end_time)
        )

        local_flux = spectral_flux[frame_mask]
        local_peak_times = peak_times[peak_mask]
        local_peak_strengths = peak_strengths[peak_mask]

        onset_count = len(local_peak_times)

        # Keep weak windows explicitly instead of silently deleting them.
        if (
            len(local_flux) < 4
            or onset_count < minimum_onsets
        ):
            windows.append({
                "start_time": float(start_time),
                "end_time": float(end_time),
                "center_time": float(center_time),
                "raw_bpm": np.nan,
                "normalized_bpm": reference_bpm,
                "candidate_bpm": np.nan,
                "bpm": reference_bpm,
                "period": reference_period,
                "phase": np.nan,
                "phase_score": 0.0,
                "onset_count": int(onset_count),
                "matched_count": 0,
                "match_ratio": 0.0,
                "matched_span_seconds": 0.0,
                "covered_beats": 0.0,
                "median_error_ms": np.nan,
                "p95_error_ms": np.nan,
                "reference_deviation": np.nan,
                "confidence": 0.0,
                "accepted": False,
                "valid_for_segmentation": False,
                "used_fallback": True,
                "rejection_reason": (
                    "insufficient onsets"
                ),
            })
            continue

        try:
            bpm_result = estimate_bpm_from_flux(
                local_flux,
                sample_rate,
                hop_length,
                min_bpm,
                max_bpm,
            )

            raw_bpm = float(
                bpm_result["bpm"]
            )

            normalized_bpm = (
                normalize_bpm_to_reference(
                    raw_bpm,
                    reference_bpm,
                    min_bpm,
                    max_bpm,
                )
            )

            candidate_bpm = float(
                round_bpm(
                    normalized_bpm,
                    bpm_decimal_places,
                )
            )

            if candidate_bpm <= 0:
                raise ValueError(
                    "Local BPM must be positive."
                )

            candidate_period = (
                60.0 / candidate_bpm
            )

            phase_result = estimate_grid_phase(
                local_peak_times,
                local_peak_strengths,
                candidate_period,
                phase_steps=phase_steps,
                tolerance=phase_tolerance,
            )

            candidate_phase = float(
                phase_result["phase"]
            )

            # Distance of every local onset to the nearest beat line.
            signed_errors = (
                (
                    local_peak_times
                    - candidate_phase
                    + candidate_period / 2.0
                )
                % candidate_period
                - candidate_period / 2.0
            )

            absolute_errors = np.abs(
                signed_errors
            )

            matched_mask = (
                absolute_errors
                <= phase_tolerance
            )

            matched_times = (
                local_peak_times[matched_mask]
            )

            matched_errors = (
                absolute_errors[matched_mask]
            )

            matched_count = int(
                np.sum(matched_mask)
            )

            match_ratio = (
                matched_count
                / max(onset_count, 1)
            )

            if matched_count >= 2:
                matched_span_seconds = float(
                    matched_times[-1]
                    - matched_times[0]
                )
            else:
                matched_span_seconds = 0.0

            covered_beats = (
                matched_span_seconds
                / candidate_period
            )

            if matched_count:
                median_error_seconds = float(
                    np.median(matched_errors)
                )

                p95_error_seconds = float(
                    np.percentile(
                        matched_errors,
                        95,
                    )
                )
            else:
                median_error_seconds = np.inf
                p95_error_seconds = np.inf

            reference_deviation = abs(
                candidate_bpm
                - reference_bpm
            ) / reference_bpm

            # Individual confidence components lie in [0, 1].
            onset_score = np.clip(
                onset_count
                / max(minimum_onsets + 2, 1),
                0.0,
                1.0,
            )

            match_count_score = np.clip(
                matched_count
                / max(minimum_matches + 1, 1),
                0.0,
                1.0,
            )

            ratio_score = np.clip(
                (
                    match_ratio
                    - minimum_match_ratio
                )
                / max(
                    1.0 - minimum_match_ratio,
                    1e-12,
                ),
                0.0,
                1.0,
            )

            coverage_score = np.clip(
                covered_beats
                / max(
                    minimum_covered_beats * 2.0,
                    1e-12,
                ),
                0.0,
                1.0,
            )

            error_score = np.clip(
                1.0
                - (
                    p95_error_seconds
                    / maximum_p95_error_seconds
                ),
                0.0,
                1.0,
            )

            reference_score = np.clip(
                1.0
                - (
                    reference_deviation
                    / maximum_reference_deviation
                ),
                0.0,
                1.0,
            )

            confidence = float(
                0.15 * onset_score
                + 0.25 * match_count_score
                + 0.20 * ratio_score
                + 0.15 * coverage_score
                + 0.20 * error_score
                + 0.05 * reference_score
            )

            rejection_reasons = []

            if matched_count < minimum_matches:
                rejection_reasons.append(
                    "too few matches"
                )

            if match_ratio < minimum_match_ratio:
                rejection_reasons.append(
                    "low match ratio"
                )

            if covered_beats < minimum_covered_beats:
                rejection_reasons.append(
                    "insufficient beat coverage"
                )

            if (
                p95_error_seconds
                > maximum_p95_error_seconds
            ):
                rejection_reasons.append(
                    "large grid residual"
                )

            if (
                reference_deviation
                > maximum_reference_deviation
            ):
                rejection_reasons.append(
                    "implausible BPM deviation"
                )

            if confidence < minimum_confidence:
                rejection_reasons.append(
                    "low confidence"
                )

            accepted = not rejection_reasons

            if accepted:
                used_bpm = candidate_bpm
                used_period = candidate_period
                used_phase = candidate_phase
                used_phase_score = float(
                    phase_result["score"]
                )
            else:
                used_bpm = reference_bpm
                used_period = reference_period

                fallback_phase_result = (
                    estimate_grid_phase(
                        local_peak_times,
                        local_peak_strengths,
                        reference_period,
                        phase_steps=phase_steps,
                        tolerance=phase_tolerance,
                    )
                )

                used_phase = float(
                    fallback_phase_result["phase"]
                )

                used_phase_score = float(
                    fallback_phase_result["score"]
                )

            windows.append({
                "start_time": float(start_time),
                "end_time": float(end_time),
                "center_time": float(center_time),

                # Raw candidate information.
                "raw_bpm": raw_bpm,
                "normalized_bpm": float(
                    normalized_bpm
                ),
                "candidate_bpm": candidate_bpm,

                # BPM actually passed downstream.
                "bpm": float(used_bpm),
                "period": float(used_period),
                "phase": float(used_phase),
                "phase_score": used_phase_score,

                # Evidence.
                "onset_count": int(onset_count),
                "matched_count": matched_count,
                "match_ratio": float(match_ratio),
                "matched_span_seconds": float(
                    matched_span_seconds
                ),
                "covered_beats": float(
                    covered_beats
                ),
                "median_error_ms": float(
                    median_error_seconds
                    * 1000.0
                ),
                "p95_error_ms": float(
                    p95_error_seconds
                    * 1000.0
                ),
                "reference_deviation": float(
                    reference_deviation
                ),
                "confidence": confidence,

                # Decision.
                "accepted": bool(accepted),
                "valid_for_segmentation": bool(accepted),
                "used_fallback": bool(not accepted),
                "rejection_reason": (
                    ""
                    if accepted
                    else "; ".join(rejection_reasons)
                ),
            })

        except (
            ValueError,
            FloatingPointError,
            OverflowError,
        ) as error:
            windows.append({
                "start_time": float(start_time),
                "end_time": float(end_time),
                "center_time": float(center_time),
                "raw_bpm": np.nan,
                "normalized_bpm": reference_bpm,
                "candidate_bpm": np.nan,
                "bpm": reference_bpm,
                "period": reference_period,
                "phase": np.nan,
                "phase_score": 0.0,
                "onset_count": int(onset_count),
                "matched_count": 0,
                "match_ratio": 0.0,
                "matched_span_seconds": 0.0,
                "covered_beats": 0.0,
                "median_error_ms": np.nan,
                "p95_error_ms": np.nan,
                "reference_deviation": np.nan,
                "confidence": 0.0,
                "accepted": False,
                "valid_for_segmentation": False,
                "used_fallback": True,
                "rejection_reason": (
                    f"estimation failed: {error}"
                ),
            })

    return windows


def _piecewise_constant_window_segments(
    windows: list[dict],
    *,
    section_penalty: float,
    minimum_windows: int,
    bpm_noise_scale: float,
) -> list[tuple[int, int, float]]:
    """Return minimum-cost weighted constant-BPM segments over trusted windows."""
    n = len(windows)
    if n == 0:
        return []

    minimum_windows = max(1, int(minimum_windows))
    scale = max(float(bpm_noise_scale), 1e-6)

    bpm = np.asarray([float(item["candidate_bpm"]) for item in windows])
    weight = np.asarray([
        max(float(item.get("confidence", 0.0)), 1e-3)
        for item in windows
    ])

    cumulative_w = np.concatenate([[0.0], np.cumsum(weight)])
    cumulative_wx = np.concatenate([[0.0], np.cumsum(weight * bpm)])
    cumulative_wx2 = np.concatenate([[0.0], np.cumsum(weight * bpm * bpm)])

    def segment_fit(start: int, end: int) -> tuple[float, float]:
        total_w = cumulative_w[end] - cumulative_w[start]
        total_wx = cumulative_wx[end] - cumulative_wx[start]
        total_wx2 = cumulative_wx2[end] - cumulative_wx2[start]
        mean = total_wx / max(total_w, 1e-12)
        sse = max(total_wx2 - 2.0 * mean * total_wx + mean * mean * total_w, 0.0)
        return float(mean), float(sse / (scale * scale))

    best = np.full(n + 1, np.inf, dtype=float)
    previous = np.full(n + 1, -1, dtype=int)
    best[0] = -float(section_penalty)

    for end in range(1, n + 1):
        for start in range(0, end):
            length = end - start
            if length < minimum_windows and not (start == 0 and end == n):
                continue
            mean, fit_cost = segment_fit(start, end)
            candidate = best[start] + fit_cost + float(section_penalty)
            if candidate < best[end]:
                best[end] = candidate
                previous[end] = start

    if previous[n] < 0:
        mean, _ = segment_fit(0, n)
        return [(0, n, mean)]

    segments: list[tuple[int, int, float]] = []
    end = n
    while end > 0:
        start = int(previous[end])
        mean, _ = segment_fit(start, end)
        segments.append((start, end, mean))
        end = start

    return list(reversed(segments))


def _section_fit_objective(
    matches: dict,
    *,
    onset_count: int,
    grid_match_tolerance: float,
) -> float:
    """Robust fit cost including unmatched-onset evidence."""
    errors = np.abs(np.asarray(matches.get("errors", []), dtype=float))
    clipped = np.minimum(errors, grid_match_tolerance)
    unmatched_count = max(int(onset_count) - len(errors), 0)
    return float(
        np.sum(clipped * clipped)
        + unmatched_count * grid_match_tolerance * grid_match_tolerance
    )


def _evaluate_section_grid(
    peak_times: np.ndarray,
    peak_strengths: np.ndarray,
    *,
    start_time: float,
    end_time: float,
    bpm: float,
    phase_steps: int,
    phase_tolerance: float,
    grid_match_tolerance: float,
) -> dict:
    """Fit phase for a fixed BPM and return residual diagnostics."""
    mask = (peak_times >= start_time) & (peak_times < end_time)
    local_times = np.asarray(peak_times[mask], dtype=float)
    local_strengths = np.asarray(peak_strengths[mask], dtype=float)

    if len(local_times) == 0:
        return {
            "bpm": float(bpm),
            "phase": float(start_time),
            "matches": {
                "grid_times": np.asarray([], dtype=float),
                "peak_times": np.asarray([], dtype=float),
                "errors": np.asarray([], dtype=float),
            },
            "slope": np.nan,
            "objective": np.inf,
            "p95_ms": np.inf,
            "onset_count": 0,
        }

    period = 60.0 / float(bpm)
    phase_result = estimate_grid_phase(
        local_times,
        local_strengths,
        period,
        phase_steps=phase_steps,
        tolerance=phase_tolerance,
    )
    phase = float(phase_result["phase"])
    first_grid = phase + np.ceil((start_time - phase) / period) * period
    grid = np.arange(first_grid, end_time + period / 2.0, period)
    matches = match_onsets_to_grid(
        local_times,
        grid,
        tolerance=grid_match_tolerance,
    )

    errors = np.asarray(matches["errors"], dtype=float)
    if len(errors) >= 4 and np.ptp(matches["grid_times"]) > 0:
        slope = float(np.polyfit(matches["grid_times"], errors, deg=1)[0])
    else:
        slope = np.nan

    absolute_ms = np.abs(errors) * 1000.0
    p95_ms = (
        float(np.percentile(absolute_ms, 95))
        if len(absolute_ms)
        else np.inf
    )
    objective = _section_fit_objective(
        matches,
        onset_count=len(local_times),
        grid_match_tolerance=grid_match_tolerance,
    )

    return {
        "bpm": float(bpm),
        "phase": phase,
        "matches": matches,
        "slope": slope,
        "objective": objective,
        "p95_ms": p95_ms,
        "onset_count": int(len(local_times)),
    }


def _fit_section_grid_with_slope_correction(
    peak_times: np.ndarray,
    peak_strengths: np.ndarray,
    *,
    start_time: float,
    end_time: float,
    initial_bpm: float,
    bpm_decimal_places: int | None,
    phase_steps: int,
    phase_tolerance: float,
    grid_match_tolerance: float,
    maximum_iterations: int = 6,
    minimum_slope_seconds_per_second: float = 0.0005,
) -> dict:
    """
    Refine BPM from residual slope and keep the best-scoring iteration.

    The old implementation returned matches from before its final BPM update,
    which could leave a visibly sloped residual band despite reporting the
    corrected BPM. This version always reevaluates the final candidate and
    retains only objective improvements.
    """
    initial_bpm = float(initial_bpm)
    if not np.isfinite(initial_bpm) or initial_bpm <= 0:
        raise ValueError("initial_bpm must be finite and positive.")

    lower_bpm = initial_bpm * 0.85
    upper_bpm = initial_bpm * 1.15
    current_bpm = initial_bpm
    best: dict | None = None
    seen_bpms: set[float] = set()

    for _ in range(maximum_iterations):
        rounded_bpm = float(round_bpm(current_bpm, bpm_decimal_places))
        if rounded_bpm in seen_bpms:
            break
        seen_bpms.add(rounded_bpm)

        fitted = _evaluate_section_grid(
            peak_times,
            peak_strengths,
            start_time=start_time,
            end_time=end_time,
            bpm=rounded_bpm,
            phase_steps=phase_steps,
            phase_tolerance=phase_tolerance,
            grid_match_tolerance=grid_match_tolerance,
        )

        if best is None or fitted["objective"] < best["objective"]:
            best = fitted

        slope = fitted["slope"]
        if not np.isfinite(slope) or abs(slope) < minimum_slope_seconds_per_second:
            break

        period = 60.0 / rounded_bpm
        corrected_period = period * (1.0 + slope)
        if not np.isfinite(corrected_period) or corrected_period <= 0:
            break

        corrected_bpm = float(np.clip(
            60.0 / corrected_period,
            lower_bpm,
            upper_bpm,
        ))

        # Try the full correction and a damped correction. The latter is useful
        # when one-to-one rematching causes the residual slope to change abruptly.
        proposals = [
            corrected_bpm,
            0.5 * (rounded_bpm + corrected_bpm),
        ]
        proposal_fits = []
        for proposal in proposals:
            proposal_bpm = float(round_bpm(proposal, bpm_decimal_places))
            if proposal_bpm in seen_bpms:
                continue
            proposal_fit = _evaluate_section_grid(
                peak_times,
                peak_strengths,
                start_time=start_time,
                end_time=end_time,
                bpm=proposal_bpm,
                phase_steps=phase_steps,
                phase_tolerance=phase_tolerance,
                grid_match_tolerance=grid_match_tolerance,
            )
            proposal_fits.append(proposal_fit)
            if proposal_fit["objective"] < best["objective"]:
                best = proposal_fit

        if not proposal_fits:
            break
        next_fit = min(proposal_fits, key=lambda item: item["objective"])
        if next_fit["objective"] >= fitted["objective"] - 1e-12:
            break
        current_bpm = float(next_fit["bpm"])

    if best is None:
        best = _evaluate_section_grid(
            peak_times,
            peak_strengths,
            start_time=start_time,
            end_time=end_time,
            bpm=float(round_bpm(initial_bpm, bpm_decimal_places)),
            phase_steps=phase_steps,
            phase_tolerance=phase_tolerance,
            grid_match_tolerance=grid_match_tolerance,
        )

    return {
        "bpm": float(best["bpm"]),
        "phase": float(best["phase"]),
        "matches": best["matches"],
        "residual_slope_seconds_per_second": float(best["slope"]),
        "objective": float(best["objective"]),
        "p95_ms": float(best["p95_ms"]),
        "onset_count": int(best["onset_count"]),
    }


def _candidate_split_times(
    section: dict,
    *,
    minimum_edge_seconds: float,
) -> list[float]:
    """Candidate split positions from evidence windows and residual kinks."""
    start_time = float(section["start_time"])
    end_time = float(section["end_time"])
    candidates: set[float] = set()

    windows = section.get("windows", [])
    centers = sorted({float(item["center_time"]) for item in windows})
    for left, right in zip(centers[:-1], centers[1:]):
        candidate = (left + right) / 2.0
        if (
            candidate - start_time >= minimum_edge_seconds
            and end_time - candidate >= minimum_edge_seconds
        ):
            candidates.add(candidate)

    matched_times = np.asarray(section.get("matched_peak_times", []), dtype=float)
    residuals = np.asarray(section.get("residuals_ms", []), dtype=float)
    if len(matched_times) >= 12 and len(matched_times) == len(residuals):
        # Large changes in local residual slope indicate that one straight
        # correction cannot explain the whole interval.
        differences = np.diff(residuals)
        if len(differences) >= 3:
            ranked = np.argsort(np.abs(differences))[::-1][:4]
            for index in ranked:
                candidate = float((matched_times[index] + matched_times[index + 1]) / 2.0)
                if (
                    candidate - start_time >= minimum_edge_seconds
                    and end_time - candidate >= minimum_edge_seconds
                ):
                    candidates.add(candidate)

    return sorted(candidates)



def segment_local_tempo_windows(
    local_windows: list[dict],
    peak_times: np.ndarray,
    peak_strengths: np.ndarray,
    *,
    duration: float,
    reference_bpm: float,
    reference_phase: float,
    bpm_change_threshold: float,
    phase_change_threshold: float,
    minimum_windows_per_section: int,
    bpm_decimal_places: int | None,
    phase_steps: int,
    phase_tolerance: float,
    grid_match_tolerance: float,
    maximum_window_gap_seconds: float | None = None,
    interpolation_gap_seconds: float = 6.0,
    section_penalty: float = 12.0,
    bpm_noise_scale: float = 1.0,
) -> list[dict]:
    """
    Build a piecewise timing model from trusted local windows.

    Rejected windows are treated as missing observations, not evidence that the
    song returned to the global BPM. Short gaps are bridged by the surrounding
    local trajectory; the global grid is used only outside supported blocks or
    across genuinely long unsupported gaps.
    """
    if duration <= 0:
        raise ValueError("duration must be positive.")
    if reference_bpm <= 0 or not np.isfinite(reference_bpm):
        raise ValueError("reference_bpm must be finite and positive.")
    if not np.isfinite(reference_phase):
        raise ValueError("reference_phase must be finite.")

    reference_bpm = float(round_bpm(reference_bpm, bpm_decimal_places))
    reference_period = 60.0 / reference_bpm

    valid_windows = sorted(
        [
            window
            for window in local_windows
            if window.get("valid_for_segmentation", window.get("accepted", False))
            and np.isfinite(float(window.get("candidate_bpm", np.nan)))
            and np.isfinite(float(window.get("phase", np.nan)))
        ],
        key=lambda item: float(item["center_time"]),
    )

    if not valid_windows:
        supported_blocks: list[list[dict]] = []
    else:
        supported_blocks = [[valid_windows[0]]]
        for window in valid_windows[1:]:
            gap = float(window["center_time"]) - float(supported_blocks[-1][-1]["center_time"])
            if gap <= interpolation_gap_seconds:
                supported_blocks[-1].append(window)
            else:
                supported_blocks.append([window])

    local_sections: list[dict] = []
    minimum_windows = max(2, int(minimum_windows_per_section))

    for block in supported_blocks:
        if len(block) < minimum_windows:
            continue

        segments = _piecewise_constant_window_segments(
            block,
            section_penalty=section_penalty,
            minimum_windows=minimum_windows,
            bpm_noise_scale=bpm_noise_scale,
        )

        for segment_index, (start_index, end_index, mean_bpm) in enumerate(segments):
            segment_windows = block[start_index:end_index]

            if segment_index == 0:
                start_time = max(0.0, float(segment_windows[0]["start_time"]))
            else:
                left = float(block[start_index - 1]["center_time"])
                right = float(block[start_index]["center_time"])
                start_time = (left + right) / 2.0

            if segment_index == len(segments) - 1:
                end_time = min(duration, float(segment_windows[-1]["end_time"]))
            else:
                next_start = segments[segment_index + 1][0]
                left = float(block[next_start - 1]["center_time"])
                right = float(block[next_start]["center_time"])
                end_time = (left + right) / 2.0

            confidences = np.asarray([
                max(float(item.get("confidence", 0.0)), 1e-3)
                for item in segment_windows
            ])
            candidate_bpms = np.asarray([
                float(item["candidate_bpm"])
                for item in segment_windows
            ])
            weighted_bpm = float(np.average(candidate_bpms, weights=confidences))

            fitted = _fit_section_grid_with_slope_correction(
                peak_times,
                peak_strengths,
                start_time=start_time,
                end_time=end_time,
                initial_bpm=weighted_bpm,
                bpm_decimal_places=bpm_decimal_places,
                phase_steps=phase_steps,
                phase_tolerance=phase_tolerance,
                grid_match_tolerance=grid_match_tolerance,
            )

            local_sections.append({
                "start_time": start_time,
                "end_time": end_time,
                "bpm": fitted["bpm"],
                "phase": fitted["phase"],
                "source": "trusted trajectory",
                "windows": list(segment_windows),
                "confidence": float(np.average(confidences)),
                "residual_slope_seconds_per_second": fitted[
                    "residual_slope_seconds_per_second"
                ],
            })

    local_sections.sort(key=lambda item: float(item["start_time"]))

    # Join adjacent local sections across short unsupported gaps by placing the
    # boundary halfway between their evidence supports. Long gaps remain global.
    for index in range(len(local_sections) - 1):
        left = local_sections[index]
        right = local_sections[index + 1]
        gap = float(right["start_time"]) - float(left["end_time"])
        if 0 < gap <= interpolation_gap_seconds:
            boundary = (float(left["end_time"]) + float(right["start_time"])) / 2.0
            left["end_time"] = boundary
            right["start_time"] = boundary

    raw_sections: list[dict] = []
    cursor = 0.0
    for local in local_sections:
        if float(local["start_time"]) > cursor:
            raw_sections.append({
                "start_time": cursor,
                "end_time": float(local["start_time"]),
                "bpm": reference_bpm,
                "phase": reference_phase,
                "source": "global fallback",
                "windows": [],
                "confidence": 0.0,
                "residual_slope_seconds_per_second": np.nan,
            })
        raw_sections.append(local)
        cursor = max(cursor, float(local["end_time"]))

    if cursor < duration:
        raw_sections.append({
            "start_time": cursor,
            "end_time": float(duration),
            "bpm": reference_bpm,
            "phase": reference_phase,
            "source": "global fallback",
            "windows": [],
            "confidence": 0.0,
            "residual_slope_seconds_per_second": np.nan,
        })

    if not raw_sections:
        raw_sections = [{
            "start_time": 0.0,
            "end_time": float(duration),
            "bpm": reference_bpm,
            "phase": reference_phase,
            "source": "global fallback",
            "windows": [],
            "confidence": 0.0,
            "residual_slope_seconds_per_second": np.nan,
        }]

    # Merge only genuinely equivalent neighbors. A short absence of confidence
    # no longer creates a ceremonial return to the global BPM.
    merged: list[dict] = []
    for section in raw_sections:
        if merged:
            previous = merged[-1]
            same_source = previous["source"] == section["source"]
            bpm_close = abs(float(previous["bpm"]) - float(section["bpm"])) <= bpm_change_threshold
            phase_close = abs(grid_offset_at_time(
                local_phase=float(previous["phase"]),
                local_period=60.0 / float(previous["bpm"]),
                reference_phase=float(section["phase"]),
                reference_period=60.0 / float(section["bpm"]),
                comparison_time=float(section["start_time"]),
            )) <= phase_change_threshold
            if same_source and bpm_close and phase_close:
                previous["end_time"] = float(section["end_time"])
                previous["windows"].extend(section["windows"])
                previous["confidence"] = max(float(previous["confidence"]), float(section["confidence"]))
                continue
        merged.append({**section, "windows": list(section["windows"])})

    merged = _refine_and_optionally_split_sections(
        merged,
        peak_times,
        peak_strengths,
        bpm_decimal_places=bpm_decimal_places,
        phase_steps=phase_steps,
        phase_tolerance=phase_tolerance,
        grid_match_tolerance=grid_match_tolerance,
    )

    sections: list[dict] = []
    for raw in merged:
        start_time = float(raw["start_time"])
        end_time = float(raw["end_time"])
        bpm = float(round_bpm(float(raw["bpm"]), bpm_decimal_places))
        period = 60.0 / bpm
        phase = float(raw["phase"])

        mask = (peak_times >= start_time) & (peak_times < end_time)
        section_peak_times = np.asarray(peak_times[mask], dtype=float)
        first_grid = phase + np.ceil((start_time - phase) / period) * period
        predicted_grid = np.arange(first_grid, end_time + period / 2.0, period)
        matches = match_onsets_to_grid(
            section_peak_times,
            predicted_grid,
            tolerance=grid_match_tolerance,
        )
        residuals_ms = np.asarray(matches["errors"], dtype=float) * 1000.0
        absolute = np.abs(residuals_ms)

        slope = raw.get("residual_slope_seconds_per_second", np.nan)
        sections.append({
            "section_number": len(sections) + 1,
            "start_time": start_time,
            "end_time": end_time,
            "raw_bpm": bpm,
            "bpm": bpm,
            "period": period,
            "phase": phase,
            "offset": phase,
            "phase_score": 0.0,
            "source": raw["source"],
            "confidence": float(raw["confidence"]),
            "window_count": len(raw["windows"]),
            "matched_count": int(len(matches["peak_times"])),
            "matched_peak_times": matches["peak_times"],
            "predicted_times": matches["grid_times"],
            "residuals_ms": residuals_ms,
            "residual_slope_ms_per_second": (
                float(slope) * 1000.0 if np.isfinite(slope) else np.nan
            ),
            "fit_objective": float(raw.get("fit_objective", np.nan)),
            "fit_p95_ms": float(raw.get("fit_p95_ms", np.nan)),
            "quantile_error_ms": float(np.quantile(absolute, 0.95)) if len(absolute) else np.nan,
            "max_error_ms": float(np.max(absolute)) if len(absolute) else np.nan,
        })

    return sections

def fit_linear_grid(
    beat_indices: np.ndarray,
    beat_times: np.ndarray,
    start: int,
    end: int,
    integer_bpm: bool = False,
) -> tuple[float, float, np.ndarray]:
    indices = beat_indices[start:end + 1].astype(float)
    times = beat_times[start:end + 1].astype(float)

    if len(indices) < 2 or np.ptp(indices) == 0:
        raise ValueError("At least two distinct beat indices are required.")

    design = np.column_stack([np.ones_like(indices), indices])
    offset, period = np.linalg.lstsq(design, times, rcond=None)[0]

    if not np.isfinite(period) or period <= 0:
        raise ValueError("The fitted period is invalid.")

    if integer_bpm:
        fitted_bpm = 60 / period
        if not np.isfinite(fitted_bpm) or fitted_bpm <= 0:
            raise ValueError("The fitted BPM is invalid.")

        rounded_bpm = int(round(fitted_bpm))
        if rounded_bpm <= 0:
            raise ValueError("Rounded BPM must be positive.")

        period = 60 / rounded_bpm
        offset = float(np.mean(times - period * indices))

    predicted = offset + period * indices
    residuals = times - predicted
    return float(offset), float(period), residuals


def minimum_timing_sections(
    beat_indices: np.ndarray,
    beat_times: np.ndarray,
    tolerance_seconds: float = 0.010,
    minimum_section_points: int = 4,
    error_quantile: float = 0.95,
    integer_bpm: bool = False,
) -> list[dict]:
    beat_indices = np.asarray(beat_indices, dtype=int)
    beat_times = np.asarray(beat_times, dtype=float)

    if beat_indices.shape != beat_times.shape:
        raise ValueError("beat_indices and beat_times must have the same shape.")
    if not 0 < error_quantile <= 1:
        raise ValueError("error_quantile must be between 0 and 1.")
    if minimum_section_points < 2:
        raise ValueError("minimum_section_points must be at least 2.")

    n = len(beat_times)
    if n == 0:
        return []
    if n < minimum_section_points:
        raise ValueError("Not enough matched beats for one timing section.")

    infinity = n + 1
    best_count = np.full(n + 1, infinity, dtype=int)
    best_error = np.full(n + 1, np.inf, dtype=float)
    previous = np.full(n + 1, -1, dtype=int)
    best_count[0] = 0
    best_error[0] = 0.0
    segment_cache: dict[tuple[int, int], dict[str, float]] = {}

    for end_exclusive in range(1, n + 1):
        for start in range(end_exclusive):
            length = end_exclusive - start
            if length < minimum_section_points:
                continue
            if best_count[start] == infinity:
                continue

            try:
                offset, period, residuals = fit_linear_grid(
                    beat_indices,
                    beat_times,
                    start,
                    end_exclusive - 1,
                    integer_bpm=integer_bpm,
                )
            except ValueError:
                continue

            if not np.all(np.isfinite(residuals)):
                continue

            absolute_errors = np.abs(residuals)
            representative_error = float(np.quantile(absolute_errors, error_quantile))
            max_error = float(np.max(absolute_errors))

            if not np.isfinite(representative_error) or not np.isfinite(max_error):
                continue

            segment_cache[(start, end_exclusive)] = {
                "offset": offset,
                "period": period,
                "representative_error": representative_error,
                "max_error": max_error,
            }

            if representative_error > tolerance_seconds:
                continue

            candidate_count = best_count[start] + 1
            segment_error = float(np.sum(residuals ** 2))
            candidate_error = best_error[start] + segment_error

            better_count = candidate_count < best_count[end_exclusive]
            same_count_better_fit = (
                candidate_count == best_count[end_exclusive]
                and candidate_error < best_error[end_exclusive]
            )

            if better_count or same_count_better_fit:
                best_count[end_exclusive] = candidate_count
                best_error[end_exclusive] = candidate_error
                previous[end_exclusive] = start

    if previous[n] == -1:
        raise ValueError(
            "No valid segmentation found. Increase SECTION_TOLERANCE_MS, "
            "reduce MINIMUM_SECTION_POINTS, or improve onset matching."
        )

    sections: list[dict] = []
    end_exclusive = n

    while end_exclusive > 0:
        start = int(previous[end_exclusive])
        if start < 0:
            raise RuntimeError("Segmentation backtracking failed.")

        cached = segment_cache[(start, end_exclusive)]
        offset = cached["offset"]
        period = cached["period"]
        representative_error = cached["representative_error"]
        max_error = cached["max_error"]

        if representative_error > tolerance_seconds + 1e-12:
            raise RuntimeError("Internal error: returned section exceeds tolerance.")

        bpm = 60 / period
        bpm_value = int(round(bpm)) if integer_bpm else float(bpm)

        sections.append({
            "start_observation": start,
            "end_observation": end_exclusive - 1,
            "start_beat_index": int(beat_indices[start]),
            "end_beat_index": int(beat_indices[end_exclusive - 1]),
            "start_time": float(beat_times[start]),
            "end_time": float(beat_times[end_exclusive - 1]),
            "offset": float(offset),
            "period": float(period),
            "bpm": bpm_value,
            "quantile_error_ms": representative_error * 1000,
            "max_error_ms": max_error * 1000,
            "number_of_points": end_exclusive - start,
        })

        end_exclusive = start

    return list(reversed(sections))

def analyze_song(
    audio_path: Path | str,
    *,
    n_fft: int = N_FFT,
    hop_length: int = HOP_LENGTH,
    min_bpm: float = MIN_BPM,
    max_bpm: float = MAX_BPM,
    round_initial_bpm: int | None = ROUND_INITIAL_BPM,
    use_local_tempo: bool = USE_LOCAL_TEMPO,
    integer_section_bpm: bool = USE_INTEGER_BPM,
    section_tolerance_ms: float = SECTION_TOLERANCE_MS,
) -> dict:
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path.resolve()}")

    audio, sample_rate = librosa.load(
        audio_path,
        sr=None,
        mono=True,
        dtype=np.float32,
    )
    duration = len(audio) / sample_rate

    stft = librosa.stft(
        audio,
        n_fft=n_fft,
        hop_length=hop_length,
        dtype=np.complex64,
    )

    magnitude = np.abs(stft).astype(
        np.float32,
        copy=False,
    )
    del stft

    spectral_flux = np.zeros(
        magnitude.shape[1],
        dtype=np.float32,
    )

    frequency_chunk_size = 128

    for frequency_start in range(
        0,
        magnitude.shape[0],
        frequency_chunk_size,
    ):
        frequency_end = min(
            magnitude.shape[0],
            frequency_start + frequency_chunk_size,
        )

        chunk = magnitude[
            frequency_start:frequency_end
        ]

        differences = np.diff(
            chunk,
            axis=1,
        )

        np.maximum(
            differences,
            0,
            out=differences,
        )

        spectral_flux[1:] += np.sum(
            differences,
            axis=0,
            dtype=np.float32,
        )

    del magnitude

    spectral_flux /= (
        float(np.max(spectral_flux))
        + 1e-12
    )

    flux_times = librosa.frames_to_time(
        np.arange(len(spectral_flux)),
        sr=sample_rate,
        hop_length=hop_length,
    )

    rise_peaks, rise_features = detect_rising_onsets(
        spectral_flux,
        minimum_rise=MINIMUM_RISE,
        minimum_slope=MINIMUM_SLOPE,
        lookback=LOOKBACK_FRAMES,
        minimum_distance=MINIMUM_PEAK_DISTANCE_FRAMES,
    )

    if not rise_peaks:
        raise ValueError("No rising onsets were detected. Relax the onset settings.")

    peak_times = flux_times[rise_peaks]
    peak_strengths = spectral_flux[rise_peaks]

    bpm_result = estimate_bpm_from_flux(
        spectral_flux,
        sample_rate,
        hop_length,
        min_bpm,
        max_bpm,
    )
    initial_bpm = round_bpm(bpm_result["bpm"], round_initial_bpm)
    initial_period = 60.0 / float(initial_bpm)

    phase_result = estimate_grid_phase(
        peak_times,
        peak_strengths,
        initial_period,
        phase_steps=PHASE_STEPS,
        tolerance=PHASE_MATCH_TOLERANCE_SECONDS,
    )

    predicted_beats = np.arange(
        phase_result["phase"],
        duration + initial_period,
        initial_period,
    )
    global_matches = match_onsets_to_grid(
        peak_times,
        predicted_beats,
        tolerance=GRID_MATCH_TOLERANCE_SECONDS,
    )

    local_windows: list[dict] = []

    if use_local_tempo:
        local_windows = estimate_local_tempo_windows(
            spectral_flux,
            flux_times,
            peak_times,
            peak_strengths,
            sample_rate,
            hop_length,
            duration=duration,
            window_seconds=LOCAL_WINDOW_SECONDS,
            step_seconds=LOCAL_STEP_SECONDS,
            minimum_onsets=LOCAL_MINIMUM_ONSETS,
            min_bpm=min_bpm,
            max_bpm=max_bpm,
            reference_bpm=float(initial_bpm),
            bpm_decimal_places=round_initial_bpm,
            phase_steps=PHASE_STEPS,
            phase_tolerance=LOCAL_PHASE_MATCH_TOLERANCE_SECONDS,
            minimum_matches=LOCAL_MINIMUM_MATCHES,
            minimum_match_ratio=LOCAL_MINIMUM_MATCH_RATIO,
            minimum_covered_beats=LOCAL_MINIMUM_COVERED_BEATS,
            maximum_p95_error_seconds=(
                LOCAL_MAXIMUM_P95_ERROR_SECONDS
            ),
            maximum_reference_deviation=(
                LOCAL_MAXIMUM_REFERENCE_DEVIATION
            ),
            minimum_confidence=LOCAL_MINIMUM_CONFIDENCE,
        )

        for window in local_windows:
            if not window.get("valid_for_segmentation", False):
                window["reference_grid_offset"] = np.nan
                continue
            window["reference_grid_offset"] = grid_offset_at_time(
                local_phase=float(window["phase"]),
                local_period=float(window["period"]),
                reference_phase=float(phase_result["phase"]),
                reference_period=float(initial_period),
                comparison_time=float(window["center_time"]),
            )

        sections = segment_local_tempo_windows(
            local_windows,
            peak_times,
            peak_strengths,
            duration=duration,
            reference_bpm=float(initial_bpm),
            reference_phase=float(phase_result["phase"]),
            bpm_change_threshold=LOCAL_BPM_CHANGE_THRESHOLD,
            phase_change_threshold=LOCAL_PHASE_CHANGE_THRESHOLD_SECONDS,
            minimum_windows_per_section=LOCAL_MINIMUM_WINDOWS_PER_SECTION,
            bpm_decimal_places=round_initial_bpm,
            phase_steps=PHASE_STEPS,
            phase_tolerance=LOCAL_PHASE_MATCH_TOLERANCE_SECONDS,
            grid_match_tolerance=GRID_MATCH_TOLERANCE_SECONDS,
            maximum_window_gap_seconds=0.60,
        )
    else:
        sections = minimum_timing_sections(
            beat_indices=global_matches["beat_indices"],
            beat_times=global_matches["peak_times"],
            tolerance_seconds=section_tolerance_ms / 1000,
            minimum_section_points=MINIMUM_SECTION_POINTS,
            error_quantile=ERROR_QUANTILE,
            integer_bpm=integer_section_bpm,
        )



    dense_offset_windows = estimate_dense_grid_offset_windows(
        flux_times,
        spectral_flux,
        duration=duration,
        bpm=float(initial_bpm),
        phase=float(phase_result["phase"]),
    )
    dense_offset_summary = summarize_dense_offset_trajectory(
        dense_offset_windows
    )

    dense_offset_segments = segment_dense_offset_trajectory(
        dense_offset_windows
    )

    apply_dense_segment_metadata(
        dense_offset_windows,
        dense_offset_segments,
    )

    return {
        "engine_version": __version__,
        "dense_offset_method": DENSE_OFFSET_METHOD,
        "audio_path": audio_path,
        "audio": audio,
        "sample_rate": sample_rate,
        "duration": duration,
        "n_fft": n_fft,
        "hop_length": hop_length,
        "spectral_flux": spectral_flux,
        "flux_times": flux_times,
        "rise_peaks": np.asarray(rise_peaks, dtype=int),
        "rise_features": rise_features,
        "peak_times": peak_times,
        "peak_strengths": peak_strengths,
        "bpm_result": bpm_result,
        "initial_bpm": initial_bpm,
        "initial_period": initial_period,
        "phase_result": phase_result,
        "predicted_beats": predicted_beats,
        "matches": global_matches,
        "dense_offset_windows": dense_offset_windows,
        "dense_offset_summary": dense_offset_summary,
        "dense_offset_segments": dense_offset_segments,
        "local_windows": local_windows,
        "sections": sections,
        "use_local_tempo": use_local_tempo,
        "section_tolerance_ms": section_tolerance_ms,
    }


def print_analysis_summary(result: dict) -> None:
    matches = result["matches"]
    errors_ms = np.abs(matches["errors"]) * 1000

    print(f"File: {result['audio_path']}")
    print(f"Duration: {result['duration']:.3f} s")
    print(f"Sample rate: {result['sample_rate']} Hz")
    print(f"Hop length: {result['hop_length']} samples")
    print(f"Raw global BPM: {result['bpm_result']['bpm']:.6f}")
    print(f"Used global BPM: {result['initial_bpm']}")
    print(f"Analysis mode: {'local tempo' if result['use_local_tempo'] else 'global grid'}")
    print(f"Detected onset candidates: {len(result['peak_times'])}")
    print(f"Global-grid matched observations: {len(matches['peak_times'])}")

    if len(errors_ms):
        print(f"Global-grid median error: {np.median(errors_ms):.3f} ms")
        print(f"Global-grid 95% error: {np.percentile(errors_ms, 95):.3f} ms")

    dense_summary = result.get("dense_offset_summary")
    if dense_summary is not None:
        print(
            "Dense offset windows: "
            f"{dense_summary['valid_windows']}"
        )
        print(
            "Dense offset p95 absolute: "
            f"{dense_summary['p95_absolute_offset_ms']:.3f} ms"
        )
        print(
            "Dense offset global slope: "
            f"{dense_summary['slope_ms_per_second']:+.4f} ms/s"
        )

        dense_segments = result.get(
            "dense_offset_segments",
            [],
        )
        accepted_dense_segments = sum(
            bool(segment.get("accepted", False))
            for segment in dense_segments
        )
        print(
            "Dense local segments: "
            f"{accepted_dense_segments} accepted / "
            f"{len(dense_segments)} total"
        )

    if result["use_local_tempo"]:
        trusted_count = sum(
            bool(window.get("valid_for_segmentation", False))
            for window in result["local_windows"]
        )
        print(f"Local windows: {len(result['local_windows'])}")
        print(f"Trusted local windows: {trusted_count}")

    print(f"Timing sections: {len(result['sections'])}")
    for i, section in enumerate(result["sections"], start=1):
        if result["use_local_tempo"]:
            print(
                f"  {i}: {section['start_time']:.3f}s–{section['end_time']:.3f}s, "
                f"BPM={section['bpm']}, windows={section['window_count']}, "
                f"matches={section['matched_count']}, "
                f"95%={section['quantile_error_ms']:.3f}ms"
            )
        else:
            print(
                f"  {i}: beats {section['start_beat_index']}–{section['end_beat_index']}, "
                f"BPM={section['bpm']}, offset={section['offset']:.6f}s, "
                f"95%={section['quantile_error_ms']:.3f}ms"
            )


def tolerance_sweep(
    result: dict,
    start_ms: int = 15,
    stop_ms: int = 1,
) -> list[dict]:
    rows: list[dict] = []
    matches = result["matches"]

    for tolerance_ms in range(start_ms, stop_ms - 1, -1):
        try:
            sections = minimum_timing_sections(
                beat_indices=matches["beat_indices"],
                beat_times=matches["peak_times"],
                tolerance_seconds=tolerance_ms / 1000,
                minimum_section_points=MINIMUM_SECTION_POINTS,
                error_quantile=ERROR_QUANTILE,
                integer_bpm=USE_INTEGER_BPM,
            )
            rows.append({
                "tolerance_ms": tolerance_ms,
                "section_count": len(sections),
                "status": "ok",
            })
            print(f"Tolerance {tolerance_ms:2d} ms: {len(sections)} section(s)")
        except ValueError:
            rows.append({
                "tolerance_ms": tolerance_ms,
                "section_count": None,
                "status": "no valid segmentation",
            })
            print(f"Tolerance {tolerance_ms:2d} ms: no valid segmentation")
            break

    return rows







# ---------------------------------------------------------------------------
# v0.8 overrides: recursive residual-shape refinement and fast visualization.
# These definitions intentionally appear late in the module so existing public
# APIs remain compatible while the improved implementations take precedence.
# ---------------------------------------------------------------------------

def _linear_residual_sse(
    times: np.ndarray,
    residuals_seconds: np.ndarray,
) -> float:
    times = np.asarray(times, dtype=float)
    residuals_seconds = np.asarray(residuals_seconds, dtype=float)

    if len(times) < 2 or len(times) != len(residuals_seconds):
        return np.inf

    centered = times - float(np.mean(times))
    design = np.column_stack([
        np.ones_like(centered),
        centered,
    ])
    fitted = design @ np.linalg.lstsq(
        design,
        residuals_seconds,
        rcond=None,
    )[0]
    errors = residuals_seconds - fitted
    return float(np.sum(errors * errors))


def _residual_shape_split_candidates_v08(
    section: dict,
    *,
    minimum_edge_seconds: float,
    minimum_points_per_side: int = 5,
    maximum_candidates: int = 10,
) -> list[float]:
    """
    Rank candidate split positions by how much two residual lines improve over
    one residual line. Window-transition positions are included as additional
    candidates, but noisy single-point jumps no longer dominate the ranking.
    """
    start_time = float(section["start_time"])
    end_time = float(section["end_time"])
    matched_times = np.asarray(
        section.get("matched_peak_times", []),
        dtype=float,
    )
    residuals_seconds = (
        np.asarray(
            section.get("residuals_ms", []),
            dtype=float,
        )
        / 1000.0
    )

    ranked: list[tuple[float, float]] = []

    if (
        len(matched_times) == len(residuals_seconds)
        and len(matched_times) >= 2 * minimum_points_per_side
    ):
        baseline = _linear_residual_sse(
            matched_times,
            residuals_seconds,
        )

        for split_index in range(
            minimum_points_per_side,
            len(matched_times) - minimum_points_per_side + 1,
        ):
            split_time = float(
                0.5
                * (
                    matched_times[split_index - 1]
                    + matched_times[split_index]
                )
            )

            if (
                split_time - start_time < minimum_edge_seconds
                or end_time - split_time < minimum_edge_seconds
            ):
                continue

            left_sse = _linear_residual_sse(
                matched_times[:split_index],
                residuals_seconds[:split_index],
            )
            right_sse = _linear_residual_sse(
                matched_times[split_index:],
                residuals_seconds[split_index:],
            )
            improvement = baseline - left_sse - right_sse

            if np.isfinite(improvement) and improvement > 0:
                ranked.append((
                    float(improvement),
                    split_time,
                ))

    ranked.sort(reverse=True)
    candidates = [
        split_time
        for _, split_time in ranked[:maximum_candidates]
    ]

    # Trusted-window transitions are useful when audio residuals are sparse.
    centers = sorted({
        float(item["center_time"])
        for item in section.get("windows", [])
        if np.isfinite(float(item.get("center_time", np.nan)))
    })

    for left, right in zip(centers[:-1], centers[1:]):
        split_time = 0.5 * (left + right)
        if (
            split_time - start_time >= minimum_edge_seconds
            and end_time - split_time >= minimum_edge_seconds
        ):
            candidates.append(float(split_time))

    return sorted(set(candidates))


def _weighted_window_bpm_v08(
    windows: list[dict],
    *,
    fallback_bpm: float,
    bpm_decimal_places: int | None,
) -> float:
    valid = [
        item
        for item in windows
        if np.isfinite(float(item.get("candidate_bpm", np.nan)))
    ]

    if not valid:
        return float(
            round_bpm(
                fallback_bpm,
                bpm_decimal_places,
            )
        )

    bpms = np.asarray([
        float(item["candidate_bpm"])
        for item in valid
    ])
    weights = np.asarray([
        max(float(item.get("confidence", 0.0)), 1e-6)
        for item in valid
    ])

    return float(
        round_bpm(
            np.average(bpms, weights=weights),
            bpm_decimal_places,
        )
    )


def _refine_section_recursive_v08(
    raw: dict,
    peak_times: np.ndarray,
    peak_strengths: np.ndarray,
    *,
    bpm_decimal_places: int | None,
    phase_steps: int,
    phase_tolerance: float,
    grid_match_tolerance: float,
    depth: int,
    maximum_depth: int,
    minimum_split_duration_seconds: float,
    minimum_split_improvement_ratio: float,
    split_penalty: float,
) -> list[dict]:
    start_time = float(raw["start_time"])
    end_time = float(raw["end_time"])

    base = _fit_section_grid_with_slope_correction(
        peak_times,
        peak_strengths,
        start_time=start_time,
        end_time=end_time,
        initial_bpm=float(raw["bpm"]),
        bpm_decimal_places=bpm_decimal_places,
        phase_steps=phase_steps,
        phase_tolerance=phase_tolerance,
        grid_match_tolerance=grid_match_tolerance,
    )

    base_section = {
        **raw,
        "bpm": base["bpm"],
        "phase": base["phase"],
        "matched_peak_times": np.asarray(
            base["matches"]["peak_times"],
            dtype=float,
        ),
        "residuals_ms": (
            np.asarray(
                base["matches"]["errors"],
                dtype=float,
            )
            * 1000.0
        ),
        "residual_slope_seconds_per_second": base[
            "residual_slope_seconds_per_second"
        ],
        "fit_objective": base["objective"],
        "fit_p95_ms": base["p95_ms"],
        "refinement_depth": depth,
    }

    duration = end_time - start_time
    if (
        depth >= maximum_depth
        or duration < 2.0 * minimum_split_duration_seconds
        or base["onset_count"] < 10
        or not np.isfinite(base["objective"])
    ):
        return [base_section]

    best_split = None

    for split_time in _residual_shape_split_candidates_v08(
        base_section,
        minimum_edge_seconds=minimum_split_duration_seconds,
    ):
        left_windows = [
            item
            for item in raw.get("windows", [])
            if float(item["center_time"]) < split_time
        ]
        right_windows = [
            item
            for item in raw.get("windows", [])
            if float(item["center_time"]) >= split_time
        ]

        left_initial_bpm = _weighted_window_bpm_v08(
            left_windows,
            fallback_bpm=base["bpm"],
            bpm_decimal_places=bpm_decimal_places,
        )
        right_initial_bpm = _weighted_window_bpm_v08(
            right_windows,
            fallback_bpm=base["bpm"],
            bpm_decimal_places=bpm_decimal_places,
        )

        left = _fit_section_grid_with_slope_correction(
            peak_times,
            peak_strengths,
            start_time=start_time,
            end_time=split_time,
            initial_bpm=left_initial_bpm,
            bpm_decimal_places=bpm_decimal_places,
            phase_steps=phase_steps,
            phase_tolerance=phase_tolerance,
            grid_match_tolerance=grid_match_tolerance,
        )
        right = _fit_section_grid_with_slope_correction(
            peak_times,
            peak_strengths,
            start_time=split_time,
            end_time=end_time,
            initial_bpm=right_initial_bpm,
            bpm_decimal_places=bpm_decimal_places,
            phase_steps=phase_steps,
            phase_tolerance=phase_tolerance,
            grid_match_tolerance=grid_match_tolerance,
        )

        if left["onset_count"] < 4 or right["onset_count"] < 4:
            continue

        split_objective = (
            float(left["objective"])
            + float(right["objective"])
            + split_penalty
        )
        improvement = float(base["objective"]) - split_objective
        improvement_ratio = improvement / max(
            float(base["objective"]),
            1e-12,
        )

        # A split must improve the penalized objective and produce at least
        # some visible tail improvement. This prevents recursive confetti.
        split_p95 = max(
            float(left["p95_ms"]),
            float(right["p95_ms"]),
        )
        tail_improvement = float(base["p95_ms"]) - split_p95

        if (
            improvement <= 0
            or improvement_ratio < minimum_split_improvement_ratio
            or tail_improvement < 1.0
        ):
            continue

        candidate = (
            split_objective,
            split_time,
            left_windows,
            right_windows,
            left_initial_bpm,
            right_initial_bpm,
        )
        if best_split is None or candidate[0] < best_split[0]:
            best_split = candidate

    if best_split is None:
        return [base_section]

    (
        _,
        split_time,
        left_windows,
        right_windows,
        left_initial_bpm,
        right_initial_bpm,
    ) = best_split

    left_raw = {
        **raw,
        "end_time": float(split_time),
        "bpm": float(left_initial_bpm),
        "windows": left_windows,
        "source": f"{raw['source']} residual split",
    }
    right_raw = {
        **raw,
        "start_time": float(split_time),
        "bpm": float(right_initial_bpm),
        "windows": right_windows,
        "source": f"{raw['source']} residual split",
    }

    return (
        _refine_section_recursive_v08(
            left_raw,
            peak_times,
            peak_strengths,
            bpm_decimal_places=bpm_decimal_places,
            phase_steps=phase_steps,
            phase_tolerance=phase_tolerance,
            grid_match_tolerance=grid_match_tolerance,
            depth=depth + 1,
            maximum_depth=maximum_depth,
            minimum_split_duration_seconds=minimum_split_duration_seconds,
            minimum_split_improvement_ratio=minimum_split_improvement_ratio,
            split_penalty=split_penalty,
        )
        + _refine_section_recursive_v08(
            right_raw,
            peak_times,
            peak_strengths,
            bpm_decimal_places=bpm_decimal_places,
            phase_steps=phase_steps,
            phase_tolerance=phase_tolerance,
            grid_match_tolerance=grid_match_tolerance,
            depth=depth + 1,
            maximum_depth=maximum_depth,
            minimum_split_duration_seconds=minimum_split_duration_seconds,
            minimum_split_improvement_ratio=minimum_split_improvement_ratio,
            split_penalty=split_penalty,
        )
    )


def _refine_and_optionally_split_sections(
    raw_sections: list[dict],
    peak_times: np.ndarray,
    peak_strengths: np.ndarray,
    *,
    bpm_decimal_places: int | None,
    phase_steps: int,
    phase_tolerance: float,
    grid_match_tolerance: float,
    split_improvement_ratio: float = 0.15,
    minimum_split_duration_seconds: float = 2.0,
    maximum_splits_per_section: int = 2,
) -> list[dict]:
    """
    v0.8 residual-shape refinement.

    Each section first receives the existing slope correction. Sections whose
    residuals still prefer two lines are recursively split up to a small depth.
    A fit penalty and tail-error requirement keep the process conservative.
    """
    maximum_depth = max(int(maximum_splits_per_section), 0)
    split_penalty = (
        0.12
        * float(grid_match_tolerance)
        * float(grid_match_tolerance)
    )

    refined: list[dict] = []
    for raw in raw_sections:
        refined.extend(
            _refine_section_recursive_v08(
                raw,
                peak_times,
                peak_strengths,
                bpm_decimal_places=bpm_decimal_places,
                phase_steps=phase_steps,
                phase_tolerance=phase_tolerance,
                grid_match_tolerance=grid_match_tolerance,
                depth=0,
                maximum_depth=maximum_depth,
                minimum_split_duration_seconds=(
                    minimum_split_duration_seconds
                ),
                minimum_split_improvement_ratio=(
                    split_improvement_ratio
                ),
                split_penalty=split_penalty,
            )
        )

    refined.sort(key=lambda item: float(item["start_time"]))
    return refined


def visualize_timing_segments(
    result: dict,
    *,
    pixels_per_second: int = PIXELS_PER_SECOND,
    minimum_width: int = MINIMUM_FIGURE_WIDTH,
    time_range: tuple[float, float] | None = None,
    show_grid: bool = True,
    maximum_grid_lines: int = 1200,
    maximum_flux_points: int = 8000,
) -> go.Figure:
    """
    Fast interactive timing visualizer.

    v0.8 avoids thousands of Plotly shape objects. Beat grids, boundaries,
    and residuals are emitted as a handful of WebGL traces instead.
    """
    duration = float(result["duration"])

    if time_range is None:
        start_time = 0.0
        end_time = duration
    else:
        start_time, end_time = map(float, time_range)

    if start_time < 0 or end_time <= start_time:
        raise ValueError("time_range must satisfy 0 <= start < end.")

    width = max(
        minimum_width,
        min(
            1800,
            int((end_time - start_time) * pixels_per_second),
        ),
    )

    flux_times = np.asarray(result["flux_times"], dtype=float)
    spectral_flux = np.asarray(result["spectral_flux"], dtype=float)
    peak_times = np.asarray(result["peak_times"], dtype=float)
    peak_strengths = np.asarray(result["peak_strengths"], dtype=float)
    sections = result["sections"]
    local_windows = result.get("local_windows", [])

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.50, 0.25, 0.25],
        subplot_titles=(
            "Onset evidence and section grids",
            "Local BPM estimates",
            "Section residuals",
        ),
    )

    flux_mask = (
        (flux_times >= start_time)
        & (flux_times <= end_time)
    )
    visible_flux_times = flux_times[flux_mask]
    visible_flux = spectral_flux[flux_mask]

    if len(visible_flux_times) > maximum_flux_points:
        stride = int(np.ceil(
            len(visible_flux_times) / maximum_flux_points
        ))
        visible_flux_times = visible_flux_times[::stride]
        visible_flux = visible_flux[::stride]

    fig.add_trace(
        go.Scattergl(
            x=visible_flux_times,
            y=visible_flux,
            mode="lines",
            name="Spectral flux",
            line={"width": 1},
        ),
        row=1,
        col=1,
    )

    peak_mask = (
        (peak_times >= start_time)
        & (peak_times <= end_time)
    )
    fig.add_trace(
        go.Scattergl(
            x=peak_times[peak_mask],
            y=peak_strengths[peak_mask],
            mode="markers",
            name="Onset candidates",
            marker={"symbol": "x", "size": 5},
        ),
        row=1,
        col=1,
    )

    if local_windows:
        local_centers = np.asarray([
            float(item["center_time"])
            for item in local_windows
        ])
        candidate_bpms = np.asarray([
            float(item.get("candidate_bpm", np.nan))
            for item in local_windows
        ])
        accepted = np.asarray([
            bool(item.get("valid_for_segmentation", False))
            for item in local_windows
        ])
        visible = (
            (local_centers >= start_time)
            & (local_centers <= end_time)
            & accepted
            & np.isfinite(candidate_bpms)
        )

        fig.add_trace(
            go.Scattergl(
                x=local_centers[visible],
                y=candidate_bpms[visible],
                mode="markers",
                name="Trusted local BPM",
                marker={"size": 5},
                hovertemplate=(
                    "time=%{x:.3f}s"
                    "<br>BPM=%{y:.2f}"
                    "<extra></extra>"
                ),
            ),
            row=2,
            col=1,
        )

    section_x: list[float | None] = []
    section_y: list[float | None] = []
    residual_x: list[float] = []
    residual_y: list[float] = []
    boundary_times: list[float] = []
    grid_times: list[float] = []

    for section_index, section in enumerate(sections):
        section_start = float(section["start_time"])
        section_end = float(section["end_time"])

        if section_end < start_time or section_start > end_time:
            continue

        visible_start = max(start_time, section_start)
        visible_end = min(end_time, section_end)
        bpm = float(section["bpm"])
        period = float(section["period"])
        phase = float(section["phase"])

        section_x.extend([
            visible_start,
            visible_end,
            None,
        ])
        section_y.extend([
            bpm,
            bpm,
            None,
        ])

        if section_index > 0 and start_time <= section_start <= end_time:
            boundary_times.append(section_start)

        if show_grid:
            first_grid = (
                phase
                + np.ceil((visible_start - phase) / period)
                * period
            )
            grid_times.extend(
                np.arange(
                    first_grid,
                    visible_end + period / 2.0,
                    period,
                ).tolist()
            )

        matched_times = np.asarray(
            section.get("matched_peak_times", []),
            dtype=float,
        )
        residuals_ms = np.asarray(
            section.get("residuals_ms", []),
            dtype=float,
        )
        mask = (
            (matched_times >= start_time)
            & (matched_times <= end_time)
        )
        residual_x.extend(matched_times[mask].tolist())
        residual_y.extend(residuals_ms[mask].tolist())

    fig.add_trace(
        go.Scattergl(
            x=section_x,
            y=section_y,
            mode="lines",
            name="Selected section BPM",
            line={"width": 2},
        ),
        row=2,
        col=1,
    )

    fig.add_trace(
        go.Scattergl(
            x=residual_x,
            y=residual_y,
            mode="markers",
            name="Section residuals",
            marker={"size": 4},
            hovertemplate=(
                "time=%{x:.4f}s"
                "<br>residual=%{y:.3f}ms"
                "<extra></extra>"
            ),
        ),
        row=3,
        col=1,
    )

    # Thousands of add_vline calls were the main rendering bottleneck.
    # Draw all grid lines in one WebGL trace and decimate only when necessary.
    if show_grid and grid_times:
        grid_times = sorted(set(float(value) for value in grid_times))

        if len(grid_times) > maximum_grid_lines:
            stride = int(np.ceil(
                len(grid_times) / maximum_grid_lines
            ))
            grid_times = grid_times[::stride]

        flux_min = (
            float(np.min(visible_flux))
            if len(visible_flux)
            else 0.0
        )
        flux_max = (
            float(np.max(visible_flux))
            if len(visible_flux)
            else 1.0
        )

        grid_x: list[float | None] = []
        grid_y: list[float | None] = []
        for grid_time in grid_times:
            grid_x.extend([grid_time, grid_time, None])
            grid_y.extend([flux_min, flux_max, None])

        fig.add_trace(
            go.Scattergl(
                x=grid_x,
                y=grid_y,
                mode="lines",
                name="Beat grid",
                line={"width": 1, "dash": "dot"},
                opacity=0.22,
                hoverinfo="skip",
            ),
            row=1,
            col=1,
        )

    if boundary_times:
        for row in (1, 2, 3):
            if row == 1:
                y_low = (
                    float(np.min(visible_flux))
                    if len(visible_flux)
                    else 0.0
                )
                y_high = (
                    float(np.max(visible_flux))
                    if len(visible_flux)
                    else 1.0
                )
            elif row == 2:
                y_low = float(result["initial_bpm"]) - 20.0
                y_high = float(result["initial_bpm"]) + 50.0
            else:
                y_low = -float(result["section_tolerance_ms"]) * 2.0
                y_high = float(result["section_tolerance_ms"]) * 2.0

            boundary_x: list[float | None] = []
            boundary_y: list[float | None] = []
            for boundary in boundary_times:
                boundary_x.extend([boundary, boundary, None])
                boundary_y.extend([y_low, y_high, None])

            fig.add_trace(
                go.Scattergl(
                    x=boundary_x,
                    y=boundary_y,
                    mode="lines",
                    name=(
                        "Section boundaries"
                        if row == 1
                        else None
                    ),
                    showlegend=(row == 1),
                    line={"width": 1, "dash": "dash"},
                    opacity=0.5,
                    hoverinfo="skip",
                ),
                row=row,
                col=1,
            )

    tolerance = float(result["section_tolerance_ms"])
    for value in (0.0, tolerance, -tolerance):
        fig.add_hline(
            y=value,
            line_dash="dot",
            row=3,
            col=1,
        )

    for row in (1, 2):
        fig.update_xaxes(
            range=[start_time, end_time],
            row=row,
            col=1,
        )

    fig.update_xaxes(
        range=[start_time, end_time],
        title_text="Time (seconds)",
        rangeslider={"visible": True, "thickness": 0.06},
        row=3,
        col=1,
    )
    fig.update_yaxes(
        title_text="Normalized flux",
        row=1,
        col=1,
    )
    fig.update_yaxes(
        title_text="BPM",
        range=[
            float(result["initial_bpm"]) - 20.0,
            float(result["initial_bpm"]) + 50.0,
        ],
        row=2,
        col=1,
    )
    fig.update_yaxes(
        title_text="Residual (ms)",
        row=3,
        col=1,
    )

    fig.update_layout(
        title=(
            f"{result['audio_path'].name} | "
            f"{len(sections)} timing section(s)"
        ),
        width=width,
        height=850,
        autosize=False,
        hovermode="closest",
        dragmode="pan",
        margin={"l": 70, "r": 30, "t": 85, "b": 80},
    )

    fig.show(
        config={
            "scrollZoom": True,
            "displaylogo": False,
            "responsive": True,
        }
    )
    return fig


# ---------------------------------------------------------------------------
# v0.9 dense rhythmic-evidence diagnostics.
# This stage is intentionally diagnostic-only. It measures whether the full
# spectral-flux signal contains timing-drift information before the section
# builder is allowed to consume it.
# ---------------------------------------------------------------------------

def _weighted_flux_at_times(
    flux_times: np.ndarray,
    spectral_flux: np.ndarray,
    query_times: np.ndarray,
    *,
    shoulder_seconds: float = 0.018,
) -> np.ndarray:
    """
    Sample spectral flux near query times with a small triangular shoulder.

    This is more stable than evaluating one frame exactly at each grid point.
    """
    flux_times = np.asarray(flux_times, dtype=float)
    spectral_flux = np.asarray(spectral_flux, dtype=float)
    query_times = np.asarray(query_times, dtype=float)

    if len(query_times) == 0:
        return np.asarray([], dtype=float)

    center = np.interp(
        query_times,
        flux_times,
        spectral_flux,
        left=0.0,
        right=0.0,
    )
    left = np.interp(
        query_times - shoulder_seconds,
        flux_times,
        spectral_flux,
        left=0.0,
        right=0.0,
    )
    right = np.interp(
        query_times + shoulder_seconds,
        flux_times,
        spectral_flux,
        left=0.0,
        right=0.0,
    )

    return 0.60 * center + 0.20 * left + 0.20 * right


def score_dense_flux_grid(
    flux_times: np.ndarray,
    spectral_flux: np.ndarray,
    *,
    start_time: float,
    end_time: float,
    bpm: float,
    phase: float,
    offset_seconds: float = 0.0,
    subdivisions: tuple[int, ...] = (1, 2, 4),
    minimum_grid_points: int = 6,
) -> dict:
    """
    Score a BPM/phase grid against the full spectral-flux signal.

    Subdivision weights decrease for finer grids so dense subdivisions cannot
    win merely by placing more sample points.
    """
    if bpm <= 0 or not np.isfinite(bpm):
        raise ValueError("bpm must be finite and positive.")
    if end_time <= start_time:
        raise ValueError("end_time must exceed start_time.")
    if not subdivisions or any(value <= 0 for value in subdivisions):
        raise ValueError("subdivisions must contain positive integers.")

    period = 60.0 / float(bpm)
    total_score = 0.0
    total_weight = 0.0
    grid_point_count = 0

    for subdivision in subdivisions:
        step = period / float(subdivision)
        shifted_phase = float(phase) + float(offset_seconds)

        first_grid = (
            shifted_phase
            + np.ceil((start_time - shifted_phase) / step) * step
        )
        grid_times = np.arange(
            first_grid,
            end_time + step / 2.0,
            step,
        )

        if len(grid_times) == 0:
            continue

        # Beat lines matter most; finer subdivisions contribute less.
        subdivision_weight = 1.0 / np.sqrt(float(subdivision))
        values = _weighted_flux_at_times(
            flux_times,
            spectral_flux,
            grid_times,
        )

        # Reward consistent rhythmic activation rather than one giant transient.
        clipped = np.minimum(
            values,
            np.percentile(values, 85) if len(values) >= 4 else np.max(values),
        )
        total_score += subdivision_weight * float(np.mean(clipped))
        total_weight += subdivision_weight
        grid_point_count += int(len(grid_times))

    if grid_point_count < minimum_grid_points or total_weight <= 0:
        return {
            "score": np.nan,
            "grid_point_count": grid_point_count,
        }

    return {
        "score": float(total_score / total_weight),
        "grid_point_count": grid_point_count,
    }









# ---------------------------------------------------------------------------
# v0.10 override: continuity-regularized dense-offset path with phase unwrapping.
# ---------------------------------------------------------------------------

DENSE_PATH_CONTINUITY_WEIGHT = 0.18
DENSE_PATH_JUMP_PENALTY = 0.45
DENSE_PATH_MAX_JUMP_MS = 18.0
DENSE_PATH_CONFIDENCE_FLOOR = 0.08


def _circular_offset_difference_ms(
    newer_ms: np.ndarray | float,
    older_ms: np.ndarray | float,
    equivalence_period_ms: float,
) -> np.ndarray:
    """
    Signed shortest offset difference modulo one rhythmic equivalence period.
    """
    return (
        (
            np.asarray(newer_ms, dtype=float)
            - np.asarray(older_ms, dtype=float)
            + equivalence_period_ms / 2.0
        )
        % equivalence_period_ms
        - equivalence_period_ms / 2.0
    )


def _dense_offset_viterbi_path(
    score_matrix: np.ndarray,
    offsets_ms: np.ndarray,
    *,
    equivalence_period_ms: float,
    continuity_weight: float,
    jump_penalty: float,
    maximum_soft_jump_ms: float,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Choose one temporally coherent offset path through dense-window scores.

    Scores are normalized independently per window before Viterbi inference,
    preventing loud windows from completely dominating quiet ones.
    """
    scores = np.asarray(score_matrix, dtype=float)
    offsets_ms = np.asarray(offsets_ms, dtype=float)

    if scores.ndim != 2:
        raise ValueError("score_matrix must be two-dimensional.")
    if scores.shape[1] != len(offsets_ms):
        raise ValueError("score_matrix width must match offsets_ms.")
    if len(scores) == 0:
        return (
            np.asarray([], dtype=int),
            np.asarray([], dtype=float),
        )

    finite_scores = np.where(np.isfinite(scores), scores, np.nan)

    row_min = np.nanmin(finite_scores, axis=1, keepdims=True)
    row_max = np.nanmax(finite_scores, axis=1, keepdims=True)
    row_span = np.maximum(row_max - row_min, 1e-12)

    normalized = (finite_scores - row_min) / row_span
    normalized = np.where(np.isfinite(normalized), normalized, -1e6)

    circular_delta = np.abs(
        _circular_offset_difference_ms(
            offsets_ms[:, None],
            offsets_ms[None, :],
            equivalence_period_ms,
        )
    )

    transition_cost = (
        continuity_weight
        * (circular_delta / max(maximum_soft_jump_ms, 1e-9)) ** 2
        + jump_penalty
        * (circular_delta > maximum_soft_jump_ms)
    )

    n_windows, n_states = normalized.shape
    best_cost = np.full((n_windows, n_states), np.inf, dtype=float)
    previous = np.full((n_windows, n_states), -1, dtype=int)

    best_cost[0] = -normalized[0]

    for window_index in range(1, n_windows):
        candidate_costs = (
            best_cost[window_index - 1][:, None]
            + transition_cost
        )

        best_predecessors = np.argmin(
            candidate_costs,
            axis=0,
        )

        best_cost[window_index] = (
            candidate_costs[
                best_predecessors,
                np.arange(n_states),
            ]
            - normalized[window_index]
        )

        previous[window_index] = best_predecessors

    final_state = int(np.argmin(best_cost[-1]))
    path_states = np.full(n_windows, -1, dtype=int)

    for window_index in range(n_windows - 1, -1, -1):
        path_states[window_index] = final_state
        final_state = (
            int(previous[window_index, final_state])
            if window_index > 0
            else -1
        )

    path_offsets_ms = offsets_ms[path_states]
    return path_states, path_offsets_ms


def _unwrap_dense_offset_path(
    wrapped_offsets_ms: np.ndarray,
    *,
    equivalence_period_ms: float,
) -> np.ndarray:
    """
    Convert the wrapped offset path into a continuous trajectory.
    """
    wrapped = np.asarray(wrapped_offsets_ms, dtype=float)

    if len(wrapped) == 0:
        return wrapped.copy()

    unwrapped = np.empty_like(wrapped)
    unwrapped[0] = wrapped[0]

    for index in range(1, len(wrapped)):
        increment = float(
            _circular_offset_difference_ms(
                wrapped[index],
                wrapped[index - 1],
                equivalence_period_ms,
            )
        )
        unwrapped[index] = unwrapped[index - 1] + increment

    return unwrapped


def estimate_dense_grid_offset_windows(
    flux_times: np.ndarray,
    spectral_flux: np.ndarray,
    *,
    duration: float,
    bpm: float,
    phase: float,
    window_seconds: float = DENSE_OFFSET_WINDOW_SECONDS,
    step_seconds: float = DENSE_OFFSET_STEP_SECONDS,
    search_ms: float = DENSE_OFFSET_SEARCH_MS,
    resolution_ms: float = DENSE_OFFSET_RESOLUTION_MS,
    subdivisions: tuple[int, ...] = DENSE_OFFSET_SUBDIVISIONS,
    minimum_grid_points: int = DENSE_OFFSET_MINIMUM_GRID_POINTS,
    smoothing_windows: int = DENSE_OFFSET_SMOOTHING_WINDOWS,
    continuity_weight: float = DENSE_PATH_CONTINUITY_WEIGHT,
    jump_penalty: float = DENSE_PATH_JUMP_PENALTY,
    maximum_soft_jump_ms: float = DENSE_PATH_MAX_JUMP_MS,
) -> list[dict]:
    """
    Estimate a continuity-regularized dense grid-offset trajectory.

    v0.10 keeps each window's full offset score curve, finds the best global
    path with Viterbi inference, and unwraps quarter-beat-equivalent offsets.
    """
    if window_seconds <= 0 or step_seconds <= 0:
        raise ValueError("Dense offset window and step must be positive.")
    if resolution_ms <= 0:
        raise ValueError("Dense offset resolution must be positive.")
    if bpm <= 0 or not np.isfinite(bpm):
        raise ValueError("bpm must be finite and positive.")

    finest_subdivision = max(subdivisions)
    equivalence_period_ms = (
        60_000.0 / float(bpm) / float(finest_subdivision)
    )

    # Cover exactly one equivalence interval. The old fixed ±45 ms search was
    # merely an approximate version of this for ~182 BPM quarter-beat timing.
    half_interval_ms = equivalence_period_ms / 2.0
    offsets_ms = np.arange(
        -half_interval_ms,
        half_interval_ms + resolution_ms / 2.0,
        resolution_ms,
    )
    offsets_seconds = offsets_ms / 1000.0

    final_start = max(duration - window_seconds, 0.0)
    starts = np.arange(
        0.0,
        final_start + step_seconds / 2.0,
        step_seconds,
    )

    score_rows: list[np.ndarray] = []
    metadata: list[dict] = []

    for start_time in starts:
        end_time = min(duration, start_time + window_seconds)

        scores = np.asarray([
            score_dense_flux_grid(
                flux_times,
                spectral_flux,
                start_time=float(start_time),
                end_time=float(end_time),
                bpm=float(bpm),
                phase=float(phase),
                offset_seconds=float(offset_seconds),
                subdivisions=subdivisions,
                minimum_grid_points=minimum_grid_points,
            )["score"]
            for offset_seconds in offsets_seconds
        ], dtype=float)

        score_rows.append(scores)

        finite_scores = scores[np.isfinite(scores)]
        if len(finite_scores):
            best_score = float(np.max(finite_scores))
            median_score = float(np.median(finite_scores))
            spread = float(np.std(finite_scores))
            confidence = float(np.clip(
                (best_score - median_score)
                / max(4.0 * spread, 1e-9),
                0.0,
                1.0,
            ))
        else:
            confidence = 0.0

        metadata.append({
            "start_time": float(start_time),
            "end_time": float(end_time),
            "center_time": float((start_time + end_time) / 2.0),
            "confidence": confidence,
        })

    score_matrix = np.vstack(score_rows) if score_rows else np.empty((0, len(offsets_ms)))

    path_states, wrapped_path_ms = _dense_offset_viterbi_path(
        score_matrix,
        offsets_ms,
        equivalence_period_ms=equivalence_period_ms,
        continuity_weight=continuity_weight,
        jump_penalty=jump_penalty,
        maximum_soft_jump_ms=maximum_soft_jump_ms,
    )

    unwrapped_path_ms = _unwrap_dense_offset_path(
        wrapped_path_ms,
        equivalence_period_ms=equivalence_period_ms,
    )

    if smoothing_windows > 1 and len(unwrapped_path_ms):
        width = int(smoothing_windows)
        half_width = width // 2
        smoothed = np.empty_like(unwrapped_path_ms)

        for index in range(len(unwrapped_path_ms)):
            left = max(0, index - half_width)
            right = min(
                len(unwrapped_path_ms),
                index + half_width + 1,
            )
            smoothed[index] = float(
                np.median(unwrapped_path_ms[left:right])
            )
    else:
        smoothed = unwrapped_path_ms.copy()

    rows: list[dict] = []

    for index, row in enumerate(metadata):
        state_index = int(path_states[index])
        scores = score_matrix[index]
        selected_score = float(scores[state_index])

        finite_scores = scores[np.isfinite(scores)]
        if len(finite_scores) >= 2:
            ordered = np.sort(finite_scores)
            score_margin = float(
                ordered[-1] - ordered[-2]
            )
        else:
            score_margin = 0.0

        rows.append({
            **row,
            "offset_seconds": float(
                wrapped_path_ms[index] / 1000.0
            ),
            "offset_ms": float(wrapped_path_ms[index]),
            "wrapped_offset_ms": float(wrapped_path_ms[index]),
            "unwrapped_offset_ms": float(unwrapped_path_ms[index]),
            "smoothed_offset_ms": float(smoothed[index]),
            "score": selected_score,
            "score_margin": score_margin,
            "equivalence_period_ms": float(equivalence_period_ms),
            "path_state": state_index,
        })

    return rows


def summarize_dense_offset_trajectory(
    dense_rows: list[dict],
    *,
    minimum_confidence: float = DENSE_PATH_CONFIDENCE_FLOOR,
) -> dict:
    valid = [
        row
        for row in dense_rows
        if (
            np.isfinite(float(row["smoothed_offset_ms"]))
            and float(row["confidence"]) >= minimum_confidence
        )
    ]

    if len(valid) < 2:
        return {
            "valid_windows": len(valid),
            "median_offset_ms": np.nan,
            "p95_absolute_offset_ms": np.nan,
            "slope_ms_per_second": np.nan,
            "equivalence_period_ms": (
                float(dense_rows[0]["equivalence_period_ms"])
                if dense_rows
                else np.nan
            ),
        }

    times = np.asarray([
        float(row["center_time"])
        for row in valid
    ])
    offsets = np.asarray([
        float(row["smoothed_offset_ms"])
        for row in valid
    ])

    slope = float(np.polyfit(times, offsets, deg=1)[0])

    return {
        "valid_windows": len(valid),
        "median_offset_ms": float(np.median(offsets)),
        "p95_absolute_offset_ms": float(
            np.percentile(
                np.abs(offsets - np.median(offsets)),
                95,
            )
        ),
        "slope_ms_per_second": slope,
        "equivalence_period_ms": float(
            valid[0]["equivalence_period_ms"]
        ),
    }


def visualize_dense_offset_trajectory(
    result: dict,
    *,
    minimum_confidence: float = DENSE_PATH_CONFIDENCE_FLOOR,
    time_range: tuple[float, float] | None = None,
) -> go.Figure:
    """
    Plot wrapped raw offsets and the continuity-regularized unwrapped path.
    """
    rows = result.get("dense_offset_windows", [])
    if not rows:
        raise ValueError(
            "No dense offset trajectory is present in this result."
        )

    times = np.asarray([
        float(row["center_time"])
        for row in rows
    ])
    wrapped = np.asarray([
        float(row["wrapped_offset_ms"])
        for row in rows
    ])
    unwrapped = np.asarray([
        float(row["smoothed_offset_ms"])
        for row in rows
    ])
    confidence = np.asarray([
        float(row["confidence"])
        for row in rows
    ])

    visible = np.isfinite(wrapped) & np.isfinite(unwrapped)

    if time_range is not None:
        start_time, end_time = map(float, time_range)
        visible &= (
            (times >= start_time)
            & (times <= end_time)
        )

    trusted = (
        visible
        & (confidence >= minimum_confidence)
    )

    figure = go.Figure()

    figure.add_trace(
        go.Scattergl(
            x=times[visible],
            y=wrapped[visible],
            mode="markers",
            name="Wrapped path",
            marker={"size": 3},
            opacity=0.22,
        )
    )

    figure.add_trace(
        go.Scattergl(
            x=times[trusted],
            y=unwrapped[trusted],
            mode="lines",
            name="Continuity-regularized path",
            line={"width": 2},
        )
    )

    figure.add_hline(
        y=0.0,
        line_dash="dash",
    )

    figure.update_layout(
        title=(
            f"Beat Timer v{result.get('engine_version', 'unknown')} | "
            f"{result.get('dense_offset_method', 'unknown')} | "
            "Dense spectral-flux offset path"
        ),
        xaxis_title="Time (seconds)",
        yaxis_title="Preferred grid offset (ms)",
        height=480,
        hovermode="closest",
        dragmode="pan",
    )

    figure.show(
        config={
            "scrollZoom": True,
            "displaylogo": False,
            "responsive": True,
        }
    )
    return figure



# ---------------------------------------------------------------------------
# v0.12: locally segmented and periodically re-anchored dense-offset path.
# ---------------------------------------------------------------------------

DENSE_SEGMENT_MAXIMUM_DURATION_SECONDS = 10.0
DENSE_SEGMENT_MINIMUM_DURATION_SECONDS = 2.0
DENSE_SEGMENT_MINIMUM_WINDOWS = 8
DENSE_SEGMENT_MAXIMUM_ABSOLUTE_SLOPE_MS_PER_SECOND = 4.0
DENSE_SEGMENT_MAXIMUM_RESIDUAL_MS = 8.0
DENSE_SEGMENT_MINIMUM_CONFIDENCE_COVERAGE = 0.60
DENSE_SEGMENT_CONFIDENCE_THRESHOLD = 0.08


def _robust_linear_fit_v012(
    times: np.ndarray,
    values: np.ndarray,
    *,
    iterations: int = 4,
    huber_delta: float = 1.5,
) -> dict:
    times = np.asarray(times, dtype=float)
    values = np.asarray(values, dtype=float)

    if len(times) != len(values) or len(times) < 2:
        raise ValueError("Need at least two paired points.")

    center_time = float(np.mean(times))
    x = times - center_time
    design = np.column_stack([np.ones_like(x), x])
    weights = np.ones(len(times), dtype=float)

    for _ in range(max(iterations, 1)):
        root_weights = np.sqrt(weights)
        coefficients = np.linalg.lstsq(
            design * root_weights[:, None],
            values * root_weights,
            rcond=None,
        )[0]

        fitted = design @ coefficients
        residuals = values - fitted
        median = float(np.median(residuals))
        mad = float(np.median(np.abs(residuals - median)))
        scale = max(1.4826 * mad, 1e-6)
        standardized = np.abs(residuals) / scale

        weights = np.where(
            standardized <= huber_delta,
            1.0,
            huber_delta / np.maximum(standardized, 1e-12),
        )

    fitted = design @ coefficients
    residuals = values - fitted

    return {
        "center_time": center_time,
        "intercept_ms": float(coefficients[0]),
        "slope_ms_per_second": float(coefficients[1]),
        "fitted_ms": fitted,
        "residuals_ms": residuals,
        "median_absolute_residual_ms": float(
            np.median(np.abs(residuals))
        ),
        "p95_absolute_residual_ms": float(
            np.percentile(np.abs(residuals), 95)
        ),
    }


def _dense_segment_fit_v012(
    rows: list[dict],
    *,
    confidence_threshold: float,
) -> dict | None:
    if len(rows) < 2:
        return None

    times = np.asarray([
        float(row["center_time"])
        for row in rows
    ])
    offsets = np.asarray([
        float(row["smoothed_offset_ms"])
        for row in rows
    ])
    confidences = np.asarray([
        float(row.get("confidence", 0.0))
        for row in rows
    ])

    finite = np.isfinite(times) & np.isfinite(offsets)

    if np.sum(finite) < 2:
        return None

    fit = _robust_linear_fit_v012(
        times[finite],
        offsets[finite],
    )

    fit.update({
        "duration_seconds": float(
            times[finite][-1] - times[finite][0]
        ),
        "confidence_coverage": float(
            np.mean(
                confidences[finite] >= confidence_threshold
            )
        ),
        "window_count": int(np.sum(finite)),
    })

    return fit


def segment_dense_offset_trajectory(
    dense_rows: list[dict],
    *,
    maximum_duration_seconds: float = (
        DENSE_SEGMENT_MAXIMUM_DURATION_SECONDS
    ),
    minimum_duration_seconds: float = (
        DENSE_SEGMENT_MINIMUM_DURATION_SECONDS
    ),
    minimum_windows: int = DENSE_SEGMENT_MINIMUM_WINDOWS,
    maximum_absolute_slope_ms_per_second: float = (
        DENSE_SEGMENT_MAXIMUM_ABSOLUTE_SLOPE_MS_PER_SECOND
    ),
    maximum_residual_ms: float = (
        DENSE_SEGMENT_MAXIMUM_RESIDUAL_MS
    ),
    minimum_confidence_coverage: float = (
        DENSE_SEGMENT_MINIMUM_CONFIDENCE_COVERAGE
    ),
    confidence_threshold: float = (
        DENSE_SEGMENT_CONFIDENCE_THRESHOLD
    ),
) -> list[dict]:
    """
    Greedily partition the unwrapped path into short robust linear pieces.

    Ambiguous regions are marked rejected rather than forced into corrections.
    Accepted pieces are re-anchored around their own local median.
    """
    if not dense_rows:
        return []

    rows = sorted(
        dense_rows,
        key=lambda row: float(row["center_time"]),
    )

    segments: list[dict] = []
    index = 0

    while index < len(rows):
        best_end = None
        best_fit = None
        candidate_end = index + minimum_windows

        while candidate_end <= len(rows):
            candidate = rows[index:candidate_end]
            duration = (
                float(candidate[-1]["center_time"])
                - float(candidate[0]["center_time"])
            )

            if duration > maximum_duration_seconds:
                break

            fit = _dense_segment_fit_v012(
                candidate,
                confidence_threshold=confidence_threshold,
            )

            if fit is not None:
                acceptable = (
                    fit["duration_seconds"]
                    >= minimum_duration_seconds
                    and abs(fit["slope_ms_per_second"])
                    <= maximum_absolute_slope_ms_per_second
                    and fit["p95_absolute_residual_ms"]
                    <= maximum_residual_ms
                    and fit["confidence_coverage"]
                    >= minimum_confidence_coverage
                )

                if acceptable:
                    best_end = candidate_end
                    best_fit = fit

            candidate_end += 1

        if best_end is not None and best_fit is not None:
            chosen = rows[index:best_end]
            offsets = np.asarray([
                float(row["smoothed_offset_ms"])
                for row in chosen
            ])
            anchor_ms = float(np.median(offsets))

            segments.append({
                "start_index": index,
                "end_index": best_end - 1,
                "start_time": float(chosen[0]["center_time"]),
                "end_time": float(chosen[-1]["center_time"]),
                "accepted": True,
                "anchor_ms": anchor_ms,
                "intercept_ms": float(
                    best_fit["intercept_ms"] - anchor_ms
                ),
                "slope_ms_per_second": float(
                    best_fit["slope_ms_per_second"]
                ),
                "median_absolute_residual_ms": float(
                    best_fit["median_absolute_residual_ms"]
                ),
                "p95_absolute_residual_ms": float(
                    best_fit["p95_absolute_residual_ms"]
                ),
                "confidence_coverage": float(
                    best_fit["confidence_coverage"]
                ),
                "window_count": int(best_fit["window_count"]),
            })
            index = best_end
            continue

        gap_start = index
        index += 1

        while index < len(rows):
            probe_end = min(
                index + minimum_windows,
                len(rows),
            )
            probe = rows[index:probe_end]

            if len(probe) >= minimum_windows:
                fit = _dense_segment_fit_v012(
                    probe,
                    confidence_threshold=confidence_threshold,
                )

                if fit is not None:
                    acceptable = (
                        fit["duration_seconds"]
                        >= minimum_duration_seconds
                        and abs(fit["slope_ms_per_second"])
                        <= maximum_absolute_slope_ms_per_second
                        and fit["p95_absolute_residual_ms"]
                        <= maximum_residual_ms
                        and fit["confidence_coverage"]
                        >= minimum_confidence_coverage
                    )

                    if acceptable:
                        break

            index += 1

        gap = rows[gap_start:index]

        if gap:
            segments.append({
                "start_index": gap_start,
                "end_index": index - 1,
                "start_time": float(gap[0]["center_time"]),
                "end_time": float(gap[-1]["center_time"]),
                "accepted": False,
                "anchor_ms": np.nan,
                "intercept_ms": np.nan,
                "slope_ms_per_second": np.nan,
                "median_absolute_residual_ms": np.nan,
                "p95_absolute_residual_ms": np.nan,
                "confidence_coverage": float(np.mean([
                    float(row.get("confidence", 0.0))
                    >= confidence_threshold
                    for row in gap
                ])),
                "window_count": len(gap),
            })

    return segments


def apply_dense_segment_metadata(
    dense_rows: list[dict],
    dense_segments: list[dict],
) -> None:
    for row in dense_rows:
        row["dense_segment_id"] = -1
        row["dense_segment_accepted"] = False
        row["reanchored_offset_ms"] = np.nan
        row["segment_fitted_offset_ms"] = np.nan

    for segment_id, segment in enumerate(dense_segments):
        start_index = int(segment["start_index"])
        end_index = int(segment["end_index"])
        midpoint = 0.5 * (
            float(segment["start_time"])
            + float(segment["end_time"])
        )

        for row_index in range(start_index, end_index + 1):
            row = dense_rows[row_index]
            row["dense_segment_id"] = segment_id
            row["dense_segment_accepted"] = bool(
                segment["accepted"]
            )

            if not segment["accepted"]:
                continue

            row["reanchored_offset_ms"] = (
                float(row["smoothed_offset_ms"])
                - float(segment["anchor_ms"])
            )

            row["segment_fitted_offset_ms"] = (
                float(segment["intercept_ms"])
                + float(segment["slope_ms_per_second"])
                * (float(row["center_time"]) - midpoint)
            )


def visualize_segmented_dense_offset_trajectory(
    result: dict,
    *,
    time_range: tuple[float, float] | None = None,
) -> go.Figure:
    rows = result.get("dense_offset_windows", [])
    segments = result.get("dense_offset_segments", [])

    if not rows or not segments:
        raise ValueError(
            "No segmented dense-offset trajectory is present."
        )

    figure = go.Figure()
    shown_accepted = False
    shown_rejected = False

    for segment in segments:
        start_index = int(segment["start_index"])
        end_index = int(segment["end_index"])
        segment_rows = rows[start_index:end_index + 1]

        times = np.asarray([
            float(row["center_time"])
            for row in segment_rows
        ])

        visible = np.ones(len(times), dtype=bool)

        if time_range is not None:
            range_start, range_end = map(float, time_range)
            visible &= (
                (times >= range_start)
                & (times <= range_end)
            )

        if not np.any(visible):
            continue

        if segment["accepted"]:
            offsets = np.asarray([
                float(row["reanchored_offset_ms"])
                for row in segment_rows
            ])
            fitted = np.asarray([
                float(row["segment_fitted_offset_ms"])
                for row in segment_rows
            ])

            figure.add_trace(
                go.Scattergl(
                    x=times[visible],
                    y=offsets[visible],
                    mode="markers",
                    name="Accepted local offsets",
                    showlegend=not shown_accepted,
                    marker={"size": 4},
                    opacity=0.32,
                )
            )
            figure.add_trace(
                go.Scattergl(
                    x=times[visible],
                    y=fitted[visible],
                    mode="lines",
                    name="Local linear fits",
                    showlegend=not shown_accepted,
                    line={"width": 2},
                )
            )
            shown_accepted = True
        else:
            figure.add_trace(
                go.Scattergl(
                    x=times[visible],
                    y=np.zeros(np.sum(visible)),
                    mode="markers",
                    name="Rejected / ambiguous",
                    showlegend=not shown_rejected,
                    marker={"size": 3, "symbol": "x"},
                    opacity=0.25,
                )
            )
            shown_rejected = True

    figure.add_hline(y=0.0, line_dash="dash")

    figure.update_layout(
        title=(
            f"Beat Timer v{result.get('engine_version', 'unknown')} | "
            "Segmented and re-anchored dense-offset trajectory"
        ),
        xaxis_title="Time (seconds)",
        yaxis_title="Local preferred offset (ms)",
        height=500,
        hovermode="closest",
        dragmode="pan",
    )

    figure.show(
        config={
            "scrollZoom": True,
            "displaylogo": False,
            "responsive": True,
        }
    )

    return figure

__all__ = [
    "__version__",
    "DENSE_OFFSET_METHOD",
    "analyze_song",
    "print_analysis_summary",
    "visualize_timing_segments",
    "visualize_dense_offset_trajectory",
    "visualize_segmented_dense_offset_trajectory",
    "tolerance_sweep",
    "detect_rising_onsets",
    "estimate_bpm_from_flux",
    "estimate_grid_phase",
    "estimate_local_tempo_windows",
    "estimate_dense_grid_offset_windows",
    "summarize_dense_offset_trajectory",
    "segment_dense_offset_trajectory",
    "apply_dense_segment_metadata",
    "score_dense_flux_grid",
    "score_grid_candidate",
    "score_continuation_grid",
    "grid_offset_at_time",
    "segment_local_tempo_windows",
    "minimum_timing_sections",
]
