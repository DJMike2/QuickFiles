"""Cancellation stops blocking readers without publishing partial results."""
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.ai.evidence import extract_evidence
from app.ai.content import read_content
from app.ai.llm_server import LLMServer
from app.core.preparation import Preparation
from app.core.scanner import Scanner


class ReaderCancellationTests(unittest.TestCase):
    def test_cancel_during_pdf_extraction_kills_reader_and_propagates_without_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'note.pdf').write_bytes(b'PDF fixture')
            inventory = Scanner(root).scan()
            event = threading.Event()
            children = []
            original = subprocess.Popen
            def start(*args, **kwargs):
                child = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
                children.append(child)
                event.set()
                return child
            # Cancellation arrives after the child starts, rather than before reading.
            cancel = threading.Event()
            def stop():
                event.wait(2)
                time.sleep(0.05)
                cancel.set()
            thread = threading.Thread(target=stop)
            thread.start()
            started = time.perf_counter()
            try:
                runtime = SimpleNamespace(summary_cache_key='fixture')
                with patch('app.ai.content.shutil.which', return_value='pdftotext'), \
                     patch('app.ai.content.subprocess.Popen', side_effect=start), \
                     self.assertNoLogs('app.ai.evidence', level='WARNING'), \
                     self.assertNoLogs('app.core.preparation', level='WARNING'):
                    with self.assertRaises(InterruptedError):
                        Preparation(runtime, cancelled=cancel.is_set).prepare(inventory)
                self.assertLess(time.perf_counter()-started, 0.5)
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll())
                self.assertTrue(children[0].stdout.closed)
                self.assertTrue(children[0].stderr.closed)
            finally:
                thread.join(2)
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.communicate()

    def test_already_cancelled_search_reader_does_not_open_a_file(self):
        entry = SimpleNamespace(abs_path='not-opened.pdf', extension='.pdf')
        with patch('app.ai.content.subprocess.Popen') as start:
            with self.assertRaises(InterruptedError):
                read_content(entry, cancelled=lambda: True)
            start.assert_not_called()

    def test_cancellation_is_not_converted_to_filename_only_evidence(self):
        entry = SimpleNamespace(abs_path='not-opened.pdf', extension='.pdf')
        with self.assertNoLogs('app.ai.evidence', level='WARNING'):
            with self.assertRaises(InterruptedError):
                extract_evidence(entry, cancelled=lambda: True)

    def test_pdf_timeout_still_stops_the_child_and_is_logged_as_a_real_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = SimpleNamespace(abs_path=str(Path(directory)/'slow.pdf'), extension='.pdf')
            original = subprocess.Popen
            children = []
            def start(*args, **kwargs):
                child = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
                children.append(child)
                return child
            try:
                with patch('app.ai.content.shutil.which', return_value='pdftotext'), \
                     patch('app.ai.content.subprocess.Popen', side_effect=start), \
                     patch('app.ai.content.time.monotonic', side_effect=[0, 100]), \
                     self.assertLogs('app.ai.evidence', level='WARNING') as logs:
                    result = extract_evidence(entry)
                self.assertEqual(result.status, 'unreadable')
                self.assertIsNotNone(children[0].poll())
                self.assertIn('TimeoutExpired', '\n'.join(logs.output))
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.communicate()

    def test_cancel_during_model_startup_is_not_logged_as_a_startup_failure(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)/'model.gguf'
            model.write_bytes(b'GGUF')
            manager = SimpleNamespace(is_available=True, gpu_binary_path=None,
                cpu_binary_path=Path(directory)/'cpu')
            server = LLMServer(manager, model, {'n_gpu_layers': 0, 'context_tokens': 8192, 'threads': 4})
            cancel = threading.Event()
            process = Mock()
            def exited():
                cancel.set()
                return 1
            process.poll.side_effect = exited
            with patch('app.ai.llm_server.socket.socket') as socket, \
                 patch('app.ai.llm_server.subprocess.Popen', return_value=process), \
                 self.assertNoLogs('app.ai.llm_server', level='WARNING'):
                socket.return_value.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 4321)
                with self.assertRaises(InterruptedError):
                    server.start(cancelled=cancel.is_set)
            self.assertIsNone(server._process)
            self.assertIsNone(server._output)


if __name__ == '__main__':
    unittest.main()
