"""Run with python -B -m app.main from the project directory."""
import sys


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from app.bootstrap import AppContext
    from app.config import AppConfig
    from app.constants import APP_NAME
    from app.ui.main_window import MainWindow

    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    application.setOrganizationName("DesktopOrganizerAI")
    context = AppContext(AppConfig())
    application.aboutToQuit.connect(context.shutdown)
    window = MainWindow(context)
    window.show()
    QTimer.singleShot(200, window.show_first_run_if_needed)
    try:
        return application.exec()
    finally:
        context.shutdown()


if __name__ == "__main__":
    sys.exit(main())
