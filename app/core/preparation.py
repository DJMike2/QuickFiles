"""Organizer steps 1–5: safety, eligibility, bounded evidence and grounded summaries."""
from dataclasses import asdict, dataclass, field
from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import stat
import sys

from app.ai.evidence import extract_evidence
from app.ai.runtime import SummaryGroundingError
from app.constants import EXECUTABLE_EXTENSIONS, SHORTCUT_EXTENSIONS
from app.core.blocked_paths import checked_path, resolve_safe, validate_root_selection
from app.core.file_kinds import KIND_LABELS, kind_of
from app.core.file_state import identity
from app.core.identities import literal_header_identity

SUMMARY_VERSION = "organizer-literal-excerpts-v5"
log = logging.getLogger(__name__)


@dataclass
class FileSummary:
    basis: str
    summary: str
    subject: str
    identifiers: list[str]


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
                if all(isinstance(getattr(value, name), str) for name in ("basis", "summary", "subject")) and (
                        isinstance(value.identifiers, list) and all(isinstance(item, str) for item in value.identifiers)):
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

    def _summarize(self, entry, evidence, basis, source):
        if basis == 'content':
            # Preparation collects facts; semantic interpretation belongs
            # to the profile/matching/review stages. Never hold readable
            # content merely because a model failed to copy a substring.
            header_identity = literal_header_identity(evidence.text)
            summary = FileSummary(basis, ' … '.join(literal_excerpts(evidence.text)),
                header_identity or evidence.text[:96], [header_identity] if header_identity else [])
        elif basis in {'filename', 'archive_listing'} and (label := KIND_LABELS.get(kind_of(entry))):
            # The extension already proves a broad format category. Asking a
            # model to restate it adds no evidence about unread contents.
            summary = FileSummary(basis,
                (f'Filename: {entry.filename}; kind from extension: {label}'
                 + ('; '+source[:600] if basis == 'archive_listing' else '')), label, [])
        else:
            value = self.runtime.summarize_file(source, basis=basis, status_cb=self.progress,
                                                cancelled=self.cancelled)
            summary = FileSummary(basis, value["summary"], value["subject"], value["identifiers"])
        return summary

    def prepare(self, inventory):
        self._check()
        root = resolve_safe(Path(inventory.root))
        valid, reason = validate_root_selection(root)
        if not valid:
            raise ValueError(reason)
        if self.cache.path and resolve_safe(self.cache.path).is_relative_to(root):
            raise ValueError("Summary cache must be outside the folder being organized")
        result = PreparedInventory(str(root))
        if any((root/marker).is_dir() for marker in ('.git','.hg','.svn')) or (root/'.git').is_file():
            # Selecting the repository itself must preserve the same boundary as
            # encountering it below Downloads/Desktop. A lone downloaded manifest
            # is deliberately not enough to classify an entire selected folder.
            result.held = {e.rel_path:'Selected repository contents stay in place' for e in inventory.entries}
            return result
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
                # Also protect extensionless ELF files and executable scripts on Linux.
                if (sys.platform != "win32" and before.st_mode & 0o111 and not stat.S_ISDIR(before.st_mode)
                        and not self.executables_movable):
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
                evidence = extract_evidence(entry, inventory.content_read_bytes, cancelled=self.cancelled)
                after = path.stat()
                if identity(before) != identity(after):
                    result.held[entry.rel_path] = "File changed while reading; scan again"
                    continue
                basis = evidence.status if evidence.status in {"content", "archive_listing"} else "filename"
                # The key includes full identity and evidence, so changes invalidate old summaries.
                source = evidence.text if basis != "filename" else (
                    f"Filename: {entry.filename}\nKind inferred from filename extension: "
                    f"{KIND_LABELS.get(kind_of(entry), 'unknown')}")
                key = hashlib.sha256(json.dumps([
                    SUMMARY_VERSION if basis != 'filename' else 'organizer-extractive-v4-metadata',
                    str(root), entry.rel_path, identity(after),
                    self.runtime.summary_cache_key, asdict(evidence), source,
                ], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                summary = self.cache.get(key)
                if summary:
                    result.cache_hits += 1
                else:
                    try:
                        summary = self._summarize(entry, evidence, basis, source)
                    except SummaryGroundingError:
                        result.held[entry.rel_path] = "AI summary failed evidence checks; leave in place"
                        result.summaries[entry.rel_path] = FileSummary(basis, "Subject could not be verified", "", [])
                        continue
                    self.cache.put(key, summary)
                self._check()
                # Model calls may take seconds; exclude files altered in the meantime as well.
                checked_path(root, entry.rel_path)
                if identity(after) != identity(path.stat()):
                    result.held[entry.rel_path] = "File changed during summarization; scan again"
                    continue
                result.summaries[entry.rel_path] = summary
                if not summary.subject:
                    result.held[entry.rel_path] = "Insufficient subject evidence; leave in place"
                    continue
                result.files.append(entry)
            except InterruptedError:
                raise
            except OSError as exc:
                log.warning('File unavailable while preparing scan: %s', entry.rel_path, exc_info=True)
                result.held[entry.rel_path] = f"File unavailable: {exc.__class__.__name__}"
            except ValueError as exc:
                result.held[entry.rel_path] = str(exc)
        self._check()
        return result


def literal_excerpts(text, width=360):
    """Bounded, exact head/middle/tail passages; no generated factual claims."""
    if len(text) <= width * 3:
        return [text[i:i+width] for i in range(0, len(text), width)]
    starts = [0, (len(text)-width)//2, len(text)-width]
    return [text[start:start+width] for start in starts]
