"""Remember folders from the last scan so one new file can be compared to them."""
import fnmatch
from pathlib import PurePosixPath

_PARTIAL = (".crdownload", ".partial", ".part", ".tmp", ".download", ".opdownload")


def is_ignored(name, patterns):
    """Skip download leftovers and the scan's own exclusion patterns."""
    lower = name.casefold()
    if any(lower.endswith(suffix) for suffix in _PARTIAL):
        return True
    return any(fnmatch.fnmatch(lower, pattern.casefold()) for pattern in patterns)


class ScanIndex:
    """Short descriptions of folders the last scan already saw."""

    def __init__(self):
        self.folders = {}

    def learn(self, inventory):
        protected = [project.casefold() for project in inventory.protected_projects]

        def blocked(path):
            lower = path.casefold()
            return any(lower == project or lower.startswith(project + "/") for project in protected)

        names = {}
        for directory in inventory.directories:
            if directory.count("/") > 1 or blocked(directory):
                continue
            names[directory] = []
        for entry in inventory.entries:
            parent = str(PurePosixPath(entry.rel_path).parent)
            if parent in names and len(names[parent]) < 3:
                names[parent].append(entry.filename)
        self.folders = {
            folder: ", ".join(samples) or "existing folder"
            for folder, samples in names.items()
        }

    def add(self, folder, filename):
        if folder not in self.folders or filename.casefold() in self.folders[folder].casefold():
            return
        current = self.folders[folder]
        if current == "existing folder":
            current = ""
        self.folders[folder] = f"{current}, {filename}".strip(", ")[:160]
