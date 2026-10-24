"""Local logging with bounded disk usage."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "app.log", maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler, logging.StreamHandler()],
    )
