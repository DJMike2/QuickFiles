"""Search orchestration, incremental indexing, scope, and grounded UI messages."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import QCoreApplication, QEvent

from app.ai.llm_server import LLMServer, LLMServerError
from app.ai.runtime import AIRuntime
from app.ai.vision import VisionRuntime
from app.core.search import FileSearch, identifiers, keyword_pattern, readable
from app.ui.search_panel import SearchPanel
from app.ui.main_window import MainWindow
from app.config import AppConfig
from app.ai.schema import OrganizationPlan


class Rewriter:
    available = True

    def __init__(self):
        self.topic = 'automobile repair'
        self.filters = {'extensions': [], 'exclude_extensions': [], 'exclude_terms': []}

        self.rejected, self.reviews, self.review_error = set(), [], None

    def rewrite_query(self, query, status, cancelled):
        return {'query': self.topic, 'answer': 'Found 9999 files', **self.filters}

    def review_results(self, query, files, status, cancelled):
        self.reviews.append([file['name'] for file in files])
        if self.review_error:
            raise self.review_error
        return {file['key'] for file in files if file['name'] not in self.rejected}


class Embeddings:
    fingerprint = 'test-encoder'
    dimensions = 3

    def __init__(self):
        self.documents = []

    def embed(self, texts, *args, query=False):
        rows = []
        for text in texts:
            if '\n' in text:
                self.documents.append(text)
            lower = text.lower()
            if 'lowsim' in lower:
                rows.append([.5, .866, 0.])  # Related, but far less similar than a direct match.
            elif any(word in lower for word in ('car', 'automobile', 'brakes')):
                rows.append([1., 0., 0.])
            else:
                rows.append([0., 0., 1.] if 'pasta' in lower else [0., 1., 0.])
        return np.asarray(rows, dtype=np.float32)


class Vision:
    available = True
    fingerprint = 'vision-test'

    def __init__(self):
        self.calls, self.shutdowns, self.error = [], 0, None

    def describe(self, path, kind, status, cancelled):
        self.calls.append(Path(path).name)
        if self.error:
            raise self.error
        return f'Image: a red automobile in a car repair garage ({kind})'

    def shutdown(self):
        self.shutdowns += 1


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
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT path FROM search_files').fetchall(), [('b.txt',)])

    def test_filters_and_no_matches_have_real_counts(self):
        self.rewriter.filters['extensions'] = ['.pdf']
        payload = self.search.search(self.root, 'car repair PDFs')
        self.assertEqual(payload['results'], [])
        self.assertEqual(payload['matched_files'], 0)
        self.assertTrue(payload['answer'].startswith('No close matches'))
        # Exclusions apply to names: a.txt only mentions brakes in its text, so it stays.
        (self.root/'brakes_manual.txt').write_text('car brakes repair manual')
        self.rewriter.filters['extensions'] = ['.txt']
        self.rewriter.filters['exclude_terms'] = ['brakes']
        payload = self.search.search(self.root, 'car repair excluding brakes')
        self.assertEqual([row['name'] for row in payload['results']], ['a.txt'])
        self.assertIn('excluding brakes', payload['answer'])

    def test_metadata_only_requests_filter_and_sort_without_a_topic(self):
        (self.root/'big.bin').write_bytes(b'x' * 3 * 1024 * 1024)
        (self.root/'bigger.bin').write_bytes(b'x' * 5 * 1024 * 1024)
        self.rewriter.topic = ''
        self.rewriter.filters.update(min_size_mb=2., sort='largest')
        payload = self.search.search(self.root, 'files over 2 MB, biggest first')
        self.assertEqual([row['name'] for row in payload['results']], ['bigger.bin', 'big.bin'])
        self.assertGreater(payload['results'][0]['score'], payload['results'][1]['score'])
        self.assertIn('larger than 2 MB; largest first', payload['answer'])

    def test_names_are_split_and_abbreviations_spelled_out_for_keywords(self):
        self.assertEqual(readable('CMPSC473_PracticeMidterm'), 'CMPSC 473 Practice Midterm')
        self.assertEqual(readable('hw7soln'), 'hw 7 soln / homework 7 solution')
        self.assertTrue(keyword_pattern('homework 2').search(readable('HW2_final').casefold()))
        self.assertFalse(keyword_pattern('homework 2').search('homework 21'))
        self.assertTrue(keyword_pattern('stats').search('math stat 415'))
        self.assertTrue(keyword_pattern('problem sets').search('problem_set_2'))
        self.assertFalse(keyword_pattern('hw').search('show notes'))
        self.assertTrue(keyword_pattern('promo').search('a promotional banner'))
        self.assertFalse(keyword_pattern('bill').search('billing address'))
        self.assertTrue(keyword_pattern('homework 2').search(readable('HW02').casefold()))
        self.assertEqual(identifiers(['stat 415', 'homework 2', 'last 3', '2025', '1099', 'w 2', 'lab']),
                         ['stat 415', 'homework 2', '1099', 'w 2'])
        (self.root/'HW6.txt').write_text('problem 1')
        self.rewriter.topic = 'homework'
        self.rewriter.filters['keywords'] = ['homework']
        payload = self.search.search(self.root, 'homework')
        self.assertEqual([row['name'] for row in payload['results']], ['HW6.txt'])

    def test_total_count_is_distinct_from_ten_displayed_results(self):
        for i in range(11):
            (self.root/f'item_{i}.txt').write_text('car repair')
        payload = self.search.search(self.root, 'car repair')
        self.assertEqual(len(self.rewriter.reviews[-1]), 10)
        self.assertEqual((payload['matched_files'], len(payload['results'])), (10, 10))
        self.assertIn('Found 10 matching files. Checked the top 10 of 12 possible matches.', payload['answer'])
        # Filter-only requests are not reviewed and report every match.
        self.rewriter.topic = ''
        self.rewriter.filters['extensions'] = ['.txt']
        payload = self.search.search(self.root, 'txt files')
        self.assertEqual((payload['matched_files'], len(payload['results'])), (13, 10))
        self.assertIn('Found 13 matching files (TXT files). Showing the top 10.', payload['answer'])

    def test_the_review_makes_the_final_call_and_failures_keep_the_matches(self):
        (self.root/'manual.txt').write_text('car repair manual')
        self.rewriter.rejected = {'a.txt'}
        payload = self.search.search(self.root, 'car repair')
        self.assertEqual(sorted(self.rewriter.reviews[-1]), ['a.txt', 'manual.txt'])
        self.assertEqual([row['name'] for row in payload['results']], ['manual.txt'])
        self.assertIn('Found 1 matching file.', payload['answer'])
        self.rewriter.rejected = {'a.txt', 'manual.txt'}
        payload = self.search.search(self.root, 'car repair')
        self.assertEqual(payload['results'], [])
        self.assertTrue(payload['answer'].startswith('No close matches'))
        self.rewriter.review_error = LLMServerError('engine stopped')
        with self.assertLogs('app.core.search', level='WARNING'):
            payload = self.search.search(self.root, 'car repair')
        self.assertEqual({row['name'] for row in payload['results']}, {'a.txt', 'manual.txt'})

    def test_requests_with_only_filters_skip_the_review(self):
        self.rewriter.topic = ''
        self.rewriter.filters['extensions'] = ['.txt']
        payload = self.search.search(self.root, 'txt files')
        self.assertEqual(len(payload['results']), 2)
        self.assertEqual(self.rewriter.reviews, [])

    def test_a_file_named_for_every_typed_word_is_never_cut_as_weak(self):
        (self.root/'car_guide.txt').write_text('car repair guide')
        (self.root/'car_scan.txt').write_text('lowsim')
        (self.root/'other.txt').write_text('car lowsim')
        self.rewriter.topic = 'car'
        payload = self.search.search(self.root, 'car')
        names = {row['name'] for row in payload['results']}
        self.assertIn('car_scan.txt', names)
        self.assertNotIn('other.txt', names)

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
            (self.root/'local:notes.txt').write_text('car repair local filename')
        payload = self.search.search(self.root, 'car repair')
        paths = {row['id'] for row in payload['results']}
        self.assertIn('Project/code.txt', paths)
        self.assertFalse(any('node_modules' in path or path.startswith('.') or 'linked' in path for path in paths))
        if os.name != 'nt':
            self.assertIn('local:notes.txt', paths)

    def test_a_numbered_name_in_the_request_keeps_only_files_that_carry_it(self):
        (self.root/'homework_a.txt').write_text('STAT 415 problems')
        (self.root/'homework_b.txt').write_text('MATH 312 problems')
        self.rewriter.topic = 'stat 415 homework'
        payload = self.search.search(self.root, 'stat 415 homework')
        self.assertEqual([row['name'] for row in payload['results']], ['homework_a.txt'])
        # When no file carries the name, nothing is gated away.
        self.rewriter.topic = 'stat 999 homework'
        payload = self.search.search(self.root, 'stat 999 homework')
        self.assertEqual({row['name'] for row in payload['results']}, {'homework_a.txt', 'homework_b.txt'})

    def test_matches_far_below_the_best_one_are_dropped(self):
        (self.root/'pasta.txt').write_text('pasta')
        (self.root/'dinner.txt').write_text('tomatoes and pasta')
        self.rewriter.topic = 'pasta'
        payload = self.search.search(self.root, 'pasta')
        self.assertEqual([row['name'] for row in payload['results']], ['pasta.txt'])

    def test_files_without_text_need_a_topic_word_in_their_name_or_folder(self):
        (self.root/'Carpool').mkdir()
        (self.root/'Carpool/IMG_1.jpg').write_bytes(b'\xff\xd8\xff')
        (self.root/'Carpool/repair_photo.jpg').write_bytes(b'\xff\xd8\xff')
        payload = self.search.search(self.root, 'automobile repair')
        names = {row['name'] for row in payload['results']}
        self.assertIn('repair_photo.jpg', names)
        self.assertNotIn('IMG_1.jpg', names)

    def test_exclusions_match_abbreviated_names(self):
        (self.root/'HW2.txt').write_text('problem set')
        (self.root/'HW2_soln.txt').write_text('problem set')
        self.rewriter.topic = 'homework'
        self.rewriter.filters['exclude_terms'] = ['solution']
        payload = self.search.search(self.root, 'homework, not the solutions')
        self.assertEqual([row['name'] for row in payload['results']], ['HW2.txt'])

    def test_keywords_without_a_query_still_name_the_subject(self):
        self.rewriter.topic = ''
        self.rewriter.filters.update(extensions=['.txt'], keywords=['pasta'])
        payload = self.search.search(self.root, 'pasta txt files')
        self.assertEqual([row['name'] for row in payload['results']], ['b.txt'])

    def test_files_beside_a_match_in_a_folder_named_for_the_request_are_reviewed(self):
        (self.root/'Repair').mkdir()
        (self.root/'Repair/brakes.txt').write_text('car brakes replaced')
        (self.root/'Repair/receipt.txt').write_text('oil change receipt')
        (self.root/'receipt.txt').write_text('oil change receipt')
        payload = self.search.search(self.root, 'car repair')
        paths = {row['id'] for row in payload['results']}
        self.assertTrue({'Repair/brakes.txt', 'Repair/receipt.txt'} <= paths)
        self.assertNotIn('receipt.txt', paths)
        # A numbered request wants one item, not the rest of its folder.
        payload = self.search.search(self.root, 'car repair 473')
        self.assertNotIn('Repair/receipt.txt', {row['id'] for row in payload['results']})

    def test_photos_are_found_by_what_they_show_and_described_once(self):
        (self.root/'IMG_0001.jpg').write_bytes(b'\xff\xd8\xff photo')
        payload = self.search.search(self.root, 'car repair')
        self.assertNotIn('IMG_0001.jpg', {row['name'] for row in payload['results']})
        vision = self.search.vision = Vision()
        payload = self.search.search(self.root, 'car repair')
        self.assertIn('IMG_0001.jpg', {row['name'] for row in payload['results']})
        self.assertEqual((vision.calls, vision.shutdowns), (['IMG_0001.jpg'], 1))
        self.search.search(self.root, 'car repair')
        self.assertEqual(vision.calls, ['IMG_0001.jpg'])  # Cached: never described twice.
        # Without the vision model the stored description is still used.
        vision.available = False
        payload = self.search.search(self.root, 'car repair')
        self.assertIn('IMG_0001.jpg', {row['name'] for row in payload['results']})

    def test_a_failing_vision_model_still_indexes_and_retries_later(self):
        (self.root/'IMG_0001.jpg').write_bytes(b'\xff\xd8\xff photo')
        vision = self.search.vision = Vision()
        vision.error = LLMServerError('vision engine stopped')
        with self.assertLogs('app.core.search', level='WARNING'):
            payload = self.search.search(self.root, 'car repair')
        self.assertEqual({row['name'] for row in payload['results']}, {'a.txt'})
        self.assertEqual(vision.shutdowns, 1)
        vision.error = None
        payload = self.search.search(self.root, 'car repair')
        self.assertIn('IMG_0001.jpg', {row['name'] for row in payload['results']})

    def test_unavailable_llm_and_cancellation_never_become_fake_empty_results(self):
        with self.assertRaisesRegex(ValueError, '12,000'):
            self.search.search(self.root, 'a'*12001)
        with self.assertRaises(InterruptedError):
            self.search.search(self.root, 'car repair', cancelled=lambda: True)
        self.assertFalse(self.database.exists())
        self.rewriter.available = False
        with self.assertRaises(LLMServerError):
            self.search.search(self.root, 'car repair')


class ScriptedServer:
    is_running = True

    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat(self, *args, **kwargs):
        self.calls.append(kwargs)
        return self.response

    def stop(self):
        pass


class SearchPlanTests(unittest.TestCase):
    PLAN = {'query': 'resume', 'keywords': ['Resume', 'cv', 'resume'], 'kinds': ['document', 'image'],
            'extensions': ['pdf', '*.exe'], 'exclude_extensions': ['.zip'], 'exclude_terms': ['draft'],
            'min_size_mb': 5, 'max_size_mb': -1, 'modified_after': '2026-10-25', 'modified_before': 'soon',
            'sort': 'newest'}

    def plan(self, request, **changes):
        manager = SimpleNamespace(runtime_settings={'max_tokens': 64, 'temperature': .2},
                                  search_settings={'max_tokens': 64, 'temperature': 0})
        runtime = AIRuntime(SimpleNamespace(is_available=True), manager)
        runtime._server = ScriptedServer(__import__('json').dumps({**self.PLAN, **changes}))
        return runtime.rewrite_query(request)

    def test_filters_the_request_never_asked_for_are_dropped(self):
        plan = self.plan('my resume')
        self.assertEqual(plan['keywords'], ['resume', 'cv'])
        self.assertEqual((plan['kinds'], plan['extensions']), ([], []))
        self.assertEqual((plan['exclude_terms'], plan['exclude_extensions']), ([], []))
        self.assertEqual((plan['min_size_mb'], plan['modified_after'], plan['sort']), (0, '', 'relevance'))

    def test_filters_the_request_names_are_kept_and_validated(self):
        plan = self.plan('newest resume pdf documents over 5 MB since last week, no drafts or zips')
        self.assertEqual((plan['kinds'], plan['extensions']), (['document'], ['.pdf']))
        self.assertEqual((plan['exclude_terms'], plan['exclude_extensions']), (['draft'], ['.zip']))
        self.assertEqual((plan['min_size_mb'], plan['max_size_mb']), (5., 0.))
        self.assertEqual((plan['modified_after'], plan['modified_before']), ('2026-10-25', ''))
        self.assertEqual(plan['sort'], 'newest')

    def test_review_can_only_name_candidates_and_keeps_the_listed_ones(self):
        manager = SimpleNamespace(runtime_settings={'max_tokens': 64, 'temperature': .2},
                                  search_settings={'max_tokens': 64, 'temperature': 0})
        runtime = AIRuntime(SimpleNamespace(is_available=True), manager)
        files = [{'key': f'dir/{name}', 'name': name, 'folder': 'dir', 'kind': 'document', 'excerpt': 'text'}
                 for name in ('a.pdf', 'b.pdf', 'c.pdf')]
        runtime._server = server = ScriptedServer('[1,3]')
        self.assertEqual(runtime.review_results('reports', files), {'dir/a.pdf', 'dir/c.pdf'})
        self.assertEqual(server.calls[0]['grammar'], 'root ::= "[" ( id ( "," id )* )? "]"\nid ::= "1" | "2" | "3"')
        runtime._server = ScriptedServer('[]')
        self.assertEqual(runtime.review_results('reports', files), set())
        self.assertEqual(runtime.review_results('reports', []), set())

    def test_images_are_sent_as_image_parts_and_short_answers_may_be_truncated(self):
        server = LLMServer(None, None, {'top_p': .8, 'top_k': 20, 'min_p': 0, 'seed': 1})
        server._process = SimpleNamespace(poll=lambda: None)
        sent = []
        server._request = lambda endpoint, payload: sent.append(payload) or {
            'choices': [{'finish_reason': 'length', 'message': {'content': 'a red car'}}]}
        self.assertEqual(server.chat('describe', 'Describe it.', 8, 0, images=[b'jpeg'], allow_truncated=True),
                         'a red car')
        text, image = sent[0]['messages'][1]['content']
        self.assertEqual(text, {'type': 'text', 'text': 'Describe it.'})
        self.assertEqual(image['image_url']['url'], 'data:image/jpeg;base64,anBlZw==')
        with self.assertRaises(LLMServerError):
            server.chat('describe', 'Describe it.', 8, 0)

    def test_only_the_text_model_uses_speculative_decoding(self):
        settings = {'context_tokens': 8192, 'threads': 4, 'speculative': 'draft-mtp', 'pooling': 'mean'}
        text = LLMServer(None, 'q.gguf', settings)._command(Path('llama-server'), 99)
        vision = LLMServer(None, 'vl.gguf', settings, mmproj='mm.gguf')._command(Path('llama-server'), 99)
        embedding = LLMServer(None, 'e.gguf', settings, embedding=True)._command(Path('llama-server'), 0)
        self.assertEqual(text[-2:], ['--spec-type', 'draft-mtp'])
        self.assertNotIn('--spec-type', vision + embedding)

    def test_vision_unloads_the_search_model_and_labels_descriptions(self):
        events = []
        runtime = SimpleNamespace(shutdown=lambda: events.append('search model unloaded'))
        manager = SimpleNamespace(vision_downloaded=True, vision_model_path='vl.gguf', vision_mmproj_path='mmproj.gguf',
                                  runtime_settings={'context_tokens': 8192})
        vision = VisionRuntime(SimpleNamespace(is_available=True), manager, runtime)
        with tempfile.TemporaryDirectory() as directory, mock.patch('app.ai.vision.LLMServer') as server:
            photo, broken = Path(directory)/'photo.png', Path(directory)/'broken.jpg'
            Image.new('RGB', (8, 8)).save(photo)
            broken.write_bytes(b'x')
            server.return_value.start.side_effect = lambda *args: events.append('vision model started')
            server.return_value.chat.return_value = ' a red\ncar '
            self.assertEqual(vision.describe(photo, 'image'), 'Image: a red car')
            self.assertEqual(vision.describe(broken, 'image'), '')
        self.assertEqual(events, ['search model unloaded', 'vision model started'])
        self.assertEqual(server.call_args.kwargs['mmproj'], 'mmproj.gguf')
        self.assertEqual(server.call_args.args[2]['context_tokens'], 4096)

    def test_omitted_filters_default_to_none(self):
        manager = SimpleNamespace(runtime_settings={'max_tokens': 64, 'temperature': .2},
                                  search_settings={'max_tokens': 64, 'temperature': 0})
        runtime = AIRuntime(SimpleNamespace(is_available=True), manager)
        runtime._server = ScriptedServer('{"query":"resume","keywords":["resume","cv"]}')
        plan = runtime.rewrite_query('my newest resume')
        self.assertEqual((plan['query'], plan['keywords']), ('resume', ['resume', 'cv']))
        self.assertEqual((plan['kinds'], plan['extensions'], plan['exclude_terms']), ([], [], []))
        self.assertEqual((plan['min_size_mb'], plan['modified_after'], plan['sort']), (0., '', 'relevance'))

    def test_a_wanted_word_is_never_also_excluded(self):
        plan = self.plan('homework solutions, not drafts', query='homework solutions',
                         keywords=['homework', 'solution'], exclude_terms=['solution', 'draft'])
        self.assertEqual(plan['exclude_terms'], ['draft'])


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
        context = SimpleNamespace(config=config, search=search, shutdown=lambda: None, cancel_ai=lambda: None,
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
        self.assertEqual(window.search_panel.response.text(),
                         "Search failed: Couldn't complete this request. Please try again.")
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

    def test_model_failure_shows_retry_message_without_internal_details(self):
        def fail(*args):
            raise LLMServerError('Invalid JSON schema: hidden internal model decision')
        window = self.window(SimpleNamespace(search=fail))
        with self.assertLogs('app.ui.worker', level='ERROR') as logs:
            window._search('car repair')
            self.finish(window)
        message = window.search_panel.response.text()
        self.assertIn('Please try again', message)
        self.assertNotIn('schema', message)
        self.assertNotIn('hidden internal', message)
        self.assertIn('hidden internal model decision', '\n'.join(logs.output))
        self.assertEqual(window.search_panel.graph._history, [])


if __name__ == '__main__':
    unittest.main()
