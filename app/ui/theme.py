"""Shared light/dark palette and compact widget styling."""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


def apply_theme(name):
    application = QApplication.instance()
    application.setStyle("Fusion")
    dark = name == "dark"
    background, surface, text, border, accent = (
        ("#16161e", "#252840", "#e2e4f0", "#3a3d5c", "#4d9fff") if dark else
        ("#f0f2f5", "#ffffff", "#1a1a1a", "#c8ccd4", "#0066cc")
    )
    palette = QPalette()
    for role, color in {
        QPalette.Window: background, QPalette.WindowText: text,
        QPalette.Base: surface, QPalette.AlternateBase: background,
        QPalette.Text: text, QPalette.Button: surface, QPalette.ButtonText: text,
        QPalette.Highlight: accent, QPalette.HighlightedText: "#ffffff",
        QPalette.ToolTipBase: surface, QPalette.ToolTipText: text,
        QPalette.PlaceholderText: "#8890b0" if dark else "#777777",
    }.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor("#70758a" if dark else "#999999"))
    application.setPalette(palette)
    application.setStyleSheet(f"""
        QWidget {{ font-size: 13px; }}
        QPushButton, QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{
            border: 1px solid {border}; border-radius: 4px; padding: 5px 8px;
        }}
        QPushButton:hover, QLineEdit:focus {{ border-color: {accent}; }}
        QPushButton#applyBtn {{ background: {accent}; color: white; font-weight: 600; }}
        QPushButton:disabled {{ color: {'#70758a' if dark else '#999999'}; }}
        QTreeView {{ border: 1px solid {border}; }}
        QTreeView::item {{ min-height: 23px; }}
        QSplitter::handle {{ background: {border}; width: 2px; }}
        QProgressBar {{ border: 1px solid {border}; border-radius: 3px; }}
        QProgressBar::chunk {{ background: {accent}; }}
        QLabel#banner {{ padding: 5px; color: {'#ffd060' if dark else '#7a5500'}; }}
    """)
