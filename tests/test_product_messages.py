"""Internal model diagnostics remain available without entering the product UI."""
import os
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QLabel

from app.ai.llm_server import LLMServerError
from app.ai.schema import OrganizationPlan, PlanAction
from app.core.file_kinds import type_label
from app.core.scanner import Scanner
from app.core.simulator import TreeNode
from app.ui.preview_panel import PreviewPanel, _tooltip
from app.ui.worker import Worker
from app.ui.search_panel import SearchPanel


class ProductMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_model_failure_keeps_diagnostics_in_logs_and_a_safe_message_for_ui(self):
        def fail(*args):
            raise LLMServerError('Invalid evidence catalog: unsupported ID 999')
        worker = Worker(fail)
        with self.assertLogs('app.ui.worker', level='ERROR') as logs:
            worker.run()
        self.assertIn('Please try again', worker.error)
        self.assertNotIn('catalog', worker.error)
        self.assertIn('unsupported ID 999', '\n'.join(logs.output))

    def test_actionable_file_failure_is_still_available_to_the_ui(self):
        def fail(*args):
            raise PermissionError('Folder is read-only')
        worker = Worker(fail)
        with self.assertLogs('app.ui.worker', level='ERROR'):
            worker.run()
        self.assertIn('Check its permissions', worker.error)

    def test_unexpected_failure_also_cannot_expose_raw_diagnostics(self):
        def fail(*args):
            raise ValueError('Internal JSON token mismatch: private diagnostic')
        worker = Worker(fail)
        with self.assertLogs('app.ui.worker', level='ERROR') as logs:
            worker.run()
        self.assertIn('Please try again', worker.error)
        self.assertNotIn('JSON', worker.error)
        self.assertIn('private diagnostic', '\n'.join(logs.output))

    def test_preview_does_not_expose_internal_reasons_or_review_holds(self):
        internal = 'Unsupported evidence quote\nEvidence: INTERNAL\nReview: ID mismatch'
        action = PlanAction('1', 'move', 'a.txt', 'Notes/a.txt', reason=internal)
        node = TreeNode('a.txt', False, 'Notes/a.txt', action=action)
        panel = PreviewPanel()
        self.addCleanup(panel.deleteLater)
        panel.plan = OrganizationPlan('/example', [action], review={'held': {'b.txt': internal}})
        panel._set_badge(1)
        panel._show_details(node)
        visible = '\n'.join(label.text() for label in panel.findChildren(QLabel)) + _tooltip(node, True)
        self.assertNotIn('INTERNAL', visible)
        self.assertNotIn('Review:', visible)
        self.assertNotIn('held by review', visible)
        self.assertNotIn('Unsupported evidence', visible)
        self.assertEqual(panel.badge.text(), '1 changes')
        self.assertEqual(action.reason, internal)
        self.assertIn('b.txt', panel.plan.review['held'])

    def test_file_types_are_specific(self):
        expected = {'report.final.pdf': 'PDF document', 'moneypuck.zip': 'ZIP archive',
                    'backup.tar.gz': 'TAR.GZ archive', 'IMG_2003.JPG': 'JPG image',
                    'Qwen.gguf': 'GGUF AI model', 'xubuntu.iso': 'ISO disk image', 'setup.exe': 'EXE installer',
                    'run.py': 'PY file', 'deck.pptx': 'PPTX presentation', 'ubuntu.torrent': 'TORRENT file',
                    'fastscan': 'File without extension', 'v1.2 notes': 'File without extension'}
        for name, label in expected.items():
            with self.subTest(name=name):
                self.assertEqual(type_label(name), label)
        self.assertEqual(type_label('fastscan', executable=True), 'Executable')
        self.assertEqual(type_label('report.pdf', executable=True), 'PDF document')
        self.assertEqual(type_label('Taxes.2026', is_dir=True), 'Folder')

    def test_details_show_the_specific_type_and_no_category(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'receipt.pdf').write_text('Receipt')
            (root / 'tool').write_text('#!/bin/sh\n')
            (root / 'tool').chmod(0o755)
            panel = PreviewPanel()
            self.addCleanup(panel.deleteLater)
            panel.set_plan(Scanner(root).scan(), OrganizationPlan(str(root), [
                PlanAction('1', 'move', 'receipt.pdf', 'Receipts/receipt.pdf', category='Misc')]))
            model = panel.models[1]
            shown = lambda: [label.text() for label in panel.findChildren(QLabel) if label.isVisibleTo(panel)]
            panel._show_details(model._find_node('Receipts/receipt.pdf'))
            self.assertIn('PDF document', shown())
            self.assertNotIn('Category', shown())
            self.assertNotIn('Misc', shown())
            panel._show_details(model._find_node('Receipts'))
            self.assertIn('Folder', shown())
            if sys.platform != 'win32':
                panel._show_details(model._find_node('tool'))
                self.assertIn('Executable', shown())

    def test_manual_edit_explanation_is_preserved(self):
        action = PlanAction('1', 'move', 'a.txt', 'Notes/a.txt', reason='Moved in preview')
        node = TreeNode('a.txt', False, 'Notes/a.txt', action=action)
        self.assertIn('Moved in preview', _tooltip(node, True))

    def test_failure_to_open_a_real_file_is_logged_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'note.txt'
            target.write_text('Example')
            panel = SearchPanel()
            self.addCleanup(panel.deleteLater)
            with patch('app.ui.search_panel.QDesktopServices.openUrl', return_value=False), \
                 patch('app.ui.search_panel.QMessageBox.warning') as warning, \
                 self.assertLogs('app.ui.search_panel', level='WARNING') as logs:
                panel._open_file(str(target))
            self.assertIn('could not open file', '\n'.join(logs.output))
            warning.assert_called_once()


if __name__ == '__main__':
    unittest.main()
