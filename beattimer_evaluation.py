"""
Beat Timer v0.5 evaluation utilities.

This module parses .osu files and scores an already-produced audio-only
prediction. It must never alter or feed map information into prediction.
"""

from copy import deepcopy
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def parse_osu_file(osu_path: Path | str) -> dict:
    """Parse hit-object starts and uninherited timing points from an .osu file."""
    osu_path = Path(osu_path)

    if not osu_path.exists():
        raise FileNotFoundError(
            f".osu file not found: {osu_path.resolve()}"
        )

    sections: dict[str, list[str]] = {}
    current_section: str | None = None

    with osu_path.open(
        "r",
        encoding="utf-8-sig",
        errors="replace",
    ) as file:
        for raw_line in file:
            line = raw_line.strip()

            if not line or line.startswith("//"):
                continue

            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1]
                sections.setdefault(current_section, [])
                continue

            if current_section is not None:
                sections[current_section].append(line)

    hit_objects: list[dict] = []

    for line in sections.get("HitObjects", []):
        parts = line.split(",")

        if len(parts) < 4:
            continue

        try:
            time_seconds = float(parts[2]) / 1000.0
            object_type = int(parts[3])
        except ValueError:
            continue

        hit_objects.append({
            "time": time_seconds,
            "type": object_type,
            "is_circle": bool(object_type & 1),
            "is_slider": bool(object_type & 2),
            "is_spinner": bool(object_type & 8),
            "is_hold": bool(object_type & 128),
        })

    redlines: list[dict] = []

    for line in sections.get("TimingPoints", []):
        parts = line.split(",")

        if len(parts) < 2:
            continue

        try:
            offset_seconds = float(parts[0]) / 1000.0
            beat_length_ms = float(parts[1])
            uninherited = int(parts[6]) if len(parts) > 6 else 1
        except ValueError:
            continue

        if uninherited == 1 and beat_length_ms > 0:
            redlines.append({
                "time": offset_seconds,
                "beat_length_ms": beat_length_ms,
                "bpm": 60000.0 / beat_length_ms,
            })

    hit_objects.sort(key=lambda item: item["time"])
    redlines.sort(key=lambda item: item["time"])

    return {
        "path": osu_path,
        "hit_objects": hit_objects,
        "object_times": np.asarray(
            [item["time"] for item in hit_objects],
            dtype=float,
        ),
        "redlines": redlines,
        "redline_times": np.asarray(
            [item["time"] for item in redlines],
            dtype=float,
        ),
        "redline_bpms": np.asarray(
            [item["bpm"] for item in redlines],
            dtype=float,
        ),
    }


def score_objects_against_sections(
    object_times: np.ndarray,
    predicted_sections: list[dict],
    subdivisions: tuple[int, ...] = (1, 2, 3, 4),
) -> dict:
    """
    Score mapped object starts against audio-only predicted grids.

    The .osu data is evaluation-only and does not alter the prediction.
    """
    if not subdivisions or any(value <= 0 for value in subdivisions):
        raise ValueError("subdivisions must contain positive integers.")

    object_times = np.asarray(object_times, dtype=float)

    errors_seconds = np.full(len(object_times), np.nan, dtype=float)
    section_indices = np.full(len(object_times), -1, dtype=int)
    chosen_subdivisions = np.full(len(object_times), -1, dtype=int)

    for section_index, section in enumerate(predicted_sections):
        start_time = float(section["start_time"])
        end_time = float(section["end_time"])
        bpm = float(section["bpm"])
        phase = float(section.get("phase", section.get("offset", 0.0)))

        if bpm <= 0 or end_time < start_time:
            continue

        is_last = section_index == len(predicted_sections) - 1
        if is_last:
            mask = (
                (object_times >= start_time)
                & (object_times <= end_time)
            )
        else:
            mask = (
                (object_times >= start_time)
                & (object_times < end_time)
            )

        for object_index in np.flatnonzero(mask):
            object_time = object_times[object_index]
            best_error = np.inf
            best_subdivision = -1

            for subdivision in subdivisions:
                step = (60.0 / bpm) / subdivision
                grid_index = np.round((object_time - phase) / step)
                predicted_time = phase + grid_index * step
                error = object_time - predicted_time

                if abs(error) < abs(best_error):
                    best_error = float(error)
                    best_subdivision = int(subdivision)

            errors_seconds[object_index] = best_error
            section_indices[object_index] = section_index
            chosen_subdivisions[object_index] = best_subdivision

    valid_mask = np.isfinite(errors_seconds)
    absolute_errors_ms = np.abs(errors_seconds[valid_mask]) * 1000.0

    def safe_stat(function, default=np.nan):
        return float(function(absolute_errors_ms)) if len(absolute_errors_ms) else default

    return {
        "errors_seconds": errors_seconds,
        "errors_ms": errors_seconds * 1000.0,
        "absolute_errors_ms": absolute_errors_ms,
        "section_indices": section_indices,
        "subdivisions": chosen_subdivisions,
        "valid_mask": valid_mask,
        "object_count": int(len(object_times)),
        "scored_count": int(np.sum(valid_mask)),
        "mean_error_ms": safe_stat(np.mean),
        "median_error_ms": safe_stat(np.median),
        "percentile_90_ms": safe_stat(lambda values: np.percentile(values, 90)),
        "percentile_95_ms": safe_stat(lambda values: np.percentile(values, 95)),
        "maximum_error_ms": safe_stat(np.max),
    }


def shift_predicted_sections(
    sections: list[dict],
    shift_seconds: float,
) -> list[dict]:
    """Return a copy whose grid phase/offset is shifted for diagnosis."""
    shifted = deepcopy(sections)

    for section in shifted:
        if "phase" in section:
            section["phase"] = float(section["phase"]) + shift_seconds
        if "offset" in section:
            section["offset"] = float(section["offset"]) + shift_seconds

    return shifted


def find_diagnostic_global_shift(
    object_times: np.ndarray,
    predicted_sections: list[dict],
    *,
    subdivisions: tuple[int, ...] = (1, 2, 3, 4),
    minimum_shift_ms: float = -100.0,
    maximum_shift_ms: float = 100.0,
    step_ms: float = 0.5,
) -> dict:
    """
    Find the phase shift minimizing median object error.

    This is an evaluation diagnostic, not part of audio inference.
    """
    if step_ms <= 0:
        raise ValueError("step_ms must be positive.")

    shift_values_ms = np.arange(
        minimum_shift_ms,
        maximum_shift_ms + step_ms / 2.0,
        step_ms,
    )

    rows: list[dict] = []

    for shift_ms in shift_values_ms:
        shifted_sections = shift_predicted_sections(
            predicted_sections,
            shift_seconds=float(shift_ms) / 1000.0,
        )

        score = score_objects_against_sections(
            object_times,
            shifted_sections,
            subdivisions=subdivisions,
        )

        rows.append({
            "shift_ms": float(shift_ms),
            "median_ms": float(score["median_error_ms"]),
            "p95_ms": float(score["percentile_95_ms"]),
        })

    valid_rows = [
        row for row in rows
        if np.isfinite(row["median_ms"])
    ]

    if not valid_rows:
        raise ValueError("No objects could be scored against the sections.")

    best = min(valid_rows, key=lambda row: row["median_ms"])

    return {
        "best_shift_ms": best["shift_ms"],
        "best_median_ms": best["median_ms"],
        "best_p95_ms": best["p95_ms"],
        "rows": rows,
    }


def compare_redlines(
    ground_truth_times: np.ndarray,
    ground_truth_bpms: np.ndarray,
    detected_times: np.ndarray,
    detected_bpms: np.ndarray,
    *,
    time_tolerance: float = 0.300,
) -> dict:
    """Greedily match detected and mapped redlines one-to-one by time."""
    ground_truth_times = np.asarray(ground_truth_times, dtype=float)
    ground_truth_bpms = np.asarray(ground_truth_bpms, dtype=float)
    detected_times = np.asarray(detected_times, dtype=float)
    detected_bpms = np.asarray(detected_bpms, dtype=float)

    possible_matches: list[tuple[float, int, int]] = []

    for truth_index, truth_time in enumerate(ground_truth_times):
        for detected_index, detected_time in enumerate(detected_times):
            time_error = abs(float(detected_time - truth_time))
            if time_error <= time_tolerance:
                possible_matches.append(
                    (time_error, truth_index, detected_index)
                )

    possible_matches.sort()

    used_truth: set[int] = set()
    used_detected: set[int] = set()
    matches: list[dict] = []

    for time_error, truth_index, detected_index in possible_matches:
        if truth_index in used_truth or detected_index in used_detected:
            continue

        used_truth.add(truth_index)
        used_detected.add(detected_index)

        matches.append({
            "truth_index": truth_index,
            "detected_index": detected_index,
            "truth_time": float(ground_truth_times[truth_index]),
            "detected_time": float(detected_times[detected_index]),
            "time_error_ms": float(time_error * 1000.0),
            "truth_bpm": float(ground_truth_bpms[truth_index]),
            "detected_bpm": float(detected_bpms[detected_index]),
            "bpm_error": float(
                detected_bpms[detected_index]
                - ground_truth_bpms[truth_index]
            ),
        })

    true_positives = len(matches)
    false_positives = len(detected_times) - true_positives
    false_negatives = len(ground_truth_times) - true_positives

    precision = true_positives / max(true_positives + false_positives, 1)
    recall = true_positives / max(true_positives + false_negatives, 1)
    f1 = (
        2.0 * precision * recall / max(precision + recall, 1e-12)
    )

    return {
        "matches": matches,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
    }


def summarize_object_errors(
    object_score: dict,
    thresholds_ms: tuple[int, ...] = (2, 5, 10, 20, 40),
) -> dict:
    absolute_errors_ms = np.asarray(
        object_score["absolute_errors_ms"],
        dtype=float,
    )

    if not len(absolute_errors_ms):
        return {"count": 0}

    summary = {
        "count": int(len(absolute_errors_ms)),
        "mean_ms": float(np.mean(absolute_errors_ms)),
        "median_ms": float(np.median(absolute_errors_ms)),
        "p90_ms": float(np.percentile(absolute_errors_ms, 90)),
        "p95_ms": float(np.percentile(absolute_errors_ms, 95)),
        "maximum_ms": float(np.max(absolute_errors_ms)),
    }

    for threshold_ms in thresholds_ms:
        summary[f"within_{threshold_ms}ms"] = float(
            np.mean(absolute_errors_ms <= threshold_ms)
        )

    return summary


def summarize_errors_by_section(
    object_times: np.ndarray,
    object_score: dict,
    predicted_sections: list[dict],
) -> list[dict]:
    errors_ms = np.asarray(object_score["errors_ms"], dtype=float)
    section_indices = np.asarray(
        object_score["section_indices"],
        dtype=int,
    )

    summaries: list[dict] = []

    for section_index, section in enumerate(predicted_sections):
        mask = (
            (section_indices == section_index)
            & np.isfinite(errors_ms)
        )

        times = np.asarray(object_times, dtype=float)[mask]
        errors = errors_ms[mask]

        if not len(errors):
            continue

        absolute_errors = np.abs(errors)

        if len(errors) >= 2 and np.ptp(times) > 0:
            slope, _ = np.polyfit(times, errors, deg=1)
        else:
            slope = np.nan

        summaries.append({
            "section_index": section_index,
            "start_time": float(section["start_time"]),
            "end_time": float(section["end_time"]),
            "bpm": float(section["bpm"]),
            "object_count": int(len(errors)),
            "median_signed_ms": float(np.median(errors)),
            "median_absolute_ms": float(np.median(absolute_errors)),
            "p95_absolute_ms": float(
                np.percentile(absolute_errors, 95)
            ),
            "residual_slope_ms_per_second": float(slope),
        })

    return summaries


def classify_section_quality(
    section_summary: dict,
    *,
    minimum_objects: int = 3,
    good_median_ms: float = 10.0,
    acceptable_median_ms: float = 20.0,
    good_slope_ms_per_second: float = 3.0,
    acceptable_slope_ms_per_second: float = 8.0,
) -> str:
    if section_summary["object_count"] < minimum_objects:
        return "insufficient evidence"

    median_error = float(section_summary["median_absolute_ms"])
    slope = float(section_summary["residual_slope_ms_per_second"])
    absolute_slope = abs(slope) if np.isfinite(slope) else np.inf

    if (
        median_error <= good_median_ms
        and absolute_slope <= good_slope_ms_per_second
    ):
        return "good"

    if (
        median_error <= acceptable_median_ms
        and absolute_slope <= acceptable_slope_ms_per_second
    ):
        return "acceptable"

    return "bad"



def evaluate_against_osu(
    result: dict,
    osu_path: Path | str,
    *,
    subdivisions: tuple[int, ...] = (1, 2, 3, 4),
    redline_time_tolerance: float = 0.300,
    shift_range_ms: tuple[float, float] = (-100.0, 100.0),
    shift_step_ms: float = 0.5,
) -> dict:
    """Run evaluation without feeding any .osu information into prediction."""
    osu_data = parse_osu_file(osu_path)
    raw_object_score = score_objects_against_sections(
        osu_data["object_times"], result["sections"], subdivisions=subdivisions
    )
    shift_result = find_diagnostic_global_shift(
        osu_data["object_times"],
        result["sections"],
        subdivisions=subdivisions,
        minimum_shift_ms=shift_range_ms[0],
        maximum_shift_ms=shift_range_ms[1],
        step_ms=shift_step_ms,
    )
    aligned_sections = shift_predicted_sections(
        result["sections"], shift_result["best_shift_ms"] / 1000.0
    )
    aligned_object_score = score_objects_against_sections(
        osu_data["object_times"], aligned_sections, subdivisions=subdivisions
    )

    predicted_boundary_sections = list(result["sections"])[1:]
    truth_boundary_times = osu_data["redline_times"][1:]
    truth_boundary_bpms = osu_data["redline_bpms"][1:]
    boundary_comparison = compare_redlines(
        truth_boundary_times,
        truth_boundary_bpms,
        np.asarray([section["start_time"] for section in predicted_boundary_sections], dtype=float),
        np.asarray([section["bpm"] for section in predicted_boundary_sections], dtype=float),
        time_tolerance=redline_time_tolerance,
    )

    initial_anchor = None
    if len(osu_data["redline_times"]) and result["sections"]:
        truth_time = float(osu_data["redline_times"][0])
        truth_bpm = float(osu_data["redline_bpms"][0])
        first = result["sections"][0]
        period = float(first["period"])
        phase = float(first.get("phase", first.get("offset", 0.0)))
        index = np.round((truth_time - phase) / period)
        predicted_time = float(phase + index * period)
        initial_anchor = {
            "truth_time": truth_time,
            "predicted_grid_time": predicted_time,
            "time_error_ms": float((predicted_time - truth_time) * 1000.0),
            "absolute_time_error_ms": float(abs(predicted_time - truth_time) * 1000.0),
            "truth_bpm": truth_bpm,
            "predicted_bpm": float(first["bpm"]),
            "bpm_error": float(first["bpm"] - truth_bpm),
        }

    section_summary = summarize_errors_by_section(
        osu_data["object_times"], aligned_object_score, aligned_sections
    )
    for row in section_summary:
        row["quality"] = classify_section_quality(row)

    return {
        "osu_data": osu_data,
        "raw_object_score": raw_object_score,
        "shift_result": shift_result,
        "aligned_sections": aligned_sections,
        "aligned_object_score": aligned_object_score,
        "object_summary": summarize_object_errors(aligned_object_score),
        "redline_comparison": boundary_comparison,
        "boundary_comparison": boundary_comparison,
        "initial_anchor": initial_anchor,
        "section_summary": section_summary,
    }


def print_evaluation_summary(evaluation: dict) -> None:
    osu_data = evaluation["osu_data"]
    raw_score = evaluation["raw_object_score"]
    aligned_score = evaluation["aligned_object_score"]
    shift = evaluation["shift_result"]
    summary = evaluation["object_summary"]
    boundaries = evaluation["boundary_comparison"]

    print(f".osu file: {osu_data['path']}")
    print(f"Hit objects: {len(osu_data['hit_objects'])}")
    print(f"Mapped redlines: {len(osu_data['redlines'])}")
    print(f"Objects scored: {aligned_score['scored_count']} / {aligned_score['object_count']}")
    print()
    print(f"Raw median object error: {raw_score['median_error_ms']:.3f} ms")
    print(f"Diagnostic global shift: {shift['best_shift_ms']:+.3f} ms")
    print(f"Aligned median object error: {aligned_score['median_error_ms']:.3f} ms")
    print(f"Aligned p95 object error: {aligned_score['percentile_95_ms']:.3f} ms")
    print()
    for threshold in (2, 5, 10, 20, 40):
        key = f"within_{threshold}ms"
        if key in summary:
            print(f"Objects within {threshold:2d} ms: {100.0 * summary[key]:5.1f}%")
    print()
    anchor = evaluation.get("initial_anchor")
    if anchor is not None:
        print(
            "Initial anchor absolute time error: "
            f"{anchor['absolute_time_error_ms']:.3f} ms; "
            f"BPM error: {anchor['bpm_error']:+.3f}"
        )
    print(
        "Boundary precision / recall / F1: "
        f"{boundaries['precision']:.3f} / "
        f"{boundaries['recall']:.3f} / "
        f"{boundaries['f1']:.3f}"
    )

def print_section_diagnostics(evaluation: dict) -> None:
    for row in evaluation["section_summary"]:
        slope = row["residual_slope_ms_per_second"]
        slope_text = (
            f"{slope:+7.2f}"
            if np.isfinite(slope)
            else "    n/a"
        )

        print(
            f"Section {row['section_index']:2d} | "
            f"{row['start_time']:6.2f}–{row['end_time']:6.2f}s | "
            f"{row['bpm']:7.3f} BPM | "
            f"n={row['object_count']:3d} | "
            f"median={row['median_absolute_ms']:6.2f} ms | "
            f"p95={row['p95_absolute_ms']:6.2f} ms | "
            f"slope={slope_text} ms/s | "
            f"{row['quality']}"
        )


def plot_evaluation(evaluation: dict) -> None:
    """Plot shift objective and aligned object residuals."""
    shift_rows = evaluation["shift_result"]["rows"]
    best_shift = evaluation["shift_result"]["best_shift_ms"]

    plt.figure(figsize=(12, 4))
    plt.plot(
        [row["shift_ms"] for row in shift_rows],
        [row["median_ms"] for row in shift_rows],
    )
    plt.axvline(best_shift, linestyle="--")
    plt.xlabel("Global grid shift (ms)")
    plt.ylabel("Median absolute object error (ms)")
    plt.title("Diagnostic global phase alignment")
    plt.show()

    osu_data = evaluation["osu_data"]
    score = evaluation["aligned_object_score"]
    sections = evaluation["aligned_sections"]
    valid = score["valid_mask"]

    plt.figure(figsize=(18, 5))
    plt.scatter(
        osu_data["object_times"][valid],
        score["errors_ms"][valid],
        s=15,
    )
    plt.axhline(0, linestyle="--")

    for section in sections[1:]:
        plt.axvline(
            section["start_time"],
            linestyle=":",
            alpha=0.5,
        )

    plt.xlabel("Object time (seconds)")
    plt.ylabel("Nearest-grid error (ms)")
    plt.title("Audio-only predicted grids versus mapped objects")
    plt.show()


__all__ = [
    "parse_osu_file",
    "score_objects_against_sections",
    "shift_predicted_sections",
    "find_diagnostic_global_shift",
    "compare_redlines",
    "summarize_object_errors",
    "summarize_errors_by_section",
    "classify_section_quality",
    "evaluate_against_osu",
    "print_evaluation_summary",
    "print_section_diagnostics",
    "plot_evaluation",
]


# ---------------------------------------------------------------------------
# v0.8 override: faster evaluation figures.
# ---------------------------------------------------------------------------

def plot_evaluation(
    evaluation: dict,
    *,
    maximum_points: int = 12000,
) -> None:
    """
    Plot evaluation diagnostics efficiently.

    Section boundaries are drawn in one Matplotlib collection rather than one
    artist per boundary. Large residual sets are deterministically downsampled.
    """
    shift_rows = evaluation["shift_result"]["rows"]
    best_shift = evaluation["shift_result"]["best_shift_ms"]

    figure, axis = plt.subplots(figsize=(12, 4))
    axis.plot(
        [row["shift_ms"] for row in shift_rows],
        [row["median_ms"] for row in shift_rows],
    )
    axis.axvline(best_shift, linestyle="--")
    axis.set_xlabel("Global grid shift (ms)")
    axis.set_ylabel("Median absolute object error (ms)")
    axis.set_title("Diagnostic global phase alignment")
    figure.tight_layout()
    plt.show()
    plt.close(figure)

    osu_data = evaluation["osu_data"]
    score = evaluation["aligned_object_score"]
    sections = evaluation["aligned_sections"]
    valid = np.asarray(score["valid_mask"], dtype=bool)

    object_times = np.asarray(
        osu_data["object_times"][valid],
        dtype=float,
    )
    errors_ms = np.asarray(
        score["errors_ms"][valid],
        dtype=float,
    )

    if len(object_times) > maximum_points:
        stride = int(np.ceil(len(object_times) / maximum_points))
        object_times = object_times[::stride]
        errors_ms = errors_ms[::stride]

    figure, axis = plt.subplots(figsize=(18, 5))
    axis.scatter(
        object_times,
        errors_ms,
        s=10,
        rasterized=True,
    )
    axis.axhline(0, linestyle="--")

    boundaries = np.asarray([
        float(section["start_time"])
        for section in sections[1:]
    ])
    if len(boundaries):
        y_min, y_max = axis.get_ylim()
        axis.vlines(
            boundaries,
            y_min,
            y_max,
            linestyles=":",
            alpha=0.45,
            linewidth=0.8,
        )
        axis.set_ylim(y_min, y_max)

    axis.set_xlabel("Object time (seconds)")
    axis.set_ylabel("Nearest-grid error (ms)")
    axis.set_title("Audio-only predicted grids versus mapped objects")
    figure.tight_layout()
    plt.show()
    plt.close(figure)
