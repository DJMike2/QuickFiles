"""Steps 6–9: destination evidence, shared groups, assignment and independent review."""
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path, PurePosixPath
import re

from app.ai.evidence import compact, extract_evidence
from app.ai.llm_server import LLMServerError
from app.ai.schema import OrganizationPlan, PlanAction, PlanWarning
from app.core.blocked_paths import checked_path
from app.core.naming import relative_parts
from app.core.organization import (Profile, ProfileCache, course_codes, explicit_client, kind_of, normalized,
                                   profile_key, legacy_profile_key,
                                   scope_compatible, scope_matches)
from app.core.preparation import Preparation
from app.core.file_kinds import folder_kind
from app.core.identities import COURSE, same_course, same_scope

KIND_FOLDERS = {'audio': 'Audio', 'image': 'Images', 'video': 'Videos', 'code': 'Code',
                'ai model': 'AI Models', 'disk image': 'Disk Images', 'archive': 'Archives'}


class Planner:
    def __init__(self, runtime, progress_cb=None, cancelled=None, *, shortcuts_movable=False,
                 executables_movable=False, summary_cache_path=None, allow_suffix_on_collision=True):
        self.runtime = runtime
        self.progress_cb = progress_cb or (lambda message: None)
        self.cancelled = cancelled or (lambda: False)
        self.shortcuts_movable = shortcuts_movable
        self.executables_movable = executables_movable
        self.summary_cache_path = summary_cache_path
        self.allow_suffix_on_collision = allow_suffix_on_collision
        self.prepared = None
        self.catalog = {}
        self.destinations = {}
        self.decisions = {}
        self.course_aliases = {}

    def _canonicalize_courses(self, profiles, evidence_rows):
        counts = Counter(normalized(code).upper() for p in profiles.values()
                         if p.scope_kind == 'course' for code in p.scope)
        parent = {code: code for code in counts}
        def root(code):
            while parent[code] != code:
                parent[code] = parent[parent[code]]
                code = parent[code]
            return code
        def join(a, b):
            if a in parent and b in parent:
                parent[root(b)] = root(a)
        for row in evidence_rows:
            for match in COURSE.finditer(row['evidence']):
                if match[2]:
                    join(match[1].upper()+match[3], match[2].upper()+match[3])
        for a in parent:
            for b in parent:
                if same_course(a, b):
                    join(a, b)
        sets = defaultdict(list)
        for code in parent:
            sets[root(code)].append(code)
        mapping = {}
        self.course_aliases = {}
        for values in sets.values():
            preferred = sorted(values, key=lambda code: (-counts[code], code))[0]
            for code in values:
                mapping[code] = preferred
            if len(values) > 1:
                self.course_aliases[preferred] = sorted(values)
        for profile in profiles.values():
            if profile.scope_kind == 'course':
                profile.scope = sorted({mapping[normalized(code).upper()] for code in profile.scope})

    def _check_cancelled(self):
        if self.cancelled():
            raise InterruptedError('Planning stopped')

    def _profiles(self, rows):
        cache = ProfileCache(self.summary_cache_path)
        found, pending = {}, []
        for row in rows:
            self._check_cancelled()
            source = compact(row['evidence'])
            if row.get('origin') == 'file' and (course_codes(source) or explicit_client(source)):
                # Explicit identities need no probabilistic reinterpretation.
                found[row['id']] = Profile.from_result({'description': source[:180], 'scope_kind': 'topic',
                    'scope': [], 'topic': row.get('subject', '')[:96], 'quote': source[:100]}, source)
                continue
            if row.get('origin') == 'file' and row.get('kind') in {'audio', 'image', 'video', 'ai model', 'disk image'}:
                found[row['id']] = Profile('Loose ' + row['kind'] + ' files', 'kind', [],
                                           row['kind'], source[:100])
                continue
            key = profile_key(self.runtime, {k: v for k, v in row.items() if k != 'id'})
            value = cache.get_profile(key)
            if value is None:
                value = cache.get_profile(legacy_profile_key(self.runtime, {k: v for k, v in row.items() if k != 'id'}))
            try:
                if value is None:
                    raise ValueError('Not cached')
                found[row['id']] = Profile.from_result(value, row['evidence'])
                cache.put_profile(key, value)
            except (ValueError, TypeError, KeyError, AttributeError):
                pending.append((row, key))
        for offset in range(0, len(pending), 4):
            batch = pending[offset:offset+4]
            self.progress_cb(f'Describing subjects and folder purposes… {offset+1}/{len(pending)}')
            values = self.runtime.profile_items([row for row, key in batch], self.progress_cb, self.cancelled)
            for (row, key), value in zip(batch, values):
                self._check_cancelled()
                try:
                    profile = Profile.from_result(value, row['evidence'])
                except ValueError:
                    retry = self.runtime.profile_items([row], self.progress_cb, self.cancelled)[0]
                    try:
                        profile = Profile.from_result(retry, row['evidence'])
                        value = retry
                    except ValueError:
                        source = compact(row['evidence'])
                        fallback = {'description': source[:200], 'scope_kind': 'topic', 'scope': [],
                                    'topic': row.get('subject', '')[:96], 'quote': source[:100]}
                        profile = Profile.from_result(fallback, row['evidence'])
                        value = fallback
                found[row['id']] = profile
                cache.put_profile(key, value)
        return found

    def _off_limits(self, path):
        value = path.casefold()
        return any(value == p or value.startswith(p + '/') for p in self._protected)

    def _new_levels(self, parts):
        """Existing parent structure is reused, not charged as newly created depth."""
        directories = getattr(self, '_existing_directories', set())
        for length in range(len(parts), -1, -1):
            if length == 0 or '/'.join(parts[:length]) in directories:
                return len(parts) - length

    def _folder_rows(self, inventory, first_id):
        """Only direct, checked samples: parent containers cannot inherit child identities."""
        rows, metadata = [], {}
        by_parent = defaultdict(list)
        for entry in inventory.entries:
            by_parent[str(PurePosixPath(entry.rel_path).parent)].append(entry)
        directories = set(inventory.directories)
        for path in sorted(directories):
            self._check_cancelled()
            if self._off_limits(path):
                continue
            direct = by_parent[path]
            children = sorted(p for p in directories if str(PurePosixPath(p).parent) == path)
            if not direct and children:
                metadata[path] = {'container': True, 'children': children}
                continue
            samples = []
            for entry in direct[:4]:
                self._check_cancelled()
                try:
                    file = checked_path(Path(inventory.root), entry.rel_path)
                    before = file.stat()
                    if entry.is_conservative or before.st_size == 0 or before.st_size != entry.size_bytes:
                        continue
                    evidence = extract_evidence(entry, inventory.content_read_bytes)
                    if Preparation._identity(before) != Preparation._identity(file.stat()):
                        continue
                    samples.append((f'Readable sample: {evidence.text[:1600]}' if evidence.status == 'content'
                                    else f'Filename-only sample: {entry.filename}')
                                   + f'\nFile kind: {kind_of(entry)}')
                except (OSError, ValueError):
                    continue
            source = 'Existing folder: ' + path + '\n' + '\n'.join(samples)
            identifier = first_id + len(rows)
            rows.append({'id': identifier, 'origin': 'existing folder', 'evidence': source})
            metadata[path] = {'id': identifier, 'source': source,
                              'families': sorted({kind_of(e) for e in direct if not e.is_conservative}),
                              'container': False}
        return rows, metadata

    @staticmethod
    def _source(entry, summary):
        return (summary.subject + '\n' + summary.summary + '\nExplicit identifiers: '
                + '; '.join(summary.identifiers) + '\nFile kind: ' + kind_of(entry)
                + (('\nFilename: ' + entry.filename) if summary.basis != 'content' else ''))

    @staticmethod
    def _witness(row, destination):
        target = destination['profile']
        if target.scope_kind == 'kind':
            return 'File kind: ' + row['kind']
        if target.scope_kind == 'course':
            for match in COURSE.finditer(row['evidence']):
                if any(same_course(code, scope) for code in course_codes(match[0]) for scope in target.scope):
                    return match[0]
        for value in row['profile'].scope:
            if any(same_scope(target.scope_kind, value, scope) for scope in target.scope):
                match = re.search(re.escape(value), row['evidence'], re.I)
                if match:
                    return match[0]
        return row['profile'].quote

    def _add_destination(self, path, profile, members, families, *, existing=False, source=''):
        parts = relative_parts(path)
        if (not existing and self._new_levels(parts) > 2) or self._off_limits(path):
            raise ValueError('Destination is protected or too deep')
        path = '/'.join(parts)
        if (not existing and path in getattr(self, '_existing_directories', set())
                and path not in self.destinations):
            raise ValueError('Existing parent containers are not file destinations')
        if any(path.casefold() == value or path.casefold().startswith(value + '/') for value in self._file_paths):
            raise ValueError('A file occupies the proposed destination')
        key = '/'.join(normalized(part) for part in parts)
        for old_path, old in self.destinations.items():
            equal_name = old_path == path if existing and old['existing'] else (
                '/'.join(normalized(part) for part in old_path.split('/')) == key)
            equal_scope = (not existing and not old['existing']
                and profile.scope_kind in {'course', 'client', 'project'}
                and profile.scope_kind == old['profile'].scope_kind
                and scope_compatible([old['profile'], profile]))
            if equal_name or equal_scope:
                if not scope_compatible([old['profile'], profile]):
                    raise ValueError('Equivalent destination name has a conflicting scope')
                old['members'].update(members)
                return old_path
        self.destinations[path] = {'profile': profile, 'members': set(members),
            'families': set(families), 'existing': existing, 'source': source}
        return path

    def _compatible(self, row, path, destination):
        profile = row['profile']
        target = destination['profile']
        if profile.scope_kind in {'unknown', 'mixed'}:
            return False
        if not scope_matches(profile, target):
            return False
        if target.scope_kind in {'course', 'client', 'project'}:
            return True
        if target.scope_kind == 'kind':
            if PurePosixPath(path).name.casefold() == 'pdfs' and not row['name'].casefold().endswith('.pdf'):
                return False
            return row['kind'] in destination['families']
        if row['id'] in destination['members']:
            return True
        if row['kind'] in {'audio', 'image', 'video', 'code', 'ai model', 'disk image', 'archive'}:
            if row['basis'] != 'content':
                words = {w for w in re.findall(r'\w+', (path + ' ' + target.topic).casefold())
                         if len(w) >= 4 and w not in {'files', 'documents', 'notes'}}
                return any(word in row['name'].casefold() for word in words)
            return row['kind'] in destination['families']
        return not destination['families'] or bool(destination['families'] & {'document', 'spreadsheet', 'presentation'})

    def _groups(self, rows):
        partitions = []
        for row in rows:
            p = row['profile']
            if p.scope_kind in {'unknown', 'mixed'}:
                continue
            scoped = p.scope_kind in {'course', 'client', 'project'}
            family = ('scoped' if scoped else 'unreadable_documents'
                      if row['basis'] == 'filename' and row['kind'] in {'document', 'spreadsheet', 'presentation'}
                      else row['kind'] if row['kind'] in KIND_FOLDERS else 'documents')
            for group in partitions:
                if group['family'] == family and scope_compatible([r['profile'] for r in group['rows']] + [p]):
                    group['rows'].append(row)
                    break
            else:
                partitions.append({'family': family, 'rows': [row]})
        for group in partitions:
            self._check_cancelled()
            members, family = group['rows'], group['family']
            if len(members) < 2:
                continue
            first = members[0]['profile']
            ids = {r['id'] for r in members}
            families = {r['kind'] for r in members}
            if family == 'unreadable_documents':
                existing = [path for path, d in self.destinations.items() if d['existing']
                            and d['profile'].scope_kind == 'kind' and 'document' in d['families']]
                if not existing:
                    profile = Profile('Documents whose contents are unavailable', 'kind', [], 'documents', '')
                    self._add_destination('Documents', profile, ids, {'document', 'spreadsheet', 'presentation'})
                continue
            if family == 'scoped':
                matches = [path for path, dest in self.destinations.items()
                           if dest['existing'] and dest['profile'].scope_kind in {'course', 'client', 'project'}
                           and all(self._compatible(row, path, dest) for row in members)]
                if matches:
                    continue
                labels = Counter(label for row in members for label in row['profile'].scope)
                label = labels.most_common(1)[0][0]
                if normalized(label) == normalized(Path(self._plan.root).name):
                    # The selected folder already supplies this identity; do not nest a duplicate.
                    continue
                name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', label).strip().rstrip('.')[:80]
                profile = Profile('Files explicitly identifying ' + ', '.join(first.scope),
                                  first.scope_kind, first.scope, first.topic, first.quote)
                try:
                    self._add_destination(name, profile, ids, families)
                except ValueError as exc:
                    self._plan.warnings.append(PlanWarning('', f'Ignored scoped group: {exc}'))
                continue
            if family in KIND_FOLDERS and family != 'archive':
                existing = [p for p, d in self.destinations.items() if d['existing']
                            and d['profile'].scope_kind == 'kind' and d['families'] == {family}]
                if not existing:
                    profile = Profile('Loose ' + family + ' files', 'kind', [], family, '')
                    self._add_destination(KIND_FOLDERS[family], profile, ids, {family})
                continue
            offered = [{'id': r['id'], 'name': r['name'] if r['basis'] != 'content' else '',
                        'profile': asdict(r['profile']), 'kind': r['kind'], 'evidence': r['evidence'][:900]}
                       for r in members]
            existing = [{'path': p, 'profile': asdict(d['profile'])}
                        for p, d in self.destinations.items() if d['existing']]
            self.progress_cb('Finding shared subjects…')
            proposals = self.runtime.propose_groups(offered, existing, self.progress_cb, self.cancelled)
            for proposal in proposals:
                try:
                    parts = relative_parts(proposal['path'])
                    if self._new_levels(parts) > 2 or self._off_limits('/'.join(parts)):
                        raise ValueError('Destination is protected or too deep')
                except ValueError as exc:
                    self._plan.warnings.append(PlanWarning('', f'Ignored group: {exc}'))
                    continue
                chosen = set(proposal['members'])
                if len(chosen) < 2 or not chosen <= ids:
                    self._plan.warnings.append(PlanWarning('', 'Ignored group without two valid supporting members'))
                    continue
                included = [r for r in members if r['id'] in chosen]
                if not scope_compatible([r['profile'] for r in included]):
                    continue
                profile = Profile(proposal['purpose'], 'topic', [], proposal['purpose'], '')
                duplicate = next((path for path, dest in self.destinations.items() if not dest['existing']
                    and dest['profile'].scope_kind == 'topic' and dest['families'] == {r['kind'] for r in included}
                    and len(dest['members'] & chosen) >= min(len(chosen), len(dest['members'])) * .6), None)
                try:
                    if duplicate:
                        self.destinations[duplicate]['members'].update(chosen)
                    else:
                        self._add_destination(proposal['path'], profile, chosen, {r['kind'] for r in included})
                except ValueError as exc:
                    self._plan.warnings.append(PlanWarning('', f'Ignored group: {exc}'))

    def plan(self, inventory):
        if not self.runtime or not self.runtime.available:
            raise LLMServerError('Local AI is required. Install the AI engine and model using Setup AI.')
        self._plan = OrganizationPlan(root=inventory.root)
        self._protected = [p.casefold() for p in inventory.protected_projects]
        self._file_paths = {e.rel_path.casefold() for e in inventory.entries}
        self._existing_directories = set(inventory.directories)
        self.catalog, self.destinations, self.decisions = {}, {}, {}
        try:
            self.prepared = Preparation(self.runtime, self.progress_cb, self.cancelled,
                shortcuts_movable=self.shortcuts_movable, executables_movable=self.executables_movable,
                cache_path=self.summary_cache_path).prepare(inventory)
            if not self.prepared.files:
                self._plan.excluded_count = inventory.total_files
                return self._plan
            rows = []
            for identifier, entry in enumerate(self.prepared.files, 1):
                summary = self.prepared.summaries[entry.rel_path]
                rows.append({'id': identifier, 'name': entry.filename, 'rel_path': entry.rel_path,
                    'kind': kind_of(entry), 'basis': summary.basis, 'subject': summary.subject,
                    'evidence': self._source(entry, summary)})
            fingerprints = {entry.rel_path: Preparation._identity(checked_path(
                Path(inventory.root), entry.rel_path).stat()) for entry in self.prepared.files}
            folders, metadata = self._folder_rows(inventory, len(rows)+1)
            evidence_rows = [{'id': r['id'], 'origin': 'file', 'subject': r['subject'],
                              'kind': r['kind'], 'evidence': r['evidence']} for r in rows] + folders
            profiles = self._profiles(evidence_rows)
            self._canonicalize_courses(profiles, evidence_rows)
            for row in rows:
                row['profile'] = profiles[row['id']]
                if row['basis'] == 'filename' and row['profile'].scope_kind not in {'course', 'client', 'project'}:
                    row['profile'].scope_kind = 'kind' if row['kind'] in KIND_FOLDERS else 'topic'
                self.catalog[row['rel_path']] = asdict(row['profile'])
            for path, meta in metadata.items():
                if meta['container']:
                    continue
                profile = profiles[meta['id']]
                families = set(meta['families'])
                broad_kind = folder_kind(PurePosixPath(path).name)
                if profile.scope_kind not in {'course', 'client', 'project', 'mixed'} and broad_kind:
                    accepted = {'document', 'spreadsheet', 'presentation'} if broad_kind == 'document' else {broad_kind}
                    if not families or families <= accepted:
                        profile.scope_kind, profile.scope, profile.topic = 'kind', [], broad_kind
                        families = accepted
                elif profile.scope_kind == 'kind':
                    profile.scope_kind = 'topic'
                self._add_destination(path, profile, set(), families, existing=True, source=meta['source'])
            self._groups(rows)
            chosen = {}
            for number, row in enumerate(rows, 1):
                self._check_cancelled()
                self.progress_cb(f'Matching files with evidence… {number}/{len(rows)}: {row["name"]}')
                candidates = [(path, d) for path, d in self.destinations.items() if self._compatible(row, path, d)]
                scoped_existing = [(path, d) for path, d in candidates if d['existing']
                                   and d['profile'].scope_kind in {'course', 'client', 'project'}]
                scoped_candidates = [(path, d) for path, d in candidates
                                     if d['profile'].scope_kind in {'course', 'client', 'project'}]
                if scoped_existing:
                    candidates = scoped_existing
                elif scoped_candidates:
                    candidates = scoped_candidates
                if not candidates:
                    self.decisions[row['rel_path']] = {'folder': '', 'reason': 'No compatible destination'}
                    continue
                existing_candidates = [(path, d) for path, d in candidates if d['existing']]
                decision = None
                if existing_candidates and len(existing_candidates) != len(candidates) and not scoped_existing:
                    decision = self.runtime.choose_destination(
                        {k: v for k, v in row.items() if k != 'profile'},
                        [{'path': path, 'existing': True, 'profile': asdict(d['profile'])}
                         for path, d in existing_candidates], self.progress_cb, self.cancelled)
                    if decision['folder']:
                        candidates = existing_candidates
                    else:
                        decision = None
                if decision is not None:
                    pass
                elif len(candidates) == 1 and candidates[0][1]['profile'].scope_kind in {'course', 'client', 'project', 'kind'}:
                    path, destination = candidates[0]
                    scope = ', '.join(destination['profile'].scope) or row['kind']
                    decision = {'folder': path, 'reason': f'Matches explicit {destination["profile"].scope_kind} evidence: {scope}',
                                'evidence': self._witness(row, destination), 'match': 'clear'}
                else:
                    decision = self.runtime.choose_destination(
                        {k: v for k, v in row.items() if k != 'profile'},
                        [{'path': path, 'existing': d['existing'], 'profile': asdict(d['profile'])}
                         for path, d in candidates], self.progress_cb, self.cancelled)
                if (not decision['folder'] and row['basis'] == 'filename'
                        and row['profile'].scope_kind not in {'course', 'client', 'project'}):
                    broad = [(path, d) for path, d in candidates if d['profile'].scope_kind == 'kind']
                    preferred = [(path, d) for path, d in broad if d['existing']] or broad
                    if len(preferred) == 1:
                        path, destination = preferred[0]
                        decision = {'folder': path, 'reason': 'Contents unavailable; retain only the verified broad file kind',
                                    'evidence': self._witness(row, destination), 'match': 'clear'}
                self.decisions[row['rel_path']] = decision
                if decision['folder']:
                    if decision['folder'] not in {p for p, d in candidates}:
                        raise LLMServerError('Destination escaped its evidence-compatible candidates')
                    chosen[row['rel_path']] = decision
            by_target = defaultdict(list)
            for row in rows:
                if row['rel_path'] in chosen:
                    by_target[chosen[row['rel_path']]['folder']].append(row)
            for path, assigned in by_target.items():
                identities = [r['profile'] for r in assigned
                              if r['profile'].scope_kind in {'course', 'client', 'project'}]
                if not scope_compatible(identities):
                    for row in assigned:
                        chosen.pop(row['rel_path'], None)
                        self.decisions[row['rel_path']] = {'folder': '',
                            'reason': 'Destination would mix incompatible explicit identities'}
            counts = Counter(value['folder'] for value in chosen.values())
            for row in rows:
                decision = chosen.get(row['rel_path'])
                if not decision:
                    continue
                path = decision['folder']
                if not self.destinations[path]['existing'] and counts[path] < 2:
                    self.decisions[row['rel_path']]['reason'] = 'New destination has fewer than two supported files'
                    self.decisions[row['rel_path']]['folder'] = ''
                    continue
                try:
                    source = checked_path(Path(inventory.root), row['rel_path'])
                    if Preparation._identity(source.stat()) != fingerprints[row['rel_path']]:
                        raise ValueError('File changed while its destination was being planned')
                except (OSError, ValueError) as exc:
                    self._plan.warnings.append(PlanWarning('', f'Kept {row["rel_path"]}: {exc}'))
                    self.decisions[row['rel_path']]['folder'] = ''
                    continue
                self._plan.actions.append(PlanAction(str(len(self._plan.actions)+1), 'move', row['rel_path'],
                    path + '/' + row['name'], category=path, confidence=0.0,
                    reason=decision['reason'] + '\nEvidence: ' + decision['evidence'],
                    source_identity=fingerprints[row['rel_path']]))
            self._check_cancelled()
            final_counts = Counter(action.category for action in self._plan.actions)
            self._plan.actions = [action for action in self._plan.actions
                if self.destinations[action.category]['existing'] or final_counts[action.category] >= 2]
            for identifier, action in enumerate(self._plan.actions, 1):
                action.id = str(identifier)
            from app.core.reviewer import Reviewer
            self.progress_cb('Reviewing the complete preview…')
            Reviewer(self.runtime, self.progress_cb, self.cancelled,
                shortcuts_movable=self.shortcuts_movable, executables_movable=self.executables_movable,
                allow_suffix_on_collision=self.allow_suffix_on_collision).review(self._plan, rows, self.destinations)
            for source, reason in self._plan.review['held'].items():
                self.decisions[source] = {'folder': '', 'reason': reason}
            self._plan.excluded_count = inventory.total_files - len(self._plan.actions)
            return self._plan
        except (LLMServerError, InterruptedError):
            self.runtime.shutdown()
            raise
