"""Optional local AI installation using the shared background worker."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout,
)

from app.ui.worker import Worker


class FirstRunDialog(QDialog):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.ctx = context
        self.worker = None
        self._close_pending = False
        self.setWindowTitle("QuickFiles — Setup AI")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        hardware = context.hardware
        model = context.model_manager.model_info
        title = QLabel("Set up local AI")
        font = title.font()
        font.setPointSize(17)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        info = QLabel(
            f"{hardware.cpu_name} · {hardware.cpu_cores} CPU cores · {hardware.ram_gb:.1f} GB RAM\n"
            f"GPU: {hardware.gpu_name or 'none detected'} ({hardware.backend.upper()})\n\n"
            f"Install llama.cpp and {model['label']} (about {model['size_mb'] / 1000:.1f} GB).\n"
            "After installation, file planning runs on this device.\n"
            "You can skip installation and use the built-in rules."
        )
        info.setTextFormat(Qt.PlainText)
        info.setWordWrap(True)
        layout.addWidget(info)
        self.status = QLabel("Ready to install.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.skip = QPushButton("Use built-in rules")
        self.skip.clicked.connect(self._skip)
        buttons.addWidget(self.skip)
        buttons.addStretch()
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self._cancel)
        self.cancel.hide()
        buttons.addWidget(self.cancel)
        self.install = QPushButton("Install AI")
        self.install.setObjectName("applyBtn")
        self.install.clicked.connect(self._install)
        buttons.addWidget(self.install)
        layout.addLayout(buttons)
        if context.runtime.available:
            self.status.setText("The local AI engine and model are installed.")
            self.install.setText("Use local AI")
        elif hardware.os_warning:
            self.status.setText(hardware.os_warning)
            self.install.setEnabled(False)

    def _install(self):
        if self.ctx.runtime.available:
            self.ctx.config.update({"setup_complete": True, "ai_mode_preference": "ai"})
            self.accept()
            return
        self.install.setEnabled(False)
        self.skip.setEnabled(False)
        self.cancel.show()
        self.cancel.setEnabled(True)
        self.ctx.runtime.shutdown()

        def operation(status, progress, cancelled):
            status("Step 1/2: Installing the AI engine…")
            self.ctx.binary_manager.download(progress, cancelled, status)
            if cancelled():
                raise InterruptedError("Installation cancelled")
            status("Step 2/2: Installing the AI model…")
            self.ctx.model_manager.download(progress, cancelled)

        self.worker = Worker(operation, self)
        self.worker.status.connect(self.status.setText)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _progress(self, value):
        done, total, message = value
        self.progress.setRange(0, 1000 if total else 0)
        if total:
            self.progress.setValue(int(done / total * 1000))
        self.status.setText(message)

    def _finished(self):
        worker = self.worker
        self.worker = None
        self.cancel.hide()
        self.install.setEnabled(True)
        self.skip.setEnabled(True)
        if worker.error:
            self.status.setText(f"Installation failed: {worker.error}")
            self.install.setText("Retry")
        elif worker.stopped or worker.cancel_event.is_set():
            self.status.setText("Installation cancelled.")
        else:
            self.ctx.config.update({"setup_complete": True, "ai_mode_preference": "ai"})
            self.status.setText("Installation complete. Local AI is ready.")
            self.progress.setRange(0, 1000)
            self.progress.setValue(1000)
            self.install.setText("Use local AI")
        worker.deleteLater()
        if self._close_pending:
            self.reject()

    def _cancel(self):
        if self.worker:
            self.worker.cancel()
            self.cancel.setEnabled(False)
            self.status.setText("Cancelling installation…")

    def _skip(self):
        self.ctx.config.update({"setup_skipped": True, "ai_mode_preference": "rule"})
        self.accept()

    def reject(self):
        if self.worker:
            self._close_pending = True
            self._cancel()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self._close_pending = True
            self._cancel()
            event.ignore()
        else:
            event.accept()
