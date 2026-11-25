"""QA runner ownership: test processes finish and timeouts never hang pytest."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile

APP = Path(__file__).resolve().parents[1]
QA = APP.parent/'qa'/'verify_beatsolver_v0410.py'
if not QA.exists():
    QA = APP.parent/'qa'/'verify_beatsolver_v049.py'


def load_runner():
    assert QA.is_file(), 'Versioned QA supervisor must be installed alongside the app'
    spec=importlib.util.spec_from_file_location('beatsolver_qa_supervisor_v0410',QA)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module


def test_run_supervisor_owns_timeout_and_normal_exits(tmp_path):
    qa=load_runner()
    normal=qa.subprocess_test('normal',[sys.executable,'-c','print("finished")'],
                              tmp_path,os.environ.copy(),8,tmp_path)
    assert normal.outcome=='PASS' and 'finished' in normal.output
    hanging=qa.subprocess_test('intentional hang',
                              [sys.executable,'-c','import time; time.sleep(120)'],
                              tmp_path,os.environ.copy(),.4,tmp_path)
    assert hanging.outcome=='FAIL'
    assert 'cleanup=completed' in hanging.detail
    assert 'Timed out' in hanging.detail
