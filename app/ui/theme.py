"""Light and dark styles loaded from the stylesheets in themes/."""
import logging
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

THEMES_DIR = Path(__file__).parent / "themes"


def apply_theme(name):
    application = QApplication.instance()
    application.setStyle("Fusion")
    dark = name == "dark"
    background, surface, text, accent = (
        ("#16161e", "#1f2030", "#e2e4f0", "#2d6fcc") if dark else
        ("#f0f2f5", "#ffffff", "#1a1a1a", "#0066cc")
    )
    palette = QPalette()
    for role, color in {
        QPalette.Window: background, QPalette.WindowText: text,
        QPalette.Base: surface, QPalette.AlternateBase: "#1a1b2a" if dark else "#f7f8fa",
        QPalette.Text: text, QPalette.Button: surface, QPalette.ButtonText: text,
        QPalette.Highlight: accent, QPalette.HighlightedText: "#ffffff",
        QPalette.ToolTipBase: surface, QPalette.ToolTipText: text,
        QPalette.PlaceholderText: "#8890b0" if dark else "#777777",
    }.items():
        palette.setColor(role, QColor(color))
    application.setPalette(palette)
    try:
        application.setStyleSheet((THEMES_DIR / f"{'dark' if dark else 'light'}.qss").read_text(encoding="utf-8"))
    except OSError as exc:
        logging.getLogger(__name__).warning("Theme load failed: %s", exc)
