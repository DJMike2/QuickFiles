"""Local logging with bounded disk usage."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    path = (directory / "app.log").resolve()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not any(isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == path
               for handler in root.handlers):
        handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(formatter)
        root.addHandler(handler)
    if not any(isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
               for handler in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
    root.setLevel(logging.INFO)


def log_unhandled_exception(exc_type, exc, traceback):
    logging.getLogger(__name__).critical("Unhandled application error", exc_info=(exc_type, exc, traceback))
