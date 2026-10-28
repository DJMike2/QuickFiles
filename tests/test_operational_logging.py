"""Real operational failures are logged even when the UI uses simple messages."""
from io import BytesIO, StringIO
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.ai.evidence import extract_evidence
from app.ai.llm_server import LLMServer
from app.core.scanner import Scanner
from app.logging_setup import log_unhandled_exception, setup_logging


class OperationalLoggingTests(unittest.TestCase):
    def test_log_file_is_added_when_logging_is_already_configured_and_is_not_duplicated(self):
        root = logging.getLogger()
        original_handlers, original_level = root.handlers[:], root.level
        console = logging.StreamHandler(StringIO())
        root.handlers = [console]
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                setup_logging(path)
                setup_logging(path)
                logging.getLogger('app.test').error('Genuine scan failure')
                logging.getLogger('app.core.planner').debug('Skipped suggested group: too deep')
                content = (path/'app.log').read_text()
                self.assertEqual(content.count('Genuine scan failure'), 1)
                self.assertNotIn('Skipped suggested group', content)
                for handler in root.handlers:
                    handler.close()
                root.handlers = []
        finally:
            for handler in root.handlers:
                handler.close()
            root.handlers, root.level = original_handlers, original_level

    def test_unhandled_callback_failure_logs_its_traceback(self):
        with self.assertLogs('app.logging_setup', level='ERROR') as logs:
            try:
                raise OSError('Failed to open selected file')
            except OSError:
                log_unhandled_exception(*sys.exc_info())
        content = '\n'.join(logs.output)
        self.assertIn('Traceback', content)
        self.assertIn('Failed to open selected file', content)

    def test_application_startup_failure_is_saved_to_the_log(self):
        script = '''
from pathlib import Path
import sys
from unittest.mock import patch
from app.config import AppConfig
from app.main import main
config = AppConfig(Path(sys.argv[1]))
previous = sys.excepthook
with patch('app.config.AppConfig', return_value=config), \\
     patch('app.bootstrap.AppContext', side_effect=OSError('Startup database failed')):
    assert main() == 1
assert sys.excepthook is previous
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-B', '-c', script, directory],
                cwd=Path(__file__).resolve().parents[1],
                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = (Path(directory)/'logs/app.log').read_text()
            self.assertIn('Application failed to start or run', content)
            self.assertIn('Startup database failed', content)
            self.assertIn('Traceback', content)

    def test_failed_gpu_startup_logs_a_warning_even_when_cpu_fallback_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)/'model.gguf'
            model.write_bytes(b'GGUF')
            manager = SimpleNamespace(is_available=True,
                gpu_binary_path=Path(directory)/'gpu', cpu_binary_path=Path(directory)/'cpu',
                hardware=SimpleNamespace(backend='cuda'))
            server = LLMServer(manager, model, {'n_gpu_layers': 99, 'context_tokens': 8192, 'threads': 4})
            gpu, cpu = Mock(), Mock()
            gpu.poll.return_value, cpu.poll.return_value = 1, None
            response = BytesIO(b'healthy')
            response.status = 200
            statuses = []
            try:
                with patch('app.ai.llm_server.socket.socket') as socket, \
                     patch('app.ai.llm_server.subprocess.Popen', side_effect=[gpu, cpu]), \
                     patch('app.ai.llm_server.urllib.request.urlopen', return_value=response), \
                     self.assertLogs('app.ai.llm_server', level='WARNING') as logs:
                    socket.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 4321)
                    server.start(statuses.append)
                self.assertEqual(server.backend, 'cpu')
                self.assertIn('startup failed (cuda)', '\n'.join(logs.output))
                self.assertIn('Engine exited during startup', '\n'.join(logs.output))
                self.assertTrue(all(s == 'Starting local AI…' for s in statuses))
            finally:
                server.stop()

    def test_file_read_failure_is_logged_while_filename_fallback_remains_available(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = SimpleNamespace(abs_path=str(Path(directory)/'missing.txt'), extension='.txt')
            with self.assertLogs('app.ai.evidence', level='WARNING') as logs:
                result = extract_evidence(entry)
            self.assertEqual(result.status, 'unreadable')
            self.assertIn('missing.txt', '\n'.join(logs.output))
            self.assertIn('FileNotFoundError', '\n'.join(logs.output))

    def test_unsupported_format_is_not_an_operational_warning(self):
        entry = SimpleNamespace(abs_path='/example/photo.png', extension='.png')
        with self.assertNoLogs('app.ai.evidence', level='WARNING'):
            self.assertEqual(extract_evidence(entry).status, 'unsupported')

    def test_partial_native_scan_errors_are_logged_without_failing_the_whole_scan(self):
        records = [{'t': 'H', 'protocol': 1, 'threads': 1}, {'t': 'S', 'skipped': 0, 'errors': 3}]
        child = SimpleNamespace(returncode=0,
            stdout=BytesIO(b''.join(json.dumps(r).encode()+b'\n' for r in records)), poll=lambda: 0)
        with tempfile.TemporaryDirectory() as directory, \
             patch('app.core.scanner.subprocess.Popen', return_value=child), \
             self.assertLogs('app.core.scanner', level='WARNING') as logs:
            inventory = Scanner(Path(directory)).scan()
        self.assertEqual(inventory.error_count, 3)
        self.assertIn('Scan completed with 3 errors', '\n'.join(logs.output))


if __name__ == '__main__':
    unittest.main()
