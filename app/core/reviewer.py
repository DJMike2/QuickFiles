"""Independent semantic review followed by whole-plan deterministic checks."""
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
import re

from app.ai.schema import PlanWarning
from app.core.blocked_paths import checked_path
from app.core.file_state import identity
from app.core.organization import scope_compatible, scope_matches
from app.core.validator import Validator


class Reviewer:
    def __init__(self, runtime, progress=None, cancelled=None, *, shortcuts_movable=False,
                 executables_movable=False, allow_suffix_on_collision=True):
        self.runtime = runtime
        self.progress = progress or (lambda message: None)
        self.cancelled = cancelled or (lambda: False)
        self.shortcuts_movable = shortcuts_movable
        self.executables_movable = executables_movable
        self.allow_suffix_on_collision = allow_suffix_on_collision

    def _check(self):
        if self.cancelled():
            raise InterruptedError('Independent review stopped')

    def review(self, plan, rows, destinations):
        self._check()
        root = Path(plan.root)
        by_source = {row['rel_path']: row for row in rows}
        proposed = len(plan.actions)
        audit, retained = {}, []
        groups = defaultdict(list)
        for action in plan.actions:
            groups[action.category].append(action)
        existing = [{'path': path, 'profile': asdict(d['profile'])}
                    for path, d in destinations.items() if d['existing']]
        for path, actions in groups.items():
            self._check()
            target = destinations[path]
            profiles = [by_source[a.src_rel]['profile'] for a in actions]
            scoped = [p for p in profiles if p.scope_kind in {'course', 'client', 'project'}]
            words = set(re.findall(r'[^\W\d_]+', path.casefold()))
            generic = {'data', 'archive', 'archives', 'file', 'files', 'document', 'documents',
                       'folder', 'folders', 'general', 'other', 'misc', 'notes', 'note', 'processed'}
            if (not target['existing'] and target['profile'].scope_kind == 'topic'
                    and not words - generic):
                decisions = {by_source[a.src_rel]['id']: {'verdict': 'hold',
                    'reason': 'New topic destination is only a date or generic container, not a shared subject'} for a in actions}
            elif not scope_compatible(scoped):
                decisions = {by_source[a.src_rel]['id']: {'verdict': 'hold',
                    'reason': 'Destination mixes incompatible explicit identities'} for a in actions}
            elif target['profile'].scope_kind in {'course', 'client', 'project', 'kind'}:
                # The planner's type/identity boundaries have no probabilistic ambiguity.
                decisions = {by_source[a.src_rel]['id']: {'verdict': 'keep',
                    'reason': 'Rechecked explicit identity or broad file kind'} for a in actions}
            else:
                self.progress(f'Independently reviewing {path}… {len(actions)} files')
                files = [{k: row[k] for k in ('id', 'kind', 'basis', 'evidence')}
                         for row in (by_source[a.src_rel] for a in actions)]
                decisions = self.runtime.review_destination(files, {'path': path, 'existing': target['existing'],
                    'profile': asdict(target['profile']), 'samples': target.get('source', '')[:2400]},
                    existing, self.progress, self.cancelled)
            for action in actions:
                self._check()
                decision = decisions.get(by_source[action.src_rel]['id'],
                    {'verdict': 'hold', 'reason': 'Independent review omitted this file'})
                row = by_source[action.src_rel]
                if (not scope_matches(row['profile'], target['profile'])
                        or row['profile'].scope_kind in {'unknown', 'mixed'}
                        or (target['profile'].scope_kind == 'kind' and row['kind'] not in target['families'])):
                    decision = {'verdict': 'hold', 'reason': 'Final type or explicit identity check rejected the match'}
                audit[action.src_rel] = dict(decision)
                if decision['verdict'] == 'keep':
                    action.reason += '\nReview: ' + decision['reason']
                    retained.append(action)
        plan.actions = retained
        self._check()
        # Recheck after inference, not just before it. Retain the preview identity for apply.
        unchanged = []
        for action in plan.actions:
            try:
                if action.source_identity != identity(checked_path(root, action.src_rel).stat()):
                    raise ValueError('File changed during independent review')
                unchanged.append(action)
            except (OSError, ValueError) as exc:
                audit[action.src_rel] = {'verdict': 'hold', 'reason': str(exc)}
        plan.actions = unchanged
        report = Validator(root, allow_suffix_on_collision=self.allow_suffix_on_collision,
            shortcuts_movable=self.shortcuts_movable, executables_movable=self.executables_movable).validate_plan(plan)
        plan.actions, plan.warnings = report.valid_actions, report.warnings
        for action, reason in report.rejected_actions:
            audit[action.src_rel] = {'verdict': 'hold', 'reason': reason}
        counts = Counter(action.category for action in plan.actions)
        final = []
        for action in plan.actions:
            if not destinations[action.category]['existing'] and counts[action.category] < 2:
                audit[action.src_rel] = {'verdict': 'hold', 'reason': 'New destination lost its second supported file during review'}
            else:
                final.append(action)
        plan.actions = final
        held = {source: d['reason'] for source, d in audit.items() if d['verdict'] != 'keep'}
        for source, reason in held.items():
            plan.warnings.append(PlanWarning('', f'Kept {source}: {reason}'))
        for number, action in enumerate(plan.actions, 1):
            action.id = str(number)
        self._check()
        plan.review = {'proposed': proposed, 'retained': len(plan.actions), 'held': held, 'decisions': audit}
        return plan
