from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path, PurePosixPath

from app.ai.llm_server import LLMServerError
from app.ai.prompts import CATALOG_PROMPT
from app.ai.schema import OrganizationPlan, PlanAction
from app.core.blocked_paths import checked_path
from app.core.file_kinds import kind_of
from app.core.folder_policy import FolderAuthorization, FolderPolicy
from app.core.naming import relative_parts
from app.core.organization import EvidenceCache, cache_key, validate_profile
from app.core.preparation import Preparation
from app.core.validator import Validator
from app.logging_setup import log_stage

log = logging.getLogger(__name__)


class Planner:
    def __init__(self, runtime, progress_cb=None, cancelled=None, *, shortcuts_movable=False,
                 executables_movable=False, cache_path=None, allow_suffix_on_collision=False):
        self.runtime = runtime
        self.progress = progress_cb or (lambda message: None)
        self.cancelled = cancelled or (lambda: False)
        self.shortcuts_movable = shortcuts_movable
        self.executables_movable = executables_movable
        self.cache_path = Path(cache_path) if cache_path else None
        self.allow_suffix_on_collision = allow_suffix_on_collision

    def check(self):
        if self.cancelled():
            raise InterruptedError('Planning stopped')

    def scan(self, root, *, exclude_patterns=None, progress_cb=None, only_files=None, existing_only=False):
        if not self.runtime or not self.runtime.available:
            raise LLMServerError('Local AI is required. Install the AI engine and model using Setup AI.')
        root = Path(root).resolve()
        if self.cache_path and self.cache_path.resolve().is_relative_to(root):
            self.cache_path = None
        inventory = FolderPolicy(self.runtime, self.progress, self.cancelled, self.cache_path).scan(
            root, exclude_patterns=exclude_patterns, progress_cb=progress_cb)
        return inventory, self.plan(inventory, only_files=only_files, existing_only=existing_only)

    def _profile(self, evidence):
        cache = EvidenceCache(self.cache_path)
        key = cache_key('folder-profile-v1', CATALOG_PROMPT, self.runtime.model_cache_key, evidence)
        stored = cache.get(key)
        if isinstance(stored, dict):
            try:
                return validate_profile(stored, evidence)
            except ValueError:
                pass
        feedback = ''
        for attempt in range(3):
            self.check()
            row = {'id': 1, 'origin': 'existing folder', 'evidence': evidence}
            if feedback:
                row['validation_feedback'] = feedback
            value = self.runtime.profile_items([row], self.progress, self.cancelled)[0]
            try:
                profile = validate_profile(value, evidence)
                if profile['scope_kind'] not in {'unknown', 'mixed'}:
                    cache.put(key, profile)
                return profile
            except ValueError as error:
                feedback = str(error)
        log.debug('Folder profile held after evidence validation: %s', feedback)
        return None

    def _destinations(self, inventory, preparation):
        root = Path(inventory.root)
        by_parent = defaultdict(list)
        for entry in inventory.entries:
            by_parent[str(PurePosixPath(entry.rel_path).parent)].append(entry)
        destinations = []
        for directory in inventory.directories:
            self.check()
            if os.path.normcase(directory).replace('\\', '/') not in inventory.authorization:
                continue
            direct = by_parent[directory]
            if not direct:
                continue
            samples = []
            for entry in direct[:3]:
                try:
                    evidence, _ = preparation.read(root, entry, 4096)
                    samples.append(f'File: {entry.filename}; kind: {kind_of(entry)}\n' + evidence.text[:600])
                except InterruptedError:
                    raise
                except (OSError, ValueError):
                    log.warning('Folder sample unavailable: %s', entry.rel_path, exc_info=True)
            if samples:
                evidence = 'Existing folder: ' + directory + '\n' + '\n'.join(samples)
                profile = self._profile(evidence)
                if profile is not None:
                    destinations.append({'path': directory, 'existing': True, 'profile': profile})
        return destinations

    def plan(self, inventory, *, only_files=None, existing_only=False):
        self.check()
        root = Path(inventory.root)
        if self.cache_path and self.cache_path.resolve().is_relative_to(root):
            self.cache_path = None
        plan = OrganizationPlan(str(root), authorization=inventory.authorization)
        if not inventory.authorization:
            if inventory.entries:
                raise ValueError('Folder authorization is missing; collect the inventory with Planner.scan')
            plan.excluded_count = inventory.total_files
            return plan
        preparation = Preparation(self.progress, self.cancelled, cache_path=self.cache_path,
                                  shortcuts_movable=self.shortcuts_movable, executables_movable=self.executables_movable)
        with log_stage(log, 'Content preparation'):
            files = preparation.prepare(inventory, only_files)
        if not files:
            plan.excluded_count = inventory.total_files
            return plan
        with log_stage(log, 'Existing folder sampling'):
            destinations = self._destinations(inventory, preparation)
        existing = list(destinations)
        offered = [{key: row[key] for key in ('id', 'name', 'kind', 'basis', 'evidence')} for row in files]
        with log_stage(log, 'Group discovery'):
            proposals = [] if existing_only else self.runtime.propose_groups(offered, destinations, self.progress, self.cancelled)
            for proposal in proposals:
                self.check()
                if len(set(proposal['members'])) < 2 or proposal['path'] in {dest['path'] for dest in destinations}:
                    continue
                try:
                    relative_parts(proposal['path'])
                    FolderAuthorization(root, inventory.authorization).check(checked_path(root, proposal['path'] + '/__check__'))
                except (OSError, ValueError):
                    log.debug('Ignored unsupported destination proposal', exc_info=True)
                    continue
                destinations.append({'path': proposal['path'], 'existing': False, 'profile': {
                    'description': proposal['purpose'], 'scope_kind': 'topic', 'scope': [],
                    'topic': proposal['purpose'], 'quote': ''}})

        def choose(row):
            self.check()
            decision = self.runtime.choose_destination(
                {key: row[key] for key in ('id', 'kind', 'basis', 'evidence')}, destinations,
                self.progress, self.cancelled) if destinations else {'folder': ''}
            self.check()
            return decision

        self.progress('Matching files to folders…')
        with log_stage(log, 'Destination matching'), ThreadPoolExecutor(max_workers=self.runtime.slots) as pool:
            matches = list(pool.map(choose, files))
        selected = {row['id']: match for row, match in zip(files, matches) if match['folder']}
        audit = {}
        self.progress('Reviewing the complete preview…')
        with log_stage(log, 'Independent review'):
            for destination in destinations:
                self.check()
                assigned = [row for row in files if selected.get(row['id'], {}).get('folder') == destination['path']]
                if not assigned or (not destination['existing'] and len(assigned) < 2):
                    continue
                decisions = self.runtime.review_destination(
                    [{key: row[key] for key in ('id', 'kind', 'basis', 'evidence')} for row in assigned],
                    destination, existing, self.progress, self.cancelled)
                accepted = [row for row in assigned if decisions.get(row['id'], {}).get('verdict') == 'keep']
                if not destination['existing'] and len(accepted) < 2:
                    accepted = []
                for row in assigned:
                    audit[row['rel_path']] = decisions.get(row['id'], {'verdict': 'hold', 'reason': 'Review omitted this file'})
                for row in accepted:
                    match = selected[row['id']]
                    decision = decisions[row['id']]
                    plan.actions.append(PlanAction('move', row['rel_path'], destination['path'] + '/' + row['name'],
                        category=destination['path'], source_identity=row['source_identity'],
                        reason=match['reason'] + '\nEvidence: ' + match['evidence'] + '\nReview: ' + decision['reason']))
        self.check()
        report = Validator(root, shortcuts_movable=self.shortcuts_movable, executables_movable=self.executables_movable,
                           allow_suffix_on_collision=self.allow_suffix_on_collision).validate_plan(plan)
        counts = Counter(action.category for action in report.valid_actions)
        existing_paths = {dest['path'] for dest in existing}
        plan.actions = [action for action in report.valid_actions if action.category in existing_paths or counts[action.category] >= 2]
        plan.warnings = report.warnings
        plan.excluded_count = inventory.total_files - len(plan.actions)
        plan.review = {'proposed': len(selected), 'retained': len(plan.actions), 'decisions': audit}
        log.info('Preview ready: moves=%d held=%d warnings=%d', len(plan.actions), plan.excluded_count, len(plan.warnings))
        return plan
