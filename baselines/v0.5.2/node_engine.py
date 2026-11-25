"""Typed, extensible, non-destructive candidate pipeline for BeatSolver.

Third-party plugins are *Python code* and can execute with your permissions.
Load only plugins you trust. Execution is kept out of the GUI thread by the app.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import importlib.util
import math
import json
import csv
import time
import uuid

import numpy as np

AUDIO = "audio"
CURVE = "curve"
CANDIDATES = "candidates"
SPECTROGRAM = "spectrogram"
PORT_COLORS = {AUDIO: "#87c4dc", CURVE: "#b19bf5", CANDIDATES: "#edbf68",
               SPECTROGRAM: "#65c6ac"}


@dataclass(frozen=True)
class AudioData:
    path: str
    duration: float
    low: np.ndarray  # Audio-envelope minima, not raw/stereo PCM.
    high: np.ndarray
    bin_seconds: float | None = None  # Exact interval of each envelope bin.
    sample_rate: int | None = None  # Native decoded PCM rate when available.
    pcm: np.ndarray | None = None  # Optional shared, native-rate mono PCM; never modified.
    spectral_features: Any = None  # Optional cached spectral analysis for the same PCM.
    spectral_cache: Any = None  # SpectralCache, or legacy dict for plugins.


@dataclass(frozen=True)
class Curve:
    values: np.ndarray
    duration: float
    start_time: float = 0.0  # Timestamp of the first sample (seconds).
    latency_s: float = 0.0  # Reported algorithmic latency, for inspection.
    sample_step_s: float | None = None  # Bin start-to-start spacing; optional for plugins.
    metadata: dict[str, Any] = field(default_factory=dict)  # Optional immutable-by-convention evidence.

    def time_at(self, index: float) -> float:
        """Use exact bin timing when known; otherwise preserve legacy semantics."""
        step = (self.sample_step_s if self.sample_step_s is not None
                else self.duration / max(1, len(self.values) - 1))
        return self.start_time + float(index) * step

    def index_at(self, seconds: float) -> float:
        step = (self.sample_step_s if self.sample_step_s is not None
                else self.duration / max(1, len(self.values) - 1))
        return (seconds - self.start_time) / step if step > 0 else 0.0


@dataclass(frozen=True)
class CandidateSet:
    times: np.ndarray
    scores: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: tuple[dict[str, Any], ...] = ()  # One entry per candidate; empty for legacy plugins.

    def __post_init__(self):
        if self.provenance and len(self.provenance) != len(self.times):
            raise ValueError("candidate provenance must align one-to-one with candidate times")
        if len(self.times) != len(self.scores):
            raise ValueError("candidate times and scores must have the same length")
        if (not np.all(np.isfinite(self.times)) or not np.all(np.isfinite(self.scores))):
            raise ValueError("candidate timestamps and scores must be finite")
        if np.any(self.times < 0):
            raise ValueError("candidate timestamps must be non-negative")
        if np.any(np.diff(self.times) < 0):
            raise ValueError("candidate timestamps must be sorted in ascending order")


@dataclass(frozen=True)
class SpectrogramData:
    """Typed readonly view of one shared spectral calculation (never RGB pixels)."""
    features: Any
    audio_path: str = ''

    @property
    def center_samples(self):
        return self.features.center_samples

    @property
    def magnitudes(self):
        return self.features.harmonic

    @property
    def time_step_s(self):
        return self.features.step_s

    @property
    def metadata(self):
        f = self.features
        return dict(audio_path=self.audio_path, rate=f.rate, pcm_frames=f.frames,
                    representation_version=f.representation_version,
                    requested_window_ms=f.window_ms, actual_window_ms=f.actual_window_ms,
                    window_samples=f.window_samples, fft_samples=f.fft_samples,
                    hop_samples=f.hop_samples, window_mode=f.window_mode,
                    sample_convention='centered window; no onset time correction',
                    band_count=len(f.frequency_hz))


def _subset_candidates(source: CandidateSet, selected, step: str) -> CandidateSet:
    selected = np.asarray(selected)
    if selected.dtype == bool:
        selected = np.flatnonzero(selected)
    selected = selected.astype(int)
    prov = tuple(dict(source.provenance[int(i)],
                      transforms=list(source.provenance[int(i)].get('transforms', [])) + [step])
                 for i in selected) if source.provenance else ()
    return CandidateSet(source.times[selected].copy(), source.scores[selected].copy(),
                        dict(source.metadata), prov)


def export_candidate_evidence_csv(candidates: CandidateSet, filename):
    """Opt-in research export of raw Preview evidence without modifying Current."""
    with open(filename, 'w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['time_seconds','raw_score','metadata_json','provenance_json'])
        for i, (t, score) in enumerate(zip(candidates.times, candidates.scores)):
            provenance = candidates.provenance[i] if candidates.provenance else {}
            writer.writerow([f'{float(t):.9f}', f'{float(score):.9g}',
                             json.dumps(candidates.metadata, sort_keys=True, default=str),
                             json.dumps(provenance, sort_keys=True, default=str)])


@dataclass(frozen=True)
class Port:
    name: str
    kind: str


@dataclass(frozen=True)
class Parameter:
    name: str
    default: float | int
    minimum: float
    maximum: float
    step: float = 1.0
    decimals: int = 0


@dataclass(frozen=True)
class NodeDefinition:
    type_id: str
    label: str
    inputs: tuple[Port, ...]
    outputs: tuple[Port, ...]
    parameters: tuple[Parameter, ...]
    process: Callable[[dict[str, Any], dict[str, float | int], AudioData], dict[str, Any]]
    description: str = ""


@dataclass
class GraphNode:
    id: str
    type_id: str
    x: float
    y: float
    params: dict[str, float | int] = field(default_factory=dict)


@dataclass(frozen=True)
class Connection:
    source: str
    output: str
    target: str
    input: str


class GraphError(Exception):
    pass


class Registry:
    def __init__(self):
        self.defs: dict[str, NodeDefinition] = {}
        self.plugin_paths: list[str] = []

    def register(self, definition: NodeDefinition):
        if definition.type_id in self.defs:
            raise ValueError(f"Node type already registered: {definition.type_id}")
        if not definition.type_id or not definition.label:
            raise ValueError("Plugin node needs type_id and label")
        if any(p.kind not in PORT_COLORS for p in (*definition.inputs, *definition.outputs)):
            raise ValueError(f"Unsupported port type in {definition.type_id}")
        self.defs[definition.type_id] = definition

    def load_plugin(self, path: str):
        path = str(Path(path).resolve())
        if not path.lower().endswith(".py") or not Path(path).is_file():
            raise GraphError("Select a valid Python plugin (*.py)")
        name = "beatsolver_plugin_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise GraphError(f"Could not load plugin: {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # Deliberate execution of user-selected Python
        register = getattr(mod, "register", None)
        if not callable(register):
            raise GraphError("Plugin must define register(registry)")
        before = set(self.defs)
        try:
            register(self)
        except Exception:
            for key in set(self.defs) - before:
                del self.defs[key]
            raise
        if path not in self.plugin_paths:
            self.plugin_paths.append(path)
        return sorted(set(self.defs) - before)


def _smooth(x: np.ndarray, size: int) -> np.ndarray:
    size = max(1, int(size))
    if size <= 1 or x.size == 0:
        return x.astype(np.float32, copy=True)
    # Prefix sums give O(n) smoothing without huge temporary windows.
    prefix = np.concatenate(([0.0], np.cumsum(x, dtype=np.float64)))
    starts = np.maximum(0, np.arange(len(x)) - size // 2)
    ends = np.minimum(len(x), starts + size)
    return ((prefix[ends] - prefix[starts]) / np.maximum(1, ends - starts)).astype(np.float32)


def _energy(inputs, params, audio):
    wave: AudioData = inputs["audio"]
    # Peak envelope only: approximate loudness; not true FFT/RMS or onset strength.
    energy = np.maximum(np.abs(wave.low), np.abs(wave.high))
    bin_seconds = (wave.bin_seconds if wave.bin_seconds is not None
                   else wave.duration / max(1, len(energy)))
    bin_ms = bin_seconds * 1000
    smoothed = _smooth(energy, max(1, round(float(params["smoothing_ms"]) / bin_ms)))
    return {"curve": Curve(smoothed, wave.duration, sample_step_s=wave.bin_seconds,
                           metadata={'source': 'wave.energy', 'sample_step_s': bin_seconds})}


def _transients(inputs, params, audio):
    curve: Curve = inputs["curve"]
    x = np.asarray(curve.values, np.float32)
    bin_ms = (curve.sample_step_s * 1000 if curve.sample_step_s is not None
              else curve.duration * 1000 / max(1, len(x)))
    smooth = _smooth(x, max(1, round(float(params["smoothing_ms"]) / bin_ms)))
    lag = max(1, round(float(params["lag_ms"]) / bin_ms))
    result = np.zeros_like(smooth)
    if lag < len(smooth):
        result[lag:] = np.maximum(0.0, smooth[lag:] - smooth[:-lag])
    return {"curve": Curve(result, curve.duration, curve.start_time,
                           curve.latency_s, curve.sample_step_s,
                           dict(curve.metadata, latest_transform='signal.transients'))}


def _peaks(inputs, params, audio):
    curve: Curve = inputs["curve"]
    values = np.asarray(curve.values, dtype=np.float32)
    if len(values) < 3 or not np.any(values > 0):
        return {"candidates": CandidateSet(np.array([], dtype=np.float64), np.array([], dtype=np.float32))}
    positive = values[values > 0]
    percentile = max(0.0, min(100.0, float(params["percentile"])))
    threshold = np.percentile(positive, percentile)
    peaks = np.flatnonzero((values[1:-1] >= values[:-2]) &
                           (values[1:-1] > values[2:]) &
                           (values[1:-1] >= threshold)) + 1
    if peaks.size == 0:
        return {"candidates": CandidateSet(np.array([], dtype=np.float64), np.array([], dtype=np.float32))}
    max_score = max(1e-8, float(values[peaks].max()))
    gap_s = max(0.0, float(params["min_spacing_ms"]) / 1000.0)
    step = (curve.sample_step_s if curve.sample_step_s is not None
            else curve.duration / max(1, len(values) - 1))
    t = curve.start_time + peaks * step
    scores = (values[peaks] / max_score).astype(np.float32)
    # Prefer the loudest event when two nearby events compete.
    order = np.argsort(-scores, kind="stable")
    chosen: list[int] = []
    if gap_s > 0:
        import bisect
        accepted_times: list[float] = []
        for i in order:
            ti = float(t[i])
            ix = bisect.bisect_left(accepted_times, ti)
            if ((ix and ti - accepted_times[ix - 1] < gap_s) or
                    (ix < len(accepted_times) and accepted_times[ix] - ti < gap_s)):
                continue
            bisect.insort(accepted_times, ti)
            chosen.append(int(i))
    else:
        chosen = order.tolist()
    chosen = sorted(chosen, key=lambda i: t[i])
    times = t[chosen].astype(np.float64)
    scored = scores[chosen]
    evidence = tuple(dict(time_s=float(ti), curve_index=int(peaks[i]),
                          curve_time_s=float(curve.time_at(int(peaks[i]))),
                          raw_score=float(sc), score_semantics='uncalibrated relative peak height',
                          parent_curve=dict(curve.metadata), transforms=['signal.peaks'])
                     for i, (ti, sc) in zip(chosen, zip(times, scored)))
    return {"candidates": CandidateSet(times, scored,
                                     {'score_semantics': 'uncalibrated relative peak height',
                                      'curve': dict(curve.metadata)}, evidence)}


def _spacing(inputs, params, audio):
    c: CandidateSet = inputs["candidates"]
    if not len(c.times):
        return {"candidates": c}
    gap = float(params["min_spacing_ms"]) / 1000
    indices = np.argsort(-c.scores, kind="stable")
    import bisect
    sorted_times: list[float] = []
    picked: list[int] = []
    for i in indices:
        t = float(c.times[i]); at = bisect.bisect_left(sorted_times, t)
        if ((at and t - sorted_times[at-1] < gap) or
                (at < len(sorted_times) and sorted_times[at] - t < gap)):
            continue
        bisect.insort(sorted_times, t); picked.append(int(i))
    picked.sort(key=lambda i: c.times[i])
    return {"candidates": _subset_candidates(c, picked, 'candidates.spacing')}



def _burst_cleanup(inputs, params, audio):
    """Conservatively remove weak detections inside short candidate bursts.

    This node only consumes/returns CandidateSet. It cannot distinguish a
    piano trill from repeated detection of one sound without extra features,
    so competitors with similar scores survive. Original upstream data is
    unchanged; parallel Candidate Output nodes allow direct A/B comparisons.

    Clusters are bounded by BOTH consecutive gap and total span to prevent
    a long chain of fast notes from collapsing into one event.
    """
    source: CandidateSet = inputs["candidates"]
    count = len(source.times)
    if count <= 1:
        return {"candidates": source}
    gap = max(0.0, float(params["neighbor_gap_ms"])) * .001
    span = max(gap, float(params["max_burst_ms"]) * .001)
    # Keep ratios are relative to each burst maximum; 0.0 disables suppression.
    retention = max(0.0, min(1.0, float(params["weak_retention"])))
    # Treat ratio as a fraction of the strongest score; scores can be 0.
    # For a zero-score burst, keep everything (no evidence for suppression).
    keep = np.ones(count, dtype=bool)
    first = 0

    def finish(start: int, stop: int) -> None:
        scores = source.scores[start:stop]
        maximum = float(np.max(scores))
        if maximum <= 0 or stop-start < 2 or retention >= 1.0:
            return
        # Only suppress strictly weaker peaks. Ties never disappear.
        weak = (scores < maximum * retention) & (scores < maximum)
        keep[start:stop] = ~weak

    for i in range(1, count):
        if (source.times[i] - source.times[i-1] > gap or
                source.times[i] - source.times[first] > span):
            finish(first, i)
            first = i
    finish(first, count)
    return {"candidates": _subset_candidates(source, keep, 'candidates.burst_cleanup')}



def _require_pcm(signal: AudioData):
    if signal.pcm is None or signal.sample_rate is None:
        raise ValueError('Spectral processing requires retained native-rate PCM. '
                         'Use an audio shorter than the PCM cache limit.')


def _spectrogram_source(inputs, params, audio):
    from spectral_analysis import shared_spectral
    signal: AudioData = inputs['audio']
    _require_pcm(signal)
    # New typed Spectrogram defaults to a true physical Hann window, whereas
    # existing Audio->Curve nodes retain their legacy-compatible window.
    mode = 'exact' if int(params.get('exact_window', 1)) else 'legacy_fft'
    features = shared_spectral(signal.pcm, signal.sample_rate, cache=signal.spectral_cache,
                               hop_ms=float(params['hop_ms']), window_ms=float(params['window_ms']),
                               window_mode=mode)
    return {'spectrogram': SpectrogramData(features, signal.path)}


def _spectral_novelty_from_spectrum(inputs, params, audio):
    spectrum: SpectrogramData = inputs['spectrogram']
    features = spectrum.features
    values = (features.novelty * float(params['intensity'])).astype(np.float32)
    meta = dict(spectrum.metadata, source='signal.spectral_novelty',
                lookahead_s=features.actual_window_ms / 2000.,
                score_semantics='uncalibrated spectral change evidence')
    return {'curve': Curve(values, features.duration, 0., 0.,
                            features.step_s, meta)}


def _spectral_attack(inputs, params, audio):
    """Legacy Audio->Curve node remains numerically compatible with v0.4.6."""
    from spectral_analysis import shared_spectral
    signal: AudioData = inputs['audio']
    _require_pcm(signal)
    mode = 'exact' if int(params.get('exact_window', 0)) else 'legacy_fft'
    features = shared_spectral(signal.pcm, int(signal.sample_rate), cache=signal.spectral_cache,
                               hop_ms=float(params['hop_ms']), window_ms=float(params['window_ms']),
                               window_mode=mode)
    values = (features.novelty * float(params['intensity'])).astype(np.float32)
    meta = dict(source='signal.spectral_attack', requested_window_ms=features.window_ms,
                actual_window_ms=features.actual_window_ms,
                window_samples=features.window_samples, fft_samples=features.fft_samples,
                hop_samples=features.hop_samples, window_mode=features.window_mode,
                representation_version=features.representation_version,
                lookahead_s=features.actual_window_ms / 2000.,
                timestamp_convention='centered window; no automatic onset shift')
    return {'curve': Curve(values, signal.duration, 0., 0.,
                            features.step_s, meta)}


def _evidence_cleanup(inputs, params, audio):
    from spectral_analysis import evidence_cleanup
    c: CandidateSet = inputs['candidates']
    curve: Curve = inputs['attack_curve']
    if len(c.times)<2:
        return {'candidates':c}
    step=(curve.sample_step_s if curve.sample_step_s is not None
          else curve.duration / max(1,len(curve.values)-1))
    mask = evidence_cleanup(c.times,c.scores,curve.values,curve.start_time,step,
                            neighbor_gap_ms=float(params['neighbor_gap_ms']),
                            max_burst_ms=float(params['max_burst_ms']),
                            weak_retention=float(params['weak_retention']),
                            prominence=float(params['spectral_valley']),
                            search_ms=float(params['search_ms']))
    return {'candidates': _subset_candidates(c, mask, 'candidates.evidence_cleanup')}


def _merge(inputs, params, audio):
    """v0.4.6-compatible strongest-selected-event chaining, with full lineage.

    IMPORTANT: the tolerance compares against the *currently selected strongest*
    event, not the beginning of the cluster. Changing that anchor silently alters
    saved project's output for increasing-strength event chains.
    """
    a: CandidateSet = inputs["a"]
    b: CandidateSet = inputs["b"]
    times = np.concatenate([a.times, b.times])
    scores = np.concatenate([a.scores, b.scores])
    if not len(times):
        return {'candidates': CandidateSet(times, scores)}
    records = []
    for source in (a, b):
        for i, (timestamp, score) in enumerate(zip(source.times, source.scores)):
            records.append(dict(source.provenance[i]) if source.provenance else
                           {'time_s': float(timestamp), 'raw_score': float(score),
                            'origin': 'legacy plugin'})
    order = np.lexsort((-scores, times))
    tolerance = float(params['merge_within_ms']) / 1000.
    chosen = [int(order[0])]
    # Parents follow the historical selection groups, even when the strongest
    # event changes during iteration. No input candidate is mutated or discarded
    # from the provenance of the event that represents its group.
    groups = [[int(order[0])]]
    for raw_idx in order[1:]:
        idx = int(raw_idx)
        if float(times[idx] - times[chosen[-1]]) <= tolerance:
            groups[-1].append(idx)
            if scores[idx] > scores[chosen[-1]]:
                chosen[-1] = idx
        else:
            chosen.append(idx)
            groups.append([idx])
    evidence = tuple(dict(records[best],
                          transforms=list(records[best].get('transforms', [])) +
                          ['candidates.merge'],
                          parents=[records[i] for i in group])
                     for best, group in zip(chosen, groups))
    return {'candidates': CandidateSet(times[chosen].copy(), scores[chosen].copy(),
                   {'source': 'candidates.merge', 'parents': [a.metadata, b.metadata]},
                   evidence)}


def _passthrough(inputs, params, audio):
    return {"candidates": inputs["candidates"]}


def _audio_source(inputs, params, audio):
    return {"audio": audio}


def _sink(inputs, params, audio):
    return {}


def default_registry() -> Registry:
    r = Registry()
    defs = [
        NodeDefinition("audio.source", "Audio Source", (), (Port("audio", AUDIO),), (), _audio_source,
                       "Song path and a resolution-selectable min/max PCM envelope."),
        NodeDefinition("wave.energy", "Wave Energy", (Port("audio", AUDIO),), (Port("curve", CURVE),),
                       (Parameter("smoothing_ms", 8.0, 0.0, 200.0, 1.0, 1),), _energy,
                       "Peak amplitude envelope; cheap starting feature, not spectral onset detection."),
        NodeDefinition("signal.transients", "Energy Changes", (Port("curve", CURVE),), (Port("curve", CURVE),),
                       (Parameter("smoothing_ms", 5.0, 0.0, 150.0, 1.0, 1),
                        Parameter("lag_ms", 18.0, 2.0, 150.0, 1.0, 1)), _transients,
                       "Positive local change in energy, optionally smoothed."),
        NodeDefinition("signal.peaks", "Peak Picker", (Port("curve", CURVE),), (Port("candidates", CANDIDATES),),
                       (Parameter("percentile", 70.0, 0.0, 100.0, 1.0, 1),
                        Parameter("min_spacing_ms", 28.0, 0.0, 500.0, 1.0, 1)), _peaks,
                       "Find local curve peaks above an activity percentile."),
        NodeDefinition("candidates.spacing", "Min Spacing", (Port("candidates", CANDIDATES),),
                       (Port("candidates", CANDIDATES),),
                       (Parameter("min_spacing_ms", 60.0, 0.0, 1000.0, 1.0, 1),), _spacing,
                       "Retain stronger candidates when events occur too close together."),
        NodeDefinition("candidates.burst_cleanup", "Burst Cleanup",
                       (Port("candidates", CANDIDATES),), (Port("candidates", CANDIDATES),),
                       (Parameter("neighbor_gap_ms", 35.0, 0.0, 300.0, 1.0, 1),
                        Parameter("max_burst_ms", 110.0, 1.0, 500.0, 1.0, 1),
                        Parameter("weak_retention", 0.60, 0.0, 1.0, 0.05, 2)),
                       _burst_cleanup,
                       "Bounded onset bursts: suppress only candidates substantially weaker than "
                       "the strongest nearby event. Similar-strength candidates survive. "
                       "No timestamp is moved; upstream candidates are preserved."),
        NodeDefinition("signal.spectral_attack", "Spectral Attack Novelty",
                       (Port("audio", AUDIO),), (Port("curve", CURVE),),
                       (Parameter("window_ms", 46.0, 15.0, 160.0, 1.0, 1),
                        Parameter("hop_ms", 6.0, 2.0, 20.0, 0.5, 1),
                        Parameter("intensity", 1.0, 0.05, 10.0, 0.1, 2),
                        Parameter("exact_window", 0, 0, 1, 1, 0)),
                       _spectral_attack,
                       "Frequency-band rearticulation evidence from native PCM. "
                       "FFT centered windows can smear attack timestamps."),
        NodeDefinition("signal.spectrogram", "Shared Spectrogram",
                       (Port("audio", AUDIO),), (Port("spectrogram", SPECTROGRAM),),
                       (Parameter("window_ms", 46., 15., 160., 1., 1),
                        Parameter("hop_ms", 6., 1.5, 20., .5, 1),
                        Parameter("exact_window", 1, 0, 1, 1, 0)),
                       _spectrogram_source,
                       "Readonly numerical time-frequency evidence. Exact window=1 uses the requested "
                       "physical Hann length with FFT zero padding. 0 reproduces legacy power-of-two "
                       "windowing. This typed output can feed multiple plugins."),
        NodeDefinition("signal.spectral_novelty", "Novelty from Spectrogram",
                       (Port("spectrogram", SPECTROGRAM),), (Port("curve", CURVE),),
                       (Parameter("intensity", 1., .05, 10., .1, 2),),
                       _spectral_novelty_from_spectrum,
                       "Convert reusable spectral evidence into a Curve for the Peak Picker."),
        NodeDefinition("candidates.evidence_cleanup", "Evidence-Aware Cleanup",
                       (Port("candidates", CANDIDATES), Port("attack_curve", CURVE)),
                       (Port("candidates", CANDIDATES),),
                       (Parameter("neighbor_gap_ms", 35.0, 0.0, 300.0, 1., 1),
                        Parameter("max_burst_ms", 110.0, 1.0, 500.0, 1., 1),
                        Parameter("weak_retention", .65, 0.0, 1.0, .05, 2),
                        Parameter("spectral_valley", .35, 0.0, .95, .05, 2),
                        Parameter("search_ms", 12., 2., 60., 1., 1)),
                       _evidence_cleanup,
                       "Candidates + independent spectral attack evidence -> Candidates. "
                       "Preserves likely reattacks within bursts; does not move timestamps."),
        NodeDefinition("candidates.merge", "Merge Candidates", (Port("a", CANDIDATES), Port("b", CANDIDATES)),
                       (Port("candidates", CANDIDATES),),
                       (Parameter("merge_within_ms", 10.0, 0.0, 150.0, 1.0, 1),), _merge,
                       "Combine two candidate lists, merging events within a tolerance."),
        NodeDefinition("candidates.pass", "Pass Through", (Port("candidates", CANDIDATES),),
                       (Port("candidates", CANDIDATES),), (), _passthrough,
                       "Placeholder for custom processing branches."),
        NodeDefinition("candidates.output", "Candidate Output", (Port("candidates", CANDIDATES),),
                       (), (), _sink, "Choose this output in the graph panel to display its candidates."),
    ]
    for d in defs:
        r.register(d)
    return r


class Graph:
    def __init__(self, registry: Registry):
        self.registry = registry
        self.nodes: dict[str, GraphNode] = {}
        self.connections: list[Connection] = []

    def add(self, type_id: str, x=0, y=0, node_id=None) -> GraphNode:
        d = self.registry.defs.get(type_id)
        if d is None:
            raise GraphError(f"Unknown node type: {type_id}")
        uid = node_id or uuid.uuid4().hex[:10]
        if uid in self.nodes:
            raise GraphError("Duplicate node ID")
        n = GraphNode(uid, type_id, x, y, {p.name: p.default for p in d.parameters})
        self.nodes[uid] = n
        return n

    def disconnect_input(self, target: str, input_name: str):
        self.connections = [e for e in self.connections if (e.target, e.input) != (target, input_name)]

    def connect(self, source: str, output: str, target: str, input_name: str):
        if source == target:
            raise GraphError("Cannot connect a node to itself")
        if source not in self.nodes or target not in self.nodes:
            raise GraphError("Unknown node")
        src = self.registry.defs[self.nodes[source].type_id]
        dst = self.registry.defs[self.nodes[target].type_id]
        out = next((p for p in src.outputs if p.name == output), None)
        inp = next((p for p in dst.inputs if p.name == input_name), None)
        if out is None or inp is None:
            raise GraphError("Unknown port")
        if out.kind != inp.kind:
            raise GraphError(f"Incompatible ports: {out.kind} → {inp.kind}")
        trial = [e for e in self.connections if (e.target, e.input) != (target, input_name)]
        trial.append(Connection(source, output, target, input_name))
        # Evaluate reachability without allowing cycles.
        neighbors: dict[str, set[str]] = {}
        for e in trial:
            neighbors.setdefault(e.source, set()).add(e.target)
        pending = [target]; seen = set()
        while pending:
            current = pending.pop()
            if current == source:
                raise GraphError("Connection would create a cycle")
            if current not in seen:
                seen.add(current); pending.extend(neighbors.get(current, set()))
        self.connections = trial

    def delete(self, node_id: str):
        self.nodes.pop(node_id, None)
        self.connections = [e for e in self.connections if node_id not in (e.source, e.target)]

    def to_dict(self):
        return {"nodes": [{"id": n.id, "type": n.type_id, "x": n.x, "y": n.y,
                            "params": n.params} for n in self.nodes.values()],
                "connections": [vars(e) for e in self.connections]}

    @classmethod
    def from_dict(cls, registry: Registry, data: dict):
        graph = cls(registry)
        for item in data.get("nodes", []):
            n = graph.add(item["type"], float(item.get("x", 0)), float(item.get("y", 0)), item["id"])
            definition = registry.defs[n.type_id]
            for p in definition.parameters:
                if p.name in item.get("params", {}):
                    val = float(item["params"][p.name])
                    if not math.isfinite(val):
                        raise GraphError(f"Invalid {p.name}")
                    n.params[p.name] = max(p.minimum, min(p.maximum, val))
        for e in data.get("connections", []):
            graph.connect(e["source"], e["output"], e["target"], e["input"])
        return graph

    def run(self, audio: AudioData, output_node: str):
        """Backward-compatible API: final candidates plus execution metrics."""
        candidates, metrics, _ = self.run_with_intermediates(audio, output_node)
        return candidates, metrics

    def run_with_intermediates(self, audio: AudioData, output_node: str):
        """Return candidates, metrics, and evaluated node outputs.

        Cache contains only nodes feeding the selected Candidate Output. Values
        remain raw and time-aligned; display adjustments never enter plugins.
        """
        if output_node not in self.nodes or self.nodes[output_node].type_id != "candidates.output":
            raise GraphError("Select a Candidate Output node")
        cache: dict[str, dict[str, Any]] = {}
        metrics: list[tuple[str, str, str, int, float]] = []
        visiting: set[str] = set()

        def resolve(uid: str):
            if uid in cache:
                return cache[uid]
            if uid in visiting:
                raise GraphError("Cycle detected")
            visiting.add(uid)
            node = self.nodes[uid]
            definition = self.registry.defs[node.type_id]
            inputs = {}
            for port in definition.inputs:
                edge = next((e for e in self.connections if e.target == uid and e.input == port.name), None)
                if edge is None:
                    raise GraphError(f"Missing connection: {definition.label}.{port.name}")
                data = resolve(edge.source)
                if edge.output not in data:
                    raise GraphError(f"Missing output: {edge.source}.{edge.output}")
                inputs[port.name] = data[edge.output]
            started = time.perf_counter()
            try:
                result = definition.process(inputs, dict(node.params), audio)
            except Exception as exc:
                raise GraphError(f"{definition.label}: {exc}") from exc
            ms = (time.perf_counter() - started) * 1000
            if not isinstance(result, dict):
                raise GraphError(f"{definition.label} must return a dictionary")
            for port in definition.outputs:
                output = result.get(port.name)
                if output is None:
                    raise GraphError(f"{definition.label} did not return {port.name}")
                correct = {AUDIO: AudioData, CURVE: Curve, CANDIDATES: CandidateSet, SPECTROGRAM: SpectrogramData}[port.kind]
                if not isinstance(output, correct):
                    raise GraphError(f"{definition.label}.{port.name} must be {correct.__name__}")
                if isinstance(output, CandidateSet) and len(output.times):
                    if output.provenance:
                        entries = output.provenance
                    else:
                        # Legacy plugins often construct CandidateSet(times,scores)
                        # without preserving metadata. Recover lineage from exactly
                        # matching input timestamps when possible.
                        from bisect import bisect_left
                        upstream = [value for value in inputs.values()
                                    if isinstance(value, CandidateSet)]
                        rebuilt = []
                        for t, score in zip(output.times, output.scores):
                            parents = []
                            for src in upstream:
                                ix = bisect_left(src.times, t)
                                if ix < len(src.times) and abs(float(src.times[ix])-float(t)) < 1e-10:
                                    if src.provenance:
                                        parents.append(dict(src.provenance[ix]))
                                    else:
                                        parents.append({'time_s': float(t),
                                                        'raw_score': float(src.scores[ix])})
                            rebuilt.append(dict(time_s=float(t), raw_score=float(score),
                                                origin='legacy plugin' if upstream else 'generated',
                                                parents=parents))
                        entries = tuple(rebuilt)
                    result[port.name] = CandidateSet(output.times, output.scores,
                        dict(output.metadata), tuple(dict(item,
                          latest_node_id=uid, latest_node_type=definition.type_id,
                          last_params=dict(node.params)) for item in entries))
            count = 0
            out_type = "-"
            for p in definition.outputs:
                d = result[p.name]
                out_type = p.kind
                count = (len(d.times) if isinstance(d, CandidateSet) else
                         len(d.values) if isinstance(d, Curve) else
                         len(d.center_samples) if isinstance(d, SpectrogramData) else len(d.low))
            if definition.type_id == "candidates.output":
                count = len(inputs["candidates"].times)
                out_type = "candidates"
            metrics.append((uid, definition.label, out_type, count, round(ms, 2)))
            cache[uid] = result
            visiting.remove(uid)
            return result

        resolve(output_node)
        edge = next((e for e in self.connections if e.target == output_node and e.input == "candidates"), None)
        result: CandidateSet = cache[edge.source][edge.output]
        return result, metrics, cache


def starter_graph(registry: Registry) -> Graph:
    g = Graph(registry)
    ids = [g.add(t, i * 210, 30).id for i, t in enumerate(
        ("audio.source", "wave.energy", "signal.transients", "signal.peaks", "candidates.output"))]
    edges = [("audio", "audio"), ("curve", "curve"), ("curve", "curve"), ("candidates", "candidates")]
    for i, (out_port, in_port) in enumerate(edges):
        g.connect(ids[i], out_port, ids[i+1], in_port)
    return g


def default_spectral_graph(registry: Registry) -> Graph:
    """New unsaved sessions: spectral evidence -> picker -> reversible burst filter.

    Keep starter_graph unchanged for older projects, plugins and unit tests.
    The default preserves the v0.4.6 physical FFT window because the measured
    Hanatan intro recalls 41/45 versus 34/45 using the shorter exact window.
    No uncommitted generated event is written to Current.
    """
    graph = Graph(registry)
    kinds = ('audio.source', 'signal.spectrogram', 'signal.spectral_novelty',
             'signal.peaks', 'candidates.burst_cleanup', 'candidates.output')
    nodes = [graph.add(kind, i * 215, 45) for i, kind in enumerate(kinds)]
    nodes[1].params['exact_window'] = 0  # intentional legacy 85.33 ms @ 48 kHz
    nodes[3].params.update(percentile=60., min_spacing_ms=12.)
    for first, second, out_port, in_port in zip(
        nodes, nodes[1:], ('audio', 'spectrogram', 'curve', 'candidates', 'candidates'),
        ('audio', 'spectrogram', 'curve', 'candidates', 'candidates')):
        graph.connect(first.id, out_port, second.id, in_port)
    return graph
