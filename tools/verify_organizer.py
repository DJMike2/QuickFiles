"""Live-model verification on fresh generated messes, including apply/hash/undo checks.

Never accepts an existing source directory. All moved files are created by this tool.
Expectations are written before execution and are independent of model responses.
Semantic grouping flags are provisional; inspect every reported destination as well.
"""
import argparse
import base64
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
from time import perf_counter
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.binary_manager import BinaryManager
from app.ai.hardware_probe import probe
from app.ai.model_manager import ModelManager
from app.ai.prompts import FILE_SUMMARY_PROMPT, REVIEW_PROMPT
from app.ai.runtime import AIRuntime
from app.config import AppConfig
from app.core.executor import Executor
from app.core.planner import Planner
from app.core.preparation import Preparation
from app.core.scanner import Scanner
from app.core.naming import relative_parts
from app.core.undo import UndoEngine
from app.core.validator import Validator
from app.storage import Journal


def snapshot(root):
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = ['link', str(path.readlink())]
        elif path.is_dir():
            result[name] = ['directory']
        else:
            result[name] = ['file', hashlib.sha256(path.read_bytes()).hexdigest()]
    return result


def create_pdf(path, pages):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject(
            {NameObject('/F1'): writer._add_object(font)})})
        text = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 11 Tf 36 720 Td ({text}) Tj ET'.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as output:
        writer.write(output)


def build(base, scenario):
    if len(relative_parts(scenario['name'])) != 1:
        raise ValueError('Scenario name must be one directory name')
    root = base / scenario['name']
    root.mkdir()
    for row in scenario['files']:
        path = root.joinpath(*relative_parts(row['path']))
        path.parent.mkdir(parents=True, exist_ok=True)
        if row.get('link'):
            path.symlink_to(base / 'outside-sentinel.txt')
        elif 'pages' in row:
            create_pdf(path, row['pages'])
        elif 'members' in row:
            with zipfile.ZipFile(path, 'w') as archive:
                for name, text in row['members'].items():
                    archive.writestr(name, text)
        elif 'base64' in row:
            path.write_bytes(base64.b64decode(row['base64']))
        else:
            path.write_text(row['text'], encoding='utf-8')
        if row.get('executable'):
            path.chmod(0o755)
    return root


def flags_for(scenario, actions):
    rows = {row['path']: row for row in scenario['files']}
    issues = []
    by_folder = defaultdict(list)
    for action in actions:
        source, folder = action.src_rel, str(Path(action.dst_rel).parent).replace('\\', '/')
        row = rows[source]
        by_folder[folder].append(row)
        if row.get('stay'):
            issues.append({'source': source, 'kind': 'should_stay', 'destination': folder})
        if row.get('existing') and folder != row['existing']:
            issues.append({'source': source, 'kind': 'existing_folder_not_reused', 'destination': folder})
        if row.get('forbidden_scope') and re.search(row['forbidden_scope'], folder, re.I):
            issues.append({'source': source, 'kind': 'unsupported_scope', 'destination': folder})
    for folder, members in by_folder.items():
        cohorts = {row['cohort'] for row in members if row.get('cohort')}
        if len(cohorts) > 1:
            issues.append({'kind': 'mixed_cohorts', 'destination': folder, 'cohorts': sorted(cohorts)})
    return issues


def extra_checks(base, runtime, status):
    """Check unchanged-cache reuse separately from the identity changes caused by undo."""
    calls = 0
    generate = runtime._generate
    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return generate(*args, **kwargs)
    runtime._generate = counted
    try:
        root = base / 'freelance'
        before = snapshot(root)
        prep = Preparation(runtime, status, cache_path=base / 'summaries.db')
        prep.prepare(Scanner(root).scan())
        refresh_calls = calls
        reused = prep.prepare(Scanner(root).scan())
        checks = {'cache_refresh_calls': refresh_calls, 'unchanged_cache_hits': reused.cache_hits,
                  'unchanged_cache_new_calls': calls - refresh_calls,
                  'cache_probe_preserved_files': before == snapshot(root)}
        alias = base / 'selected-folder-link'
        alias.symlink_to(base / 'boundaries', target_is_directory=True)
        linked_inventory = Scanner(alias).scan()
        checks['selected_link_resolved_to_real_root'] = Path(linked_inventory.root) == (base / 'boundaries').resolve()
        before_boundary = calls
        linked = Planner(runtime, status).plan(linked_inventory)
        checks['boundary_alias_actions'] = len(linked.actions)
        checks['boundary_alias_model_calls'] = calls - before_boundary
        probe = base / 'identity-check'
        probe.mkdir()
        changed = probe / 'stale.txt'
        changed.write_text('Original subject', encoding='utf-8')
        inventory = Scanner(probe).scan()
        changed.write_text('Changed since the scan; must not receive a stale plan.', encoding='utf-8')
        before_stale = calls
        stale = Preparation(runtime).prepare(inventory)
        checks['changed_since_scan_held'] = 'stale.txt' in stale.held and not stale.files
        checks['changed_since_scan_model_calls'] = calls - before_stale
        try:
            Preparation(runtime, cancelled=lambda: True).prepare(Scanner(probe).scan())
            checks['cancel_at_file_boundary'] = False
        except InterruptedError:
            checks['cancel_at_file_boundary'] = True
        (base / 'extra-checks.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
        return checks
    finally:
        runtime._generate = generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='NEW output directory, default a fresh directory in system temp')
    parser.add_argument('--additional-fixtures', type=Path, action='append', default=[],
                        help='Append controlled scenarios to the baseline (repeatable)')
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error('Output must not exist; this tool only operates on its own generated fixtures')
    base = args.output or Path(tempfile.mkdtemp(prefix='quickfiles-verification-'))
    if args.output:
        base.mkdir(parents=True)
    fixture = Path(__file__).resolve().parents[1] / 'tests/fixtures/organizer_verification.json'
    scenarios = json.loads(fixture.read_text(encoding='utf-8'))
    for fixture_path in args.additional_fixtures:
        scenarios += json.loads(fixture_path.read_text(encoding='utf-8'))
    # Save expectations before any model calls, alongside the complete generated inputs.
    (base / 'expectations.json').write_text(json.dumps(scenarios, indent=2), encoding='utf-8')
    sentinel = base / 'outside-sentinel.txt'
    sentinel.write_text('External link target must remain unchanged.', encoding='utf-8')
    sentinel_hash = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    roots = {case['name']: build(base, case) for case in scenarios}
    config = AppConfig()
    manager = ModelManager(config.models_dir)
    runtime = AIRuntime(BinaryManager(config.app_data_dir, probe()), manager)
    if not runtime.available:
        parser.error('Install the configured local engine and model first')
    model_calls = Counter()
    generate = runtime._generate
    def counted(system, *positional, **kwargs):
        model_calls['summaries' if system == FILE_SUMMARY_PROMPT else
                    'reviews' if system == REVIEW_PROMPT else 'downstream'] += 1
        return generate(system, *positional, **kwargs)
    runtime._generate = counted
    report = {'model': manager.model_info['label'], 'seed': manager.runtime_settings['seed'],
              'output_directory': str(base), 'scenarios': []}
    print(f'Generated fixtures and reports: {base}', flush=True)
    started = perf_counter()
    last_status = None
    def status(message):
        nonlocal last_status
        if message != last_status:
            print(message, flush=True)
            last_status = message
    try:
        for case in scenarios:
            root = roots[case['name']]
            print(f'CASE {case["name"]}: {case["description"]}', flush=True)
            initial = snapshot(root)
            calls_before = model_calls.copy()
            began = perf_counter()
            item = {'name': case['name'], 'generated_files': len(case['files']), 'errors': []}
            try:
                inventory = Scanner(root, exclude_patterns=config.get('exclude_patterns')).scan()
                planner = Planner(runtime, status, summary_cache_path=base / 'summaries.db')
                plan = planner.plan(inventory)
                validation = Validator(root, allow_suffix_on_collision=True).validate_plan(plan)
                actions = validation.valid_actions
                item.update({'scanned_files': inventory.total_files,
                    'protected_folders': inventory.protected_projects,
                    'usable_subjects': len(planner.prepared.files), 'held': planner.prepared.held,
                    'summaries': [asdict(value) for value in planner.prepared.summaries.values()],
                    'moves': [asdict(action) for action in actions],
                    'review': plan.review,
                    'rejected': [{'source': action.src_rel, 'reason': reason}
                                 for action, reason in validation.rejected_actions],
                    'semantic_flags': flags_for(case, actions), 'preview_unchanged': initial == snapshot(root)})
                moved = {action.src_rel for action in actions}
                clear = {row['path'] for row in case['files'] if row.get('cohort')}
                item['clear_eligible'] = len(clear)
                item['clear_files_moved'] = len(clear & moved)
                item['clear_files_left'] = sorted(clear - moved)
                # Apply every validated proposal, including semantically questionable ones,
                # only to disposable fixtures. Check exact paths and hashes, then undo.
                expected = dict(initial)
                for action in actions:
                    expected[action.dst_rel] = expected.pop(action.src_rel)
                    for parent in Path(action.dst_rel).parents:
                        if str(parent) != '.':
                            expected[parent.as_posix()] = ['directory']
                journal = Journal(base / f'{case["name"]}-journal.db')
                try:
                    applied = Executor(root, journal).apply(actions)
                    item['execution'] = asdict(applied)
                    item['all_applied_moves_verified'] = applied.verified == len(actions)
                    item['applied_tree_matches_plan_and_hashes'] = snapshot(root) == expected
                    if actions:
                        batch = journal.get_latest_batch()
                        undone = UndoEngine(root, journal).undo_batch(batch.id)
                        item['undo'] = asdict(undone)
                    item['undo_restored_original_paths_and_hashes'] = snapshot(root) == initial
                finally:
                    journal.close()
                if case['name'] == 'freelance':
                    before_warm = model_calls['summaries']
                    warm = Preparation(runtime, status, cache_path=base / 'summaries.db').prepare(Scanner(root).scan())
                    # Undo changes file identities/ctime even when bytes match.
                    # This is a post-undo refresh, not a truly unchanged warm run.
                    item['post_undo_cache_refresh'] = {'hits': warm.cache_hits,
                                          'new_summary_calls': model_calls['summaries'] - before_warm}
                    changed = root / 'photo.txt'
                    original = changed.read_bytes()
                    changed.write_text('Client: Northstar Bakery. Invoice NB-99 for website services.', encoding='utf-8')
                    before_change = model_calls['summaries']
                    refreshed = Preparation(runtime, status, cache_path=base / 'summaries.db').prepare(Scanner(root).scan())
                    value = refreshed.summaries['photo.txt']
                    item['changed_file_cache'] = {'new_summary_calls': model_calls['summaries'] - before_change,
                        'updated_summary': asdict(value), 'stale_acme_absent': 'Acme' not in value.hint(),
                        'northstar_present': 'Northstar' in value.hint()}
                    changed.write_bytes(original)
                    item['restored_after_cache_probe'] = snapshot(root) == initial
            except Exception as exc:
                item['errors'].append(f'{type(exc).__name__}: {exc}')
                runtime.shutdown()
            item['seconds'] = round(perf_counter()-began, 3)
            item['model_calls'] = dict(model_calls - calls_before)
            report['scenarios'].append(item)
            (base / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'RESULT {case["name"]}: {len(item.get("moves", []))} moves; '
                  f'{len(item.get("semantic_flags", []))} semantic flags; {item["errors"]}', flush=True)
        report['scenario_seconds'] = round(perf_counter()-started, 3)
        report['backend'] = runtime._server.backend if runtime._server else 'not running'
        report['outside_link_target_unchanged'] = hashlib.sha256(sentinel.read_bytes()).hexdigest() == sentinel_hash
        report['extra_checks'] = extra_checks(base, runtime, status)
        report['seconds'] = round(perf_counter()-started, 3)
        report['model_calls'] = dict(model_calls)
        report['mechanical_pass'] = all(not item['errors'] and item.get('preview_unchanged')
            and item.get('applied_tree_matches_plan_and_hashes')
            and item.get('all_applied_moves_verified')
            and item.get('undo_restored_original_paths_and_hashes')
            and item.get('execution', {}).get('failed') == 0 for item in report['scenarios'])
        (base / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Finished: {base / "report.json"}', flush=True)
        print('Semantic flags require review: ' + str(sum(len(item.get('semantic_flags', []))
                                                         for item in report['scenarios'])), flush=True)
        if not report['mechanical_pass']:
            raise SystemExit('Mechanical verification failed; inspect report.json')
    finally:
        runtime.shutdown()


if __name__ == '__main__':
    main()
