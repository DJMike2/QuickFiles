"""Right-side chat controls plus a left-side graph for semantic search JSON.

The UI makes no AI/model calls. A future background search worker can connect
to `searchRequested`, then call `display_results` on the GUI thread.

Expected backend JSON format (see sample-search-results.json):
    {
      "query": "find my algorithms assignment",             # string
      "answer": "Found 2 files",                           # optional string
      "results": [                                         # array of objects
        {"id": "1", "name": "a.pdf", "path": "C:/files/a.pdf",
         "type": "pdf", "score": 0.92},                # score: 0.0-1.0
        {"id": "2", "name": "b.cpp", "path": "C:/files/b.cpp",
         "type": "cpp", "score": 0.85}
      ]
    }

Optional metadata on each result (not required for existing JSON samples):
    "size_bytes": 24576, "modified": "2026-10-24"
or  "metadata": {"size_bytes": 24576, "modified": "2026-10-24"}
Existing local files supply their actual size/date automatically on hover.

Python comments above are illustrative; real .json files cannot contain // or
# comments. The provided sample uses an optional `_format_notes` object instead.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from app.ui.search_graph import SearchGraphWidget


class SearchPanel(QWidget):
    """A single-turn chat sidebar, graph, and optional development controls."""

    searchRequested = Signal(str)

    def __init__(self, parent=None, *, show_json_loader: bool = True):
        super().__init__(parent)
        self._source_actions = {}
        self._action_proxies = {}
        self.show_json_loader = show_json_loader
        self._hovered_file_path = ""

        outer = QHBoxLayout(self)
        outer.setContentsMargins(5, 5, 5, 5)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)

        self.graph = SearchGraphWidget(open_files_on_click=False)
        self.graph.file_clicked.connect(self._open_file)
        self.graph.search_selected.connect(self._restore_query)
        self.graph.results_displayed.connect(self._update_response)
        self.graph.history_view_changed.connect(self._history_view_changed)
        self.graph.file_hovered.connect(self._update_file_preview)
        splitter.addWidget(self.graph)

        # The right column is split into two independent, resizable panels:
        # file metadata on top and the compact one-turn search chat below.
        right_column = QSplitter(Qt.Orientation.Vertical)
        right_column.setChildrenCollapsible(False)
        right_column.setHandleWidth(8)
        splitter.addWidget(right_column)

        metadata = QFrame()
        metadata.setObjectName("fileMetadataPanel")
        metadata.setStyleSheet("""
            QFrame#fileMetadataPanel {
                background: #172236; border: 1px solid #344562;
                border-radius: 12px;
            }
            QFrame#fileMetadataPanel QLabel {color: #e6edf7;}
            QFrame#fileMetadataPanel QPushButton {
                background: #324762; color: #f8fafc;
                border: 1px solid #526b8a; border-radius: 8px;
                padding: 7px 9px;
            }
            QFrame#fileMetadataPanel QPushButton:hover {background: #405c7e;}
            QFrame#fileMetadataPanel QPushButton:disabled {color: #8292aa;}
        """)
        metadata.setMinimumHeight(200)
        meta_frame_layout = QVBoxLayout(metadata)
        meta_frame_layout.setContentsMargins(14, 13, 14, 13)
        meta_frame_layout.setSpacing(7)
        meta_heading = QLabel("File details")
        meta_heading.setStyleSheet("font-size: 18px; font-weight:700;")
        meta_frame_layout.addWidget(meta_heading)
        meta_subheading = QLabel("Hover over a result circle to inspect its file.")
        meta_subheading.setStyleSheet("color:#a9bcd6; font-size:11px;")
        meta_subheading.setWordWrap(True)
        meta_frame_layout.addWidget(meta_subheading)

        # Scrolling keeps long paths visible without overlapping the lower
        # search panel when the application window is short.
        meta_scroll = QScrollArea()
        meta_scroll.setFrameShape(QFrame.Shape.NoFrame)
        meta_scroll.setWidgetResizable(True)
        meta_scroll.setStyleSheet("QScrollArea{background:#172236;border:none;}")
        meta_scroll.viewport().setStyleSheet("background:#172236;")
        meta_content = QWidget()
        meta_content.setStyleSheet("background:#172236;")
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
            field.setStyleSheet("color:#bbcce2; font-size:11px;")
            meta_rows.addWidget(field)
        self.meta_availability = QLabel("Select a file to see its details.")
        self.meta_availability.setWordWrap(True)
        self.meta_availability.setStyleSheet("color:#8fa8ca; font-size:10px;")
        meta_rows.addWidget(self.meta_availability)
        meta_rows.addStretch(1)
        meta_scroll.setWidget(meta_content)
        meta_frame_layout.addWidget(meta_scroll, 1)

        self.meta_open_button = QPushButton("Open file")
        self.meta_open_button.setEnabled(False)
        self.meta_open_button.clicked.connect(self._open_hovered_file)
        meta_frame_layout.addWidget(self.meta_open_button)
        right_column.addWidget(metadata)

        sidebar = QFrame()
        sidebar.setObjectName("searchChatSidebar")
        sidebar.setStyleSheet("""
            QFrame#searchChatSidebar {
                background: #172236; border: 1px solid #344562;
                border-radius: 12px;
            }
            QFrame#searchChatSidebar QLabel {color: #e6edf7;}
            QFrame#searchChatSidebar QGroupBox {
                color: #c7d5ed; border: 1px solid #40516c;
                border-radius: 8px; margin-top: 11px; padding-top: 10px;
            }
            QFrame#searchChatSidebar QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 4px;
            }
            QFrame#searchChatSidebar QLineEdit {
                background: #27364e; color: white;
                border: 1px solid #607594; border-radius: 8px;
                padding: 8px;
            }
            QFrame#searchChatSidebar QPushButton {
                background: #324762; color: #f8fafc;
                border: 1px solid #526b8a; border-radius: 8px;
                padding: 7px 9px;
            }
            QFrame#searchChatSidebar QPushButton:hover {background: #405c7e;}
            QFrame#searchChatSidebar QPushButton:disabled {color: #8996a9;}
        """)
        sidebar.setMinimumHeight(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 12, 14, 12)
        side.setSpacing(7)

        heading = QLabel("AI File Search")
        heading.setStyleSheet("font-size: 18px; font-weight: 700;")
        side.addWidget(heading)
        tip = QLabel("Search your files by meaning, not just filename.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#a9bcd6; font-size:11px;")
        side.addWidget(tip)

        # The latest prompt/answer and temporary organizer controls scroll as
        # needed; the JSON and Send buttons are always kept below them.
        chat_scroll = QScrollArea()
        chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        chat_scroll.setWidgetResizable(True)
        chat_scroll.setStyleSheet("QScrollArea{background:#172236;border:none;}")
        chat_scroll.viewport().setStyleSheet("background:#172236;")
        chat_content = QWidget()
        chat_content.setStyleSheet("background:#172236;")
        chat_rows = QVBoxLayout(chat_content)
        chat_rows.setContentsMargins(0, 4, 0, 3)
        chat_rows.setSpacing(9)

        self.latest_prompt = QLabel("Your latest search will appear here.")
        self.latest_prompt.setWordWrap(True)
        self.latest_prompt.setTextFormat(Qt.TextFormat.PlainText)
        self.latest_prompt.setStyleSheet(
            "background:#264b87; color:#ffffff; border-radius:10px; padding:11px;"
        )
        chat_rows.addWidget(self.latest_prompt)

        self.response = QLabel("The search backend is not connected yet.")
        self.response.setTextFormat(Qt.TextFormat.PlainText)
        self.response.setWordWrap(True)
        self.response.setStyleSheet(
            "background:#26354b; color:#f1f5f9; border-radius:10px; padding:11px;"
        )
        chat_rows.addWidget(self.response)

        # Organizer controls are duplicated in the Search tab temporarily,
        # and connected to their existing buttons in MainWindow.
        self.organizer_group = QGroupBox("Organizer controls · temporary")
        self.organizer_grid = QGridLayout(self.organizer_group)
        self.organizer_group.hide()
        chat_rows.addWidget(self.organizer_group)
        chat_rows.addStretch(1)
        chat_scroll.setWidget(chat_content)
        side.addWidget(chat_scroll, 1)

        # The footer has its own spacing, so Load JSON and Send cannot overlap.
        footer = QWidget(sidebar)
        footer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 5, 0, 0)
        footer_layout.setSpacing(8)

        # This is deliberately the only testing control. In the full app,
        # MainWindow passes show_json_loader=False so users never see it.
        if show_json_loader:
            self.json_button = QPushButton("Load JSON…")
            self.json_button.clicked.connect(self._load_json)
            self.json_button.setMinimumHeight(34)
            footer_layout.addWidget(self.json_button)

        input_row = QHBoxLayout()
        input_row.setSpacing(7)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Ask about your files…")
        self.query.setMinimumHeight(38)
        self.query.returnPressed.connect(self._request_search)
        input_row.addWidget(self.query, 1)
        self.search_button = QPushButton("Send")
        self.search_button.setMinimumWidth(65)
        self.search_button.setMinimumHeight(38)
        self.search_button.setStyleSheet(
            "background:#2563eb; color:white; border:none; font-weight:600; padding:8px 12px;"
        )
        self.search_button.clicked.connect(self._request_search)
        input_row.addWidget(self.search_button)
        footer_layout.addLayout(input_row)
        side.addWidget(footer)

        right_column.addWidget(sidebar)
        right_column.setStretchFactor(0, 1)
        right_column.setStretchFactor(1, 1)
        right_column.setSizes([350, 350])

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([870, 380])

    def bind_organizer_controls(self, actions: dict, folder_path: QLineEdit) -> None:
        """Make temporary sidebar copies of MainWindow's existing actions."""
        self._source_actions = actions
        self.folder_label = QLabel(folder_path.text())
        self.folder_label.setWordWrap(True)
        self.folder_label.setToolTip(folder_path.text())
        self.organizer_grid.addWidget(self.folder_label, 0, 0, 1, 3)
        folder_path.textChanged.connect(self.folder_label.setText)
        folder_path.textChanged.connect(self.folder_label.setToolTip)

        for index, (label, original) in enumerate(actions.items()):
            copy = QPushButton(label)
            copy.clicked.connect(original.click)
            self.organizer_grid.addWidget(copy, 2 + index // 3, index % 3)
            self._action_proxies[label] = copy
        self.organizer_group.show()
        self.sync_organizer_controls()

    def sync_organizer_controls(self) -> None:
        for label, original in self._source_actions.items():
            self._action_proxies[label].setEnabled(original.isEnabled())
            self._action_proxies[label].setText(original.text())

    def _request_search(self) -> None:
        query = self.query.text().strip()
        if not query:
            self.query.setFocus()
            return
        self.latest_prompt.setText(query)
        self.response.setText(
            "The search input is ready. The semantic-search worker still needs to be connected."
        )
        self.searchRequested.emit(query)

    def display_results(self, payload: dict | str | bytes) -> None:
        """Render backend JSON. Call on Qt's GUI thread after search finishes.

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
        For test JSON containing nonexistent paths, use optional JSON fields
        (size_bytes, modified, metadata) when supplied and show placeholders.
        """
        if not isinstance(record, dict):
            self._hovered_file_path = ""
            self.meta_file_name.setText("No file selected")
            self.meta_type.setText("Type: —")
            self.meta_size.setText("Size: —")
            self.meta_modified.setText("Last modified: —")
            self.meta_location.setText("Location: —")
            self.meta_availability.setText("Hover over a result circle to see file information.")
            self.meta_open_button.setEnabled(False)
            return

        path = str(record.get("path") or "")
        name = str(record.get("name") or (Path(path).name if path else "Unnamed file"))
        file_type = str(record.get("type") or Path(name).suffix.lstrip(".") or "File")
        extra = record.get("metadata")
        indexed = extra if isinstance(extra, dict) else {}
        raw_size = record.get("size_bytes", record.get(
            "size", indexed.get("size_bytes", indexed.get("size"))))
        raw_modified = record.get("modified", record.get(
            "last_modified", indexed.get("modified", indexed.get("last_modified"))))
        if raw_modified is None:
            raw_modified = record.get("modified_at", indexed.get("modified_at"))

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

        self._hovered_file_path = path if available else ""
        self.meta_file_name.setText(name)
        self.meta_type.setText("Type: " + file_type.upper())
        self.meta_size.setText("Size: " + self._format_file_size(raw_size))
        self.meta_modified.setText("Last modified: " + self._format_modified(raw_modified))
        self.meta_location.setText("Location: " + (path or "—"))
        self.meta_availability.setText(
            "Ready to open" if available else "File not accessible here (sample or moved file)."
        )
        self.meta_open_button.setEnabled(available)

    def _open_hovered_file(self) -> None:
        if self._hovered_file_path:
            self._open_file(self._hovered_file_path)

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

    def _load_json(self) -> None:
        # Standalone UI testing: exercise the real JSON integration path.
        # No local model, downloads, embeddings, or fake search are invoked.
        path, _ = QFileDialog.getOpenFileName(self, "Load search JSON", "", "JSON files (*.json)")
        if not path:
            return
        try:
            self.display_results(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Invalid JSON results", str(exc))

    def _open_file(self, path: str) -> None:
        if path.startswith("demo://"):
            self.response.setText("This is a demo node, not a real file.")
            return
        target = Path(path)
        if not target.is_file():
            QMessageBox.warning(self, "File unavailable", "File not found:\n" + path)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.resolve()))):
            QMessageBox.warning(self, "Cannot open file", "Could not open:\n" + path)

