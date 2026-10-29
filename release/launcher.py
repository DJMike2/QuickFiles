import os
import sys

if sys.platform == "linux" and getattr(sys, "frozen", False):
    # Host GIO plugins may require a newer GLib than the app bundles.
    os.environ.setdefault("GIO_MODULE_DIR", os.path.join(sys._MEIPASS, "gio", "modules"))

from app.main import main

if sys.argv[1:2] == ["--check"]:
    from release.check import check_package

    if len(sys.argv) not in {3, 4}:
        sys.exit("Usage: QuickFiles --check report.json [llama-server]")
    sys.exit(check_package(sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None))

sys.exit(main())
