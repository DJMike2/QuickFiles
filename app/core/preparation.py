from dataclasses import asdict
import logging
from pathlib import Path
import sys

from app.ai.evidence import ContentEvidence, extract_evidence
from app.constants import EXECUTABLE_EXTENSIONS, SHORTCUT_EXTENSIONS
from app.core.blocked_paths import checked_path
from app.core.file_kinds import kind_of
from app.core.file_state import identity
from app.core.organization import EvidenceCache, cache_key

log = logging.getLogger(__name__)


class Preparation:
    def __init__(self, progress_cb=None, cancelled=None, *, cache_path=None,
                 shortcuts_movable=False, executables_movable=False):
        self.progress = progress_cb or (lambda message: None)
        self.cancelled = cancelled or (lambda: False)
        self.cache = EvidenceCache(cache_path)
        self.shortcuts_movable = shortcuts_movable
        self.executables_movable = executables_movable
        self.held = {}
        self.cache_hits = 0

    def check(self):
        if self.cancelled():
            raise InterruptedError('Content preparation stopped')

    def read(self, root, entry, limit):
        self.check()
        path = checked_path(root, entry.rel_path)
        before = identity(path.stat())
        if (not path.is_file() or Path(entry.abs_path).resolve() != path
                or before[2] != entry.size_bytes or abs(path.stat().st_mtime - entry.modified_ts) > .001):
            raise ValueError('File changed since inventory; scan again')
        key = cache_key('content-evidence-v1', str(root), entry.rel_path, before, limit)
        stored = self.cache.get(key)
        if isinstance(stored, dict) and stored.get('status') in {'content', 'archive_listing'} and set(stored) == {'status', 'text', 'limitation'} and all(
                isinstance(value, str) for value in stored.values()):
            evidence = ContentEvidence(**stored)
            self.cache_hits += 1
        else:
            evidence = extract_evidence(entry, limit, cancelled=self.cancelled)
        self.check()
        checked_path(root, entry.rel_path)
        if identity(path.stat()) != before:
            raise ValueError('File changed during reading; scan again')
        if evidence.status in {'content', 'archive_listing'}:
            self.cache.put(key, asdict(evidence))
        return evidence, before

    def prepare(self, inventory, only_files=None):
        root = Path(inventory.root)
        rows = []
        for entry in sorted(inventory.entries, key=lambda entry: entry.rel_path):
            self.check()
            if entry.depth or (only_files is not None and entry.rel_path not in only_files):
                continue
            try:
                path = checked_path(root, entry.rel_path)
                state = path.stat()
                permitted = ((self.shortcuts_movable and entry.extension in SHORTCUT_EXTENSIONS)
                             or (self.executables_movable and entry.extension in EXECUTABLE_EXTENSIONS))
                if (not state.st_size or entry.extension == '.desktop' or (entry.is_conservative and not permitted)
                        or (sys.platform != 'win32' and state.st_mode & 0o111 and not self.executables_movable)):
                    self.held[entry.rel_path] = 'File eligibility policy'
                    continue
                self.progress(f'Reading files… {len(rows)+1:,}')
                evidence, snapshot = self.read(root, entry, inventory.content_read_bytes)
                basis = evidence.status if evidence.status in {'content', 'archive_listing'} else 'filename'
                text = evidence.text if basis != 'filename' else f'Filename: {entry.filename}; format: {entry.extension}'
                rows.append({'id': len(rows)+1, 'name': entry.filename, 'rel_path': entry.rel_path,
                             'kind': kind_of(entry), 'basis': basis, 'evidence': text, 'source_identity': snapshot})
            except InterruptedError:
                raise
            except (OSError, ValueError) as error:
                log.warning('File could not be prepared: %s', entry.rel_path, exc_info=True)
                self.held[entry.rel_path] = str(error)
        log.info('Prepared files: eligible=%d held=%d evidence_cache_hits=%d', len(rows), len(self.held), self.cache_hits)
        return rows
