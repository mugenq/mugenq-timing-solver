"""Qt-independent checks of the actual application worker-slot control flow.

These execute exact method bodies extracted from source with inert stand-ins.
The separate Qt tests verify QObject sender identity and queued delivery.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

SOURCE=Path(__file__).resolve().parents[1]/'beatsolver_timeline.py'


class _Timer:
    callbacks=[]

    @staticmethod
    def singleShot(delay,fn):
        assert delay==0
        _Timer.callbacks.append(fn)


def _method(name):
    tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
    main=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='BeatSolverWindow')
    node=next(node for node in main.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name==name)
    node.decorator_list=[]
    program=ast.Module(body=[node],type_ignores=[])
    ast.fix_missing_locations(program)
    env={'QTimer':_Timer, 'deferred_run_is_current':lambda old,new,has_audio=True:old == new and has_audio}
    exec(compile(program,str(SOURCE),'exec'),env)
    return env[name]


class _Worker:
    def __init__(self):self.deletion_requested=False
    def deleteLater(self):self.deletion_requested=True


class _Window:
    def __init__(self,active,sender):
        self.candidate_worker=active
        self._sender=sender
        self.graph_panel=SimpleNamespace(set_busy=lambda state:self.busy_calls.append(state))
        self.busy_calls=[]
        self.timeline=SimpleNamespace(has_audio=True)
        self._queued_graph_output=None
        self._queued_graph_generation=None
        self._graph_request_generation=0
        self._active_graph_generation=None
        self._active_graph_snapshot=None
        self._active_graph_output_id=None
        self._closing=False
        self.started=[]
    def sender(self):return self._sender
    def run_graph(self,output,**kwargs):self.started.append(output)


def test_late_finished_cleans_only_old_emitter_and_preserves_new_worker():
    finished=_method('graph_thread_finished')
    old,current=_Worker(),_Worker()
    win=_Window(current,old)
    finished(win)
    assert old.deletion_requested
    assert not current.deletion_requested
    assert win.candidate_worker is current
    assert win.busy_calls==[]


def test_current_finished_clears_state_and_schedules_queued_latest_run():
    finished=_method('graph_thread_finished')
    worker=_Worker()
    win=_Window(worker,worker)
    win._queued_graph_output='preview-new'
    win._queued_graph_generation=1
    win._graph_request_generation=1
    _Timer.callbacks=[]
    finished(win)
    assert worker.deletion_requested
    assert win.candidate_worker is None
    assert win._queued_graph_output is None
    assert win.busy_calls==[False]
    assert len(_Timer.callbacks)==1
    _Timer.callbacks.pop()()
    assert win.started==['preview-new']


def test_no_sender_never_disposes_active_worker():
    finished=_method('graph_thread_finished')
    current=_Worker()
    win=_Window(current,None)
    finished(win)
    assert win.candidate_worker is current
    assert not current.deletion_requested
    assert win.busy_calls==[]


def test_run_request_queues_while_thread_has_finished_but_cleanup_is_pending():
    run=_method('run_graph')
    old=_Worker()
    win=_Window(old,old)
    win.statusBar=lambda:SimpleNamespace(showMessage=lambda *args:None)
    win.timeline.has_audio=True
    win.graph=SimpleNamespace(nodes={'next-output':object()})
    run(win,'next-output')
    assert win._queued_graph_output=='next-output'
    assert win.candidate_worker is old
    assert not old.deletion_requested
