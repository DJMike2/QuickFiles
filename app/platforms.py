"""Supported platforms and external process environments."""
import os
import sys

UNSUPPORTED_PLATFORM_MESSAGE = "QuickFiles supports only Windows and Linux."
MIN_WINDOWS_BUILD = 17763  # Windows 10 version 1809.
MIN_GLIBC = (2, 35)
MIN_CUDA_GLIBC = (2, 38)


def glibc_version():
    try:
        return tuple(int(part) for part in os.confstr("CS_GNU_LIBC_VERSION").split()[1].split("."))
    except (AttributeError, OSError, TypeError, ValueError, IndexError):
        return ()


def require_supported_platform():
    if sys.platform not in {"win32", "linux"}:
        raise RuntimeError(UNSUPPORTED_PLATFORM_MESSAGE)
    if sys.platform == "win32":
        version = sys.getwindowsversion()
        if (version.major, version.build) < (10, MIN_WINDOWS_BUILD):
            raise RuntimeError("QuickFiles requires Windows 10 version 1809 or newer, or Windows 11.")
    elif glibc_version() < MIN_GLIBC:
        raise RuntimeError("QuickFiles requires Ubuntu 22.04 or a Linux system with glibc 2.35 or newer.")


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
