"""Pure graph-selection and deferred-run invariants (no Qt dependency).

An output may have multiple upstream typed spectra. The viewer selects one
contributing *node*, never the last computed STFT or an unrelated canvas node.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SpectralSource:
    node_id: str
    signature: tuple[str, float, int]
    label: str


def contributing_spectra(graph, output_id, rate):
    """Typed spectrogram nodes reachable upstream, stable graph insertion order."""
    if not output_id or output_id not in graph.nodes or not rate:
        return ()
    incoming = {}
    for edge in graph.connections:
        incoming.setdefault(edge.target, []).append(edge.source)
    seen, todo = set(), [output_id]
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        todo.extend(incoming.get(current, ()))
    matches = []
    for node in graph.nodes.values():
        if node.id not in seen or node.type_id != 'signal.spectrogram':
            continue
        params = node.params
        try:
            window = float(params.get('window_ms', 46.))
            hop = float(params.get('hop_ms', 6.))
            mode = 'exact' if int(params.get('exact_window', 1)) else 'legacy_fft'
        except (ValueError, TypeError, OverflowError):
            continue
        if not (0 < window < float('inf') and 0 < hop < float('inf')):
            continue
        signature = (mode, round(window, 4), max(1, round(rate * hop / 1000)))
        label = f"{mode.replace('_', ' ')} · {window:g} ms · {str(node.id)[:8]}"
        matches.append(SpectralSource(node.id, signature, label))
    return tuple(matches)


def choose_spectrum(graph, output_id, rate, preferred_id=None):
    """Resolve explicit user selection, else the first contributing source."""
    options = contributing_spectra(graph, output_id, rate)
    if preferred_id is not None:
        for source in options:
            if source.node_id == preferred_id:
                return source
    return options[0] if options else None


def deferred_run_is_current(deferred_generation, latest_generation, has_audio=True):
    """A deferred callback cannot re-submit work older than a manual request."""
    return bool(has_audio and deferred_generation is not None
                and deferred_generation == latest_generation)
