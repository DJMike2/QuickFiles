"""Supported platforms and external process environments."""
import os
import sys

UNSUPPORTED_PLATFORM_MESSAGE = "QuickFiles supports only Windows and Linux."


def require_supported_platform():
    if sys.platform not in {"win32", "linux"}:
        raise RuntimeError(UNSUPPORTED_PLATFORM_MESSAGE)


def external_environment():
    environment = os.environ.copy()
    if sys.platform == "linux" and getattr(sys, "frozen", False):
        # External programs need the host libraries, not PyInstaller's bundled copies.
        original = environment.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            environment.pop("LD_LIBRARY_PATH", None)
        else:
            environment["LD_LIBRARY_PATH"] = original
        bundled_gio = os.path.join(sys._MEIPASS, "gio", "modules")
        if environment.get("GIO_MODULE_DIR") == bundled_gio:
            environment.pop("GIO_MODULE_DIR")
    return environment
