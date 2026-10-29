import os
import sys

if sys.platform == "linux" and getattr(sys, "frozen", False):
    # Host GIO plugins may require a newer GLib than the app bundles.
    os.environ.setdefault("GIO_MODULE_DIR", os.path.join(sys._MEIPASS, "gio", "modules"))

from app.main import main

sys.exit(main())
