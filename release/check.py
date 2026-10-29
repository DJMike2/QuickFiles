"""Verify a packaged application using disposable files and no model downloads."""
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback


def _run_checks(engine):
    import av
    import numpy as np
    from PIL import Image
    from pypdf import PdfReader, PdfWriter
    from PySide6.QtCore import qVersion
    from PySide6.QtWidgets import QApplication

    from app.ai.content import read_content
    from app.ai.llm_server import engine_environment
    from app.ai.media import image_frame, scanned_page
    from app.bootstrap import AppContext
    from app.config import AppConfig
    from app.core.scanner import Scanner
    from app.logging_setup import qt_logging, setup_logging
    from app.platforms import external_environment, glibc_version, require_supported_platform
    from app.ui.main_window import MainWindow
    from app.ui.theme import THEMES_DIR, apply_theme

    require_supported_platform()
    assert av.library_versions and np.isfinite(np.asarray([1.0])).all()
    with tempfile.TemporaryDirectory(prefix="quickfiles-check-") as directory:
        root = Path(directory)
        files = root / "files"
        files.mkdir()
        (files / "notes.txt").write_text("QuickFiles compatibility check", encoding="utf-8")
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.write(files / "document.pdf")
        assert len(PdfReader(files / "document.pdf").pages) == 1
        Image.new("RGB", (32, 32), "white").save(files / "picture.png")
        assert image_frame(files / "picture.png").startswith(b"\xff\xd8")
        assert scanned_page(files / "document.pdf").startswith(b"\xff\xd8")
        project = files / "project"
        (project / ".git").mkdir(parents=True)
        (project / "keep.txt").write_text("Protected project file", encoding="utf-8")
        inventory = Scanner(files).scan()
        assert {entry.rel_path for entry in inventory.entries} == {"notes.txt", "document.pdf", "picture.png"}
        notes = next(entry for entry in inventory.entries if entry.filename == "notes.txt")
        assert read_content(notes) == "QuickFiles compatibility check"
        assert (project / "keep.txt").read_text() == "Protected project file"

        environment = external_environment()
        if sys.platform == "linux" and getattr(sys, "frozen", False):
            assert sys._MEIPASS not in environment.get("LD_LIBRARY_PATH", "").split(os.pathsep)
        reader = shutil.which("pdftotext")
        if reader:
            subprocess.run([reader, "-v"], env=environment, capture_output=True, check=True, timeout=10)
        if engine:
            binary = Path(engine).resolve()
            subprocess.run([str(binary), "--version"], env=engine_environment(binary),
                           capture_output=True, check=True, timeout=30)

        config = AppConfig(root / "state")
        config.update({"allowed_root": str(files), "search_root": str(files), "watch_new_files": False})
        setup_logging(config.app_data_dir / "logs")
        try:
            with qt_logging():
                application = QApplication([])
                context = AppContext(config)
                try:
                    window = MainWindow(context)
                    for theme in ("light", "dark"):
                        assert (THEMES_DIR / (theme + ".qss")).is_file()
                        apply_theme(theme)
                    window.show()
                    application.processEvents()
                    window.close()
                    application.processEvents()
                finally:
                    context.shutdown()
        finally:
            # Release temporary logs before cleanup; Windows keeps open files locked.
            for handler in logging.getLogger().handlers[:]:
                if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename).is_relative_to(root):
                    handler.close()
                    logging.getLogger().removeHandler(handler)
    return {"passed": True, "platform": sys.platform, "qt": qVersion(),
            "glibc": glibc_version() if sys.platform == "linux" else None,
            "checks": ["scanner", "project protection", "PDF/image/video libraries", "themes", "GUI open/close",
                       "external libraries"] + (["CPU engine startup"] if engine else [])}


def check_package(report, engine=None):
    try:
        result = _run_checks(engine)
    except Exception as exc:
        result = {"passed": False, "platform": sys.platform, "error": traceback.format_exc()}
        if isinstance(exc, subprocess.CalledProcessError):
            result["stderr"] = (exc.stderr or b"")[:4096].decode("utf-8", "replace")
    Path(report).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["passed"] else 1
