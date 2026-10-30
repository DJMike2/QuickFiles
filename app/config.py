"""Preferences stored in the user's application-data directory."""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
from pathlib import Path

from app.constants import APP_DATA_DIR_NAME, DEFAULT_CONTENT_READ_BYTES, DEFAULT_MAX_FILES
from app.platforms import require_supported_platform

DEFAULTS = {
    "theme": "light",
    "allowed_root": str(Path.home() / "Desktop"),
    "search_root": str(Path.home() / "Desktop"),
    "exclude_patterns": ["*.tmp", "~$*", "Thumbs.db", ".DS_Store"],
    "max_files_per_scan": DEFAULT_MAX_FILES,
    "content_read_bytes": DEFAULT_CONTENT_READ_BYTES,
    "shortcuts_movable": True,
    "executables_movable": True,
    "watch_new_files": False,
    "window_geometry": None,
}


def app_data_path() -> Path:
    require_supported_platform()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        base = Path.home() / ".local/share"
    return base / APP_DATA_DIR_NAME


def desktop_path() -> Path:
    # Qt resolves Windows known-folder redirection and Linux desktop settings.
    from PySide6.QtCore import QStandardPaths

    path = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
    return Path(path) if path else Path.home() / "Desktop"


class AppConfig:
    def __init__(self, app_data_dir: Path | None = None):
        self.app_data_dir = app_data_dir or app_data_path()
        self._path = self.app_data_dir / "config.json"
        self._data = copy.deepcopy(DEFAULTS)
        desktop = desktop_path()
        self._data.update(allowed_root=str(desktop), search_root=str(desktop))
        stored = None
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                stored = loaded
                self._data.update({key: stored[key] for key in DEFAULTS if key in stored})
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("Could not read preferences: %s", exc)
        # Older settings used one switch for both shortcuts and executables.
        if isinstance(stored, dict) and "executables_movable" not in stored:
            self._data["executables_movable"] = bool(stored.get("shortcuts_movable", True))
        # Repair only the missing legacy default, never a custom selection.
        legacy = Path.home() / "Desktop"
        if desktop != legacy:
            try:
                if desktop.is_dir() and not legacy.is_dir():
                    for key in ("allowed_root", "search_root"):
                        if self._data[key] == str(legacy):
                            self._data[key] = str(desktop)
            except OSError as exc:
                logging.getLogger(__name__).warning("Could not check the old Desktop location: %s", exc)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self.update({key: value})

    def update(self, values):
        self._data.update(values)
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        temporary.replace(self._path)

    @property
    def models_dir(self):
        return self.app_data_dir / "models"

    @property
    def db_path(self):
        return self.app_data_dir / "organizer.db"
