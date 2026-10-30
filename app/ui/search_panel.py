# AI search graph, file details, and a single-turn chat sidebar.

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from app.ui.search_graph import SearchGraphWidget

log = logging.getLogger(__name__)

QUERY_LIMIT = 80
SIDEBAR_WIDTH = 340

PANEL_COLORS = {
    True: dict(panel="#172236", border="#344562", text="#e6edf7", sub="#a9bcd6", field="#bbcce2",
               faint="#8fa8ca", warn="#ffd060", input="#27364e", input_text="#ffffff",
               input_border="#607594", button="#324762", button_text="#f8fafc",
               button_border="#526b8a", button_hover="#405c7e", disabled="#8996a9",
               disabled_input="#1d293b",
               prompt="#264b87", bubble="#26354b", bubble_text="#f1f5f9"),
    False: dict(panel="#ffffff", border="#d5dae1", text="#1a1a1a", sub="#5a6372", field="#3d4654",
                faint="#7a828f", warn="#b7791f", input="#f7f8fa", input_text="#1a1a1a",
                input_border="#b8c0cc", button="#f0f2f5", button_text="#1a1a1a",
                button_border="#c4cad3", button_hover="#e4e8ee", disabled="#a0a6b0",
                disabled_input="#eceff3",
                prompt="#0066cc", bubble="#eef1f5", bubble_text="#1a1a1a"),
}

PANEL_STYLE = """
QFrame#fileMetadataPanel, QFrame#searchChatSidebar {{
    background: {panel}; border: 1px solid {border}; border-radius: 12px;
}}
QFrame#fileMetadataPanel QLabel, QFrame#searchChatSidebar QLabel {{ color: {text}; background: transparent; }}
QScrollArea#panelScroll, QWidget#panelContent {{ background: {panel}; border: none; }}
QFrame#searchChatSidebar QLineEdit {{
    background: {input}; color: {input_text}; border: 1px solid {input_border};
    border-radius: 8px; padding: 8px;
}}
QFrame#searchChatSidebar QLineEdit:disabled {{
    background: {disabled_input}; color: {disabled}; border-color: {border};
}}
QFrame#fileMetadataPanel QPushButton, QFrame#searchChatSidebar QPushButton {{
    background: {button}; color: {button_text}; border: 1px solid {button_border};
    border-radius: 8px; padding: 7px 9px;
}}
QFrame#fileMetadataPanel QPushButton:hover, QFrame#searchChatSidebar QPushButton:hover {{ background: {button_hover}; }}
QFrame#fileMetadataPanel QPushButton:disabled, QFrame#searchChatSidebar QPushButton:disabled {{ color: {disabled}; }}
QFrame#searchChatSidebar QPushButton#searchSendButton {{
    background: #2563eb; color: white; border: none; font-weight: 600; padding: 8px 12px;
}}
QFrame#searchChatSidebar QPushButton#searchSendButton:disabled {{
    background: {disabled_input}; color: {disabled}; border: 1px solid {border};
}}
QFrame QLabel#subText {{ color: {sub}; }}
QFrame QLabel#fieldText {{ color: {field}; }}
QFrame QLabel#faintText {{ color: {faint}; }}
QFrame QLabel#promptBubble {{ background: {prompt}; color: #ffffff; border-radius: 10px; padding: 11px; }}
QFrame QLabel#responseBubble {{ background: {bubble}; color: {bubble_text}; border-radius: 10px; padding: 11px; }}
"""


class SearchPanel(QWidget):
    """A single-turn chat sidebar and graph."""

    searchRequested = Signal(str)
    cancelRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._external_busy = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(5, 5, 5, 5)
        outer.setSpacing(6)

        self.graph = SearchGraphWidget()
        self.graph.file_clicked.connect(self._open_file)
        self.graph.search_selected.connect(self._restore_query)
        self.graph.results_displayed.connect(self._update_response)
        self.graph.history_view_changed.connect(self._history_view_changed)
        self.graph.file_hovered.connect(self._update_file_preview)
        outer.addWidget(self.graph, 1)

        # Fixed-width column: file details on top, the one-turn search chat below.
        right = QWidget()
        right.setFixedWidth(SIDEBAR_WIDTH)
        right_column = QVBoxLayout(right)
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(6)
        outer.addWidget(right)

        # The main window places the search folder bar in its toolbar.
        self._build_scope_bar()

        right_column.addWidget(self._build_metadata_panel())

        right_column.addWidget(self._build_chat_panel(), 1)
        self.set_dark(True)

    def _build_scope_bar(self):
        self.scope_bar = QWidget()
        self.scope_bar.setObjectName("scopeBar")
        scope_layout = QHBoxLayout(self.scope_bar)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.setSpacing(6)
        scope_label = QLabel("Search in:")
        scope_label.setObjectName("toolbarLabel")
        scope_layout.addWidget(scope_label)
        self.folder = QLineEdit()
        self.folder.setMinimumWidth(200)
        self.folder.setPlaceholderText("Folder to search…")
        self.folder.setToolTip("Everything inside this folder and its subfolders is searched")
        scope_layout.addWidget(self.folder, 1)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.setFixedHeight(28)
        self.browse_button.clicked.connect(self._browse)
        scope_layout.addWidget(self.browse_button)

    def _build_metadata_panel(self):
        metadata = QFrame()
        metadata.setObjectName("fileMetadataPanel")
        metadata.setFixedHeight(245)
        meta_frame_layout = QVBoxLayout(metadata)
        meta_frame_layout.setContentsMargins(14, 13, 14, 13)
        meta_frame_layout.setSpacing(7)
        meta_heading = QLabel("File details")
        meta_heading.setStyleSheet("font-size: 18px; font-weight:700;")
        meta_frame_layout.addWidget(meta_heading)
        meta_subheading = QLabel("Hover over a result circle to inspect its file.")
        meta_subheading.setObjectName("subText")
        meta_subheading.setStyleSheet("font-size:11px;")
        meta_subheading.setWordWrap(True)
        meta_frame_layout.addWidget(meta_subheading)

        # Scrolling keeps long paths visible without overlapping the lower
        # search panel when the application window is short.
        meta_scroll = QScrollArea()
        meta_scroll.setFrameShape(QFrame.Shape.NoFrame)
        meta_scroll.setWidgetResizable(True)
        meta_scroll.setObjectName("panelScroll")
        meta_scroll.viewport().setObjectName("panelContent")
        meta_content = QWidget()
        meta_content.setObjectName("panelContent")
        meta_rows = QVBoxLayout(meta_content)
        meta_rows.setContentsMargins(2, 6, 2, 3)
        meta_rows.setSpacing(8)
        self.meta_file_name = QLabel("No file selected")
        self.meta_file_name.setTextFormat(Qt.TextFormat.PlainText)
        self.meta_file_name.setWordWrap(True)
        self.meta_file_name.setStyleSheet("font-weight:700; font-size:14px;")
        meta_rows.addWidget(self.meta_file_name)

        self.meta_type = QLabel("Type: —")
        self.meta_size = QLabel("Size: —")
        self.meta_modified = QLabel("Last modified: —")
        self.meta_location = QLabel("Location: —")
        self.meta_location.setWordWrap(True)
        self.meta_location.setTextFormat(Qt.TextFormat.PlainText)
        self.meta_location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for field in (self.meta_type, self.meta_size, self.meta_modified, self.meta_location):
            field.setObjectName("fieldText")
            field.setStyleSheet("font-size:11px;")
            meta_rows.addWidget(field)
        self.meta_availability = QLabel("Select a file to see its details.")
        self.meta_availability.setWordWrap(True)
        self.meta_availability.setObjectName("faintText")
        self.meta_availability.setStyleSheet("font-size:10px;")
        meta_rows.addWidget(self.meta_availability)
        meta_rows.addStretch(1)
        meta_scroll.setWidget(meta_content)
        meta_frame_layout.addWidget(meta_scroll, 1)
        return metadata

    def _build_chat_panel(self):
        sidebar = QFrame()
        sidebar.setObjectName("searchChatSidebar")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 12, 14, 12)
        side.setSpacing(7)

        heading = QLabel("AI File Search")
        heading.setStyleSheet("font-size: 18px; font-weight: 700;")
        side.addWidget(heading)
        tip = QLabel("Describe what a file is about, or type part of its name.")
        tip.setWordWrap(True)
        tip.setObjectName("subText")
        tip.setStyleSheet("font-size:11px;")
        side.addWidget(tip)

        # The latest prompt/answer scroll as needed; the Send button is always kept below them.
        chat_scroll = QScrollArea()
        chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        chat_scroll.setWidgetResizable(True)
        chat_scroll.setObjectName("panelScroll")
        chat_scroll.viewport().setObjectName("panelContent")
        chat_content = QWidget()
        chat_content.setObjectName("panelContent")
        chat_rows = QVBoxLayout(chat_content)
        chat_rows.setContentsMargins(0, 4, 0, 3)
        chat_rows.setSpacing(9)

        self.latest_prompt = QLabel("Your latest search will appear here.")
        self.latest_prompt.setWordWrap(True)
        self.latest_prompt.setTextFormat(Qt.TextFormat.PlainText)
        self.latest_prompt.setObjectName("promptBubble")
        chat_rows.addWidget(self.latest_prompt)

        self.response = QLabel("Pick a folder in the top bar, then describe the file you want, "
                               "by topic or by name.")
        self.response.setTextFormat(Qt.TextFormat.PlainText)
        self.response.setWordWrap(True)
        self.response.setObjectName("responseBubble")
        chat_rows.addWidget(self.response)
        chat_rows.addStretch(1)
        chat_scroll.setWidget(chat_content)
        side.addWidget(chat_scroll, 1)

        footer = QWidget(sidebar)
        footer.setObjectName("searchFooter")
        footer.setStyleSheet("QWidget#searchFooter {background: transparent;}")
        footer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 5, 0, 0)
        footer_layout.setSpacing(8)

        input_row = QHBoxLayout()
        input_row.setSpacing(7)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Ask about your files…")
        self.query.setMaxLength(QUERY_LIMIT)
        self.query.setMinimumHeight(38)
        self.query.returnPressed.connect(self._request_search)
        self.query.textChanged.connect(self._count_query)
        input_row.addWidget(self.query, 1)
        self.search_button = QPushButton("Send")
        self.search_button.setObjectName("searchSendButton")
        self.search_button.setMinimumWidth(65)
        self.search_button.setMinimumHeight(38)
        self.search_button.clicked.connect(self._request_search)
        input_row.addWidget(self.search_button)
        self.cancel_button = QPushButton("Stop")
        self.cancel_button.clicked.connect(self.cancelRequested.emit)
        self.cancel_button.hide()
        input_row.addWidget(self.cancel_button)
        footer_layout.addLayout(input_row)
        self.query_count = QLabel()
        self.query_count.setAlignment(Qt.AlignmentFlag.AlignRight)
        footer_layout.addWidget(self.query_count)
        side.addWidget(footer)
        return sidebar

    def set_dark(self, dark: bool) -> None:
        self._colors = PANEL_COLORS[dark]
        self.setStyleSheet(PANEL_STYLE.format(**self._colors))
        palette = self.query.palette()
        palette.setColor(QPalette.Disabled, QPalette.PlaceholderText, QColor(self._colors['disabled']))
        self.query.setPalette(palette)
        self.graph.set_dark(dark)
        self._count_query(self.query.text())

    def _count_query(self, text):
        near = len(text) >= QUERY_LIMIT - 20
        self.query_count.setText(f"{len(text)}/{QUERY_LIMIT}")
        color = self._colors["warn" if near else "faint"]
        self.query_count.setStyleSheet(f"color:{color}; font-size:10px;")

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a search folder", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def set_searching(self, busy):
        self._busy = busy
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        self._refresh_controls()

    def set_external_busy(self, busy):
        self._external_busy = busy
        self._refresh_controls()

    def _refresh_controls(self):
        enabled = not self._busy and not self._external_busy
        for control in (self.query, self.search_button, self.folder, self.browse_button):
            control.setEnabled(enabled)

    def set_status(self, message):
        self.response.setText(message)

    def _request_search(self) -> None:
        if self._busy or self._external_busy:
            return
        query = self.query.text().strip()
        if not query:
            self.query.setFocus()
            return
        self.latest_prompt.setText(query)
        self.response.setText("Preparing your search…")
        self.searchRequested.emit(query)

    def display_results(self, payload: dict) -> None:
        """Render the backend result. Call on Qt's GUI thread after search finishes.

        The graph sorts the `results` array by descending `score`, shows at
        most 10, and saves the complete JSON for visual search history.
        """
        self.graph.show_search_results(payload)

    def _update_response(self, payload: dict) -> None:
        total = len(payload.get("results", []))
        self.response.setText(str(payload.get("answer") or f"Found {total} matching files."))
        query = str(payload.get("query") or "")
        self.query.setText(query)
        self.latest_prompt.setText(query)
        if isinstance(payload.get('root'), str):
            self.folder.setText(payload['root'])

    @staticmethod
    def _format_file_size(value) -> str:
        """Human-readable size for indexed JSON metadata or disk stat bytes."""
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return str(value) if value not in (None, "") else "—"
        amount = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if amount < 1024 or unit == "TB":
                return f"{amount:.0f} B" if unit == "B" else f"{amount:.1f} {unit}"
            amount /= 1024
        return "—"

    @staticmethod
    def _format_modified(value) -> str:
        """Accept an ISO date string or a Unix timestamp from search JSON."""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                return datetime.fromtimestamp(value).strftime("%b %d, %Y · %I:%M %p")
            except (OverflowError, OSError, ValueError):
                return "—"
        return str(value) if value not in (None, "") else "—"

    def _update_file_preview(self, record: object) -> None:
        """Show the hovered result; never ask the AI to create a summary.

        For files on this computer, read only basic filesystem metadata.
        """
        if not isinstance(record, dict):
            self.meta_file_name.setText("No file selected")
            self.meta_type.setText("Type: —")
            self.meta_size.setText("Size: —")
            self.meta_modified.setText("Last modified: —")
            self.meta_location.setText("Location: —")
            self.meta_availability.setText("Hover over a result circle to see file information.")
            return

        path = str(record.get("path") or "")
        name = str(record.get("name") or (Path(path).name if path else "Unnamed file"))
        file_type = str(record.get("type") or Path(name).suffix.lstrip(".") or "File")
        raw_size = record.get("size_bytes")
        raw_modified = record.get("modified")

        # Checking stat does not open or read file contents or load a model.
        available = False
        if path:
            try:
                target = Path(path)
                if target.is_file():
                    stat = target.stat()
                    raw_size = stat.st_size
                    raw_modified = stat.st_mtime
                    available = True
            except OSError:
                pass

        self.meta_file_name.setText(name)
        self.meta_type.setText("Type: " + file_type.upper())
        self.meta_size.setText("Size: " + self._format_file_size(raw_size))
        self.meta_modified.setText("Last modified: " + self._format_modified(raw_modified))
        self.meta_location.setText("Location: " + (path or "—"))
        self.meta_availability.setText(
            "Click its circle to open it." if available
            else "File not accessible here (moved or deleted)."
        )

    def _restore_query(self, query: str) -> None:
        self.query.setText(query)

    def _history_view_changed(self, showing_history: bool) -> None:
        if showing_history:
            self._update_file_preview(None)
            self.latest_prompt.setText("Search History")
            self.response.setText(
                "Click a search around the center to reopen its results. "
                "Click the center node to return."
            )

    def _open_file(self, path: str) -> None:
        target = Path(path)
        if not target.is_file():
            log.warning('Cannot open search result; file is unavailable: %s', path)
            QMessageBox.warning(self, "File unavailable", "File not found:\n" + path)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve()))):
            log.warning('Desktop service could not open file: %s', target)
            QMessageBox.warning(self, "Cannot open file", "Could not open:\n" + path)

