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
    content_role: str = ""
    content_keywords: list[str] = field(default_factory=list)
    is_empty: bool = False
    parent_folder: str = ""
    depth: int = 0

    def to_compact_dict(self):
        return {
            "rel_path": self.rel_path,
            "name": self.filename,
            "ext": self.extension,
            "size_kb": round(self.size_bytes / 1024, 1),
            "bucket": self.file_type_bucket,
            "parent": self.parent_folder,
            "depth": self.depth,
            "content_role": self.content_role,
            "keywords": self.content_keywords,
            "empty": self.is_empty,
            "conservative": self.is_conservative,
        }


@dataclass
class ScanInventory:
    root: str
    entries: list[FileEntry] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    skipped_count: int = 0
    error_count: int = 0
    scan_duration_s: float = 0

    @property
    def total_files(self):
        return len(self.entries)
