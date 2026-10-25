"""Preferences and model management without a second settings database."""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from app.core.blocked_paths import blocked_roots, validate_root_selection
from app.ui.first_run_dialog import FirstRunDialog


def _note(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color:#888; font-size:11px;")
    return label


class SettingsDialog(QDialog):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.ctx = context
        self.setWindowTitle("Settings")
        self.setMinimumSize(580, 500)
        self.setModal(True)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._general(), "General")
        tabs.addTab(self._ai(), "AI")
        tabs.addTab(self._scan_rules(), "Scan Rules")
        tabs.addTab(self._runtime(), "Runtime Info")
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_model()

    def _general(self):
        config = self.ctx.config
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(12, 16, 12, 12)
        column.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(12)
        self.theme = QComboBox()
        self.theme.addItems(["light", "dark"])
        self.theme.setCurrentText(config.get("theme"))
        form.addRow("Theme:", self.theme)
        root_row = QWidget()
        row = QHBoxLayout(root_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.root = QLineEdit(config.get("allowed_root"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.root)
        row.addWidget(browse)
        form.addRow("Allowed root:", root_row)
        self.shortcuts = QCheckBox("Allow moving shortcuts (.lnk, .url)")
        self.shortcuts.setChecked(config.get("shortcuts_movable"))
        form.addRow(self.shortcuts)
        self.executables = QCheckBox("Allow moving executables (.exe, .bat, .msi…)")
        self.executables.setChecked(config.get("executables_movable"))
        form.addRow(self.executables)
        column.addLayout(form)

        features = QGroupBox("Features")
        feature_column = QVBoxLayout(features)
        self.watch = QCheckBox("Watch for new files")
        self.watch.setChecked(config.get("watch_new_files"))
        feature_column.addWidget(self.watch)
        feature_column.addWidget(_note(
            "New files in your scanned folder are sorted automatically, and you'll be told where "
            "each one went. QuickFiles stays in the tray when closed; right-click it to quit."
        ))
        column.addWidget(features)
        column.addStretch()
        return page

    def _ai(self):
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(12, 16, 12, 12)
        column.setSpacing(12)
        manager = self.ctx.model_manager
        model_group = QGroupBox("AI Model")
        model_row = QHBoxLayout(model_group)
        name = QLabel(f"<b>{manager.model_info['label']}</b>")
        name.setTextFormat(Qt.RichText)
        model_row.addWidget(name)
        self.model_status = QLabel()
        model_row.addWidget(self.model_status)
        model_row.addStretch()
        column.addWidget(model_group)
        vision_group = QGroupBox("Vision Model")
        self.vision_row = QHBoxLayout(vision_group)
        vision_name = QLabel(f"<b>{manager.vision_model_info['label']}</b>")
        vision_name.setTextFormat(Qt.RichText)
        self.vision_row.addWidget(vision_name)
        self.vision_status = QLabel()
        self.vision_row.addWidget(self.vision_status)
        self.vision_row.addStretch()
        column.addWidget(vision_group)
        engine_group = QGroupBox("AI Engine (llama-server)")
        engine_row = QHBoxLayout(engine_group)
        self.engine_status = QLabel()
        engine_row.addWidget(self.engine_status)
        engine_row.addStretch()
        self.install = QPushButton("Install Everything")
        self.install.setObjectName("applyBtn")
        self.install.clicked.connect(self._setup)
        engine_row.addWidget(self.install)
        column.addWidget(engine_group)
        column.addWidget(_note("A local AI engine and model are required to organize and search files."))
        column.addStretch()
        return page

    def _scan_rules(self):
        config = self.ctx.config
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(12, 16, 12, 12)
        column.setSpacing(12)
        locked = QGroupBox("Permanently Blocked System Paths  (read-only)")
        locked_column = QVBoxLayout(locked)
        locked_column.addWidget(_note("These locations are always protected and cannot be scanned."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(150)
        scroll.setFrameShape(QFrame.StyledPanel)
        paths = QWidget()
        path_column = QVBoxLayout(paths)
        path_column.setSpacing(3)
        path_column.setContentsMargins(4, 4, 4, 4)
        paths_shown = [str(path) for path in blocked_roots()]
        if sys.platform == "win32":
            paths_shown.append(r"Any drive:\$Recycle.Bin, System Volume Information, Recovery")
        seen = set()
        for path in paths_shown:
            if path.casefold() in seen:
                continue
            seen.add(path.casefold())
            label = QLabel(f"  🚫  {path}")
            label.setTextFormat(Qt.PlainText)
            label.setStyleSheet("color:#888; font-size:11px; font-family:Consolas,monospace;")
            path_column.addWidget(label)
        path_column.addStretch()
        scroll.setWidget(paths)
        locked_column.addWidget(scroll)
        column.addWidget(locked)
        user = QGroupBox("User-Defined Exclusion Patterns")
        user_column = QVBoxLayout(user)
        user_column.addWidget(QLabel("One pattern per line. Wildcards: *.tmp  ~$*  my_folder"))
        self.exclusions = QPlainTextEdit("\n".join(config.get("exclude_patterns")))
        self.exclusions.setFixedHeight(100)
        self.exclusions.setPlaceholderText("*.tmp\n~$*\nThumbs.db")
        user_column.addWidget(self.exclusions)
        column.addWidget(user)
        column.addStretch()
        return page

    def _runtime(self):
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(12, 16, 12, 12)
        column.setSpacing(10)
        hardware = self.ctx.hardware
        group = QGroupBox("Hardware")
        group_column = QVBoxLayout(group)
        info = QLabel(
            f"CPU: {hardware.cpu_name} ({hardware.cpu_cores} cores)\n"
            f"RAM: {hardware.ram_gb:.1f} GB  |  Backend: {hardware.backend.upper()}\n"
            f"GPU: {hardware.gpu_name or 'none detected'}\n"
            f"Application data: {self.ctx.config.app_data_dir}"
        )
        info.setTextFormat(Qt.PlainText)
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info.setStyleSheet("font-size:12px; font-family:Consolas,monospace;")
        group_column.addWidget(info)
        column.addWidget(group)
        column.addWidget(QLabel(f"Active backend: {self.ctx.runtime.backend_label}"))
        self.storage = QLabel()
        column.addWidget(self.storage)
        column.addStretch()
        return page

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Root", self.root.text())
        if folder:
            self.root.setText(folder)

    def _refresh_model(self):
        manager = self.ctx.model_manager
        installed = manager.is_downloaded
        self.model_status.setText(f"✅  {manager.disk_usage_mb:.0f} MB installed" if installed else "⬜  Not installed")
        vision = manager.vision_downloaded
        self.vision_status.setText(f"✅  {manager.vision_disk_usage_mb:.0f} MB installed" if vision
                                   else "⬜  Not installed")
        engine = self.ctx.binary_manager.is_available
        self.engine_status.setText("✅  Installed" if engine else "⬜  Not installed")
        self.install.setVisible(not (engine and installed and vision))
        self.storage.setText(f"Model storage: {manager.disk_usage_mb + manager.vision_disk_usage_mb:.0f} MB")

    def _setup(self):
        FirstRunDialog(self.ctx, self).exec()
        self._refresh_model()

    def _save(self):
        root = Path(self.root.text().strip()).expanduser()
        valid, reason = validate_root_selection(root)
        if not valid:
            QMessageBox.warning(self, "Invalid folder", reason)
            return
        self.ctx.config.update({
            "theme": self.theme.currentText(),
            "allowed_root": str(root.resolve()), "shortcuts_movable": self.shortcuts.isChecked(),
            "executables_movable": self.executables.isChecked(),
            "exclude_patterns": [line.strip() for line in self.exclusions.toPlainText().splitlines() if line.strip()],
            "watch_new_files": self.watch.isChecked(),
        })
        self.accept()
