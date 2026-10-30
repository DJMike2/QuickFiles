"""Desktop scan → preview → apply → undo workflow."""
from __future__ import annotations

import logging
import time
from contextlib import nullcontext
from pathlib import Path

from PySide6.QtCore import QByteArray, QFileSystemWatcher, Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QProgressBar, QPushButton, QStackedWidget, QStyle,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

from app.constants import APP_NAME, CONSERVATIVE_EXTENSIONS, EXECUTABLE_EXTENSIONS, SHORTCUT_EXTENSIONS
from app.ai.content import read_content
from app.ai.schema import PlanAction
from app.core.arrival import ScanIndex, is_ignored
from app.core.blocked_paths import validate_root_selection
from app.core.executor import Executor
from app.core.inventory import FileEntry
from app.core.file_state import identity
from app.core.planner import Planner
from app.core.scanner import Scanner
from app.core.undo import UndoEngine
from app.core.validator import Validator
from app.ui.first_run_dialog import FirstRunDialog
from app.ui.preview_panel import PreviewPanel
from app.ui.search_panel import SearchPanel
from app.ui.settings_dialog import SettingsDialog
from app.ui.theme import apply_theme
from app.ui.title_bar import EdgeResizer, TitleBar
from app.ui.worker import Worker
from app.logging_setup import log_stage

log = logging.getLogger(__name__)


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
        self._opened_at = time.time()
        self._seen_names = set()
        self._pending_sizes = {}
        self._index = ScanIndex()
        self.setWindowTitle(APP_NAME)
        self.setObjectName("mainWindow")
        # The minimize/maximize hints keep taskbar minimize and Win+arrow snapping on a frameless window.
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowSystemMenuHint
                            | Qt.WindowMinMaxButtonsHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.title_bar = TitleBar(self)
        self.setMenuWidget(self.title_bar)
        self.resize(1200, 750)
        self.setMinimumSize(1060, 680)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        toolbar = self._build_toolbar(layout)
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setTextFormat(Qt.PlainText)
        self.banner.setWordWrap(True)
        self.banner.hide()
        layout.addWidget(self.banner)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.preview = PreviewPanel()
        self.preview.planChanged.connect(self._plan_changed)
        self.preview.editRejected.connect(self._edit_rejected)
        self.pages.addWidget(self.preview)
        self.search_panel = SearchPanel(self)
        self.search_panel.folder.setText(self.config.get("search_root"))
        self.search_panel.searchRequested.connect(self._search)
        self.search_panel.cancelRequested.connect(self._cancel_search)
        self.search_panel.query.textEdited.connect(self._prepare_search)
        self.pages.addWidget(self.search_panel)
        toolbar.insertWidget(1, self.search_panel.scope_bar, 1)
        self.progress = QProgressBar()
        self.progress.setFixedSize(160, 14)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.statusBar().setSizeGripEnabled(True)
        self.statusBar().addWidget(self.progress)
        self.status = QLabel("Ready")
        self.status.setObjectName("statusLabel")
        self.status.setTextFormat(Qt.PlainText)
        self.statusBar().addWidget(self.status, 1)
        self.backend = QLabel()
        self.backend.setObjectName("statusLabel")
        self.statusBar().addPermanentWidget(self.backend)
        self._show_view(0)
        geometry = self.config.get("window_geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._schedule_arrivals)
        self._arrival_timer = QTimer(self)
        self._arrival_timer.setSingleShot(True)
        self._arrival_timer.setInterval(2000)
        self._arrival_timer.timeout.connect(self._check_arrivals)
        self._tray = QSystemTrayIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon), self)
        self._tray.setToolTip(APP_NAME)
        tray_menu = QMenu(self)
        tray_menu.addAction("Show", self._show_from_tray)
        tray_menu.addAction("Quit", self._quit_from_tray)
        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._tray_activated)
        self.winId()
        self._resizer = EdgeResizer(self)
        self._theme()
        self._buttons()
        if self._watching():
            self._arm_watch()

    def _build_toolbar(self, layout):
        toolbar_frame = QFrame()
        toolbar_frame.setObjectName("toolbar")
        toolbar_frame.setFixedHeight(44)
        toolbar = QHBoxLayout(toolbar_frame)
        toolbar.setContentsMargins(10, 5, 10, 5)
        toolbar.setSpacing(6)
        layout.addWidget(toolbar_frame)
        # Organize and AI Search share the toolbar; folder controls only apply to Organize.
        self.organize_tools = QWidget()
        self.organize_tools.setObjectName("organizeTools")
        tools = QHBoxLayout(self.organize_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(6)
        folder_label = QLabel("Folder:")
        folder_label.setObjectName("toolbarLabel")
        tools.addWidget(folder_label)
        self.path = QLineEdit(self.config.get("allowed_root"))
        self.path.setMinimumWidth(200)
        self.path.textEdited.connect(self._invalidate)
        tools.addWidget(self.path, 1)
        self.browse = QPushButton("Browse…")
        self.browse.clicked.connect(self._browse)
        self.scan = QPushButton("Scan")
        self.scan.clicked.connect(self._scan)
        self.apply = QPushButton("Apply")
        self.apply.setObjectName("applyBtn")
        self.apply.clicked.connect(self._apply)
        self.undo = QPushButton("⟲ Undo")
        self.undo.setObjectName("undoBtn")
        self.undo.clicked.connect(self._undo)
        for button in (self.browse, self.scan, self.apply, self.undo):
            button.setFixedHeight(28)
            tools.addWidget(button)
        toolbar.addWidget(self.organize_tools, 1)
        toolbar.addStretch()
        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setStyleSheet("color:#ccc;")
        toolbar.addWidget(separator)
        toolbar.addWidget(QLabel("View:"))
        self.view_buttons = QButtonGroup(self)
        for index, (text, radius) in enumerate((("Organize", "left"), ("AI Search", "right"))):
            button = QPushButton(text)
            button.setObjectName("viewBtn")
            button.setCheckable(True)
            button.setFixedHeight(28)
            button.setStyleSheet(f"QPushButton#viewBtn {{ border-top-{radius}-radius:4px; "
                                 f"border-bottom-{radius}-radius:4px; }}")
            self.view_buttons.addButton(button, index)
            toolbar.addWidget(button)
        self.view_buttons.idClicked.connect(self._show_view)
        self.setup = QPushButton("⬇ Setup AI")
        self.setup.setFixedHeight(28)
        self.setup.setToolTip("Download AI engine and model")
        self.setup.clicked.connect(self._setup)
        toolbar.addWidget(self.setup)
        self.settings = QPushButton("⚙")
        self.settings.setObjectName("iconBtn")
        self.settings.setFixedSize(28, 28)
        self.settings.setToolTip("Settings")
        self.settings.clicked.connect(self._settings)
        toolbar.addWidget(self.settings)
        return toolbar

    def _theme(self):
        apply_theme(self.config.get("theme"))
        self.preview.set_dark(self.config.get("theme") == "dark")
        self.search_panel.set_dark(self.config.get("theme") == "dark")

    def _show_view(self, index):
        self.view_buttons.button(index).setChecked(True)
        self.pages.setCurrentIndex(index)
        self.organize_tools.setVisible(index == 0)
        self.search_panel.scope_bar.setVisible(index == 1)

    def _buttons(self):
        busy = self.worker is not None or self.search_worker is not None
        self.apply.setEnabled(not busy and self.ctx.runtime.available and bool(self.plan and self.plan.actions))
        self.undo.setEnabled(not busy and self._undoable_batch() is not None)
        scanning = self.worker is not None and self._job_kind == "scan"
        self.scan.setText("Stop" if scanning else "Scan")
        self.scan.setObjectName("stopBtn" if scanning else "")
        self.scan.style().unpolish(self.scan)
        self.scan.style().polish(self.scan)
        self.scan.setEnabled(not busy or scanning)
        for widget in (self.path, self.browse, self.setup, self.settings):
            widget.setEnabled(not busy)
        # Preview edits only change the pending plan; search never consumes it.
        self.preview.setEnabled(self.worker is None)
        self.setup.setVisible(not self.ctx.runtime.available)
        self.search_panel.set_external_busy(self.worker is not None)
        self.backend.setText(f"  {self.ctx.runtime.backend_label}  ")

    def _undoable_batch(self):
        """Only moves applied since this window opened can be undone."""
        batch = self.ctx.journal.get_latest_batch()
        if batch is None or batch.applied_at < self._opened_at:
            return None
        if not any(op.succeeded and not op.undone for op in batch.ops):
            return None
        return batch

    def _watching(self):
        return bool(self.config.get("watch_new_files"))

    def _arm_watch(self):
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())
        self._arrival_timer.stop()
        self._pending_sizes.clear()
        if self._close_pending or not self._watching():
            self._tray.hide()
            return
        root = self._watch_root()
        if root is not None:
            self._watcher.addPath(str(root))
            self._note_present(root)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

    def _watch_root(self):
        root = Path(self.path.text().strip()).expanduser()
        valid, _reason = validate_root_selection(root)
        if not valid or not root.is_dir():
            return None
        return root.resolve()

    def _note_present(self, root):
        self._seen_names = {child.name for child in root.iterdir() if child.is_file()}

    def _learn_scan(self):
        if self.inventory is not None:
            self._index.learn(self.inventory)
        root = self._watch_root()
        if root is not None and self._watching():
            self._note_present(root)

    def _schedule_arrivals(self, _path=""):
        if not self._close_pending and self._watching():
            self._arrival_timer.start()

    def _check_arrivals(self):
        if self._close_pending:
            return
        if not self._watching() or self.worker or self.search_worker:
            if self._watching():
                self._arrival_timer.start()
            return
        root = self._watch_root()
        if root is None or not self._index.folders:
            return
        waiting = False
        ready = []
        patterns = list(self.config.get("exclude_patterns") or [])
        try:
            children = [child for child in root.iterdir() if child.is_file()]
        except OSError:
            return
        for child in children:
            if child.name in self._seen_names or is_ignored(child.name, patterns):
                continue
            try:
                size = child.stat().st_size
            except OSError:
                continue
            if self._pending_sizes.get(child.name) != size:
                self._pending_sizes[child.name] = size
                waiting = True
                continue
            self._pending_sizes.pop(child.name, None)
            self._seen_names.add(child.name)
            if self._can_move(child):
                ready.append(child)
        if ready:
            self._ask_model(root, ready)
        if waiting:
            self._arrival_timer.start()

    def _can_move(self, path):
        extension = path.suffix.lower()
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size <= 0:
            return False
        if extension in SHORTCUT_EXTENSIONS:
            return bool(self.config.get("shortcuts_movable"))
        if extension in EXECUTABLE_EXTENSIONS:
            return bool(self.config.get("executables_movable"))
        return extension not in CONSERVATIVE_EXTENSIONS

    def _ask_model(self, root, paths):
        if not self._index.folders:
            self.status.setText("New files will be organized after the next scan.")
            return
        if not self.ctx.runtime.available:
            self.status.setText("Local AI is not set up, so the new file was left in place.")
            return
        folders = dict(self._index.folders)
        limit = self.config.get("content_read_bytes")
        shortcuts = self.config.get("shortcuts_movable")
        executables = self.config.get("executables_movable")

        def operation(status, progress, cancelled):
            files = []
            try:
                for index, path in enumerate(paths, 1):
                    if cancelled():
                        raise InterruptedError("Planning stopped")
                    extension = path.suffix.lower()
                    stat = path.stat()
                    entry = FileEntry(
                        path.name, path.name, extension, str(path), size_bytes=stat.st_size,
                        modified_ts=stat.st_mtime,
                        is_conservative=extension in CONSERVATIVE_EXTENSIONS, depth=0,
                    )
                    files.append({
                        "id": index, "name": path.name, "rel_path": path.name,
                        "hint": read_content(entry, limit), "source_identity": identity(stat),
                    })
                status("Asking local AI about the new file…")
                assigned = self.ctx.runtime.assign_files(
                    files, folders, status_cb=status, cancelled=cancelled,
                )
                moved = []
                executor = Executor(root, self.ctx.journal, progress, cancelled, shortcuts, executables)
                for row in files:
                    folder = assigned.get(row["rel_path"])
                    if not folder or cancelled():
                        continue
                    action = PlanAction(
                        str(row["id"]), "move", row["name"], f"{folder}/{row['name']}",
                        category=folder, reason="Local AI matched the file to this folder",
                        source_identity=row['source_identity'],
                    )
                    if executor.apply([action]).succeeded:
                        moved.append((row["name"], folder))
                return moved
            finally:
                self.ctx.runtime.shutdown()

        self._start("watch", operation)

    def _show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        self.close()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_from_tray()

    def _invalidate(self, *_):
        self.plan = None
        self.inventory = None
        self._index = ScanIndex()
        self.preview.clear()
        self._buttons()
        if self._watching():
            self._arm_watch()

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
            self.ctx.cancel_ai()
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
            log.warning("Organizer scan rejected: %s", reason)
            QMessageBox.warning(self, "Cannot scan", reason)
            return
        root = root.resolve()
        self.path.setText(str(root))
        self.config.set("allowed_root", str(root))
        options = {
            "content_read_bytes": self.config.get("content_read_bytes"),
            "exclude_patterns": list(self.config.get("exclude_patterns")),
        }
        shortcuts = self.config.get("shortcuts_movable")
        executables = self.config.get("executables_movable")
        self._invalidate()
        self._show_banner("")

        def operation(status, progress, cancelled):
            log.info("Scan location: %s", root)
            status("Scanning folder…")
            with log_stage(log, "Inventory"):
                inventory = Scanner(root, **options, cancelled=cancelled,
                                    progress_cb=lambda n, total, name: status(f"Scanning… {n:,} files")).scan()
            log.info("Inventory: files=%d folders=%d protected=%d skipped=%d errors=%d",
                     inventory.total_files, len(inventory.directories), len(inventory.protected_projects),
                     inventory.skipped_count, inventory.error_count)
            if cancelled():
                raise InterruptedError("Scan stopped")
            plan = Planner(self.ctx.runtime, progress_cb=status, cancelled=cancelled,
                           shortcuts_movable=shortcuts, executables_movable=executables,
                           allow_suffix_on_collision=False,
                           summary_cache_path=(None if self.config.app_data_dir.is_relative_to(root) else
                                               self.config.app_data_dir / "organizer_summaries.db")).plan(inventory)
            if cancelled():
                raise InterruptedError("Planning stopped")
            status("Validating proposed changes…")
            with log_stage(log, "Plan validation"):
                report = Validator(root, shortcuts_movable=shortcuts,
                                   executables_movable=executables).validate_plan(plan)
            plan.actions = report.valid_actions
            plan.warnings = report.warnings
            plan.excluded_count = inventory.total_files - len({a.src_rel for a in report.valid_actions})
            log.info("Preview ready: moves=%d held=%d warnings=%d",
                     len(plan.actions), plan.excluded_count, len(plan.warnings))
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
        batch = self._undoable_batch()
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
                activity = getattr(self.ctx, "ai_operation", None)
                scope = activity(kind, cancelled) if activity and kind in {"scan", "watch"} else nullcontext()
                with scope:
                    return operation(status, progress, cancelled)
            finally:
                self.ctx.journal.close()

        self.worker = Worker(work, self, operation_name=kind)
        self.worker.status.connect(self.status.setText)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._job_finished)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText({
            "scan": "Scanning…", "apply": "Applying…", "undo": "Undoing…",
            "watch": "Asking local AI about the new file…",
        }[kind])
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
        if worker.error and kind == "watch":
            self.status.setText(f"New file left in place: {worker.error}")
        elif worker.error:
            self._invalidate()
            self.status.setText(f"Operation failed: {worker.error}")
            self._show_banner(worker.error)
        elif kind == "watch" and (worker.stopped or worker.cancel_event.is_set()):
            self.status.setText("Stopped. The new file was left in place.")
        elif worker.stopped or (kind == "scan" and worker.cancel_event.is_set()):
            self.status.setText("Stopped. No new plan was applied.")
        elif kind == "watch":
            for name, folder in worker.result or []:
                self._index.add(folder, name)
                self._tray.showMessage(APP_NAME, f"{name} move to {folder}",
                                       QSystemTrayIcon.MessageIcon.Information, 5000)
            moved = worker.result or []
            self.status.setText(
                f"{moved[-1][0]} move to {moved[-1][1]}" if moved else "New file left in place."
            )
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
            self._learn_scan()
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
            log.warning("File search rejected: %s", reason)
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
            activity = getattr(self.ctx, "ai_operation", None)
            with activity("search", cancelled) if activity else nullcontext():
                return self.ctx.search.search(root, query, status, progress, cancelled)

        self.search_worker = Worker(operation, self, operation_name="search")
        self.search_worker.status.connect(self.search_panel.set_status)
        self.search_worker.progress.connect(lambda value: self.search_panel.set_status(value[2]))
        self.search_worker.finished.connect(self._search_finished)
        self._buttons()
        self.search_worker.start()

    def _prepare_search(self, text):
        # Typing takes seconds, about as long as loading the models after they were released.
        prepare = getattr(self.ctx, "prepare_search", None)
        if prepare and text.strip() and not (self.worker or self.search_worker):
            prepare()

    def _cancel_search(self):
        if self.search_worker:
            self.search_worker.cancel()
            self.ctx.cancel_ai()
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
        if self._close_pending:
            self.close()
            return
        self._invalidate()
        self._buttons()

    def _settings(self):
        keys = ("allowed_root", "shortcuts_movable", "executables_movable", "exclude_patterns")
        before = [self.config.get(key) for key in keys]
        if SettingsDialog(self.ctx, self).exec():
            self.path.setText(self.config.get("allowed_root"))
            self._theme()
            # Only settings that change the scan or its plan discard the current preview.
            if before != [self.config.get(key) for key in keys]:
                self._invalidate()
            self.ctx.runtime.shutdown()
            self.ctx.search.exclude_patterns = self.config.get("exclude_patterns")
            self._arm_watch()
            if self._watching():
                self.status.setText("Watching for new files. They are compared to the next scan of this folder.")
        self._buttons()

    def show_first_run_if_needed(self):
        if self._close_pending:
            return
        if not self.ctx.runtime.available:
            self._setup()

    def closeEvent(self, event):
        # Closing the window exits, even when folder watching is enabled.
        self._close_pending = True
        self._arm_watch()
        installing = False
        for dialog in self.findChildren(QDialog):
            if dialog.isVisible():
                dialog.reject()
            if isinstance(dialog, FirstRunDialog) and dialog.worker is not None:
                installing = True
        if installing:
            # _setup resumes and closes this window after the install stops.
            event.ignore()
            return
        if self.worker or self.search_worker:
            if self.worker:
                self.worker.cancel()
            if self.search_worker:
                self._cancel_search()
            self.ctx.cancel_ai()
            self.status.setText("Finishing the current operation before closing…")
            event.ignore()
            return
        self.config.set("window_geometry", bytes(self.saveGeometry().toBase64()).decode())
        self._tray.hide()
        self.ctx.shutdown()
        event.accept()
