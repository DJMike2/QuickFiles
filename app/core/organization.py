import hashlib
import json
import logging
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import unicodedata

from app.ai.evidence import compact

log = logging.getLogger(__name__)


class EvidenceCache:
    def __init__(self, path=None):
        self.path = Path(path) if path else None

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute('CREATE TABLE IF NOT EXISTS evidence (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        return connection

    def get(self, key):
        if self.path is None:
            return None
        try:
            with closing(self._connect()) as connection, connection:
                row = connection.execute('SELECT value FROM evidence WHERE key=?', (key,)).fetchone()
        except (OSError, sqlite3.Error):
            log.warning('Organizer cache could not be read: %s', self.path, exc_info=True)
            return None
        if row:
            try:
                return json.loads(row[0])
            except ValueError:
                pass
        return None

    def put(self, key, value):
        if self.path is not None:
            try:
                with closing(self._connect()) as connection, connection:
                    connection.execute('INSERT OR REPLACE INTO evidence VALUES (?,?)',
                                       (key, json.dumps(value, ensure_ascii=False)))
            except (OSError, sqlite3.Error):
                log.warning('Organizer cache could not be written: %s', self.path, exc_info=True)


def cache_key(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_profile(value, evidence):
    def normalized(text):
        return re.sub(r'[^\w]+', '', unicodedata.normalize('NFKC', text).casefold())

    fields = ('description', 'scope_kind', 'scope', 'topic', 'quote')
    if value.get('scope_kind') not in {'course', 'client', 'project', 'topic', 'kind', 'unknown', 'mixed'}:
        raise ValueError('Invalid profile scope')
    for name, limit in (('description', 200), ('topic', 96), ('quote', 140)):
        if not isinstance(value.get(name), str) or len(value[name]) > limit:
            raise ValueError('Invalid profile text')
    if (not isinstance(value.get('scope'), list) or len(value['scope']) > 4
            or any(not isinstance(name, str) or not normalized(name)
                   or normalized(name) not in normalized(evidence) for name in value['scope'])):
        raise ValueError('Every supplied identity must occur in this item evidence')
    if value['scope_kind'] != 'unknown' and (not value['quote'] or compact(value['quote']) not in compact(evidence)):
        raise ValueError('A non-unknown profile needs an exact supporting passage; zero is only valid for unknown')
    return {name: value[name] for name in fields}
