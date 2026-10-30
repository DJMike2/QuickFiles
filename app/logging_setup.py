"""Local logging with bounded disk usage."""
from contextlib import contextmanager
from contextvars import ContextVar
from collections import Counter
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time
import uuid

log = logging.getLogger(__name__)
_run = ContextVar("log_run", default=None)


class _RunFilter(logging.Filter):
    def filter(self, record):
        run = _run.get()
        record.run_id = run["id"] if run else "-"
        return True


class _PDFWarningFilter(logging.Filter):
    def filter(self, record):
        run = _run.get()
        if run is None or record.levelno != logging.WARNING:
            return True
        message = record.getMessage()
        if message.startswith("Ignoring wrong pointing object"):
            kind = "invalid object pointers"
        elif message.startswith("fontTools is required to fully parse"):
            kind = "missing fontTools"
        else:
            return True
        run["pdf_warnings"][kind] += 1
        return run["pdf_warnings"][kind] == 1


@contextmanager
def log_run():
    token = _run.set({"id": uuid.uuid4().hex[:8], "pdf_warnings": Counter()})
    try:
        yield
    finally:
        for kind, count in _run.get()["pdf_warnings"].items():
            if count > 1:
                log.warning("PDF parser: %s occurred %d times; first warning retained, %d repeats omitted",
                            kind, count, count - 1)
        _run.reset(token)


@contextmanager
def log_stage(logger, name):
    started = time.monotonic()
    logger.info("%s started", name)
    try:
        yield
    except InterruptedError:
        logger.info("%s cancelled after %.3f s", name, time.monotonic() - started)
        raise
    except Exception:
        logger.error("%s failed after %.3f s", name, time.monotonic() - started)
        raise
    else:
        logger.info("%s completed in %.3f s", name, time.monotonic() - started)


def setup_logging(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    path = (directory / "app.log").resolve()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: [run=%(run_id)s] %(message)s")
    if not any(isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == path
               for handler in root.handlers):
        handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(formatter)
        handler.addFilter(_RunFilter())
        root.addHandler(handler)
    if not any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
               for handler in root.handlers):
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        console.setFormatter(formatter)
        console.addFilter(_RunFilter())
        root.addHandler(console)
    root.setLevel(logging.INFO)
    for name in ("pypdf._reader", "pypdf._cmap"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _PDFWarningFilter) for f in logger.filters):
            logger.addFilter(_PDFWarningFilter())


def log_unhandled_exception(exc_type, exc, traceback):
    logging.getLogger(__name__).critical("Unhandled application error", exc_info=(exc_type, exc, traceback))


def log_qt_message(message_type, context, message):
    levels = {"QtDebugMsg": logging.DEBUG, "QtInfoMsg": logging.INFO,
              "QtWarningMsg": logging.WARNING, "QtCriticalMsg": logging.ERROR,
              "QtFatalMsg": logging.CRITICAL}
    level = levels.get(message_type.name, logging.WARNING)
    category = context.category or "qt"
    if (message_type.name == "QtWarningMsg" and category == "qt.qpa.theme.gnome"
            and "org.freedesktop.DBus.Error.UnknownMethod" in message
            and "org.freedesktop.portal.Settings" in message):
        # The app supplies its own theme when desktop appearance settings are unavailable.
        level = logging.INFO
    logging.getLogger(category).log(level, message)


@contextmanager
def qt_logging():
    from PySide6.QtCore import qInstallMessageHandler

    previous = qInstallMessageHandler(log_qt_message)
    try:
        yield
    finally:
        qInstallMessageHandler(previous)
