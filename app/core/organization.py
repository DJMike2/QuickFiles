"""Evidence contracts and deterministic boundaries for organizer steps 6–8."""
from dataclasses import dataclass
from contextlib import closing
import hashlib
import json
import re

from app.ai.evidence import compact
from app.ai.prompts import CATALOG_PROMPT
from app.core.identities import (COURSE, NOT_COURSES, course_codes, explicit_clients,
                                 literal_header_identity, normalized, same_course, same_scope)
from app.core.preparation import SummaryCache

SCOPES = ('course', 'client', 'project', 'topic', 'kind', 'unknown', 'mixed')


def named_field(text, value, label=None):
    # Unfamiliar labels still need semantic review before they establish ownership.
    field = label or r'[^\W\d_][\w -]{1,39}'
    return bool(re.search(r'(?im)^\s*(?:'+field+r')\s*:\s*'+re.escape(value)
                          +r'(?=\s|[.;,]|$)', text))


@dataclass
class Profile:
    description: str
    scope_kind: str
    scope: list[str]
    topic: str
    quote: str
    literal_scope: bool = False

    def evidence_dict(self):
        # A copied heading is evidence, not a semantic approval signal.
        return {key: getattr(self, key) for key in ('description', 'scope_kind', 'scope', 'topic', 'quote')}

    @classmethod
    def from_result(cls, value, evidence):
        text = compact(evidence)
        all_codes = course_codes(text)
        header = literal_header_identity(evidence)
        clients = explicit_clients(evidence)
        lines = evidence.splitlines()
        first_line = lines[0] if lines else ''
        client_field = re.match(r'\s*(?:client|customer|bill(?:ed)? to)\s*:', evidence, re.I)
        owned_client = bool(client_field or any(normalized(first_line) == normalized(client) for client in clients))
        codes = []
        if not owned_client and (course_codes(header) or value.get('scope_kind') == 'course'):
            codes = all_codes
        scope = value.get('scope')
        if value.get('scope_kind') not in SCOPES or not isinstance(scope, list) or len(scope) > 4:
            raise ValueError('Profile scope is not present in its evidence')
        for name in scope:
            if not isinstance(name, str) or not name.strip():
                raise ValueError('Profile scope is not present in its evidence')
            if (compact(name).casefold() not in text.casefold()
                    and normalized(name) not in {normalized(code) for code in all_codes}):
                raise ValueError('Profile scope is not present in its evidence')
        for key, limit in [('description', 200), ('topic', 96), ('quote', 140)]:
            if not isinstance(value.get(key), str) or len(value[key]) > limit:
                raise ValueError('Invalid profile description')
        if value['scope_kind'] != 'unknown' and (not value['quote'] or compact(value['quote']) not in text):
            raise ValueError('Profile quote is not present in its evidence')
        client = clients[0] if clients else ''
        profile = cls(value['description'], value['scope_kind'], value['scope'], value['topic'], value['quote'])
        if profile.scope_kind == 'unknown':
            return profile
        if not (owned_client or profile.scope_kind == 'client'):
            client, clients = '', []
        if not codes and not owned_client and profile.scope_kind == 'course':
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
            profile.literal_scope = coherent and (bool(course_codes(header)) or all(
                named_field(first_line, code, r'course(?: code)?')
                or (normalized(first_line)==normalized(code)
                    and named_field(evidence, code, r'course(?: code)?')) for code in codes))
        elif client:
            if any(not same_scope('client', clients[0], other) for other in clients[1:]):
                profile.scope_kind, profile.scope = 'mixed', []
            else:
                profile.scope_kind, profile.scope = 'client', [client]
                profile.literal_scope = owned_client
        elif profile.scope_kind == 'client':
            if not profile.scope or not all(named_field(evidence, name) for name in profile.scope):
                # An unlabelled address, pet name or brand mention is a topic.
                profile.scope_kind, profile.scope = 'topic', []
        projects = list(dict.fromkeys(compact(x) for x in re.findall(
            r'\bproject(?:\s+name)?\s*:\s*([^.;…\n]{3,80})', evidence, re.I)))
        if not codes and not client:
            if projects and (re.match(r'\s*project(?:\s+name)?\s*:', evidence, re.I)
                             or profile.scope_kind == 'project'):
                profile.scope_kind, profile.scope = ('project', projects) if len(projects) == 1 else ('mixed', [])
                profile.literal_scope = len(projects)==1 and (
                    named_field(first_line,projects[0],r'project(?: name)?')
                    or (normalized(first_line)==normalized(projects[0])
                        and named_field(evidence,projects[0],r'project(?: name)?')))
            elif profile.scope_kind == 'project':
                if not profile.scope or not all(named_field(evidence,name) for name in profile.scope):
                    # Shared subject phrases are topics, not named project boundaries.
                    profile.scope_kind, profile.scope = 'topic', []
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
        with closing(self._connect()) as connection, connection:
            connection.execute('INSERT OR REPLACE INTO summaries VALUES (?,?)',
                               ('profile:' + key, json.dumps(value, ensure_ascii=False)))


def profile_key(runtime, row):
    return hashlib.sha256(json.dumps(['independent-profile-v3-proof', CATALOG_PROMPT, runtime.summary_cache_key, row],
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
