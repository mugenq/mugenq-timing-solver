"""Optional Qt UI regression: runs on Windows with the app dependencies.

The normal numerical engine tests run even without Qt. This test uses an
'offscreen' QPA platform so a CI worker does not need an actual monitor.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
pytest.importorskip('PySide6')
pytest.importorskip('miniaudio')
from PySide6.QtWidgets import QApplication
from beatsolver_timeline import BeatSolverWindow


def test_resize_docks_and_node_recenter():
    app = QApplication.instance() or QApplication([])
    main = BeatSolverWindow()
    try:
        main.resize(1250, 780)
        main.show()
        app.processEvents()
        assert main.graph_dock.isVisible()
        assert main.layer_dock.isVisible()
        main.graph_panel.canvas.focus_start()
        source = next(x for x in main.graph.nodes.values() if x.type_id == 'audio.source')
        assert source.id in main.graph_panel.scene.items_by_id

        main.resize(820, 590)
        app.processEvents()
        assert not main.graph_dock.isVisible()
        assert not main.layer_dock.isVisible()
        assert main.timeline.isVisible()
        assert main.transport_scroll.isVisible()

        main.nodes_btn.click()
        app.processEvents()
        assert main.graph_dock.isVisible()
        main.nodes_btn.click()
        app.processEvents()
        assert not main.graph_dock.isVisible()

        main.resize(1250, 780)
        app.processEvents()
        # A dock explicitly hidden by the user must stay hidden.
        assert not main.graph_dock.isVisible()
        assert main.layer_dock.isVisible()
    finally:
        main.close()
        app.processEvents()
