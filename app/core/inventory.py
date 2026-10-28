"""Metadata collected without changing the scanned files."""
from dataclasses import dataclass, field


@dataclass
class FileEntry:
    rel_path: str
    filename: str
    extension: str
    abs_path: str
    size_bytes: int = 0
    modified_ts: float = 0
    file_type_bucket: str = "Misc"
    is_conservative: bool = False
    is_empty: bool = False
    parent_folder: str = ""
    depth: int = 0


@dataclass
class ScanInventory:
    root: str
    entries: list[FileEntry] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    protected_projects: list[str] = field(default_factory=list)
    content_read_bytes: int = 1200
    skipped_count: int = 0
    error_count: int = 0
    scan_duration_s: float = 0

    @property
    def total_files(self):
        return len(self.entries)
