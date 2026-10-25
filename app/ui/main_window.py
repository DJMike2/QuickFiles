"""Desktop scan → preview → apply → undo workflow."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from app.constants import APP_NAME
from app.ai.schema import OrganizationPlan, PlanAction
from app.core.blocked_paths import validate_root_selection
from app.core.executor import Executor
from app.core.inventory import FileEntry, ScanInventory
from app.core.planner import Planner
from app.core.scanner import Scanner
from app.core.undo import UndoEngine
from app.core.validator import Validator
from app.ui.first_run_dialog import FirstRunDialog
from app.ui.preview_panel import PreviewPanel
from app.ui.search_panel import SearchPanel
from app.ui.settings_dialog import SettingsDialog
from app.ui.theme import apply_theme
from app.ui.worker import Worker


class MainWindow(QMainWindow):
    def __init__(self, context):
        super().__init__()
        self.ctx = context
        self.config = context.config
        self.worker = None
        self.search_worker = None
        self.plan = None
        self.inventory = None
        self._job_kind = ""
        self._close_pending = False
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 750)
        self.setMinimumSize(1000, 600)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 4, 6, 0)
        # Keep file organization and semantic search in separate tabs so the
        # existing scan/preview/apply workflow stays unchanged.
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        organize_page = QWidget()
        organize_layout = QVBoxLayout(organize_page)
        organize_layout.setContentsMargins(4, 4, 4, 4)
        toolbar = QHBoxLayout()
        organize_layout.addLayout(toolbar)
        toolbar.addWidget(QLabel("Folder:"))
        self.path = QLineEdit(self.config.get("allowed_root"))
        self.path.textEdited.connect(self._invalidate)
        toolbar.addWidget(self.path, 1)
        self.browse = QPushButton("Browse…")
        self.browse.clicked.connect(self._browse)
        toolbar.addWidget(self.browse)
        self.scan = QPushButton("Scan")
        self.scan.clicked.connect(self._scan)
        toolbar.addWidget(self.scan)
        self.sample = QPushButton("Sample")
        self.sample.clicked.connect(self._load_sample)
        toolbar.addWidget(self.sample)
        self.apply = QPushButton("Apply")
        self.apply.setObjectName("applyBtn")
        self.apply.clicked.connect(self._apply)
        toolbar.addWidget(self.apply)
        self.undo = QPushButton("Undo")
        self.undo.clicked.connect(self._undo)
        toolbar.addWidget(self.undo)
        self.setup = QPushButton("Setup AI")
        self.setup.clicked.connect(self._setup)
        toolbar.addWidget(self.setup)
        self.settings = QPushButton("Settings")
        self.settings.clicked.connect(self._settings)
        toolbar.addWidget(self.settings)
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setTextFormat(Qt.PlainText)
        self.banner.setWordWrap(True)
        self.banner.hide()
        organize_layout.addWidget(self.banner)
        self.preview = PreviewPanel()
        self.preview.planChanged.connect(self._plan_changed)
        self.preview.editRejected.connect(self._edit_rejected)
        organize_layout.addWidget(self.preview, 1)
        # The normal application hides test-only JSON-loading controls.
        # Standalone SearchPanel() keeps the JSON loader visible for developers.
        self.search_panel = SearchPanel(self, show_json_loader=False)
        self.search_panel.folder.setText(self.config.get("search_root"))
        self.search_panel.searchRequested.connect(self._search)
        self.search_panel.cancelRequested.connect(self._cancel_search)
        self.tabs.addTab(organize_page, "Organize Files")
        self.tabs.addTab(self.search_panel, "AI Search")
        self.progress = QProgressBar()
        self.progress.setFixedSize(160, 14)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.statusBar().addWidget(self.progress)
        self.status = QLabel("Ready")
        self.status.setTextFormat(Qt.PlainText)
        self.statusBar().addWidget(self.status, 1)
        self.backend = QLabel()
        self.statusBar().addPermanentWidget(self.backend)
        geometry = self.config.get("window_geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
        self._theme()
        self._buttons()

    def _theme(self):
        apply_theme(self.config.get("theme"))
        self.preview.set_dark(self.config.get("theme") == "dark")

    def _buttons(self):
        busy = self.worker is not None or self.search_worker is not None
        self.apply.setEnabled(not busy and self.ctx.runtime.available and bool(self.plan and self.plan.actions))
        self.undo.setEnabled(not busy and self.ctx.journal.get_latest_batch() is not None)
        scanning = self.worker is not None and self._job_kind == "scan"
        self.scan.setText("Stop" if scanning else "Scan")
        self.scan.setEnabled(not busy or scanning)
        for widget in (self.path, self.browse, self.sample, self.setup, self.settings, self.preview):
            widget.setEnabled(not busy)
        self.search_panel.set_external_busy(self.worker is not None)
        self.backend.setText(self.ctx.runtime.backend_label)

    def _invalidate(self, *_):
        self.plan = None
        self.inventory = None
        self.preview.clear()
        self._buttons()

    def _load_sample(self):
        root = Path.home() / "Desktop" / "DragDropTest"
        files = {
            "notes.txt": "Lunch with Sam.\n",
            "receipt.txt": "Coffee 4.50\n",
            "Photos/holiday.txt": "Beach photo placeholder.\n",
            "Notes/meeting.txt": "Monday standup notes.\n",
        }
        for relative, text in files.items():
            path = root.joinpath(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(text, encoding="utf-8")
        (root / "Trips").mkdir(exist_ok=True)
        entries = []
        for relative in files:
            path = root.joinpath(*relative.split("/"))
            parent = path.parent.name if path.parent != root else ""
            entries.append(FileEntry(
                relative, path.name, path.suffix.lower(), str(path),
                parent_folder=parent, depth=0 if parent == "" else 1,
            ))
        inventory = ScanInventory(str(root), entries=entries, directories=["Photos", "Notes", "Trips"])
        plan = OrganizationPlan(str(root), [
            PlanAction("1", "move", "receipt.txt", "Receipts/receipt.txt",
                       category="Documents", confidence=1, reason="Sample move"),
        ])
        self.path.setText(str(root))
        self.inventory = inventory
        self.plan = plan
        self.preview.set_plan(
            inventory, plan, self.config.get("shortcuts_movable"), self.config.get("executables_movable"),
        )
        self.status.setText("Sample loaded. Drag a file onto a folder in After.")
        self._buttons()

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder", self.path.text())
        if folder:
            self.path.setText(folder)
            self.config.set("allowed_root", folder)
            self._invalidate()

    def _show_banner(self, text):
        self.banner.setText(text)
        self.banner.setVisible(bool(text))

    def _edit_rejected(self, reason):
        self.status.setText(f"Edit rejected: {reason}")

    def _plan_changed(self, plan):
        self.plan = plan
        self.status.setText(f"Plan updated: {len(plan.actions):,} operations")
        self._buttons()

    def _scan(self):
        if self.search_worker:
            return
        if self.worker:
            self.worker.cancel()
            self.scan.setEnabled(False)
            self.status.setText("Stopping…")
            return
        if not self.ctx.runtime.available:
            self._invalidate()
            QMessageBox.warning(self, "Local AI required",
                                "Install the local AI engine and model using Setup AI before scanning and organizing files.")
            return
        root = Path(self.path.text().strip()).expanduser()
        valid, reason = validate_root_selection(root)
        if not valid:
            QMessageBox.warning(self, "Cannot scan", reason)
            return
        root = root.resolve()
        self.path.setText(str(root))
        self.config.set("allowed_root", str(root))
        options = {
            "max_files": self.config.get("max_files_per_scan"),
            "content_read_bytes": self.config.get("content_read_bytes"),
            "exclude_patterns": list(self.config.get("exclude_patterns")),
        }
        shortcuts = self.config.get("shortcuts_movable")
        executables = self.config.get("executables_movable")
        suffix = self.config.get("duplicate_suffix_allowed")
        self._invalidate()
        self._show_banner("")

        def operation(status, progress, cancelled):
            status("Scanning folder…")
            inventory = Scanner(root, **options, cancelled=cancelled,
                                progress_cb=lambda n, total, name: status(f"Scanning… {n:,} files")).scan()
            if cancelled():
                raise InterruptedError("Scan stopped")
            plan = Planner(self.ctx.runtime, progress_cb=status, cancelled=cancelled,
                           shortcuts_movable=shortcuts, executables_movable=executables,
                           allow_suffix_on_collision=suffix,
                           summary_cache_path=(None if self.config.app_data_dir.is_relative_to(root) else
                                               self.config.app_data_dir / "organizer_summaries.db")).plan(inventory)
            if cancelled():
                raise InterruptedError("Planning stopped")
            status("Validating proposed changes…")
            report = Validator(root, allow_suffix_on_collision=suffix,
                               shortcuts_movable=shortcuts,
                               executables_movable=executables).validate_plan(plan)
            plan.actions = report.valid_actions
            plan.warnings = report.warnings
            plan.excluded_count = inventory.total_files - len({a.src_rel for a in report.valid_actions})
            return inventory, plan

        self._start("scan", operation)

    def _apply(self):
        if not self.ctx.runtime.available or not self.plan or not self.plan.actions:
            return
        if QMessageBox.question(self, "Apply plan", f"Apply {len(self.plan.actions):,} file operations?",
                                QMessageBox.Yes | QMessageBox.Cancel) != QMessageBox.Yes:
            return
        plan = self.plan
        shortcuts = self.config.get("shortcuts_movable")
        executables = self.config.get("executables_movable")

        def operation(status, progress, cancelled):
            return Executor(
                Path(plan.root), self.ctx.journal, progress, cancelled, shortcuts, executables,
            ).apply(plan.actions)

        self._start("apply", operation)

    def _undo(self):
        batch = self.ctx.journal.get_latest_batch()
        if batch is None:
            return
        if QMessageBox.question(self, "Undo batch", f"Undo the most recent batch in {batch.root}?\n"
                                "Conflicting files will be skipped.",
                                QMessageBox.Yes | QMessageBox.Cancel) != QMessageBox.Yes:
            return

        def operation(status, progress, cancelled):
            return UndoEngine(Path(batch.root), self.ctx.journal, progress).undo_batch(batch.id)

        self._start("undo", operation)

    def _start(self, kind, operation):
        if self.search_worker:
            return
        self._job_kind = kind

        def work(status, progress, cancelled):
            try:
                return operation(status, progress, cancelled)
            finally:
                self.ctx.journal.close()

        self.worker = Worker(work, self)
        self.worker.status.connect(self.status.setText)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._job_finished)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText({"scan": "Scanning…", "apply": "Applying…", "undo": "Undoing…"}[kind])
        self._buttons()
        self.worker.start()

    def _progress(self, value):
        current, total, name = value
        self.progress.setRange(0, 1000 if total else 0)
        if total:
            self.progress.setValue(int(current / total * 1000))
        self.status.setText(f"{self._job_kind.capitalize()} {current:,}/{total:,}: {Path(name).name}")

    def _job_finished(self):
        worker = self.worker
        kind = self._job_kind
        self.worker = None
        self.progress.hide()
        if worker.error:
            self._invalidate()
            self.status.setText(f"Operation failed: {worker.error}")
            self._show_banner(worker.error)
        elif worker.stopped or (kind == "scan" and worker.cancel_event.is_set()):
            self.status.setText("Stopped. No new plan was applied.")
        elif kind == "scan":
            self.inventory, self.plan = worker.result
            self.preview.set_plan(
                self.inventory, self.plan,
                self.config.get("shortcuts_movable"), self.config.get("executables_movable"),
            )
            self.status.setText(
                f"{self.inventory.total_files:,} files · {len(self.plan.actions):,} changes · "
                f"{self.plan.excluded_count:,} unchanged · {self.inventory.skipped_count:,} skipped · "
                f"{self.inventory.error_count:,} scan errors"
            )
            warnings = [warning.message for warning in self.plan.warnings]
            self._show_banner("\n".join(warnings[:5]))
        else:
            result = worker.result
            self._invalidate()
            if kind == "apply":
                self.status.setText(f"{result.verified:,} applied and verified · {result.failed:,} failed · {result.skipped:,} skipped")
            else:
                self.status.setText(f"{result.reversed_ok:,} undone · {result.skipped:,} skipped")
            self._show_banner("\n".join(result.errors[:5]))
        self._buttons()
        worker.deleteLater()
        if self._close_pending:
            self.close()

    def _search(self, query):
        if self.worker or self.search_worker:
            return
        root = Path(self.search_panel.folder.text().strip()).expanduser()
        valid, reason = validate_root_selection(root)
        if not valid:
            self.search_panel.set_status(reason)
            return
        if not self.ctx.runtime.available:
            self.search_panel.set_status("Set up the local AI engine and Qwen model before searching.")
            self._setup()
            return
        root = root.resolve()
        self.config.set("search_root", str(root))
        self.search_panel.set_searching(True)

        def operation(status, progress, cancelled):
            return self.ctx.search.search(root, query, status, progress, cancelled)

        self.search_worker = Worker(operation, self)
        self.search_worker.status.connect(self.search_panel.set_status)
        self.search_worker.progress.connect(lambda value: self.search_panel.set_status(value[2]))
        self.search_worker.finished.connect(self._search_finished)
        self._buttons()
        self.search_worker.start()

    def _cancel_search(self):
        if self.search_worker:
            self.search_worker.cancel()
            self.search_panel.cancel_button.setEnabled(False)
            self.search_panel.set_status("Stopping search…")

    def _search_finished(self):
        worker = self.search_worker
        self.search_worker = None
        self.search_panel.set_searching(False)
        if worker.error:
            self.search_panel.set_status(f"Search failed: {worker.error}")
        elif worker.stopped or worker.cancel_event.is_set():
            self.search_panel.set_status("Search stopped.")
        else:
            self.search_panel.display_results(worker.result)
        worker.deleteLater()
        self._buttons()
        if self._close_pending:
            self.close()

    def _setup(self):
        FirstRunDialog(self.ctx, self).exec()
        self._invalidate()
        self._buttons()

    def _settings(self):
        if SettingsDialog(self.ctx, self).exec():
            self.path.setText(self.config.get("allowed_root"))
            self._theme()
            self._invalidate()
            self.ctx.runtime.shutdown()
            self.ctx.search.exclude_patterns = self.config.get("exclude_patterns")
        self._buttons()

    def show_first_run_if_needed(self):
        if not self.ctx.runtime.available:
            if self.ctx.hardware.os_warning:
                self._show_banner(self.ctx.hardware.os_warning)
            else:
                self._setup()

    def closeEvent(self, event):
        if self.worker or self.search_worker:
            self._close_pending = True
            if self.worker:
                self.worker.cancel()
            if self.search_worker:
                self._cancel_search()
            self.status.setText("Finishing the current operation before closing…")
            event.ignore()
            return
        self.config.set("window_geometry", bytes(self.saveGeometry().toBase64()).decode())
        self.ctx.shutdown()
        event.accept()
