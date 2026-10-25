"""Search orchestration, incremental indexing, scope, and grounded UI messages."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import sqlite3
import tempfile
import unittest
import time
from types import SimpleNamespace

import numpy as np
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import QCoreApplication, QEvent

from app.ai.llm_server import LLMServerError
from app.core.search import FileSearch
from app.ui.search_panel import SearchPanel
from app.ui.main_window import MainWindow
from app.config import AppConfig
from app.ai.schema import OrganizationPlan


class Rewriter:
    available = True

    def __init__(self):
        self.filters = {'extensions': [], 'exclude_extensions': [], 'exclude_terms': []}

    def rewrite_query(self, query, status, cancelled):
        return {'query': 'automobile repair', 'answer': 'Found 9999 files', **self.filters}


class Embeddings:
    fingerprint = 'test-encoder'
    dimensions = 3

    def __init__(self):
        self.documents = []

    def embed(self, texts, *args):
        rows = []
        for text in texts:
            if '\n' in text:
                self.documents.append(text)
            lower = text.lower()
            rows.append([1., 0., 0.] if any(word in lower for word in ('car', 'automobile', 'brakes'))
                        else [0., 1., 0.])
        return np.asarray(rows, dtype=np.float32)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base/'files'
        self.root.mkdir()
        (self.root/'a.txt').write_text('Instructions for servicing car brakes')
        (self.root/'b.txt').write_text('A recipe for pasta and tomatoes')
        self.rewriter, self.embeddings = Rewriter(), Embeddings()
        self.database = self.base/'search.sqlite3'
        self.search = FileSearch(self.database, self.rewriter, self.embeddings)

    def test_count_comes_from_results_and_sources_are_read_only(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        payload = self.search.search(self.root, 'car repair')
        self.assertEqual(payload['query'], 'car repair')
        self.assertEqual(payload['matched_files'], 1)
        self.assertEqual(payload['results'][0]['name'], 'a.txt')
        self.assertIn('Found 1 matching file.', payload['answer'])
        self.assertNotIn('9999', payload['answer'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_unchanged_files_reuse_vectors_and_edits_deletions_refresh_cache(self):
        self.search.search(self.root, 'car repair')
        self.assertEqual(len(self.embeddings.documents), 2)
        self.search.search(self.root, 'car repair')
        self.assertEqual(len(self.embeddings.documents), 2)
        (self.root/'b.txt').write_text('Additional instructions for car repair')
        (self.root/'a.txt').unlink()
        payload = self.search.search(self.root, 'car repair')
        self.assertEqual(len(self.embeddings.documents), 3)
        self.assertEqual([r['name'] for r in payload['results']], ['b.txt'])
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute('SELECT path FROM search_files').fetchall(), [('b.txt',)])

    def test_filters_and_no_matches_have_real_counts(self):
        self.rewriter.filters['extensions'] = ['.pdf']
        payload = self.search.search(self.root, 'car repair PDFs')
        self.assertEqual(payload['results'], [])
        self.assertEqual(payload['matched_files'], 0)
        self.assertTrue(payload['answer'].startswith('No close matches'))
        self.rewriter.filters['extensions'] = ['.txt']
        self.rewriter.filters['exclude_terms'] = ['brakes']
        self.assertEqual(self.search.search(self.root, 'car repair excluding brakes')['matched_files'], 0)

    def test_total_count_is_distinct_from_ten_displayed_results(self):
        for i in range(11):
            (self.root/f'car_{i}.txt').write_text('car repair')
        payload = self.search.search(self.root, 'car repair')
        self.assertEqual(payload['matched_files'], 12)
        self.assertEqual(len(payload['results']), 10)
        self.assertIn('Found 12 matching files. Showing the top 10.', payload['answer'])

    def test_project_sources_are_searchable_dependencies_and_links_are_skipped(self):
        project = self.root/'Project'
        (project/'.git').mkdir(parents=True)
        (project/'node_modules').mkdir()
        (project/'code.txt').write_text('car repair project')
        (project/'node_modules/ignored.txt').write_text('car repair dependency')
        (self.root/'.hidden.txt').write_text('car repair hidden')
        outside = self.base/'outside.txt'
        outside.write_text('car repair outside scope')
        if os.name != 'nt':
            (self.root/'linked.txt').symlink_to(outside)
            (self.root/'car:notes.txt').write_text('car repair local filename')
        payload = self.search.search(self.root, 'car repair')
        paths = {row['id'] for row in payload['results']}
        self.assertIn('Project/code.txt', paths)
        self.assertFalse(any('node_modules' in path or path.startswith('.') or 'linked' in path for path in paths))
        if os.name != 'nt':
            self.assertIn('car:notes.txt', paths)

    def test_unavailable_llm_and_cancellation_never_become_fake_empty_results(self):
        with self.assertRaisesRegex(ValueError, '12,000'):
            self.search.search(self.root, 'a'*12001)
        with self.assertRaises(InterruptedError):
            self.search.search(self.root, 'car repair', cancelled=lambda: True)
        self.assertFalse(self.database.exists())
        self.rewriter.available = False
        with self.assertRaises(LLMServerError):
            self.search.search(self.root, 'car repair')


class SearchPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = SearchPanel(show_json_loader=False)
        self.addCleanup(self.panel.deleteLater)

    def test_history_restores_original_query_and_grounded_answer(self):
        first = {'query': 'a messy original request', 'normalized_query': 'car repair',
                 'root': '/example', 'answer': 'Found 12 matching files. Showing the top 10.',
                 'results': [{'name': f'{i}.txt', 'path': f'/example/{i}.txt', 'score': .9}
                             for i in range(10)]}
        self.panel.display_results(first)
        self.panel.display_results({'query': 'something else', 'answer': 'No close matches found.', 'results': []})
        self.panel.graph.show_history_item(0)
        self.assertEqual(self.panel.query.text(), first['query'])
        self.assertEqual(self.panel.response.text(), first['answer'])
        self.assertEqual(self.panel.folder.text(), first['root'])

    def test_search_has_no_organizer_actions_and_disables_overlapping_jobs(self):
        buttons = {button.text() for button in self.panel.findChildren(QPushButton)}
        self.assertFalse(buttons & {'Scan', 'Apply', 'Undo'})
        received = []
        self.panel.searchRequested.connect(received.append)
        self.panel.query.setText('car repair')
        self.panel.set_external_busy(True)
        self.panel._request_search()
        self.assertEqual(received, [])
        self.panel.set_external_busy(False)
        self.panel.set_searching(True)
        self.panel._request_search()
        self.assertEqual(received, [])
        self.panel.set_searching(False)
        self.panel._request_search()
        self.assertEqual(received, ['car repair'])


class SearchWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self, search):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        config = AppConfig(root/'data')
        config.update({'allowed_root': str(root), 'search_root': str(root)})
        context = SimpleNamespace(config=config, search=search, shutdown=lambda: None,
            runtime=SimpleNamespace(available=True, backend_label='Local AI ready'),
            journal=SimpleNamespace(get_latest_batch=lambda: None))
        window = MainWindow(context)
        def cleanup():
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        self.addCleanup(cleanup)
        return window

    def finish(self, window):
        deadline = time.monotonic()+5
        while window.search_worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(window.search_worker)

    def test_failure_is_not_reported_as_zero_matches_or_appended_to_history(self):
        def fail(*args):
            raise RuntimeError('Index unavailable')
        window = self.window(SimpleNamespace(search=fail))
        original_plan = OrganizationPlan(window.config.get('allowed_root'))
        window.plan = original_plan
        with self.assertLogs('app.ui.worker', level='ERROR'):
            window._search('car repair')
            self.finish(window)
        self.assertEqual(window.search_panel.response.text(), 'Search failed: Index unavailable')
        self.assertEqual(window.search_panel.graph._history, [])
        self.assertIs(window.plan, original_plan)

    def test_cancelled_search_does_not_publish_partial_results(self):
        def search(root, query, status, progress, cancelled):
            while not cancelled():
                time.sleep(.005)
            raise InterruptedError('Search stopped')
        window = self.window(SimpleNamespace(search=search))
        window._search('car repair')
        self.assertFalse(window.scan.isEnabled())
        window._cancel_search()
        self.finish(window)
        self.assertEqual(window.search_panel.response.text(), 'Search stopped.')
        self.assertEqual(window.search_panel.graph._history, [])
        self.assertTrue(window.search_panel.search_button.isEnabled())


if __name__ == '__main__':
    unittest.main()
