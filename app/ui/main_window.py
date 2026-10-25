"""Desktop scan → preview → apply → undo workflow."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app.constants import APP_NAME
from app.core.blocked_paths import validate_root_selection
from app.core.executor import Executor
from app.core.planner import Planner
from app.core.scanner import Scanner
from app.core.undo import UndoEngine
from app.core.validator import Validator
from app.ui.first_run_dialog import FirstRunDialog
from app.ui.preview_panel import PreviewPanel
from app.ui.settings_dialog import SettingsDialog
from app.ui.theme import apply_theme
from app.ui.worker import Worker


class MainWindow(QMainWindow):
    def __init__(self, context):
        super().__init__()
        self.ctx = context
        self.config = context.config
        self.worker = None
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
        toolbar = QHBoxLayout()
        layout.addLayout(toolbar)
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
        layout.addWidget(self.banner)
        self.preview = PreviewPanel()
        self.preview.planChanged.connect(self._plan_changed)
        self.preview.editRejected.connect(self._edit_rejected)
        layout.addWidget(self.preview, 1)
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
        busy = self.worker is not None
        self.apply.setEnabled(not busy and self.ctx.runtime.available and bool(self.plan and self.plan.actions))
        self.undo.setEnabled(not busy and self.ctx.journal.get_latest_batch() is not None)
        self.scan.setText("Stop" if busy and self._job_kind == "scan" else "Scan")
        self.scan.setEnabled(not busy or self._job_kind == "scan")
        for widget in (self.path, self.browse, self.setup, self.settings, self.preview):
            widget.setEnabled(not busy)
        self.backend.setText(self.ctx.runtime.backend_label)

    def _invalidate(self, *_):
        self.plan = None
        self.inventory = None
        self.preview.clear()
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
                           shortcuts_movable=shortcuts).plan(inventory)
            if cancelled():
                raise InterruptedError("Planning stopped")
            status("Validating proposed changes…")
            report = Validator(root, allow_suffix_on_collision=suffix,
                               shortcuts_movable=shortcuts).validate_plan(plan)
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

        def operation(status, progress, cancelled):
            return Executor(Path(plan.root), self.ctx.journal, progress, cancelled, shortcuts).apply(plan.actions)

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
            self.preview.set_plan(self.inventory, self.plan, self.config.get("shortcuts_movable"))
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
                self.status.setText(f"{result.succeeded:,} applied · {result.failed:,} failed · {result.skipped:,} skipped")
            else:
                self.status.setText(f"{result.reversed_ok:,} undone · {result.skipped:,} skipped")
            self._show_banner("\n".join(result.errors[:5]))
        self._buttons()
        worker.deleteLater()
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
        self._buttons()

    def show_first_run_if_needed(self):
        if not self.ctx.runtime.available:
            if self.ctx.hardware.os_warning:
                self._show_banner(self.ctx.hardware.os_warning)
            else:
                self._setup()

    def closeEvent(self, event):
        if self.worker:
            self._close_pending = True
            self.worker.cancel()
            self.status.setText("Finishing the current operation before closing…")
            event.ignore()
            return
        self.config.set("window_geometry", bytes(self.saveGeometry().toBase64()).decode())
        self.ctx.shutdown()
        event.accept()
