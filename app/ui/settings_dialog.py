"""Preferences and model management without a second settings database."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from app.core.blocked_paths import blocked_roots, validate_root_selection
from app.ui.first_run_dialog import FirstRunDialog


class SettingsDialog(QDialog):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.ctx = context
        config = context.config
        self.setWindowTitle("QuickFiles — Settings")
        self.resize(620, 490)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        general = QWidget()
        form = QFormLayout(general)
        self.theme = QComboBox()
        self.theme.addItems(["light", "dark"])
        self.theme.setCurrentText(config.get("theme"))
        form.addRow("Theme", self.theme)
        root_row = QWidget()
        row = QHBoxLayout(root_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.root = QLineEdit(config.get("allowed_root"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.root)
        row.addWidget(browse)
        form.addRow("Selected folder", root_row)
        self.shortcuts = QCheckBox("Allow moving shortcuts")
        self.shortcuts.setChecked(config.get("shortcuts_movable"))
        form.addRow(self.shortcuts)
        self.executables = QCheckBox("Allow moving executables")
        self.executables.setChecked(config.get("executables_movable"))
        form.addRow(self.executables)
        self.suffix = QCheckBox("Add a numbered suffix when destinations collide")
        self.suffix.setChecked(config.get("duplicate_suffix_allowed"))
        form.addRow(self.suffix)
        tabs.addTab(general, "General")

        scan = QWidget()
        scan_form = QFormLayout(scan)
        self.exclusions = QPlainTextEdit("\n".join(config.get("exclude_patterns")))
        self.exclusions.setPlaceholderText("One pattern per line, e.g. *.tmp")
        scan_form.addRow("Exclusion patterns", self.exclusions)
        self.max_files = QSpinBox()
        self.max_files.setRange(1, 999_999_999)
        self.max_files.setValue(config.get("max_files_per_scan"))
        scan_form.addRow("Maximum files", self.max_files)
        protected = QLabel("\n".join(str(path) for path in blocked_roots()))
        protected.setTextFormat(Qt.PlainText)
        protected.setWordWrap(True)
        scan_form.addRow("Protected system paths", protected)
        tabs.addTab(scan, "Scan rules")

        ai = QWidget()
        ai_layout = QVBoxLayout(ai)
        ai_layout.addWidget(QLabel("A local AI engine and model are required to organize files."))
        self.model_status = QLabel()
        self.model_status.setWordWrap(True)
        ai_layout.addWidget(self.model_status)
        setup = QPushButton("Set up local AI…")
        setup.clicked.connect(self._setup)
        ai_layout.addWidget(setup)
        self.delete_model = QPushButton("Delete downloaded model")
        self.delete_model.clicked.connect(self._delete)
        ai_layout.addWidget(self.delete_model)
        hardware = context.hardware
        info = QLabel(
            f"CPU: {hardware.cpu_name} ({hardware.cpu_cores} cores)\n"
            f"RAM: {hardware.ram_gb:.1f} GB\nGPU: {hardware.gpu_name or 'none detected'}\n"
            f"Acceleration: {hardware.backend.upper()}\n"
            f"Application data: {config.app_data_dir}"
        )
        info.setTextFormat(Qt.PlainText)
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ai_layout.addWidget(info)
        ai_layout.addStretch()
        tabs.addTab(ai, "AI and runtime")
        self._refresh_model()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder", self.root.text())
        if folder:
            self.root.setText(folder)

    def _refresh_model(self):
        manager = self.ctx.model_manager
        engine = "installed" if self.ctx.binary_manager.is_available else "not installed"
        model = f"{manager.disk_usage_mb:.0f} MB installed" if manager.is_downloaded else "not installed"
        self.model_status.setText(f"Engine: {engine}\n{manager.model_info['label']}: {model}")
        self.delete_model.setEnabled(manager.model_path.is_file())

    def _setup(self):
        FirstRunDialog(self.ctx, self).exec()
        self._refresh_model()

    def _delete(self):
        if QMessageBox.question(self, "Delete model", "Delete the downloaded AI model? Organizing files will be unavailable until it is installed again.",
                                QMessageBox.Yes | QMessageBox.Cancel) != QMessageBox.Yes:
            return
        self.ctx.runtime.shutdown()
        try:
            self.ctx.model_manager.delete()
        except OSError as exc:
            QMessageBox.warning(self, "Cannot delete model", str(exc))
            return
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
            "duplicate_suffix_allowed": self.suffix.isChecked(), "max_files_per_scan": self.max_files.value(),
            "exclude_patterns": [line.strip() for line in self.exclusions.toPlainText().splitlines() if line.strip()],
        })
        self.accept()
