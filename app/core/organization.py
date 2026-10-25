"""Evidence contracts and deterministic boundaries for organizer steps 6–8."""
from dataclasses import dataclass
import hashlib
import json
import re

from app.ai.evidence import compact
from app.core.preparation import SummaryCache
from app.core.file_kinds import kind_of

SCOPES = ('course', 'client', 'project', 'topic', 'kind', 'unknown', 'mixed')
from app.core.identities import (COURSE, NOT_COURSES, course_codes, explicit_client, normalized, same_course, same_scope)


@dataclass
class Profile:
    description: str
    scope_kind: str
    scope: list[str]
    topic: str
    quote: str

    @classmethod
    def from_result(cls, value, evidence):
        text = compact(evidence)
        codes = course_codes(text)
        if (value.get('scope_kind') not in SCOPES or not isinstance(value.get('scope'), list)
                or len(value['scope']) > 4 or any(not isinstance(x, str) or not x.strip()
                    or (compact(x).casefold() not in text.casefold()
                        and normalized(x) not in {normalized(code) for code in codes}) for x in value['scope'])):
            raise ValueError('Profile scope is not present in its evidence')
        for key, limit in [('description', 200), ('topic', 96), ('quote', 140)]:
            if not isinstance(value.get(key), str) or len(value[key]) > limit:
                raise ValueError('Invalid profile description')
        if value['scope_kind'] != 'unknown' and (not value['quote'] or compact(value['quote']) not in text):
            raise ValueError('Profile quote is not present in its evidence')
        client = explicit_client(text)
        profile = cls(value['description'], value['scope_kind'], value['scope'], value['topic'], value['quote'])
        if not codes and profile.scope_kind == 'course':
            for scope in profile.scope:
                match = COURSE.fullmatch(scope.strip())
                if match and match[1].upper() not in NOT_COURSES:
                    codes.append(match[1].upper() + match[3])
                    if match[2]:
                        codes.append(match[2].upper() + match[3])
            if not codes:
                # Institutions and instructors are not course identifiers.
                profile.scope_kind, profile.scope = 'topic', []
        if codes:
            aliases = {frozenset((m[1].upper()+m[3], m[2].upper()+m[3]))
                       for m in COURSE.finditer(text) if m[2]}
            coherent = all(same_course(a, b) or frozenset((a, b)) in aliases for a in codes for b in codes)
            profile.scope_kind, profile.scope = ('course', codes) if coherent else ('mixed', [])
        elif client:
            clients = list(dict.fromkeys(compact(x) for x in re.findall(
                r'\b(?:client\s*:|invoice for)\s*([^.;…\n]{3,70})', text, re.I)))
            if any(not same_scope('client', clients[0], other) for other in clients[1:]):
                profile.scope_kind, profile.scope = 'mixed', []
            else:
                profile.scope_kind, profile.scope = 'client', [client]
        if profile.scope_kind in {'course', 'client', 'project'} and not profile.scope:
            profile.scope_kind = 'unknown'
        if profile.scope_kind == 'project':
            profile.scope = [x for x in profile.scope if not re.fullmatch(
                r'(?:project|lab|homework)\s*#?\s*\d+', x, re.I)]
            if not profile.scope:
                profile.scope_kind = 'topic'
        return profile


class ProfileCache(SummaryCache):
    def get_profile(self, key):
        if self.path is None:
            return None
        from contextlib import closing
        with closing(self._connect()) as connection, connection:
            row = connection.execute('SELECT value FROM summaries WHERE key=?', ('profile:' + key,)).fetchone()
        if row:
            try:
                return json.loads(row[0])
            except ValueError:
                pass
        return None

    def put_profile(self, key, value):
        if self.path is None:
            return
        from contextlib import closing
        with closing(self._connect()) as connection, connection:
            connection.execute('INSERT OR REPLACE INTO summaries VALUES (?,?)',
                               ('profile:' + key, json.dumps(value, ensure_ascii=False)))


def profile_key(runtime, row):
    from app.ai.prompts import CATALOG_PROMPT
    return hashlib.sha256(json.dumps([CATALOG_PROMPT, runtime.summary_cache_key, row],
                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def legacy_profile_key(runtime, row):
    """The output container changed; compatible cached facts are still revalidated."""
    from app.ai.prompts import CATALOG_PROMPT
    previous = CATALOG_PROMPT.partition('Return JSON')[0] + 'Return JSON with profiles in exactly the supplied ID order.'
    return hashlib.sha256(json.dumps([previous, runtime.summary_cache_key, row],
                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def scope_matches(file_profile, destination):
    if destination.scope_kind in {'unknown', 'mixed'}:
        return False
    if destination.scope_kind in {'course', 'client', 'project'}:
        return (file_profile.scope_kind == destination.scope_kind
                and any(same_scope(destination.scope_kind, a, b)
                        for a in file_profile.scope for b in destination.scope))
    return True


def scope_compatible(profiles):
    scoped = [p for p in profiles if p.scope_kind in {'course', 'client', 'project'}]
    if not scoped:
        return True
    return len(scoped) == len(profiles) and all(left.scope_kind == right.scope_kind
        and any(same_scope(left.scope_kind, a, b) for a in left.scope for b in right.scope)
        for left in scoped for right in scoped)
