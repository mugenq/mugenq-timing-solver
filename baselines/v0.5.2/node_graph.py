"""Qt graphics node canvas: typed ports, cables, drag nodes, parameters, run button."""
from __future__ import annotations

from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QTimer
from PySide6.QtGui import QColor, QBrush, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QGraphicsItem, QGraphicsPathItem,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QTabWidget, QVBoxLayout, QWidget,
)

from node_engine import Graph, GraphError, PORT_COLORS

NODE_WIDTH = 184
HEADER = 33
LINE = 23


class NodeItem(QGraphicsItem):
    def __init__(self, scene_owner, node_id):
        super().__init__()
        self.owner = scene_owner
        self.node_id = node_id
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        n = self.owner.graph.nodes[node_id]
        self.setPos(n.x, n.y)
        d = self.definition
        ports = max(len(d.inputs), len(d.outputs), 1)
        self.height = HEADER + ports * LINE + 12

    @property
    def definition(self):
        return self.owner.graph.registry.defs[self.owner.graph.nodes[self.node_id].type_id]

    def boundingRect(self):
        return QRectF(-9, -2, NODE_WIDTH + 18, self.height + 4)

    def port_at(self, pos: QPointF):
        y = pos.y()
        for side, ports in (('input', self.definition.inputs), ('output', self.definition.outputs)):
            x = 0 if side == 'input' else NODE_WIDTH
            if abs(pos.x()-x) <= 12:
                for i, port in enumerate(ports):
                    if abs(y - (HEADER + 14 + i*LINE)) <= 10:
                        return side, port.name, port.kind
        return None

    def port_center(self, direction, name):
        ports = self.definition.inputs if direction == 'input' else self.definition.outputs
        i = next((j for j,p in enumerate(ports) if p.name == name), 0)
        return self.mapToScene(QPointF(0 if direction == 'input' else NODE_WIDTH,
                                      HEADER + 14 + i*LINE))

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#68849a') if self.isSelected() else QColor('#405667'), 1.6))
        painter.setBrush(QColor('#1d2837'))
        painter.drawRoundedRect(QRectF(0, 0, NODE_WIDTH, self.height), 8, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#354b5d'))
        painter.drawRoundedRect(QRectF(1, 1, NODE_WIDTH-2, HEADER-1), 7, 7)
        painter.setPen(QColor('#edf4fc'))
        f = painter.font(); f.setPointSize(9); f.setBold(True); painter.setFont(f)
        painter.drawText(QRectF(12, 5, NODE_WIDTH-25, 23),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.definition.label)
        f.setBold(False); f.setPointSize(8); painter.setFont(f)
        for direction, ports in [('input', self.definition.inputs), ('output', self.definition.outputs)]:
            for i, port in enumerate(ports):
                y = HEADER + 14 + i * LINE
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(PORT_COLORS[port.kind]))
                painter.drawEllipse(QPointF(0 if direction=='input' else NODE_WIDTH, y), 5, 5)
                painter.setPen(QColor('#d5e2ed'))
                if direction == 'input':
                    painter.drawText(QRectF(13, y-10, 94, 20), Qt.AlignmentFlag.AlignVCenter, port.name)
                else:
                    painter.drawText(QRectF(NODE_WIDTH-110, y-10, 97, 20),
                                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, port.name)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self.owner:
            self.owner.update_cables()
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        n = self.owner.graph.nodes.get(self.node_id)
        if n and (n.x != self.pos().x() or n.y != self.pos().y()):
            n.x, n.y = self.pos().x(), self.pos().y()
            self.owner.graphChanged.emit()

    def contextMenuEvent(self, event):
        p = self.port_at(event.pos())
        menu = QMenu()
        if p and p[0] == 'input':
            act = menu.addAction(f'Disconnect input "{p[1]}"')
            selected = menu.exec(event.screenPos())
            if selected == act:
                self.owner.graph.disconnect_input(self.node_id, p[1])
                self.owner.update_cables()
                self.owner.graphChanged.emit()
            return
        remove = menu.addAction('Delete node')
        if menu.exec(event.screenPos()) == remove:
            self.owner.delete_node(self.node_id)


class GraphScene(QGraphicsScene):
    graphChanged = Signal()
    portMessage = Signal(str)

    def __init__(self, graph):
        super().__init__()
        self.graph = graph
        self.items_by_id: dict[str, NodeItem] = {}
        self.cables = []
        self.pending = None
        self.setSceneRect(-300, -300, 1700, 1050)
        self.reload()

    def reload(self):
        self.clear()
        self.cables.clear()
        self.items_by_id.clear()
        self.pending = None
        for n in self.graph.nodes.values():
            obj = NodeItem(self, n.id)
            self.addItem(obj)
            self.items_by_id[n.id] = obj
        self.update_cables()

    def update_cables(self):
        for item in self.cables:
            self.removeItem(item)
        self.cables = []
        for edge in self.graph.connections:
            src = self.items_by_id.get(edge.source)
            dst = self.items_by_id.get(edge.target)
            if not src or not dst: continue
            start = src.port_center('output', edge.output)
            end = dst.port_center('input', edge.input)
            dx = max(45, abs(end.x()-start.x()) * 0.45)
            path = QPainterPath(start)
            path.cubicTo(QPointF(start.x()+dx, start.y()), QPointF(end.x()-dx, end.y()), end)
            line = QGraphicsPathItem(path)
            kind = next(p.kind for p in src.definition.outputs if p.name==edge.output)
            line.setPen(QPen(QColor(PORT_COLORS[kind]), 2.4))
            line.setZValue(-1)
            self.addItem(line)
            self.cables.append(line)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            node = self.itemAt(event.scenePos(), self.views()[0].transform()) if self.views() else None
            if isinstance(node, NodeItem):
                port = node.port_at(node.mapFromScene(event.scenePos()))
                if port:
                    direction, name, kind = port
                    current = (node.node_id, direction, name, kind)
                    if self.pending is None:
                        self.pending = current
                        self.portMessage.emit(f'Selected {node.definition.label}.{name} ({kind}). Click matching opposite port.')
                    else:
                        a, b = self.pending, current
                        self.pending = None
                        if a[1] == b[1]:
                            self.portMessage.emit('Connect an output circle to an input circle.')
                        else:
                            source = a if a[1]=='output' else b
                            target = b if a[1]=='output' else a
                            try:
                                self.graph.connect(source[0], source[2], target[0], target[2])
                                self.update_cables()
                                self.graphChanged.emit()
                                self.portMessage.emit('Connected.')
                            except GraphError as exc:
                                self.portMessage.emit(str(exc))
                    event.accept()
                    return
            self.pending = None
        super().mousePressEvent(event)

    def add_node(self, kind: str):
        n = self.graph.add(kind, 65, 165 + len(self.graph.nodes)*36)
        self.reload()
        self.items_by_id[n.id].setSelected(True)
        self.graphChanged.emit()
        return n

    def delete_node(self, uid):
        self.graph.delete(uid)
        self.reload()
        self.graphChanged.emit()


class GraphView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setBackgroundBrush(QBrush(QColor('#101923')))
        self.setMinimumHeight(125)
        self._middle_pan_pos = None
        self._initial_focus_done = False

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_focus_done:
            self._initial_focus_done = True
            QTimer.singleShot(0, self.focus_start)

    def focus_start(self):
        """Make the source node immediately visible rather than showing empty canvas."""
        scene = self.scene()
        if scene is None or not scene.items_by_id:
            return
        first_id = next((uid for uid, n in scene.graph.nodes.items()
                         if n.type_id == 'audio.source'),
                        next(iter(scene.items_by_id)))
        item = scene.items_by_id.get(first_id)
        if item is not None:
            self.resetTransform()
            self.centerOn(item.sceneBoundingRect().center())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            # MMB pans empty space as well as space occupied by nodes and cables.
            # Avoid forwarding the event to the scene: connecting ports and
            # moving nodes are reserved for the left mouse button.
            self._middle_pan_pos = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._middle_pan_pos is not None:
            point = event.position().toPoint()
            delta = point - self._middle_pan_pos
            self._middle_pan_pos = point
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value()-delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._middle_pan_pos is not None:
            self._middle_pan_pos = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y()>0 else 1/1.15
            current = self.transform().m11()
            if .22 <= current*factor <= 2.7:
                self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
                self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)


class GraphPanel(QWidget):
    runRequested = Signal(str)
    modified = Signal()
    nodeSelected = Signal(str)

    def __init__(self, graph: Graph, parent=None):
        super().__init__(parent)
        self.graph = graph
        self._reloading = False
        self.registry = graph.registry
        body = QVBoxLayout(self)
        body.setContentsMargins(7,7,7,7)
        body.setSpacing(6)
        addrow = QHBoxLayout()
        self.type_selector = QComboBox()
        self.type_selector.setMinimumWidth(125)
        addrow.addWidget(self.type_selector, 1)
        add = QPushButton('+ Add Node')
        add.clicked.connect(self.add_node)
        addrow.addWidget(add)
        start_btn = QPushButton('Home')
        start_btn.setToolTip('Center the node view on the Audio Source node')
        start_btn.clicked.connect(lambda: self.canvas.focus_start())
        addrow.addWidget(start_btn)
        body.addLayout(addrow)
        self.scene = GraphScene(graph)
        self.scene.graphChanged.connect(self.on_graph_change)
        self.scene.selectionChanged.connect(self.show_selected)
        self.scene.portMessage.connect(self.show_message)
        self.canvas = GraphView(self.scene)
        body.addWidget(self.canvas, 3)
        hint = QLabel('Middle-drag: pan • Left-drag: move node • Click output then input: connect • Ctrl+wheel: zoom')
        hint.setWordWrap(False)
        hint.setToolTip(hint.text())
        hint.setText('Middle-drag: pan  •  Ctrl+wheel: zoom  •  Home: source')
        hint.setStyleSheet('color:#859daf; font-size:10px;')
        body.addWidget(hint)
        self.message = QLabel('')
        self.message.setWordWrap(True)
        self.message.setStyleSheet('color:#dfb86d; font-size:10px;')
        self.message.setVisible(False)
        body.addWidget(self.message)
        row = QHBoxLayout()
        row.addWidget(QLabel('Preview output:'))
        self.output_selector = QComboBox()
        self.output_selector.setMinimumWidth(95)
        row.addWidget(self.output_selector, 1)
        self.run_btn = QPushButton('Run Graph')
        self.run_btn.clicked.connect(self.start_run)
        row.addWidget(self.run_btn)
        body.addLayout(row)
        # Sharing one vertical area between node parameters and metrics prevents
        # both panels from squeezing the canvas out of existence when resized.
        self.detail_tabs = QTabWidget()
        self.detail_tabs.setMinimumHeight(103)
        self.detail_tabs.setMaximumHeight(190)
        params_page = QWidget()
        params_layout = QVBoxLayout(params_page)
        params_layout.setContentsMargins(2, 2, 2, 2)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.param_host = QWidget()
        self.param_layout = QFormLayout(self.param_host)
        self.scroll.setWidget(self.param_host)
        params_layout.addWidget(self.scroll)
        self.detail_tabs.addTab(params_page, 'Parameters')
        self.metrics = QPlainTextEdit()
        self.metrics.setReadOnly(True)
        self.metrics.setPlaceholderText('Run the graph to see intermediate output sizes and per-node timings.')
        self.detail_tabs.addTab(self.metrics, 'Run results')
        body.addWidget(self.detail_tabs, 1)
        self.update_types()
        self.update_outputs()
        self.output_selector.currentIndexChanged.connect(lambda *_: self.modified.emit())
        self.show_selected()

    def update_types(self):
        previous = self.type_selector.currentData()
        self.type_selector.clear()
        for key, definition in sorted(self.registry.defs.items(), key=lambda kv: kv[1].label):
            self.type_selector.addItem(definition.label, key)
        if previous:
            i = self.type_selector.findData(previous)
            if i >= 0: self.type_selector.setCurrentIndex(i)

    def update_outputs(self):
        previous = self.output_selector.currentData()
        self.output_selector.clear()
        for n in self.graph.nodes.values():
            if n.type_id == 'candidates.output':
                self.output_selector.addItem(f'Output {n.id[-4:]}', n.id)
        at = self.output_selector.findData(previous)
        if at >= 0: self.output_selector.setCurrentIndex(at)

    def add_node(self):
        key = self.type_selector.currentData()
        if key:
            node = self.scene.add_node(key)
            self.update_outputs()
            self.show_selected()
            item = self.scene.items_by_id.get(node.id)
            if item is not None:
                self.canvas.centerOn(item.sceneBoundingRect().center())

    def on_graph_change(self):
        self.update_outputs()
        self.modified.emit()

    def show_message(self, txt):
        self.message.setText(txt)
        self.message.setVisible(bool(txt))

    def _clear_form(self):
        while self.param_layout.rowCount():
            self.param_layout.removeRow(0)

    def show_selected(self):
        self._clear_form()
        selected = [x for x in self.scene.selectedItems() if isinstance(x, NodeItem)]
        if not selected:
            self.param_layout.addRow(QLabel('Click a node to edit its parameters.'))
            return
        n = self.graph.nodes[selected[0].node_id]
        self.nodeSelected.emit(n.id)
        definition = self.registry.defs[n.type_id]
        desc = QLabel(definition.description or definition.label)
        desc.setWordWrap(True)
        self.param_layout.addRow(desc)
        for p in definition.parameters:
            spin = QDoubleSpinBox()
            spin.setDecimals(p.decimals)
            spin.setRange(p.minimum, p.maximum)
            spin.setSingleStep(p.step)
            spin.setValue(n.params[p.name])
            spin.valueChanged.connect(lambda value, uid=n.id, key=p.name: self.set_parameter(uid,key,value))
            self.param_layout.addRow(p.name.replace('_',' '), spin)

    def set_parameter(self, uid, key, value):
        node = self.graph.nodes.get(uid)
        if node and node.params.get(key) != value:
            node.params[key] = value
            self.modified.emit()

    def start_run(self):
        output = self.output_selector.currentData()
        if output is None:
            self.show_message('Add and connect a Candidate Output node first.')
            return
        self.runRequested.emit(output)

    def set_busy(self, busy):
        self.run_btn.setEnabled(not busy)
        self.run_btn.setText('Running…' if busy else 'Run Graph')

    def show_metrics(self, results):
        self.metrics.setPlainText('\n'.join(
            f'{label:21} {kind:10} {count:>7,}  {ms:>8.1f} ms'
            for _, label, kind, count, ms in results))

    def set_graph(self, graph):
        self.graph = graph
        self.registry = graph.registry
        self.scene.graph = graph
        self.scene.reload()
        self.update_types()
        self.update_outputs()
        self.show_selected()
        self.metrics.clear()
        QTimer.singleShot(0, self.canvas.focus_start)

