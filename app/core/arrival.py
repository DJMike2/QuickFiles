"""Exclude unfinished downloads from the new-file watcher."""
import fnmatch

_PARTIAL = (".crdownload", ".partial", ".part", ".tmp", ".download", ".opdownload")


def is_ignored(name, patterns):
    """Skip download leftovers and the scan's own exclusion patterns."""
    lower = name.casefold()
    if any(lower.endswith(suffix) for suffix in _PARTIAL):
        return True
    return any(fnmatch.fnmatch(lower, pattern.casefold()) for pattern in patterns)
