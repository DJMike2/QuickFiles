"""Planning boundaries and local-engine contracts; no model downloads or file moves."""
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from app.ai.llm_server import LLMServer, LLMServerError
from app.ai.model_manager import ModelManager
from app.ai.runtime import AIRuntime
from app.core.planner import Planner
from app.core.scanner import Scanner

SETTINGS = json.loads((Path(__file__).resolve().parents[1] / "models/settings.json").read_text())["runtime"]


class ScriptedServer:
    is_running = True

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def chat(self, system, user, tokens, temperature, **constraints):
        self.requests.append((system, json.loads(user), tokens, constraints))
        return next(self.responses)

    def stop(self):
        self.is_running = False


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ("alpha.txt", "beta.txt", "unknown.bin"):
            (self.root / name).write_text("example")
        (self.root / "Existing").mkdir()
        (self.root / "Existing/anchor.txt").write_text("existing content")
        (self.root / "Project").mkdir()
        (self.root / "Project/package.json").write_text("{}")
        (self.root / "Project/source.js").write_text("do not move")
        (self.root / "protected.exe").write_bytes(b"protected")
        self.inventory = Scanner(self.root).scan()

    def planner(self, groups, choices):
        manager = SimpleNamespace(is_downloaded=True, runtime_settings=SETTINGS)
        runtime = AIRuntime(SimpleNamespace(is_available=True), manager)
        runtime.summarize_file = lambda source, **kwargs: {
            'summary': source, 'subject': 'example', 'identifiers': [], 'quotes': [source]}
        profiles_seen = []
        def profiles(rows, *args):
            profiles_seen.extend(rows)
            return [{'id': row['id'], 'description': 'example documents', 'scope_kind': 'topic',
                     'scope': [], 'topic': 'example', 'quote': row['evidence'].splitlines()[0]}
                    for row in rows]
        runtime.profile_items = profiles
        runtime.propose_groups = lambda *args: groups
        from collections import deque
        choices = deque(choices)
        def choose(file, destinations, *args):
            folder = choices[0] if choices else ''
            if folder == 'Sorted' and folder not in {d['path'] for d in destinations}:
                return {'folder': '', 'match': 'uncertain', 'reason': 'Existing folder does not match', 'evidence': ''}
            if choices:
                choices.popleft()
            return {'folder': folder, 'match': 'clear' if folder else 'uncertain',
                    'reason': 'Shared example content', 'evidence': 'example'}
        runtime.choose_destination = choose
        runtime.review_destination = lambda files, *args: {f['id']: {'verdict': 'keep',
            'reason': 'The independent fixture judge agrees on example content', 'evidence': 'example'} for f in files}
        return Planner(runtime), profiles_seen

    def test_only_loose_files_and_valid_folders_are_used(self):
        groups = [{'path': path, 'purpose': 'example documents', 'members': [1, 2]}
                  for path in ['Sorted', 'Project/Injected', '../escape']]
        planner, seen = self.planner(groups, ['Sorted', 'Sorted', ''])
        plan = planner.plan(self.inventory)
        self.assertEqual([(a.src_rel, a.dst_rel) for a in plan.actions],
                         [('alpha.txt', 'Sorted/alpha.txt'), ('beta.txt', 'Sorted/beta.txt')])
        self.assertEqual(self.inventory.protected_projects, ['Project'])
        self.assertFalse(any(entry.rel_path.startswith('Project/') for entry in self.inventory.entries))
        self.assertEqual(len(plan.warnings), 2)
        self.assertFalse(any('source.js' in row['evidence'] for row in seen))

    def test_new_singleton_is_kept_but_existing_singleton_is_allowed(self):
        group = [{'path': 'Sorted', 'purpose': 'example', 'members': [1, 2]}]
        planner, _ = self.planner(group, ['Sorted', '', ''])
        self.assertEqual(planner.plan(self.inventory).actions, [])
        planner, _ = self.planner([], ['Existing', '', ''])
        self.assertEqual([a.dst_rel for a in planner.plan(self.inventory).actions], ['Existing/alpha.txt'])

    def test_groups_need_two_real_supporting_ids(self):
        groups = [{'path': 'Invented', 'purpose': 'example', 'members': [1, 999]}]
        planner, _ = self.planner(groups, ['', '', ''])
        plan = planner.plan(self.inventory)
        self.assertNotIn('Invented', planner.destinations)
        self.assertEqual(plan.actions, [])
        self.assertTrue(plan.warnings)

    def test_assignment_cannot_escape_compatible_destinations(self):
        planner, _ = self.planner([], ['../escape'])
        with self.assertRaises(LLMServerError):
            planner.plan(self.inventory)

    def test_default_model_is_4b_and_a_truncated_download_is_not_ready(self):
        manager = ModelManager(self.root)
        self.assertEqual(manager.model_info['label'], 'Qwen3.5-4B Q6_K')
        self.assertEqual(manager.testing_model_info['label'], 'Qwen3.5-9B Q6_K_L')
        manager.model_path.write_bytes(b'GGUFpartial')
        self.assertFalse(manager.is_downloaded)

    def test_executable_permission_remains_separate_from_shortcut_permission(self):
        planner, _ = self.planner([], ['', '', '', ''])
        planner.executables_movable = True
        planner.plan(self.inventory)
        self.assertIn('protected.exe', planner.prepared.summaries)
        planner, _ = self.planner([], ['', '', ''])
        planner.shortcuts_movable = True
        planner.plan(self.inventory)
        self.assertNotIn('protected.exe', planner.prepared.summaries)

    def test_engine_turns_thinking_off_and_rejects_token_limit_answers(self):
        server = LLMServer(None, self.root/'unused.gguf', SETTINGS)
        server._process = SimpleNamespace(poll=lambda: None)
        payloads = []

        def response(request, timeout):
            payloads.append(json.loads(request.data))
            return BytesIO(json.dumps({'choices': [{'finish_reason': 'length',
                'message': {'content': '{"folders":[]}'}}]}).encode())

        with patch('app.ai.llm_server.urllib.request.urlopen', response):
            with self.assertRaises(LLMServerError):
                server.chat('system', 'user', 1024, .2, schema={'type': 'object'})
        self.assertEqual(payloads[0]['chat_template_kwargs'], {'enable_thinking': False})
        self.assertEqual(payloads[0]['seed'], 42)
        self.assertEqual(payloads[0]['top_k'], 20)
        self.assertEqual(payloads[0]['json_schema'], {'type': 'object'})


if __name__ == '__main__':
    unittest.main()
