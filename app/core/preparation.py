"""Organizer steps 1–5: safety, eligibility, bounded evidence and grounded summaries."""
from dataclasses import asdict, dataclass, field
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import sys

from app.ai.evidence import extract_evidence
from app.ai.runtime import SummaryGroundingError
from app.constants import EXECUTABLE_EXTENSIONS, SHORTCUT_EXTENSIONS
from app.core.blocked_paths import checked_path, resolve_safe, validate_root_selection
from app.core.file_kinds import KIND_LABELS, kind_of
from app.core.identities import literal_header_identity

SUMMARY_VERSION = "organizer-extractive-v3"


@dataclass
class FileSummary:
    rel_path: str
    basis: str
    read_status: str
    summary: str
    subject: str
    identifiers: list[str]
    quotes: list[str]
    limitation: str

    def hint(self):
        return json.dumps({"basis": self.basis, "subject": self.subject,
                           "identifiers": self.identifiers, "summary": self.summary,
                           "limitation": self.limitation}, ensure_ascii=False, separators=(",", ":"))


@dataclass
class PreparedInventory:
    root: str
    files: list = field(default_factory=list)
    summaries: dict[str, FileSummary] = field(default_factory=dict)
    held: dict[str, str] = field(default_factory=dict)
    cache_hits: int = 0


class SummaryCache:
    """Local summaries keyed by extracted evidence, file identity and model/prompt version."""
    def __init__(self, path):
        self.path = Path(path) if path else None

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE IF NOT EXISTS summaries (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        return connection

    def get(self, key):
        if self.path is None:
            return None
        with closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT value FROM summaries WHERE key=?", (key,)).fetchone()
        if row:
            try:
                value = FileSummary(**json.loads(row[0]))
                if all(isinstance(getattr(value, name), str) for name in
                       ("rel_path", "basis", "read_status", "summary", "subject", "limitation")) and all(
                           isinstance(items, list) and all(isinstance(item, str) for item in items)
                           for items in (value.identifiers, value.quotes)):
                    return value
            except (ValueError, TypeError):
                pass
        return None

    def put(self, key, value):
        if self.path is None:
            return
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT OR REPLACE INTO summaries VALUES (?,?)",
                               (key, json.dumps(asdict(value), ensure_ascii=False)))


class Preparation:
    def __init__(self, runtime, progress_cb=None, cancelled=None, *, shortcuts_movable=False,
                 executables_movable=False, cache_path=None):
        self.runtime = runtime
        self.progress = progress_cb or (lambda message: None)
        self.cancelled = cancelled or (lambda: False)
        self.shortcuts_movable = shortcuts_movable
        self.executables_movable = executables_movable
        self.cache = SummaryCache(cache_path)

    def _check(self):
        if self.cancelled():
            raise InterruptedError("Preparation stopped")

    def prepare(self, inventory):
        self._check()
        root = resolve_safe(Path(inventory.root))
        valid, reason = validate_root_selection(root)
        if not valid:
            raise ValueError(reason)
        if self.cache.path and resolve_safe(self.cache.path).is_relative_to(root):
            raise ValueError("Summary cache must be outside the folder being organized")
        result = PreparedInventory(str(root))
        protected = [path.casefold() for path in inventory.protected_projects]
        entries = sorted(inventory.entries, key=lambda entry: entry.rel_path)
        for number, entry in enumerate(entries, 1):
            self._check()
            relative = entry.rel_path.casefold()
            if entry.depth != 0 or any(relative == p or relative.startswith(p + "/") for p in protected):
                result.held[entry.rel_path] = "Existing folder contents stay in place"
                continue
            permitted = (self.shortcuts_movable and entry.extension in SHORTCUT_EXTENSIONS
                         or self.executables_movable and entry.extension in EXECUTABLE_EXTENSIONS)
            if (entry.is_conservative and not permitted) or entry.extension == ".desktop":
                result.held[entry.rel_path] = "Protected file type"
                continue
            try:
                path = checked_path(root, entry.rel_path)
                before = path.stat()
                if before.st_mode & 0o111 and not stat.S_ISDIR(before.st_mode) and not self.executables_movable:
                    # Also protect extensionless ELF files and executable scripts on Linux.
                    if sys.platform != "win32":
                        result.held[entry.rel_path] = "Executable permission requires explicit opt-in"
                        continue
                if not path.is_file() or before.st_size == 0:
                    result.held[entry.rel_path] = "Empty stub or nonregular file"
                    continue
                if (before.st_size != entry.size_bytes
                        or abs(before.st_mtime - entry.modified_ts) > 0.001):
                    result.held[entry.rel_path] = "File changed since scan; scan again"
                    continue
                # Never trust abs_path from a stale or constructed inventory.
                if resolve_safe(Path(entry.abs_path)) != path:
                    result.held[entry.rel_path] = "Inventory path does not match the selected folder"
                    continue
                self.progress(f"Summarizing files… {number:,}/{len(entries):,}: {entry.filename}")
                evidence = extract_evidence(entry, inventory.content_read_bytes)
                after = path.stat()
                if self._identity(before) != self._identity(after):
                    result.held[entry.rel_path] = "File changed while reading; scan again"
                    continue
                basis = evidence.status if evidence.status in {"content", "archive_listing"} else "filename"
                # The key includes full identity and evidence, so changes invalidate old summaries.
                source = evidence.text if basis != "filename" else (
                    f"Filename: {entry.filename}\nKind inferred from filename extension: "
                    f"{KIND_LABELS.get(kind_of(entry), 'unknown')}")
                key = hashlib.sha256(json.dumps([
                    SUMMARY_VERSION if basis != 'filename' else 'organizer-extractive-v4-metadata',
                    str(root), entry.rel_path, self._identity(after),
                    self.runtime.summary_cache_key, asdict(evidence), source,
                ], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                summary = self.cache.get(key)
                if summary:
                    result.cache_hits += 1
                elif basis == 'content' and literal_header_identity(evidence.text):
                    # Explicit course/client headings are already reliable facts.
                    # A literal excerpt cannot invent a topic or fail to copy its own quote.
                    identity = literal_header_identity(evidence.text)
                    excerpts = [evidence.text[i:i+140] for i in range(0, min(420, len(evidence.text)), 140)]
                    summary = FileSummary(entry.rel_path, basis, evidence.status, ' … '.join(excerpts),
                        identity, [identity], excerpts, evidence.limitation + '; literal identity heading and text excerpt')
                    self.cache.put(key, summary)
                elif basis == 'filename' and kind_of(entry) in KIND_LABELS:
                    # The extension already proves a broad format category. Asking a
                    # model to restate it adds no evidence about unread contents.
                    label = KIND_LABELS[kind_of(entry)]
                    summary = FileSummary(entry.rel_path, basis, evidence.status,
                        f'Filename: {entry.filename}; kind from extension: {label}', label, [],
                        [entry.filename], evidence.limitation)
                    self.cache.put(key, summary)
                else:
                    try:
                        value = self.runtime.summarize_file(source, basis=basis, status_cb=self.progress,
                                                            cancelled=self.cancelled)
                    except SummaryGroundingError:
                        if basis == 'filename' and kind_of(entry) in KIND_LABELS:
                            value = {'summary': f'Filename: {entry.filename}',
                                     'subject': KIND_LABELS[kind_of(entry)], 'identifiers': [],
                                     'quotes': [entry.filename]}
                        else:
                            result.held[entry.rel_path] = "AI summary failed evidence checks; leave in place"
                            result.summaries[entry.rel_path] = FileSummary(
                                entry.rel_path, basis, evidence.status,
                                "Subject could not be verified", "", [], [], evidence.limitation)
                            continue
                    summary = FileSummary(entry.rel_path, basis, evidence.status, value["summary"],
                                          value["subject"], value["identifiers"], value["quotes"],
                                          evidence.limitation)
                    if basis == 'filename' and not summary.subject and kind_of(entry) in KIND_LABELS:
                        # A known extension supports a broad kind even when a media subject
                        # cannot be read. This adds no course/client/topic inference.
                        summary.subject = KIND_LABELS[kind_of(entry)]
                        summary.summary = f'Filename: {entry.filename}; kind from extension: {summary.subject}'
                        summary.quotes = [entry.filename]
                    self.cache.put(key, summary)
                self._check()
                # Model calls may take seconds; exclude files altered in the meantime as well.
                checked_path(root, entry.rel_path)
                if self._identity(after) != self._identity(path.stat()):
                    result.held[entry.rel_path] = "File changed during summarization; scan again"
                    continue
                result.summaries[entry.rel_path] = summary
                if not summary.subject:
                    result.held[entry.rel_path] = "Insufficient subject evidence; leave in place"
                    continue
                result.files.append(entry)
            except OSError as exc:
                result.held[entry.rel_path] = f"File unavailable: {exc.__class__.__name__}"
            except ValueError as exc:
                result.held[entry.rel_path] = str(exc)
        self._check()
        return result

    @staticmethod
    def _identity(info):
        return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
