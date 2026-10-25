"""Interactive semantic-search graph for an existing PySide6 file manager.

The graph displays exactly one search at a time: query in the center,
History above it, and up to ten highest-ranked file results surrounding it.
No AI dependency: feed it a parsed JSON object from your search backend.

`show_search_results()` receives a dictionary or JSON string containing
`query`, optional `answer`, and a `results` list. Each result can provide
`id`, `name`, `path`, `type`, and numeric `score` (0-1). Scores are used to
rank the results but are never displayed as percentages. Hovering a file
emits its original JSON record so the sidebar can display metadata. Unknown
top-level keys, such as `_format_notes`, are ignored. This widget doesn't
load a model, classify files, or organize anything on disk.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    QEasingCurve, QPointF, QRectF, Qt, QLineF, Signal, QUrl, QVariantAnimation,
)
from PySide6.QtGui import (
    QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsItem, QGraphicsLineItem, QGraphicsObject, QGraphicsScene,
    QGraphicsSceneMouseEvent, QGraphicsView, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)


class _Connection(QGraphicsLineItem):
    def __init__(self, first: "_Node", second: "_Node", related: bool = False):
        super().__init__()
        self.first = first
        self.second = second
        self.related = related
        self.setZValue(-1)
        self.set_highlighted(False)
        first.connections.append(self)
        second.connections.append(self)
        self.refresh()

    def set_highlighted(self, hovered: bool) -> None:
        """Light up the connecting line when either endpoint is hovered."""
        color = QColor("#9fdaff" if hovered else
                       ("#38bdf8" if self.related else "#3a597a"))
        pen = QPen(color, 3 if hovered else (1.5 if self.related else 2))
        if self.related:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)

    def refresh(self) -> None:
        self.setLine(QLineF(self.first.pos(), self.second.pos()))


class _Node(QGraphicsObject):
    """Circular draggable node. The caller handles clicks and optional hover."""

    def __init__(self, title: str, detail: str, kind: str,
                 on_click: Callable[[], None], color: str = "#7dd3fc",
                 on_hover: Callable[[bool], None] | None = None):
        super().__init__()
        self.title = title
        self.detail = detail
        self.kind = kind
        self.on_click = on_click
        self.on_hover = on_hover
        self.color = QColor(color)
        self.connections: list[_Connection] = []
        self._press_pos: QPointF | None = None
        self._hovered = False
        if self.kind == "search":
            # The central query describes the current graph; unlike the
            # History node and file nodes, it has no action. Make it fully
            # static so it doesn't misleadingly grow, highlight links,
            # display a hand cursor, or drag when someone clicks it.
            self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
            self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.setAcceptHoverEvents(False)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setFlags(
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
            )
            self.setAcceptHoverEvents(True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(2)

    def boundingRect(self) -> QRectF:
        if self.kind in {"search", "history_center"}:
            return QRectF(-110, -110, 220, 220)
        if self.kind == "history":
            return QRectF(-80, -80, 160, 160)
        return QRectF(-76, -76, 152, 152)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.boundingRect()
        radius = rect.width() / 2
        is_center = self.kind in {"search", "history_center"}
        main_fill = {
            "search": "#2563eb", "history_center": "#6d28d9",
            "history": "#574324",
        }.get(self.kind, "#1a2a40")
        edge_fill = {
            "search": "#152a5e", "history_center": "#2e2257",
            "history": "#262439",
        }.get(self.kind, "#101e31")

        # The double ring distinguishes the query/history centers from files.
        if is_center:
            painter.setPen(QPen(QColor(self.color.red(), self.color.green(),
                                       self.color.blue(), 88), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect.adjusted(3, 3, -3, -3))
        if self._hovered:
            halo = QColor(self.color)
            halo.setAlpha(115)
            painter.setPen(QPen(halo, 5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect.adjusted(4, 4, -4, -4))

        gradient = QRadialGradient(QPointF(-radius * .3, -radius * .35), radius * 1.7)
        gradient.setColorAt(0, QColor(main_fill).lighter(115 if self._hovered else 100))
        gradient.setColorAt(1, QColor(edge_fill))
        painter.setBrush(QBrush(gradient))
        border = QColor("#f8fafc") if self._hovered or self.isSelected() else self.color
        painter.setPen(QPen(border, 3 if self._hovered or is_center else 2))
        painter.drawEllipse(rect.adjusted(9, 9, -9, -9))

        # Draw vector icons rather than font-dependent emojis. They work even
        # on computers that don't have an emoji or icon-font package installed.
        if self.kind == "file":
            self._paint_document_icon(painter)
        else:
            self._paint_search_icon(painter, history=self.kind != "search")

        font = QFont("Sans Serif", 12 if is_center else 9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#f8fafc"))
        metrics = QFontMetrics(font)
        width = int(rect.width() - (48 if is_center else 36))
        title = metrics.elidedText(self.title, Qt.TextElideMode.ElideMiddle, width)
        title_y = -17 if is_center else -4
        painter.drawText(QRectF(-width / 2, title_y, width, 23),
                         Qt.AlignmentFlag.AlignCenter, title)

        font.setBold(False)
        font.setPointSize(9 if is_center else 8)
        painter.setFont(font)
        painter.setPen(QColor("#dbeafe" if is_center else "#a9bfd8"))
        detail_metrics = QFontMetrics(font)
        detail = detail_metrics.elidedText(self.detail, Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRectF(-width / 2, title_y + 25, width, 20),
                         Qt.AlignmentFlag.AlignCenter, detail)

    def _paint_document_icon(self, painter: QPainter) -> None:
        """Document-with-fold icon carrying a compact file-type label."""
        painter.setBrush(QColor("#243750"))
        painter.setPen(QPen(self.color, 1.8))
        painter.drawRoundedRect(QRectF(-15, -50, 30, 32), 4, 4)
        painter.drawLine(QPointF(5, -50), QPointF(5, -41))
        painter.drawLine(QPointF(5, -41), QPointF(15, -41))
        font = QFont("Sans Serif", 7)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#f8fafc"))
        painter.drawText(QRectF(-14, -39, 28, 19),
                         Qt.AlignmentFlag.AlignCenter, self.detail[:4].upper())

    def _paint_search_icon(self, painter: QPainter, *, history: bool) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self.color, 2.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        if history:
            painter.drawEllipse(QRectF(-10, -53, 20, 20))
            painter.drawLine(QPointF(0, -43), QPointF(0, -49))
            painter.drawLine(QPointF(0, -43), QPointF(6, -39))
        else:
            painter.drawEllipse(QRectF(-12, -55, 20, 20))
            painter.drawLine(QPointF(5, -38), QPointF(15, -29))

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.setZValue(5)
        self.setScale(1.055)
        for connection in self.connections:
            connection.set_highlighted(True)
        self.update()
        if self.on_hover:
            self.on_hover(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.setZValue(2)
        self.setScale(1.0)
        for connection in self.connections:
            connection.set_highlighted(False)
        self.update()
        if self.on_hover:
            self.on_hover(False)
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for connection in self.connections:
                connection.refresh()
        return super().itemChange(change, value)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.scenePos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        pressed = self._press_pos
        self._press_pos = None
        # Distinguish a click from dragging a node around the canvas.
        was_click = (pressed is not None and event.button() == Qt.MouseButton.LeftButton
                     and (pressed - event.scenePos()).manhattanLength() < 7)
        super().mouseReleaseEvent(event)
        if was_click:
            self.on_click()


class _GraphCanvas(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, parent: QWidget):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#0b1220"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self._auto_fit = True

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta:
            self.zoom(1.15 if delta > 0 else 1 / 1.15)
        event.accept()

    def _zoom_limits(self) -> tuple[float, float]:
        """Closest zoom keeps every node on screen; farthest is 60% of the Fit view."""
        view = self.viewport().rect()
        nodes = self.scene().itemsBoundingRect().adjusted(-12, -12, 12, 12)
        frame = self.sceneRect()
        if nodes.isEmpty() or frame.isEmpty() or view.isEmpty():
            return 0.1, 4.0
        closest = min(view.width() / nodes.width(), view.height() / nodes.height())
        fit = min(view.width() / frame.width(), view.height() / frame.height())
        return min(fit, closest) * 0.6, closest

    def zoom(self, factor: float) -> None:
        """Zoom around the cursor, never past the point where nodes leave the view."""
        current = self.transform().m11()
        farthest, closest = self._zoom_limits()
        target = max(farthest, min(closest, current * factor))
        if abs(target - current) > 1e-4:
            self._auto_fit = False
            self.scale(target / current, target / current)

    def fit_graph(self) -> None:
        """Reset zoom and pan to show every node with breathing room."""
        self._auto_fit = True
        self.resetTransform()
        if not self.sceneRect().isEmpty():
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._auto_fit and not self.sceneRect().isEmpty():
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        elif not self._auto_fit:
            self.zoom(1.0)

    def mousePressEvent(self, event) -> None:
        # A background drag is a deliberate pan. Preserve it on future resizes
        # until Fit is pressed, rather than automatically re-centering it.
        if event.button() == Qt.MouseButton.LeftButton and self.itemAt(event.pos()) is None:
            self._auto_fit = False
        super().mousePressEvent(event)


class SearchGraphWidget(QWidget):
    """Embed this in any QLayout and call show_search_results(json_data).

    file_clicked emits the file path before any attempt to open it. Set
    open_files_on_click=False to handle file-opening in your parent UI instead.
    """

    file_clicked = Signal(str)
    search_selected = Signal(str)
    results_displayed = Signal(object)
    history_view_changed = Signal(bool)
    file_hovered = Signal(object)  # Dict on hover; None when the graph resets.

    def __init__(self, parent: QWidget | None = None, *, max_results: int = 10,
                 open_files_on_click: bool = True):
        super().__init__(parent)
        self.max_results = max(1, max_results)
        self.open_files_on_click = open_files_on_click
        self._history: list[dict[str, Any]] = []
        self._current_index: int | None = None
        self._showing_history = False
        self._transitioning = False
        self._active_animation: QVariantAnimation | None = None
        self._scene = QGraphicsScene(self)
        self._canvas = _GraphCanvas(self._scene, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Visible controls complement wheel zoom and background-drag panning.
        toolbar = QWidget(self)
        toolbar.setStyleSheet("background:#0b1220;")
        tools_row = QHBoxLayout(toolbar)
        tools_row.setContentsMargins(12, 7, 12, 7)
        hint = QLabel("Scroll to zoom · Drag empty space to pan")
        hint.setStyleSheet("color:#9bb2d1; font-size:11px;")
        tools_row.addWidget(hint)
        tools_row.addStretch()
        for label, tooltip, action in (
            ("−", "Zoom out", lambda: self._canvas.zoom(1 / 1.2)),
            ("+", "Zoom in", lambda: self._canvas.zoom(1.2)),
            ("Fit", "Reset zoom and center graph", self._canvas.fit_graph),
        ):
            button = QPushButton(label)
            button.setToolTip(tooltip)
            button.setFixedHeight(28)
            button.setMinimumWidth(34 if label != "Fit" else 45)
            button.setStyleSheet(
                "QPushButton{color:#dce9fa;background:#23364e;border:1px solid #40597c;"
                "border-radius:6px;padding:2px 7px;}"
                "QPushButton:hover{background:#345478;}"
            )
            button.clicked.connect(action)
            tools_row.addWidget(button)
        layout.addWidget(toolbar)
        layout.addWidget(self._canvas)
        self._draw({"query": "Your search", "results": []})

    def _cancel_transition(self) -> None:
        """New backend results supersede any unfinished cosmetic animation."""
        if self._active_animation is not None:
            self._active_animation.stop()
            self._active_animation.deleteLater()
            self._active_animation = None
        self._transitioning = False
        self._canvas.setInteractive(True)

    def _animate_switch(self, render: Callable[[], None], notify: Callable[[], None]) -> None:
        """Short fade/shrink then fade/expand when switching graph modes.

        This is intentionally lightweight: the JSON and graph logic stay the
        same, and only the existing graphics items' scale/opacity are changed.
        """
        if self._transitioning:
            return
        self._transitioning = True
        self._canvas.setInteractive(False)
        self.file_hovered.emit(None)
        old_items = self._scene.items()

        fade_out = QVariantAnimation(self)
        fade_out.setDuration(145)
        fade_out.setStartValue(0.0)
        fade_out.setEndValue(1.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InCubic)

        def update_old(value) -> None:
            progress = float(value)
            for item in old_items:
                item.setOpacity(1.0 - progress)
                if isinstance(item, _Node):
                    item.setScale(1.0 - 0.12 * progress)

        def swap_scene() -> None:
            fade_out.deleteLater()
            render()  # Rebuild connections and nodes without changing the API.
            fresh_items = self._scene.items()
            for item in fresh_items:
                item.setOpacity(0.0)
                if isinstance(item, _Node):
                    item.setScale(0.88)

            fade_in = QVariantAnimation(self)
            fade_in.setDuration(190)
            fade_in.setStartValue(0.0)
            fade_in.setEndValue(1.0)
            fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)

            def update_new(value) -> None:
                progress = float(value)
                for item in fresh_items:
                    item.setOpacity(progress)
                    if isinstance(item, _Node):
                        item.setScale(0.88 + 0.12 * progress)

            def complete() -> None:
                fade_in.deleteLater()
                self._active_animation = None
                self._transitioning = False
                self._canvas.setInteractive(True)
                notify()

            fade_in.valueChanged.connect(update_new)
            fade_in.finished.connect(complete)
            self._active_animation = fade_in
            fade_in.start()

        fade_out.valueChanged.connect(update_old)
        fade_out.finished.connect(swap_scene)
        self._active_animation = fade_out
        fade_out.start()

    def show_search_results(self, data: dict[str, Any] | str | bytes) -> None:
        """Use for NEW searches. Adds results to in-memory history."""
        if isinstance(data, (str, bytes)):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise ValueError("Search results must be a JSON object")
        if not isinstance(data.get("results", []), list):
            raise ValueError("'results' must be a JSON array")
        self._cancel_transition()
        self.file_hovered.emit(None)
        # Snapshot so another component can't mutate entries in history.
        payload = json.loads(json.dumps(data))
        self._history.append(payload)
        self._current_index = len(self._history) - 1
        self._showing_history = False
        self._draw(payload)
        self.results_displayed.emit(payload)
        self.history_view_changed.emit(False)

    def show_history_item(self, index: int) -> None:
        """Switch to an older search without creating another history entry."""
        if not 0 <= index < len(self._history):
            raise IndexError("Search history index out of range")
        was_showing_history = self._showing_history
        self._cancel_transition()
        self.file_hovered.emit(None)
        self._current_index = index
        payload = self._history[index]
        self._showing_history = False

        def notify() -> None:
            self.results_displayed.emit(payload)
            self.search_selected.emit(str(payload.get("query", "")))
            self.history_view_changed.emit(False)

        # Animate when returning from history; otherwise render immediately.
        if self._scene.items() and was_showing_history:
            self._animate_switch(lambda: self._draw(payload), notify)
        else:
            self._draw(payload)
            notify()

    def save_history(self, path: str | Path) -> None:
        """Optional: persist search history across application restarts."""
        Path(path).write_text(json.dumps(self._history, indent=2), encoding="utf-8")

    def load_history(self, path: str | Path) -> None:
        entries = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(entries, list) or not all(
            isinstance(x, dict) and isinstance(x.get("results", []), list) for x in entries
        ):
            raise ValueError("Invalid search history file")
        self._cancel_transition()
        self.file_hovered.emit(None)
        self._history = entries
        self._current_index = len(entries) - 1 if entries else None
        self._showing_history = False
        displayed = entries[-1] if entries else {"query": "Your search", "results": []}
        self._draw(displayed)
        self.results_displayed.emit(displayed)
        self.history_view_changed.emit(False)

    def show_history_graph(self) -> None:
        """Replace the result graph with up to ten clickable prior searches."""
        if self._transitioning or self._showing_history:
            return
        self._showing_history = True
        self._animate_switch(self._draw_history, lambda: self.history_view_changed.emit(True))

    def _draw_history(self) -> None:
        """Draw the history view; called midway through the fade transition."""
        self._scene.clear()
        # History keeps all searches from this session, but renders only the
        # latest ten to prevent a crowded graph.
        recent = list(range(max(0, len(self._history) - 10), len(self._history)))
        recent.reverse()  # newest first
        center = _Node("Search History", f"Last {len(recent)} searches",
                       "history_center", self.return_to_results, "#c4b5fd")
        center.setToolTip("Click to return to the selected search results")
        self._scene.addItem(center)
        center.setPos(0, 0)
        # Every outer node represents one *previous search*, not one file.
        # Clicking it restores the JSON snapshot without rerunning the model.
        for order, history_index in enumerate(recent):
            record = self._history[history_index]
            query = str(record.get("query") or "Untitled search")
            count = len(record.get("results", []))
            node = _Node(query, f"{count} files", "history_entry",
                         lambda idx=history_index: self.show_history_item(idx), "#c4b5fd")
            self._scene.addItem(node)
            if len(recent) == 1:
                angle = math.pi / 2
            else:
                angle = 2 * math.pi * order / len(recent) - math.pi / 2
            radius = 350
            node.setPos(radius * math.cos(angle), radius * math.sin(angle))
            self._scene.addItem(_Connection(center, node))
        self._fit_scene()

    def return_to_results(self) -> None:
        """Click the center History node to return to the selected query."""
        if self._current_index is not None:
            self.show_history_item(self._current_index)
        else:
            if self._transitioning:
                return
            self._showing_history = False
            self._animate_switch(
                lambda: self._draw({"query": "Your search", "results": []}),
                lambda: self.history_view_changed.emit(False),
            )

    def _open_result(self, path: str) -> None:
        self.file_clicked.emit(path)
        if not self.open_files_on_click:
            return
        if not path or not Path(path).is_file():
            QMessageBox.warning(self, "File not found",
                                "This file no longer exists at the indexed location:\n" + path)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve()))):
            QMessageBox.warning(self, "Could not open file", "Could not open:\n" + path)

    @staticmethod
    def _type_color(file_type: str) -> str:
        ext = file_type.lower().lstrip(".")
        if ext in {"pdf"}:
            return "#fb7185"
        if ext in {"doc", "docx", "odt", "txt", "rtf"}:
            return "#60a5fa"
        if ext in {"png", "jpg", "jpeg", "gif", "webp"}:
            return "#c4b5fd"
        if ext in {"py", "js", "jsx", "cpp", "c", "h", "hpp", "java", "ts", "tsx", "rs"}:
            return "#4ade80"
        if ext in {"xls", "xlsx", "csv", "ods"}:
            return "#34d399"
        if ext in {"ppt", "pptx", "key"}:
            return "#fb923c"
        if ext in {"zip", "rar", "7z", "tar"}:
            return "#facc15"
        if ext in {"mp4", "mov", "webm", "avi"}:
            return "#22d3ee"
        if ext in {"mp3", "wav", "flac", "m4a"}:
            return "#f0abfc"
        return "#fcd34d"

    def _draw(self, payload: dict[str, Any]) -> None:
        # Switch back to the standard query-in-center / files-around-it view.
        self._scene.clear()
        query = str(payload.get("query") or "Your search")
        raw_results = payload.get("results", [])
        # The ranking is computed locally when numeric similarity scores exist.
        indexed = [x for x in raw_results if isinstance(x, dict)]
        indexed.sort(key=lambda x: float(x.get("score", 0))
                     if isinstance(x.get("score", 0), (int, float)) else 0,
                     reverse=True)
        results = indexed[:self.max_results]
        total = len(indexed)

        center = _Node(query, f"{total} matching files", "search", lambda: None,
                       "#93c5fd")
        self._scene.addItem(center)
        center.setPos(0, 0)

        # The dedicated History node is above the query; it replaces this
        # entire graph when clicked (rather than opening a popup menu).
        history = _Node("◷  History", "Browse previous searches", "history",
                        self.show_history_graph, "#fbbf24")
        self._scene.addItem(history)
        history.setPos(0, -315)
        self._scene.addItem(_Connection(center, history))

        # File circles use equal angular spacing over the lower 250 degrees.
        # Calculate a large enough ring for 10 circles (diameter 152 units) and
        # keep a generous gap so hover enlargement cannot cause overlap.
        if len(results) > 1:
            angular_step = math.radians(250 / (len(results) - 1))
            result_radius = max(395, 194 / (2 * math.sin(angular_step / 2)))
        else:
            result_radius = 395
        for index, item in enumerate(results):
            path = str(item.get("path") or "")
            filename = str(item.get("name") or Path(path).name or f"File {index + 1}")
            file_type = str(item.get("type") or Path(filename).suffix.lstrip(".") or "FILE")
            # We still rank by score, but don't show any numerical match rating.
            node = _Node(filename, file_type.upper(), "file",
                         lambda p=path: self._open_result(p),
                         self._type_color(file_type),
                         # Keep the most recently hovered file visible in the
                         # right panel after the cursor leaves the circle.
                         lambda hovering, record=item: (
                             self.file_hovered.emit(record) if hovering else None
                         ))
            node.setToolTip(path or filename)
            self._scene.addItem(node)
            if len(results) == 1:
                angle = math.radians(90)
            else:
                angle = math.radians(-35 + index * 250 / (len(results) - 1))
            node.setPos(result_radius * math.cos(angle), result_radius * math.sin(angle))
            self._scene.addItem(_Connection(center, node))

        self._fit_scene()

    def _fit_scene(self) -> None:
        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-80, -70, 80, 70))
        self._canvas.fit_graph()
