"""Click Close in a real Qt event loop, isolated from the user's app data."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


ENGINE = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys
import time

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        Path(sys.argv[1]).write_text('request started')
        time.sleep(2 if len(sys.argv) > 2 and sys.argv[2].startswith('cancel-') else 300)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{}')

server = HTTPServer(('127.0.0.1', 0), Handler)
print(server.server_port, flush=True)
server.serve_forever()
'''


CHILD = r'''
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog
from app.ai.llm_server import LLMServer
from app.bootstrap import AppContext
from app.config import AppConfig
from app.main import main
from app.storage import Journal
from app.ui.main_window import MainWindow
from app.ui.worker import Worker

root, scenario, engine_script = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
config = AppConfig(root)
config.update({'allowed_root': str(root), 'watch_new_files': scenario == 'watch'})
state = {'shutdown': False, 'worker_finished': False, 'engine_stopped': True}
server = LLMServer(None, None, {})
if scenario in {'inference', 'embedding', 'cancel-inference', 'cancel-embedding'}:
    server._process = subprocess.Popen(
        [sys.executable, '-u', '-c', engine_script, str(root / 'request.started'), scenario],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    (root / 'engine.pid').write_text(str(server._process.pid))
    server.port = int(server._process.stdout.readline())
    server._process.stdout.close()

def stop_engine():
    process = server._process
    server.stop()
    if process is not None:
        state['engine_stopped'] = process.poll() is not None

def shutdown():
    AppContext.shutdown(context)
    state['shutdown'] = True
    (root / 'shutdown.json').write_text(json.dumps(state))

def operation(status, progress, cancelled):
    try:
        if scenario in {'inference', 'embedding', 'cancel-inference', 'cancel-embedding'}:
            server._request('chat/completions', {})
        else:
            while not cancelled():
                time.sleep(0.01)
    finally:
        # Give Qt a chance to incorrectly destroy the running worker on close.
        time.sleep(0.05)
        state['worker_finished'] = True
    raise InterruptedError('Stopped')

runtime = SimpleNamespace(available=scenario not in {'setup', 'install'},
    backend_label='Test AI', shutdown=stop_engine,
    interrupt=server.interrupt if scenario in {'inference', 'cancel-inference'} else lambda: None)
context = SimpleNamespace(config=config, runtime=runtime, journal=Journal(config.db_path),
    shutdown=shutdown, cancel_ai=lambda: AppContext.cancel_ai(context),
    embeddings=SimpleNamespace(shutdown=stop_engine,
        interrupt=server.interrupt if scenario in {'embedding', 'cancel-embedding'} else lambda: None),
    vision=SimpleNamespace(shutdown=stop_engine, interrupt=lambda: None),
    hardware=SimpleNamespace(cpu_name='Test', cpu_cores=2, ram_gb=8, gpu_name='',
                             backend='cpu', os_warning=''),
    model_manager=SimpleNamespace(vision_downloaded=False,
        model_info={'size_bytes': 1, 'label': 'Test'},
        vision_model_info={'size_bytes': 1, 'label': 'Vision', 'mmproj': {'size_bytes': 1}},
        download=lambda progress, cancelled: operation(None, progress, cancelled)),
    binary_manager=SimpleNamespace(is_available=True))

class Window(MainWindow):
    def show(self):
        super().show()
        if scenario == 'watch':
            self._tray.show()
            assert self._tray.isVisible()
        if scenario in {'worker', 'inference', 'cancel-inference'}:
            self._start('scan', operation)
        if scenario in {'embedding', 'cancel-embedding'}:
            self.search_worker = Worker(operation, self)
            self.search_worker.finished.connect(self._search_finished)
            self.search_worker.start()
            self.search_panel.set_searching(True)
        if scenario == 'dialog':
            QTimer.singleShot(0, lambda: QDialog(self).exec())
        if scenario == 'install':
            from app.ui.first_run_dialog import FirstRunDialog
            def install():
                dialog = self.findChild(FirstRunDialog)
                assert dialog is not None and dialog.isVisible()
                dialog._install()
            QTimer.singleShot(300, install)

        def close():
            if scenario in {'inference', 'embedding', 'cancel-inference', 'cancel-embedding'} and not (root / 'request.started').exists():
                QTimer.singleShot(20, close)
                return
            if scenario.startswith('cancel-'):
                started = time.perf_counter()
                if scenario == 'cancel-inference':
                    self.scan.click()
                else:
                    self.search_panel.cancel_button.click()
                def stopped():
                    if self.worker or self.search_worker:
                        QTimer.singleShot(10, stopped)
                        return
                    state['cancel_seconds'] = time.perf_counter() - started
                    state['window_open_after_cancel'] = self.isVisible()
                    state['scan_enabled_after_cancel'] = self.scan.isEnabled()
                    state['partial_plan_published'] = self.plan is not None
                    self.close()
                stopped()
                return
            if scenario == 'native':
                self.close()
            elif scenario == 'tray':
                self._tray.contextMenu().actions()[-1].trigger()
            else:
                self.title_bar.close_button.click()
        QTimer.singleShot(500, close)

with patch('app.config.AppConfig', return_value=config), \
     patch('app.bootstrap.AppContext', return_value=context), \
     patch('app.ui.main_window.MainWindow', Window):
    sys.exit(main())
'''


class WindowShutdownTests(unittest.TestCase):
    def run_scenario(self, scenario):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            try:
                completed = subprocess.run(
                    [sys.executable, '-B', '-c', CHILD, directory, scenario, ENGINE],
                    cwd=Path(__file__).resolve().parents[1],
                    env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertNotIn('Traceback', completed.stderr)
                self.assertNotIn('QThread: Destroyed', completed.stderr)
                state = json.loads((root / 'shutdown.json').read_text())
                self.assertTrue(state['shutdown'])
                self.assertTrue(state['engine_stopped'])
                if scenario in {'worker', 'install', 'inference', 'embedding'}:
                    self.assertTrue(state['worker_finished'])
                if scenario.startswith('cancel-'):
                    self.assertTrue(state['worker_finished'])
                    self.assertTrue(state['window_open_after_cancel'])
                    self.assertTrue(state['scan_enabled_after_cancel'])
                    self.assertFalse(state['partial_plan_published'])
                    self.assertLess(state['cancel_seconds'], 0.5, state)
            finally:
                # Reap only our fake engine if a regression made the app hang.
                pidfile = root / 'engine.pid'
                if pidfile.exists() and not (root / 'shutdown.json').exists():
                    try:
                        os.kill(int(pidfile.read_text()), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            return state

    def test_title_bar_close_exits_idle_application(self):
        self.run_scenario('idle')

    def test_title_bar_close_exits_with_folder_watching_enabled(self):
        self.run_scenario('watch')

    def test_native_window_close_exits(self):
        self.run_scenario('native')

    def test_tray_quit_exits(self):
        self.run_scenario('tray')

    def test_close_cancels_and_waits_for_worker(self):
        self.run_scenario('worker')

    def test_close_exits_with_modal_dialog(self):
        self.run_scenario('dialog')

    def test_close_exits_with_setup_dialog(self):
        self.run_scenario('setup')

    def test_close_cancels_installation_before_exiting(self):
        self.run_scenario('install')

    def test_close_interrupts_blocked_inference_and_stops_engine(self):
        self.run_scenario('inference')

    def test_close_interrupts_blocked_search_and_stops_engine(self):
        self.run_scenario('embedding')

    def test_stop_button_interrupts_blocked_scan_without_closing_window(self):
        self.run_scenario('cancel-inference')

    def test_cancel_button_interrupts_blocked_search_without_closing_window(self):
        self.run_scenario('cancel-embedding')


if __name__ == '__main__':
    unittest.main()
