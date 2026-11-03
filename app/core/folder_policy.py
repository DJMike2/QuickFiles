from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from itertools import islice
import fnmatch
import logging
import os
from pathlib import Path
import stat

from app.ai.prompts import FOLDER_ROLE_PROMPT
from app.core.blocked_paths import checked_read_path, resolve_safe, validate_root_selection
from app.core.file_kinds import kind_of
from app.core.file_state import identity
from app.core.inventory import FileEntry, ScanInventory
from app.core.naming import is_valid_filename
from app.core.organization import EvidenceCache, cache_key
from app.core.scanner import Scanner
from app.logging_setup import log_stage

log = logging.getLogger(__name__)


def entry_state(info):
    # Directory contents have their own snapshot; moves change directory timestamps.
    return [info.st_mode, info.st_dev, info.st_ino] if stat.S_ISDIR(info.st_mode) else [info.st_mode, *identity(info)]


def directory_state(path):
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('Folder became a link')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('Folder is no longer a directory')
    with os.scandir(path) as entries:
        contents = {entry.name: entry_state(entry.stat(follow_symlinks=False)) for entry in entries}
    return {'identity': entry_state(info), 'entries': contents}


class FolderAuthorization:
    def __init__(self, root, records):
        self.root = resolve_safe(Path(root))
        self.records = deepcopy(records)

    def key(self, path):
        return os.path.normcase(path.relative_to(self.root).as_posix()).replace('\\', '/')

    def check(self, path, *, fresh=True, checked=None):
        if not path.is_relative_to(self.root):
            raise ValueError('Path leaves the selected folder')
        for parent in path.parents:
            if not parent.is_relative_to(self.root):
                break
            if not parent.exists():
                continue
            key = self.key(parent)
            if checked is not None and key in checked:
                continue
            record = self.records.get(key)
            if record is None:
                raise ValueError('Folder was not authorized; scan again')
            if fresh:
                valid = directory_state(parent) == record
            else:
                valid = entry_state(parent.stat()) == record['identity']
            if not valid:
                raise ValueError('Folder changed since preview; scan again')
            if checked is not None:
                checked.add(key)

    def created(self, path):
        record = directory_state(path)
        if record['entries']:
            raise ValueError('New folder changed while applying')
        self.records[self.key(path)] = record
        self.records[self.key(path.parent)]['entries'][path.name] = record['identity']
        self.check(path / '__check__')

    def moved(self, source, destination):
        del self.records[self.key(source.parent)]['entries'][source.name]
        self.records[self.key(destination.parent)]['entries'][destination.name] = entry_state(destination.stat())
        self.check(source)
        self.check(destination)


def folder_evidence(path, cancelled, include=None, *, entry_limit=None):
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('Folder became a link')
    def entries(directory, limit):
        with os.scandir(directory) as listing:
            return sorted(Path(entry.path) for entry in islice(listing, limit))

    names = entries(path, entry_limit + 1 if entry_limit is not None else None)
    truncated = entry_limit is not None and len(names) > entry_limit
    if truncated:
        names = names[:entry_limit]
    metadata = [('folder', *identity(path.stat()))] if entry_limit is not None else []
    count = f'{len(names)} or more (sampled)' if truncated else str(len(names))
    lines, layout = [f'Folder: {path.name}', f'Direct entries: {count}'], []
    preview_count = 0
    for index, entry in enumerate(names):
        if cancelled():
            raise InterruptedError('Folder inspection stopped')
        info = entry.lstat()
        metadata.append((entry.name, *identity(info), info.st_mode))
        if index >= 32 or entry.is_symlink() or (include is not None and not include(entry)):
            continue
        try:
            checked_read_path(path, entry.name)
        except ValueError:
            continue
        if entry.is_dir():
            children = [child for child in entries(entry, 9 if entry_limit is not None else None)
                        if include is None or include(child)]
            metadata.append((entry.name, [(child.name, *identity(child.lstat())) for child in children]))
            sampled = ' or more (sampled)' if entry_limit is not None and len(children) >= 9 else ''
            line = f'Directory {entry.name}/ ({len(children)}{sampled} children): ' + ', '.join(
                child.name + ('/' if child.is_dir() else '') for child in children[:8])
        elif entry.is_file():
            kind = kind_of(FileEntry('', entry.name, entry.suffix.lower(), str(entry)))
            line = f'File {entry.name} ({info.st_size} bytes); kind: {kind}'
        else:
            continue
        lines.append(line)
        layout.append(line)
        if entry.is_file() and preview_count < 3 and 0 < info.st_size <= 4096:
            descriptor = os.open(entry, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
            with os.fdopen(descriptor, 'rb') as stream:
                data = stream.read(512)
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError:
                text = ''
            if text and all(char.isprintable() or char.isspace() for char in text):
                lines.append('Preview: ' + text)
                preview_count += 1
    evidence = '\n'.join(lines)[:3500]
    structure = '\n'.join([f'Folder: {path.name}', f'Direct entries: {len(names)}', *layout])[:3500]
    return evidence, structure, cache_key(evidence, metadata)


class FolderPolicy:
    def __init__(self, runtime, progress_cb=None, cancelled=None, cache_path=None):
        self.runtime = runtime
        self.progress = progress_cb or (lambda message: None)
        self.cancelled = cancelled or (lambda: False)
        self.cache = EvidenceCache(cache_path)
        self.version = cache_key('folder-structure-v2', FOLDER_ROLE_PROMPT, runtime.model_cache_key)

    def classify(self, path, include=None):
        if self.cancelled():
            raise InterruptedError('Folder classification stopped')
        try:
            evidence, structure, signature = folder_evidence(path, self.cancelled, include)
            before = directory_state(path)
            key = cache_key(self.version, signature)
            value = self.cache.get(key)
            if not (isinstance(value, dict) and value.get('role') in {'filing', 'managed'}
                    and isinstance(value.get('observation'), str)):
                value = self.runtime.describe_folder(evidence, self.progress, self.cancelled)
                if value['role'] == 'filing' and evidence != structure:
                    review = self.runtime.describe_folder(structure, self.progress, self.cancelled)
                    if review['role'] == 'managed':
                        value = {'role': 'uncertain', 'observation': review['observation']}
            if folder_evidence(path, self.cancelled, include)[2] != signature or directory_state(path) != before:
                raise ValueError('Folder changed during classification')
            if value['role'] != 'uncertain':
                self.cache.put(key, value)
            return value['role'], before
        except InterruptedError:
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            log.warning('Folder could not be classified: %s', path, exc_info=True)
            return 'uncertain', None

    def scan(self, root, *, exclude_patterns=None, progress_cb=None, threads=0):
        root = resolve_safe(Path(root))
        valid, reason = validate_root_selection(root)
        if not valid:
            raise ValueError(reason)
        patterns = exclude_patterns or []
        records, protected, allowed = {}, [], []

        def include(path):
            relative = path.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(relative, pattern) for pattern in patterns):
                return False
            try:
                checked_read_path(root, relative)
                return True
            except (OSError, ValueError):
                return False

        def children(parent):
            return [path for path in sorted(parent.iterdir()) if not path.name.startswith('.')
                    and is_valid_filename(path.name) and include(path) and path.is_dir()]

        def classify(path):
            return self.classify(path, include)

        self.progress('Checking folder structure…')
        with log_stage(log, 'Folder classification'):
            role, state = classify(root)
            if role != 'filing':
                return ScanInventory(str(root), protected_directories=['.'])
            records['.'] = state
            pending = list(children(root))
            with ThreadPoolExecutor(max_workers=self.runtime.slots) as pool:
                while pending:
                    following = []
                    for path, (role, state) in zip(pending, pool.map(classify, pending)):
                        relative = path.relative_to(root).as_posix()
                        if role == 'filing':
                            records[os.path.normcase(relative).replace('\\', '/')] = state
                            allowed.append(relative)
                            following.extend(children(path))
                        else:
                            protected.append(relative)
                    pending = following
        with log_stage(log, 'Native inventory'):
            inventory = Scanner(root, exclude_patterns=patterns, progress_cb=progress_cb,
                                cancelled=self.cancelled, threads=threads, filing_folders=allowed).scan()
        inventory.authorization = records
        inventory.protected_directories = sorted(set(inventory.protected_directories + protected))
        log.info('Inventory: files=%d folders=%d protected=%d skipped=%d errors=%d', inventory.total_files,
                 len(inventory.directories), len(inventory.protected_directories), inventory.skipped_count, inventory.error_count)
        return inventory
