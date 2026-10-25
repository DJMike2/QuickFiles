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

    def planner(self, responses):
        manager = SimpleNamespace(is_downloaded=True, runtime_settings=SETTINGS)
        runtime = AIRuntime(SimpleNamespace(is_available=True), manager)
        server = ScriptedServer(responses)
        runtime._server = server
        return Planner(runtime), server

    def test_only_loose_files_and_valid_folders_are_used(self):
        folders = json.dumps({"folders": [
            {"path": "Sorted", "holds": "related text"},
            {"path": "Project/Injected", "holds": "unsafe"},
            {"path": "../escape", "holds": "unsafe"},
        ]})
        planner, server = self.planner([folders, "1:2\n2:2\n3:0\n"])
        plan = planner.plan(self.inventory)
        self.assertEqual([(a.src_rel, a.dst_rel) for a in plan.actions],
                         [("alpha.txt", "Sorted/alpha.txt"), ("beta.txt", "Sorted/beta.txt")])
        self.assertEqual(self.inventory.protected_projects, ["Project"])
        self.assertFalse(any(entry.rel_path.startswith("Project/") for entry in self.inventory.entries))
        self.assertEqual(len(plan.warnings), 2)
        self.assertEqual(len(server.requests), 2)
        self.assertEqual([r['name'] for r in server.requests[0][1]['loose_files']],
                         ['alpha.txt', 'beta.txt', 'unknown.bin'])

    def test_new_singleton_is_kept_but_existing_singleton_is_allowed(self):
        folders = '{"folders":[{"path":"Sorted","holds":"related text"}]}'
        planner, _ = self.planner([folders, "1:2\n2:0\n3:0\n", '{"folders":[]}'])
        self.assertEqual(planner.plan(self.inventory).actions, [])
        planner, _ = self.planner(['{"folders":[]}', "1:1\n2:0\n3:0\n", '{"folders":[]}'])
        self.assertEqual([a.dst_rel for a in planner.plan(self.inventory).actions], ["Existing/alpha.txt"])

    def test_one_leftover_pass_uses_the_same_destination_ids(self):
        planner, server = self.planner([
            '{"folders":[]}', "1:0\n2:0\n3:0\n",
            '{"folders":[{"path":"Sorted","holds":"related text"}]}', "1:2\n2:2\n3:0\n",
        ])
        plan = planner.plan(self.inventory)
        self.assertEqual(len(plan.actions), 2)
        self.assertEqual(len(server.requests), 4)
        self.assertEqual(server.requests[2][1]['already_planned'], ['Existing'])

    def test_missing_or_reordered_assignments_abort_without_a_partial_plan(self):
        for response in ("1:1\n", "2:1\n1:1\n3:0\n", "1:999\n2:0\n3:0\n"):
            with self.subTest(response=response):
                planner, server = self.planner(['{"folders":[]}', response])
                with self.assertRaises(LLMServerError):
                    planner.plan(self.inventory)
                self.assertFalse(server.is_running)

    def test_default_model_is_4b_and_a_truncated_download_is_not_ready(self):
        manager = ModelManager(self.root)
        self.assertEqual(manager.model_info['label'], 'Qwen3.5-4B Q6_K')
        self.assertEqual(manager.testing_model_info['label'], 'Qwen3.5-9B Q6_K_L')
        manager.model_path.write_bytes(b'GGUFpartial')
        self.assertFalse(manager.is_downloaded)

    def test_executable_permission_remains_separate_from_shortcut_permission(self):
        planner, _ = self.planner(['{"folders":[]}', '1:0\n2:0\n3:1\n4:0\n', '{"folders":[]}'])
        planner.executables_movable = True
        plan = planner.plan(self.inventory)
        self.assertEqual([action.src_rel for action in plan.actions], ['protected.exe'])
        planner, server = self.planner(['{"folders":[]}', '1:0\n2:0\n3:0\n', '{"folders":[]}'])
        planner.shortcuts_movable = True
        planner.plan(self.inventory)
        self.assertNotIn('protected.exe', [row['name'] for row in server.requests[0][1]['loose_files']])

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
