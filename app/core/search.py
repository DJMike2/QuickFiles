"""Read-only file search: plan the request, update a local file map, retrieve, report."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
import fnmatch
import logging
import os
from pathlib import Path
import re
import sqlite3
import stat

import numpy as np

from app.ai.content import read_content
from app.ai.llm_server import LLMServerError
from app.constants import SEARCH_KIND_WORDS, SEARCH_KINDS
from app.core.blocked_paths import checked_read_path, resolve_safe, validate_root_selection
from app.core.inventory import FileEntry

log = logging.getLogger(__name__)

SKIP_DIRECTORIES = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'build', 'dist'}
# Bump when the indexed document format changes so cached vectors are rebuilt.
INDEX_FORMAT = 'map2'
# Refresh PDF excerpts after reader changes without rebuilding other file types.
PDF_TEXT_VERSION = '+pdf1'
# Minimum hybrid score for a match; a kind or format filter already vouches for every
# candidate, so less meaning evidence is needed then ("python scripts about parsing").
MATCH_SCORE = .48
FILTERED_MATCH_SCORE = .32
# Keyword evidence from the model's synonyms counts less than the user's own words.
EXPANSION_WEIGHT = .6
# Matches far below the best one are a different subject that merely shares words or a field;
# the final review judges the rest, so this only trims clear outliers.
RELATIVE_MARGIN = .30
# The final review sees this much of each file's text.
REVIEW_EXCERPT = 600
# A PDF with less extractable text than this has no real text layer: it is a scan.
SCAN_TEXT_CHARS = 40
# New files are read this many at a time (PDF text comes from a slow subprocess), in chunks of
# READ_CHUNK walked files.
READERS = 4
READ_CHUNK = 64
# Keywords at least this long also match longer words ("promo" -> promotional, "japan" -> japanese).
PREFIX_LETTERS = 5
# Words that take a number without naming anything ("last 3 days", "over 2 GB", "top 5").
QUANTITY_WORDS = set('''last past next over under than top first around within before after since
    more less least most bigger smaller larger'''.split())
# Keywords this generic match nearly every file, so they carry no signal.
GENERIC_WORDS = set('''a an the my me i we our your of and or for in on at to from by with about all any
    some this that these those it is are was be have has where which what find show get give list
    want need looking look search file files folder document documents stuff thing things
    note notes record records info paper papers'''.split())
# Common filename abbreviations, so "lecture" finds Lec14.pdf and "homework" finds HW6.pdf.
ABBREVIATIONS = {'hw': 'homework', 'hws': 'homework', 'lec': 'lecture', 'lect': 'lecture',
                 'sol': 'solution', 'sols': 'solutions', 'soln': 'solution', 'solns': 'solutions',
                 'asgn': 'assignment', 'assn': 'assignment', 'proj': 'project', 'ch': 'chapter',
                 'chap': 'chapter', 'img': 'image', 'pic': 'picture', 'pics': 'pictures', 'vid': 'video',
                 'doc': 'document', 'docs': 'documents', 'cv': 'resume', 'rpt': 'report'}


def file_kinds(extension):
    return [kind for kind, extensions in SEARCH_KINDS.items() if extension in extensions]


def readable(text):
    """Split names like CMPSC473_PracticeMidterm into words; spell out abbreviations (HW2 -> homework 2)."""
    text = re.sub(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])', ' ', text)
    words = re.sub(r'[\W_]+', ' ', text).split()
    spelled = [ABBREVIATIONS.get(word.lower(), word) for word in words]
    return ' '.join(words if spelled == words else words + ['/'] + spelled)


def keyword_pattern(keyword):
    """Match a keyword or phrase in readable text, or None when it is too short to mean anything."""
    parts = re.findall(r'[^\W\d_]+|\d+', keyword)
    if sum(map(len, parts)) < 2:
        return None
    # Plurals are folded, so "stats" matches STAT and "solution" matches solutions.
    parts = [part[:-1] if len(part) > 3 and part.endswith('s') and not part.endswith(('ss', 'us', 'is'))
             else part for part in parts]
    # Numbers ignore leading zeros, so "homework 2" finds HW02.
    body = r'[\W_]*'.join('0*' + (part.lstrip('0') or '0') if part.isdigit() else re.escape(part)
                          for part in parts)
    # Letters may not touch the keyword (hw must not match "show") but digits may (hw6),
    # except after a trailing number: "homework 2" must not match "homework 21".
    if parts[-1].isdigit():
        end = r'(?!\d)'
    elif len(parts[-1]) >= PREFIX_LETTERS:
        end = r'[^\W\d_]*'
    else:
        end = r'(?:e?s)?(?![^\W\d_])'
    return re.compile(rf'(?<![^\W\d_]){body}{end}')


def own_terms(text, noise):
    """The user's own words, keeping a number with the word before it ("homework 2", "lab 4")."""
    words = re.findall(r'[^\W\d_]+|\d+', text.casefold())
    terms = []
    for index, word in enumerate(words):
        if word.isdigit() and terms and words[index - 1] == terms[-1]:
            terms[-1] += ' ' + word
        elif word not in noise:
            terms.append(word)
    return list(dict.fromkeys(terms))


def identifiers(terms):
    """Numbered names the user typed (stat 415, homework 2, 1099); years and quantities are not names."""
    found = []
    for term in terms:
        word, _, number = term.rpartition(' ')
        if not number.isdigit() or word in QUANTITY_WORDS or re.fullmatch(r'(19|20)\d\d', number):
            continue
        if word or len(number) >= 3:
            found.append(term)
    return found


def visual_kind(extension, text=''):
    """'image', 'video' or 'scan' for files only a vision model can read, otherwise None."""
    if extension in SEARCH_KINDS['image']:
        return 'image'
    if extension in SEARCH_KINDS['video']:
        return 'video'
    if extension == '.pdf' and len(text.strip()) < SCAN_TEXT_CHARS:
        return 'scan'
    return None


def mib(value):
    return f'{value / 1024:g} GB' if value >= 1024 else f'{value:g} MB'


class FileSearch:
    max_results = 10

    def __init__(self, database_path, runtime, embeddings, exclude_patterns=None, excerpt_chars=2000,
                 vision=None):
        self.database_path = Path(database_path)
        self.runtime = runtime
        self.embeddings = embeddings
        self.exclude_patterns = exclude_patterns or []
        self.excerpt_chars = excerpt_chars
        self.vision = vision

    @property
    def encoder(self):
        return f'{self.embeddings.fingerprint}:{INDEX_FORMAT}'

    def _described(self, vision):
        """Stamp for rows the vision model described, so they are described once per model."""
        return f'{self.encoder}+vision:{vision.fingerprint}'

    def _reusable(self, old, extension, vision, described):
        encoder = old['encoder']
        if extension == '.pdf':
            if not encoder.endswith(PDF_TEXT_VERSION):
                return False
            encoder = encoder.removesuffix(PDF_TEXT_VERSION)
        if encoder == self.encoder:
            # Undescribed media is described once a vision model is available.
            return not (vision and visual_kind(extension, old['text']))
        # Described media is kept, even without the vision model, until a different one is installed.
        return encoder.startswith(self.encoder + '+vision:') and described in (None, encoder)

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
        self.embeddings.prestart(cancelled)
        # New visual descriptions need the GPU first. When vision is available,
        # decide which GPU model to start during indexing rather than loading both.
        if self.vision is None or not self.vision.available:
            self.runtime.prestart(cancelled)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(self.database_path, os.O_CREAT | os.O_RDWR, 0o600))
        # The index is updated first: describing new photos needs the GPU the search model uses.
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute('''CREATE TABLE IF NOT EXISTS search_files (
                root TEXT NOT NULL, path TEXT NOT NULL, mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL, text TEXT NOT NULL, vector BLOB NOT NULL,
                encoder TEXT NOT NULL, PRIMARY KEY(root,path))''')
            status('Checking files and updating the local search index…')
            rows, skipped = self._index(connection, root, check, status, progress_cb, cancelled)
        check()
        status('Understanding your search…')
        request = self.runtime.rewrite_query(query, status, cancelled)
        check()
        return self._retrieve(root, query, request, rows, skipped, status, progress_cb, cancelled)

    def _document(self, relative, text):
        """The text embedded for one file: its readable name, folder, kind and excerpt."""
        path = Path(relative)
        folder = readable(path.parent.as_posix()) if path.parent != Path('.') else 'top level'
        kinds = file_kinds(path.suffix.lower())
        kind = f"{' '.join(kinds) or 'other'} file ({path.suffix.lower() or 'no extension'})"
        return f'{readable(path.stem)}\nFolder: {folder}\nKind: {kind}\n{text}'

    def _index(self, connection, root, check, status, progress, cancelled):
        cached = {row['path']: dict(row) for row in connection.execute(
            'SELECT * FROM search_files WHERE root=?', (str(root),))}
        current, pending, visual = [], [], []
        seen = set()
        skipped = 0
        vision = self.vision if self.vision is not None and self.vision.available else None
        described = self._described(vision) if vision else None

        def flush():
            if not pending:
                return
            check()
            status(f'Indexing file contents… {len(current):,} files checked')
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
                encoder = row.get('encoder', self.encoder)
                if Path(row['path']).suffix.lower() == '.pdf':
                    encoder += PDF_TEXT_VERSION
                connection.execute('INSERT OR REPLACE INTO search_files VALUES(?,?,?,?,?,?,?)',
                    (str(root), row['path'], row['mtime_ns'], row['size'], row['text'],
                     row['vector'], encoder))
                current.append(row)
            connection.commit()
            pending.clear()

        def excluded(name):
            return name.startswith('.') or any(fnmatch.fnmatch(name, p) for p in self.exclude_patterns)

        def read(entry):
            try:
                return read_content(entry, 64 * 1024, max_chars=self.excerpt_chars, detailed=True,
                                    cancelled=cancelled)
            except (OSError, ValueError, RuntimeError) as exc:
                return exc  # Raised again in walk order below.

        walked = []
        reading = ThreadPoolExecutor(READERS, thread_name_prefix='search-read')

        def index_walked():
            """Index the walked files in walk order, so rows and embedding batches keep that order."""
            nonlocal skipped
            texts = reading.map(read, [entry for _, entry, _ in walked if entry])
            for relative, entry, found in walked:  # found: the cached row, or the new file's stat
                check()
                if not entry:
                    current.append(found)
                    continue
                try:
                    text = next(texts)
                    if isinstance(text, Exception):
                        raise text
                    row = {'path': relative, 'mtime_ns': found.st_mtime_ns, 'size': found.st_size, 'text': text}
                    kind = vision and visual_kind(entry.extension, text)
                    if kind:
                        if not visual:
                            vision.prestart(cancelled)
                        visual.append({**row, 'kind': kind})  # Described after the walk, with progress.
                        continue
                    pending.append({**row, 'document': self._document(relative, text)})
                    if len(pending) == 8:
                        flush()
                except (OSError, ValueError, RuntimeError) as exc:
                    if isinstance(exc, (LLMServerError, InterruptedError)):
                        raise
                    skipped += 1
            walked.clear()

        try:
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
                        extension = path.suffix.lower()
                        if (old and old['mtime_ns'] == info.st_mtime_ns and old['size'] == info.st_size
                                and len(old['vector']) == self.embeddings.dimensions * 4
                                and self._reusable(old, extension, vision, described)):
                            walked.append((relative, None, old))
                            continue
                        entry = FileEntry(relative, name, extension, str(path), size_bytes=info.st_size)
                        walked.append((relative, entry, info))
                        if len(walked) >= READ_CHUNK:
                            index_walked()
                    except (OSError, ValueError, RuntimeError) as exc:
                        if isinstance(exc, (LLMServerError, InterruptedError)):
                            raise
                        skipped += 1
            index_walked()
            if not visual:
                self.runtime.prestart(cancelled)
            flush()
            done = 0
            try:
                for row in visual:
                    check()
                    status(f'Describing photos, videos and scans… {done + 1:,}/{len(visual):,}')
                    description = vision.describe(root / row['path'], row.pop('kind'), status, cancelled)
                    pending.append({**row, 'text': description, 'encoder': described,
                                    'document': self._document(row['path'], description)})
                    done += 1
                    if len(pending) == 8:
                        flush()
            except LLMServerError:
                # Index the rest without descriptions; they are described on a later search.
                log.warning('Vision model failed; indexing remaining media without descriptions', exc_info=True)
                for row in visual[done:]:
                    row.pop('kind', None)
                    pending.append({**row, 'document': self._document(row['path'], row['text'])})
        finally:
            reading.shutdown(cancel_futures=True)
            if vision:
                vision.shutdown()  # Free the GPU for the search model.
        self.runtime.prestart(cancelled)
        flush()
        check()
        removed = set(cached) - seen
        connection.executemany('DELETE FROM search_files WHERE root=? AND path=?',
                               [(str(root), path) for path in removed])
        connection.commit()
        return current, skipped

    @staticmethod
    def _filtered(rows, request):
        include = set(request['extensions'])
        for kind in request.get('kinds', []):
            include |= SEARCH_KINDS[kind]
        exclude = set(request['exclude_extensions'])
        low = request.get('min_size_mb', 0) * 1024 * 1024
        high = request.get('max_size_mb', 0) * 1024 * 1024
        after = request.get('modified_after') or ''
        before = request.get('modified_before') or ''
        # Excluded words are matched in the readable name too, so "solutions" also drops HW2_soln.
        excluded = [p for p in map(keyword_pattern, request['exclude_terms']) if p]
        kept = []
        for row in rows:
            path = Path(row['path'])
            extension = path.suffix.lower()
            if include and extension not in include or extension in exclude:
                continue
            name = (readable(path.stem) + ' ' + readable(path.parent.as_posix())).casefold()
            if (any(term in row['path'].casefold() for term in request['exclude_terms'])
                    or any(p.search(name) for p in excluded)):
                continue
            if low and row['size'] < low or high and row['size'] > high:
                continue
            if after or before:
                modified = date.fromtimestamp(row['mtime_ns'] / 1e9).isoformat()
                if after and modified < after or before and modified > before:
                    continue
            kept.append(row)
        return kept

    @staticmethod
    def _describe(request):
        parts = []
        formats = request.get('kinds', []) + [x.lstrip('.').upper() for x in request['extensions']]
        if formats:
            parts.append(' or '.join(formats) + ' files')
        if request.get('min_size_mb'):
            parts.append(f"larger than {mib(request['min_size_mb'])}")
        if request.get('max_size_mb'):
            parts.append(f"smaller than {mib(request['max_size_mb'])}")
        if request.get('modified_after'):
            parts.append(f"modified since {request['modified_after']}")
        if request.get('modified_before'):
            parts.append(f"modified before {request['modified_before']}")
        if request['exclude_extensions'] or request['exclude_terms']:
            parts.append('excluding ' + ', '.join(request['exclude_extensions'] + request['exclude_terms']))
        if request.get('sort', 'relevance') != 'relevance':
            parts.append({'newest': 'newest first', 'oldest': 'oldest first',
                          'largest': 'largest first', 'smallest': 'smallest first'}[request['sort']])
        return '; '.join(parts)

    @staticmethod
    def _candidate(row):
        """What the reviewer sees: name, folder, kind and a short excerpt, keyed by indexed path."""
        path = Path(row['path'])
        return {'key': row['path'], 'name': path.name,
                'folder': path.parent.as_posix() if path.parent != Path('.') else '',
                'kind': ' '.join(file_kinds(path.suffix.lower())) or 'other',
                'excerpt': row['text'][:REVIEW_EXCERPT]}

    @staticmethod
    def _rank_candidates(candidates, original, request, topic, queries):
        ranked = []
        vector = .75 * queries[0] + .25 * queries[1]
        vector /= max(float(np.linalg.norm(vector)), 1e-12)
        matrix = np.stack([np.frombuffer(row['vector'], dtype='<f4') for row in candidates])
        scores = matrix @ vector
        noise = GENERIC_WORDS | set(SEARCH_KINDS)
        if request['extensions']:
            # The format is already enforced; otherwise "cpp" may pick C++ out of all code.
            noise |= {x.lstrip('.') for extensions in SEARCH_KINDS.values() for x in extensions}
        terms = own_terms(original[:1000], noise)
        own = [p for p in map(keyword_pattern, terms) if p]
        named = [p for p in map(keyword_pattern, identifiers(terms)) if p]
        expansions = own_terms(topic, noise) + [k for k in request.get('keywords', []) if k not in noise]
        extra = [p for p in map(keyword_pattern, dict.fromkeys(expansions)) if p]
        # Kind words ("photos", "img") describe the format, not what a photo shows.
        kind_words = set().union(*SEARCH_KIND_WORDS.values())
        topical = [p for term in dict.fromkeys(terms + expansions)
                   if ABBREVIATIONS.get(term, term).removesuffix('s') not in kind_words
                   and (p := keyword_pattern(term))]
        typed = [p for term in terms if ABBREVIATIONS.get(term, term).removesuffix('s') not in kind_words
                 and (p := keyword_pattern(term))]
        eligible = np.ones(len(candidates), dtype=bool)
        carries = np.zeros(len(candidates), dtype=bool)
        titled = np.zeros(len(candidates), dtype=bool)
        for index, row in enumerate(candidates):
            path = Path(row['path'])
            fields = ((readable(path.stem) + ' ' + path.suffix[1:]).casefold(),
                      readable(path.parent.as_posix()).casefold(), row['text'].casefold())
            own_hits = [sum(1 for p in own if p.search(field)) for field in fields]
            extra_hits = [sum(1 for p in extra if p.search(field)) for field in fields]
            # Hybrid ranking: meaning from the embedding plus exact keyword evidence in the
            # name, folder and text, which catches abbreviations (HW6) a small encoder misreads.
            for weight, mine, theirs in zip((.22, .10, .08), own_hits, extra_hits):
                scores[index] += weight * (1. if mine else EXPANSION_WEIGHT if theirs else 0.)
            scores[index] += .03 * min(sum(own_hits) + sum(extra_hits), 4)
            # Matching more of the user's words in the name or folder means a closer match.
            in_name = sum(1 for p in own if p.search(fields[0]) or p.search(fields[1]))
            scores[index] += .12 * in_name / max(len(own), 1)
            carries[index] = bool(named) and all(any(p.search(field) for field in fields) for p in named)
            titled[index] = bool(typed) and all(p.search(fields[0]) or p.search(fields[1]) for p in typed)
            # A file without readable text can only match through its name or folder.
            if not row['text'].strip() and not any(p.search(fields[0]) or p.search(fields[1]) for p in topical):
                eligible[index] = False
        if carries.any():
            eligible &= carries  # The request names it (stat 415, homework 2); others are other files.
        floor = FILTERED_MATCH_SCORE if request.get('kinds') or request['extensions'] else MATCH_SCORE
        threshold = max(floor, float(scores[eligible].max()) - RELATIVE_MARGIN) if eligible.any() else floor
        for index in np.argsort(-scores):
            if scores[index] < floor:
                break
            # A file named for everything the user typed ("resumes" -> X_Resume.pdf) is never an outlier.
            if eligible[index] and (scores[index] >= threshold or titled[index]):
                ranked.append((min(1., float(scores[index])), candidates[index]))
        if ranked and not named:
            # Files beside a match, in a folder named for the request (Travel/, Health/), go to the
            # review too: "japan trip" also means the flight booking that never says Japan. Not when
            # the request names a numbered item (stat 415): the rest of its folder is something else.
            have = {row['path'] for _, row in ranked}
            folders = {Path(row['path']).parent for _, row in ranked} - {Path('.')}
            named_folders = {folder for folder in folders
                             if any(p.search(readable(folder.as_posix()).casefold()) for p in topical)}
            ranked += [(min(1., float(scores[i])), candidates[i]) for i in np.argsort(-scores)
                       if eligible[i] and candidates[i]['path'] not in have
                       and Path(candidates[i]['path']).parent in named_folders]
        return ranked

    def _retrieve(self, root, original, request, rows, skipped, status, progress, cancelled):
        candidates = self._filtered(rows, request)
        # Keywords alone still name a subject ("python scripts" -> code files about python).
        topic = request['query'].strip() or ' '.join(request.get('keywords', []))
        filters = self._describe(request)
        metadata_only = not topic and bool(filters)
        if not topic and not metadata_only:
            topic = original  # The model found neither a topic nor a filter; search the words as typed.
        ranked = []
        if candidates and metadata_only:
            ranked = [(1., row) for row in candidates]
        elif candidates:
            status('Finding matching files…')
            queries = self.embeddings.embed([topic, original[:1000]], status, progress, cancelled, query=True)
            ranked = self._rank_candidates(candidates, original, request, topic, queries)
        if request.get('sort', 'relevance') != 'relevance' or metadata_only:
            order = {'newest': lambda item: -item[1]['mtime_ns'], 'oldest': lambda item: item[1]['mtime_ns'],
                     'largest': lambda item: -item[1]['size'], 'smallest': lambda item: item[1]['size']}
            ranked.sort(key=order.get(request.get('sort'), order['newest']))
            # The graph orders results by score, so scores follow the requested order.
            ranked = [(1 - index / max(len(ranked), 1), row) for index, (_, row) in enumerate(ranked)]
        shown = []
        matched = 0
        for score, row in ranked:
            if cancelled():
                raise InterruptedError('Search stopped')
            try:
                path = checked_read_path(root, row['path'])
                if not path.is_file():
                    continue
            except (OSError, ValueError, RuntimeError):
                continue
            matched += 1
            if len(shown) < self.max_results:
                shown.append((score, row, path))
        reviewed = len(shown) if shown and not metadata_only else 0
        if reviewed:
            # Retrieval is recall-oriented; the model makes the final call against the request as typed.
            status('Checking which matches fit your request…')
            try:
                kept = self.runtime.review_results(original, [self._candidate(row) for _, row, _ in shown],
                                                   status, cancelled)
                shown = [item for item in shown if item[1]['path'] in kept]
            except LLMServerError:
                log.warning('Search review failed; showing unreviewed matches', exc_info=True)
        results = [{'id': row['path'], 'name': path.name, 'path': str(path), 'type': path.suffix.lstrip('.'),
                    'score': score, 'size_bytes': row['size'], 'modified': row['mtime_ns'] / 1e9}
                   for score, row, path in shown]
        # After a review only the files it kept count; unreviewed filter results report every match.
        count = len(results) if reviewed else matched
        if count:
            answer = f"Found {count:,} matching {'file' if count == 1 else 'files'}"
            answer += f' ({filters}).' if filters else '.'
            if reviewed and matched > reviewed:
                answer += f' Checked the top {reviewed} of {matched:,} possible matches.'
            elif matched > len(results):
                answer += f' Showing the top {len(results)}.'
        else:
            answer = 'No close matches found in this folder'
            answer += f' ({filters}).' if filters else '.'
            answer += ' Try a different description or file type.'
        answer += f" Searched {len(rows):,} indexed {'file' if len(rows) == 1 else 'files'}."
        if skipped:
            answer += f' Skipped {skipped:,} inaccessible files.'
        return {'query': original, 'normalized_query': topic or filters, 'root': str(root),
                'answer': answer, 'matched_files': count, 'indexed_files': len(rows),
                'request': request, 'results': results}
