"""One reusable worker for scanning, applying, undo, and installation."""
import logging
import threading

from PySide6.QtCore import QThread, Signal

from app.logging_setup import log_run, log_stage

log = logging.getLogger(__name__)


def _public_error(exc):
    """Keep raw diagnostics in logs; show only messages users can act on."""
    if isinstance(exc, PermissionError):
        return "Couldn't access this location. Check its permissions and try again."
    if isinstance(exc, FileNotFoundError):
        return "A required file or folder is unavailable. Check the location and try again."
    return "Couldn't complete this request. Please try again."


class Worker(QThread):
    status = Signal(str)
    progress = Signal(object)

    def __init__(self, operation, parent=None, *, operation_name="operation"):
        super().__init__(parent)
        self.operation = operation
        self.operation_name = operation_name
        self.cancel_event = threading.Event()
        self.result = None
        self.error = ""
        self.stopped = False

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        with log_run():
            try:
                with log_stage(log, self.operation_name):
                    self.result = self.operation(
                        self.status.emit,
                        lambda done, total, message: self.progress.emit((done, total, message)),
                        self.cancel_event.is_set,
                    )
            except InterruptedError:
                self.stopped = True
            except Exception as exc:
                log.exception("Background operation failed")
                self.error = _public_error(exc)
