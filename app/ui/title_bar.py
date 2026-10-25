"""Custom title bar for the frameless main window, with native move, resize and snap."""
import sys

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton

RESIZE_MARGIN = 5
# Segoe MDL2 glyphs match the native Windows caption buttons; other systems use text.
GLYPHS = ({"min": "\uE921", "max": "\uE922", "restore": "\uE923", "close": "\uE8BB"}
          if sys.platform == "win32" else {"min": "—", "max": "□", "restore": "❐", "close": "✕"})


class TitleBar(QFrame):
    def __init__(self, window):
        super().__init__(window)
        self.window_ = window
        self.setObjectName("titleBar")
        self.setFixedHeight(26)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 0, 0)
        row.setSpacing(0)
        self.title = QLabel(window.windowTitle())
        self.title.setObjectName("titleText")
        row.addWidget(self.title)
        row.addStretch()
        self.minimize = self._button("min", window.showMinimized)
        self.maximize = self._button("max", self._toggle_maximized)
        self.close_button = self._button("close", window.close)
        self.close_button.setObjectName("titleCloseBtn")
        for button in (self.minimize, self.maximize, self.close_button):
            row.addWidget(button)
        window.windowTitleChanged.connect(self.title.setText)
        window.installEventFilter(self)

    def _button(self, glyph, slot):
        button = QPushButton(GLYPHS[glyph])
        button.setObjectName("titleBtn")
        button.setFixedSize(46, 26)
        button.setFocusPolicy(Qt.NoFocus)
        if sys.platform == "win32":
            button.setStyleSheet("font-family: 'Segoe MDL2 Assets'; font-size: 10px;")
        button.clicked.connect(slot)
        return button

    def _toggle_maximized(self):
        if self.window_.isMaximized():
            self.window_.showNormal()
        else:
            self.window_.showMaximized()

    def eventFilter(self, watched, event):
        if watched is self.window_ and event.type() == QEvent.Type.WindowStateChange:
            self.maximize.setText(GLYPHS["restore" if self.window_.isMaximized() else "max"])
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.window_.windowHandle().startSystemMove()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._toggle_maximized()
            event.accept()


class EdgeResizer(QObject):
    """Frameless windows lose their resize border; resize from the outer few pixels instead."""

    def __init__(self, window):
        super().__init__(window)
        self.window_ = window
        self._cursor = False
        window.windowHandle().installEventFilter(self)

    def _edges(self, pos):
        if self.window_.isMaximized() or self.window_.isFullScreen():
            return Qt.Edge(0)
        width, height = self.window_.width(), self.window_.height()
        edges = Qt.Edge(0)
        if pos.x() < RESIZE_MARGIN:
            edges |= Qt.LeftEdge
        elif pos.x() >= width - RESIZE_MARGIN:
            edges |= Qt.RightEdge
        if pos.y() < RESIZE_MARGIN:
            edges |= Qt.TopEdge
        elif pos.y() >= height - RESIZE_MARGIN:
            edges |= Qt.BottomEdge
        return edges

    def _set_cursor(self, edges):
        shapes = {
            Qt.LeftEdge | Qt.TopEdge: Qt.SizeFDiagCursor, Qt.RightEdge | Qt.BottomEdge: Qt.SizeFDiagCursor,
            Qt.RightEdge | Qt.TopEdge: Qt.SizeBDiagCursor, Qt.LeftEdge | Qt.BottomEdge: Qt.SizeBDiagCursor,
            Qt.LeftEdge: Qt.SizeHorCursor, Qt.RightEdge: Qt.SizeHorCursor,
            Qt.TopEdge: Qt.SizeVerCursor, Qt.BottomEdge: Qt.SizeVerCursor,
        }
        shape = shapes.get(edges)
        if shape is not None:
            if self._cursor:
                QApplication.changeOverrideCursor(shape)
            else:
                QApplication.setOverrideCursor(shape)
                self._cursor = True
        elif self._cursor:
            QApplication.restoreOverrideCursor()
            self._cursor = False

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.MouseMove and not event.buttons():
            self._set_cursor(self._edges(event.position().toPoint()))
        elif kind == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
            edges = self._edges(event.position().toPoint())
            if edges:
                self._set_cursor(Qt.Edge(0))
                watched.startSystemResize(edges)
                return True
        elif kind == QEvent.Type.Leave:
            self._set_cursor(Qt.Edge(0))
        return False
