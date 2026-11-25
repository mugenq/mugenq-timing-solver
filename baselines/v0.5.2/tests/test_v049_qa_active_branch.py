"""QA harness must inspect the selected output branch, not first spectrum."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

APP=Path(__file__).resolve().parents[1]
QA=APP.parent/'qa'/'verify_v0410_extended.py'
sys.path.insert(0,str(APP))
from node_engine import default_registry, default_spectral_graph


def test_extended_qa_selects_only_spectrogram_ancestor_of_active_output():
    assert QA.exists()
    module_spec=spec_from_file_location('v049_extended_qa_test',QA)
    module=module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    graph=default_spectral_graph(default_registry())
    default=next(n for n in graph.nodes.values() if n.type_id=='signal.spectrogram')
    output=next(n for n in graph.nodes.values() if n.type_id=='candidates.output')
    other=graph.add('signal.spectrogram',0,500)
    assert module.selected_spectrogram_node(graph,output.id) is default
    assert module.selected_spectrogram_node(graph,output.id) is not other
