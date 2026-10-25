"""Read-only file search: rewrite, update a local vector cache, retrieve, report."""
from contextlib import closing
import fnmatch
import os
from pathlib import Path
import re
import sqlite3
import stat

import numpy as np

from app.ai.content import read_content
from app.ai.llm_server import LLMServerError
from app.core.blocked_paths import checked_read_path, resolve_safe, validate_root_selection
from app.core.inventory import FileEntry

SKIP_DIRECTORIES = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'build', 'dist'}


class FileSearch:
    def __init__(self, database_path, runtime, embeddings, exclude_patterns=None):
        self.database_path = Path(database_path)
        self.runtime = runtime
        self.embeddings = embeddings
        self.exclude_patterns = exclude_patterns or []

    def search(self, root, query, status_cb=None, progress_cb=None, cancelled=None):
        status = status_cb or (lambda message: None)
        cancelled = cancelled or (lambda: False)

        def check():
            if cancelled():
                raise InterruptedError('Search stopped')

        check()
        root = resolve_safe(Path(root))
        valid, reason = validate_root_selection(root)
        if not valid:
            raise ValueError(reason)
        query = query.strip()
        if not query or len(query) > 12000:
            raise ValueError('Enter a search request of 1–12,000 characters')
        if not self.runtime.available:
            raise LLMServerError('Set up the local AI engine and Qwen model before searching')
        status('Clarifying your search…')
        request = self.runtime.rewrite_query(query, status, cancelled)
        check()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(self.database_path, os.O_CREAT | os.O_RDWR, 0o600))
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('''CREATE TABLE IF NOT EXISTS search_files (
                root TEXT NOT NULL, path TEXT NOT NULL, mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL, text TEXT NOT NULL, vector BLOB NOT NULL,
                encoder TEXT NOT NULL, PRIMARY KEY(root,path))''')
            status('Checking files and updating the local search index…')
            rows, skipped = self._index(connection, root, check, status, progress_cb, cancelled)
            check()
            return self._retrieve(root, query, request, rows, skipped, status, progress_cb, cancelled)

    def _index(self, connection, root, check, status, progress, cancelled):
        cached = {row['path']: dict(row) for row in connection.execute(
            'SELECT * FROM search_files WHERE root=?', (str(root),))}
        current, pending = [], []
        seen = set()
        skipped = 0

        def flush():
            if not pending:
                return
            check()
            status(f'Indexing file meaning… {len(current):,} files checked')
            vectors = self.embeddings.embed([row['document'] for row in pending], status, progress, cancelled)
            check()
            for row, vector in zip(pending, vectors):
                try:
                    info = (root / row['path']).stat()
                except OSError:
                    continue
                if info.st_mtime_ns != row['mtime_ns'] or info.st_size != row['size']:
                    continue  # An edited file is refreshed on the next search.
                row['vector'] = vector.astype('<f4').tobytes()
                connection.execute('INSERT OR REPLACE INTO search_files VALUES(?,?,?,?,?,?,?)',
                    (str(root), row['path'], row['mtime_ns'], row['size'], row['text'],
                     row['vector'], self.embeddings.fingerprint))
                current.append(row)
            connection.commit()
            pending.clear()

        def excluded(name):
            return name.startswith('.') or any(fnmatch.fnmatch(name, p) for p in self.exclude_patterns)

        for directory, directories, files in os.walk(root, followlinks=False):
            check()
            parent = Path(directory)
            allowed = []
            for name in sorted(directories):
                if excluded(name) or name in SKIP_DIRECTORIES:
                    continue
                try:
                    checked_read_path(root, (parent/name).relative_to(root).as_posix())
                    allowed.append(name)
                except (OSError, ValueError, RuntimeError):
                    continue
            directories[:] = allowed
            for name in sorted(files):
                check()
                if excluded(name):
                    continue
                try:
                    relative = (parent/name).relative_to(root).as_posix()
                    path = checked_read_path(root, relative)
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    seen.add(relative)
                    old = cached.get(relative)
                    if (old and old['mtime_ns'] == info.st_mtime_ns and old['size'] == info.st_size
                            and old['encoder'] == self.embeddings.fingerprint
                            and len(old['vector']) == self.embeddings.dimensions * 4):
                        current.append(old)
                        continue
                    entry = FileEntry(relative, name, path.suffix.lower(), str(path), size_bytes=info.st_size)
                    text = read_content(entry, max_chars=600)
                    pending.append({'path': relative, 'mtime_ns': info.st_mtime_ns, 'size': info.st_size,
                                    'text': text, 'document': f'{name}\n{Path(relative).parent}\n{text}'})
                    if len(pending) == 8:
                        flush()
                except (OSError, ValueError, RuntimeError) as exc:
                    if isinstance(exc, (LLMServerError, InterruptedError)):
                        raise
                    skipped += 1
        flush()
        check()
        removed = set(cached) - seen
        connection.executemany('DELETE FROM search_files WHERE root=? AND path=?',
                               [(str(root), path) for path in removed])
        connection.commit()
        return current, skipped

    def _retrieve(self, root, original, request, rows, skipped, status, progress, cancelled):
        include = set(request['extensions'])
        exclude = set(request['exclude_extensions'])
        candidates = []
        for row in rows:
            extension = Path(row['path']).suffix.lower()
            haystack = (row['path'] + '\n' + row['text']).casefold()
            if include and extension not in include or extension in exclude:
                continue
            if any(term in haystack for term in request['exclude_terms']):
                continue
            candidates.append(row)
        results = []
        matched = 0
        if candidates:
            status('Finding matching files…')
            queries = self.embeddings.embed([request['query'], original[:1000]], status, progress, cancelled)
            vector = .75 * queries[0] + .25 * queries[1]
            vector /= max(float(np.linalg.norm(vector)), 1e-12)
            matrix = np.stack([np.frombuffer(row['vector'], dtype='<f4') for row in candidates])
            scores = matrix @ vector
            terms = set(re.findall(r'[a-z0-9]+', request['query'].casefold())) - {
                'the', 'a', 'an', 'my', 'find', 'show', 'me', 'files', 'file', 'about', 'and', 'for', 'in', 'please'}
            for index, row in enumerate(candidates):
                title = set(re.findall(r'[a-z0-9]+', row['path'].casefold()))
                scores[index] += .15 * len(terms & title) / max(len(terms), 1)
            ranked = np.argsort(-scores)
            for index in ranked:
                if cancelled():
                    raise InterruptedError('Search stopped')
                if scores[index] < .25:
                    break
                row = candidates[index]
                try:
                    path = checked_read_path(root, row['path'])
                    if not path.is_file():
                        continue
                except (OSError, ValueError, RuntimeError):
                    continue
                matched += 1
                if len(results) < 10:
                    results.append({'id': row['path'], 'name': path.name, 'path': str(path),
                                    'type': path.suffix.lstrip('.'), 'score': min(1., float(scores[index])),
                                    'size_bytes': row['size'], 'modified': row['mtime_ns'] / 1e9})
        if matched:
            answer = f"Found {matched:,} matching {'file' if matched == 1 else 'files'}."
            if matched > len(results):
                answer += f' Showing the top {len(results)}.'
        else:
            answer = 'No close matches found in this folder. Try a different description or file type.'
        answer += f" Searched {len(rows):,} indexed {'file' if len(rows) == 1 else 'files'}."
        if skipped:
            answer += f' Skipped {skipped:,} inaccessible files.'
        return {'query': original, 'normalized_query': request['query'], 'root': str(root),
                'answer': answer, 'matched_files': matched, 'indexed_files': len(rows), 'results': results}
