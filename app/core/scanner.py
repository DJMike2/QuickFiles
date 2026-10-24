"""Walk the selected folder and inspect bounded text samples."""
from __future__ import annotations

import fnmatch
import os
import stat
import time
from pathlib import Path

from app.constants import (
    CONSERVATIVE_EXTENSIONS, DEFAULT_CONTENT_READ_BYTES, DEFAULT_MAX_FILES,
    EXT_CATEGORY_MAP, READABLE_EXTENSIONS,
)
from app.core.blocked_paths import checked_path, resolve_safe, validate_root_selection
from app.core.inventory import FileEntry, ScanInventory

_ROLE_HINTS = {
    "config": ("host", "port", "debug", "database", "api_key", "[settings]", "endpoint"),
    "log": ("error", "warning", "traceback", "exception", "stdout", "stderr"),
    "code": ("def ", "class ", "import ", "function ", "const ", "#include", "return "),
    "draft": ("dear ", "hello ", "regards", "sincerely"),
    "data": (",", "\t"),
}


def _content_role(sample):
    if not sample.strip():
        return "empty", []
    lower = sample[:1024].lower()
    for role, hints in _ROLE_HINTS.items():
        found = [hint.strip() for hint in hints if hint in lower]
        if found:
            return role, [hint for hint in found if len(hint) > 2][:6]
    return "note", []


class Scanner:
    def __init__(self, root: Path, *, max_files=DEFAULT_MAX_FILES,
                 content_read_bytes=DEFAULT_CONTENT_READ_BYTES, exclude_patterns=None,
                 progress_cb=None, cancelled=None):
        self.root = resolve_safe(root)
        self.max_files = max(1, int(max_files))
        self.content_read_bytes = max(0, min(int(content_read_bytes), 256 * 1024))
        self.exclude_patterns = exclude_patterns or []
        self.progress_cb = progress_cb or (lambda n, total, name: None)
        self.cancelled = cancelled or (lambda: False)

    def _excluded(self, name):
        return name.startswith(".") or any(
            fnmatch.fnmatch(name, pattern) for pattern in self.exclude_patterns
        )

    def _allowed(self, path):
        if self._excluded(path.name):
            return False
        try:
            checked_path(self.root, path.relative_to(self.root).as_posix())
            return True
        except (OSError, RuntimeError, ValueError):
            return False

    def scan(self) -> ScanInventory:
        valid, reason = validate_root_selection(self.root)
        if not valid:
            raise ValueError(reason)
        started = time.monotonic()
        inventory = ScanInventory(root=str(self.root))

        def walk_error(_):
            inventory.error_count += 1

        for directory, dirs, files in os.walk(self.root, followlinks=False, onerror=walk_error):
            if self.cancelled():
                raise InterruptedError("Scan stopped")
            parent = Path(directory)
            dirs[:] = sorted(name for name in dirs if self._allowed(parent / name))
            if parent != self.root:
                inventory.directories.append(parent.relative_to(self.root).as_posix())
            for name in sorted(files):
                if self.cancelled():
                    raise InterruptedError("Scan stopped")
                if inventory.total_files >= self.max_files:
                    inventory.skipped_count += 1
                    continue
                path = parent / name
                if not self._allowed(path):
                    inventory.skipped_count += 1
                    continue
                try:
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode):
                        inventory.skipped_count += 1
                        continue
                    relative = path.relative_to(self.root)
                    extension = path.suffix.lower()
                    entry = FileEntry(
                        rel_path=relative.as_posix(), filename=name, extension=extension,
                        abs_path=str(path), size_bytes=info.st_size, modified_ts=info.st_mtime,
                        file_type_bucket=EXT_CATEGORY_MAP.get(extension, "Misc"),
                        is_conservative=extension in CONSERVATIVE_EXTENSIONS,
                        is_empty=info.st_size == 0,
                        parent_folder=parent.name if parent != self.root else "",
                        depth=len(relative.parts) - 1,
                    )
                    if extension in READABLE_EXTENSIONS and self.content_read_bytes:
                        try:
                            with path.open("rb") as stream:
                                sample = stream.read(self.content_read_bytes).decode("utf-8", errors="replace")
                            entry.content_role, entry.content_keywords = _content_role(sample)
                        except OSError:
                            pass
                    inventory.entries.append(entry)
                    if inventory.total_files % 100 == 0:
                        self.progress_cb(inventory.total_files, 0, name)
                except PermissionError:
                    inventory.skipped_count += 1
                except OSError:
                    inventory.error_count += 1
        inventory.scan_duration_s = time.monotonic() - started
        return inventory
