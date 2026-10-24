"""One reusable worker for scanning, applying, undo, and installation."""
import logging
import threading

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    status = Signal(str)
    progress = Signal(object)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel_event = threading.Event()
        self.result = None
        self.error = ""
        self.stopped = False

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            self.result = self.operation(
                self.status.emit,
                lambda done, total, message: self.progress.emit((done, total, message)),
                self.cancel_event.is_set,
            )
        except InterruptedError:
            self.stopped = True
        except Exception as exc:
            logging.getLogger(__name__).exception("Background operation failed")
            self.error = str(exc)
