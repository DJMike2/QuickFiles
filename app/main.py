"""Run with python -B -m app.main from the project directory."""
import sys
import logging


def main():
    from app.platforms import require_supported_platform

    try:
        require_supported_platform()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    from app.config import AppConfig
    from app.logging_setup import log_unhandled_exception, qt_logging, setup_logging

    config = AppConfig()
    setup_logging(config.app_data_dir / "logs")
    previous_hook = sys.excepthook
    sys.excepthook = log_unhandled_exception
    context = None
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from app.bootstrap import AppContext
        from app.constants import APP_NAME
        from app.ui.main_window import MainWindow

        with qt_logging():
            application = QApplication(sys.argv)
            application.setApplicationName(APP_NAME)
            application.setOrganizationName("DesktopOrganizerAI")
            context = AppContext(config)
            application.aboutToQuit.connect(context.shutdown)
            window = MainWindow(context)
            window.show()
            QTimer.singleShot(200, window.show_first_run_if_needed)
            return application.exec()
    except Exception:
        logging.getLogger(__name__).exception("Application failed to start or run")
        return 1
    finally:
        try:
            if context is not None:
                context.shutdown()
        except Exception:
            logging.getLogger(__name__).exception("Application shutdown failed")
        finally:
            sys.excepthook = previous_hook


if __name__ == "__main__":
    sys.exit(main())
