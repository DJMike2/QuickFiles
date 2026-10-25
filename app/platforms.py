"""Operating systems supported by QuickFiles."""
import sys

UNSUPPORTED_PLATFORM_MESSAGE = "QuickFiles supports only Windows and Linux."


def require_supported_platform():
    if sys.platform not in {"win32", "linux"}:
        raise RuntimeError(UNSUPPORTED_PLATFORM_MESSAGE)
