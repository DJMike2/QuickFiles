"""Independent review, stale-preview protection, verified moves and journal recovery boundaries."""
import errno
import json
import os
from contextlib import closing
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.ai.llm_server import ContextTooLargeError, IncompleteResponseError
from app.ai.runtime import AIRuntime
from app.ai.schema import OrganizationPlan, PlanAction
from app.core.executor import Executor, safe_move
from app.core.file_state import file_hash, identity
from app.core.organization import Profile
from app.core.reviewer import Reviewer
from app.core.undo import UndoEngine
from app.core.validator import Validator
from app.storage import Journal


class ReviewContracts(unittest.TestCase):
    def setUp(self):
        self.runtime = AIRuntime(None, SimpleNamespace(runtime_settings={}))
        self.files = [{'id': 17, 'evidence': 'Bread recipe with flour'},
                      {'id': 63, 'evidence': 'Soup recipe with lentils'}]
        self.destination = {'path': 'Recipes', 'existing': False}

    def response(self, **overrides):
        return json.dumps({'coherent': True, 'reuse_existing': '', 'decisions': {
            '1': {'verdict': 'keep', 'reason': 'A recipe', 'evidence': 'Bread recipe'},
            '2': {'verdict': 'keep', 'reason': 'A recipe', 'evidence': 'Soup recipe'}}, **overrides})

    def test_exact_local_keys_preserve_global_ids_and_require_real_quotes(self):
        with patch.object(self.runtime, '_generate', return_value=self.response()):
            r = self.runtime.review_destination(self.files, self.destination, [])
        self.assertEqual(set(r), {17, 63})
        bad = {'1': {'verdict': 'keep', 'reason': 'A recipe', 'evidence': 'Soup recipe'},
               '2': {'verdict': 'keep', 'reason': 'A recipe', 'evidence': 'Soup recipe'}}
        with patch.object(self.runtime, '_generate', return_value=self.response(decisions=bad)):
            r = self.runtime.review_destination(self.files, self.destination, [])
        self.assertEqual(r[17]['verdict'], 'hold')
        self.assertEqual(r[63]['verdict'], 'keep')

    def test_missing_ids_invalid_json_and_incoherent_groups_fail_closed(self):
        for raw in ['not JSON', self.response(decisions={}), self.response(coherent=False)]:
            with self.subTest(raw=raw), patch.object(self.runtime, '_generate', return_value=raw):
                r = self.runtime.review_destination(self.files, self.destination, [])
                self.assertTrue(all(d['verdict'] == 'hold' for d in r.values()))

    def test_transport_quoted_evidence_still_requires_an_exact_file_passage(self):
        decisions = {'1': {'verdict': 'keep', 'reason': 'A recipe', 'evidence': json.dumps('Bread recipe')},
                     '2': {'verdict': 'keep', 'reason': 'A recipe', 'evidence': json.dumps('Soup recipe')}}
        with patch.object(self.runtime, '_generate', return_value=self.response(decisions=decisions)):
            r = self.runtime.review_destination(self.files, self.destination, [])
        self.assertEqual(r[17]['verdict'], 'keep')
        self.assertEqual(r[17]['evidence'], 'Bread recipe')

    def test_unsupported_positive_review_gets_one_grounded_correction(self):
        bad = {'1': {'verdict': 'keep', 'reason': 'Recipe', 'evidence': 'Invented quote'},
               '2': {'verdict': 'keep', 'reason': 'Recipe', 'evidence': 'Soup recipe'}}
        with patch.object(self.runtime, '_generate', side_effect=[self.response(decisions=bad),self.response()]) as generate:
            result=self.runtime.review_destination(self.files,self.destination,[])
        self.assertEqual(generate.call_count,2)
        self.assertTrue(all(d['verdict']=='keep' for d in result.values()))
        with patch.object(self.runtime, '_generate', return_value=self.response(decisions=bad)) as generate:
            result=self.runtime.review_destination(self.files,self.destination,[])
        self.assertEqual(generate.call_count,2)
        self.assertEqual(result[17]['verdict'],'hold')

    def test_existing_equivalent_holds_redundant_new_folder(self):
        with patch.object(self.runtime, '_generate', return_value=self.response(reuse_existing='Cooking')):
            r = self.runtime.review_destination(self.files, self.destination, [{'path': 'Cooking'}])
        self.assertTrue(all(d['verdict'] == 'hold' for d in r.values()))

    def test_context_split_preserves_all_files_and_unfinished_review_holds(self):
        files = [*self.files, {'id': 92, 'evidence': 'Bread recipe with flour'},
                 {'id': 103, 'evidence': 'Soup recipe with lentils'}]
        requests = []
        def generate(system, user, *args, **kwargs):
            data = json.loads(user);requests.append(data)
            if len(requests)==1:raise ContextTooLargeError('large')
            return json.dumps({'coherent': True, 'reuse_existing': '', 'decisions': {
                str(f['id']): {'verdict': 'keep', 'reason': 'Recipe', 'evidence': f['evidence']}
                for f in data['files']}})
        with patch.object(self.runtime, '_generate', side_effect=generate):
            r = self.runtime.review_destination(files, self.destination, [])
        self.assertEqual(set(r), {17, 63, 92, 103})
        self.assertTrue(all(d['verdict']=='keep' for d in r.values()))
        with patch.object(self.runtime, '_generate', side_effect=IncompleteResponseError('unfinished')):
            r = self.runtime.review_destination(self.files, self.destination, [])
        self.assertTrue(all(d['verdict'] == 'hold' for d in r.values()))

    def test_cancellation_is_not_mistaken_for_a_completed_review(self):
        with patch.object(self.runtime, '_generate', side_effect=InterruptedError('stop')):
            with self.assertRaises(InterruptedError):
                self.runtime.review_destination(self.files, self.destination, [])


class DirectoryCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'files'
        self.root.mkdir()
        self.journal = Journal(self.base / 'journal.db')
        self.addCleanup(self.journal.close)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = path.stat() if path.exists() else None
        path.write_text(text)
        if previous:
            # Model a later edit without assuming the filesystem clock advanced.
            os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 2_000_000_000))
        return path

    def action(self, source, destination):
        return PlanAction(source, 'move', source, destination, category=str(PurePosixPath(destination).parent),
                          source_identity=identity((self.root/source).stat()))


class WholeReviewTests(DirectoryCase):
    def setup_review(self, folder='Recipes', existing=False):
        self.write('a.txt', 'Bread recipe with flour')
        self.write('b.txt', 'Soup recipe with lentils')
        if existing:
            (self.root/folder).mkdir(parents=True)
        p = Profile('Recipes', 'topic', [], 'Recipes', '')
        rows = [{'id': n, 'rel_path': name, 'kind': 'document', 'basis': 'content',
                 'evidence': (self.root/name).read_text(), 'profile': p}
                for n, name in enumerate(['a.txt', 'b.txt'], 1)]
        destinations = {folder: {'existing': existing, 'profile': p, 'families': {'document'}}}
        plan = OrganizationPlan(str(self.root), [self.action(n, folder+'/'+n) for n in ['a.txt', 'b.txt']])
        return plan, rows, destinations

    def test_rejecting_one_member_removes_new_singleton_but_preserves_existing(self):
        for existing in (False, True):
            plan, rows, dest = self.setup_review('Recipes'+str(existing), existing)
            runtime = SimpleNamespace(review_destination=lambda *args: {
                1: {'verdict': 'keep', 'reason': 'Clear recipe'},
                2: {'verdict': 'hold', 'reason': 'Not supported'}})
            Reviewer(runtime).review(plan, rows, dest)
            self.assertEqual(len(plan.actions), int(existing))
            self.assertIn('b.txt', plan.review['held'])
            self.assertEqual(plan.warnings, [])

    def test_generic_year_container_cannot_be_approved_by_the_model(self):
        plan, rows, dest = self.setup_review('archives/2026')
        runtime = SimpleNamespace(review_destination=lambda *a: self.fail('Generic topic bypassed deterministic review'))
        Reviewer(runtime).review(plan, rows, dest)
        self.assertEqual(plan.actions, [])
        self.assertEqual(len(plan.review['held']), 2)

    def test_explicit_identity_boundary_is_rechecked_even_for_direct_matches(self):
        plan, rows, dest = self.setup_review('STAT205', existing=True)
        dest['STAT205']['profile'] = Profile('STAT205 notes', 'course', ['STAT205'], 'Probability', '')
        runtime = SimpleNamespace(review_destination=lambda *a: self.fail('An explicit scope needs deterministic checks'))
        Reviewer(runtime).review(plan, rows, dest)
        self.assertEqual(plan.actions, [])
        self.assertTrue(all('identity check' in reason for reason in plan.review['held'].values()))

    def test_review_sees_evidence_without_assignment_rationale_and_rechecks_changes(self):
        plan, rows, dest = self.setup_review()
        for a in plan.actions:a.reason = 'ORIGINAL MODEL RATIONALE'
        def changed(files, destination, existing, *args):
            self.assertNotIn('ORIGINAL MODEL RATIONALE', json.dumps([files,destination,existing]))
            self.write('b.txt', 'Entirely changed subject')
            return {f['id']: {'verdict': 'keep', 'reason': 'Clear recipe'} for f in files}
        Reviewer(SimpleNamespace(review_destination=changed)).review(plan, rows, dest)
        self.assertEqual(plan.actions, [])
        self.assertIn('changed', plan.review['held']['b.txt'])
        self.assertTrue(any('changed' in w.message and 'scan again' in w.message for w in plan.warnings))

    def test_collision_rejection_cannot_leave_new_singleton(self):
        plan, rows, dest = self.setup_review()
        self.write('Recipes/a.txt', 'Occupied original')
        # The destination was NEW in the inventory, even if it appeared meanwhile.
        runtime = SimpleNamespace(review_destination=lambda *args: {
            n: {'verdict': 'keep', 'reason': 'Clear recipe'} for n in (1,2)})
        Reviewer(runtime, allow_suffix_on_collision=False).review(plan, rows, dest)
        self.assertEqual(plan.actions, [])
        self.assertEqual((self.root/'Recipes/a.txt').read_text(), 'Occupied original')


class ApplyVerificationTests(DirectoryCase):
    def test_move_failure_is_logged_and_preserves_the_original(self):
        self.write('a.txt', 'Original')
        with patch('app.core.executor.safe_move', side_effect=OSError('Disk write failed')), \
             self.assertLogs('app.core.executor', level='ERROR') as logs:
            result = Executor(self.root, self.journal).apply([self.action('a.txt', 'Subject/a.txt')])
        self.assertEqual(result.failed, 1)
        self.assertEqual((self.root/'a.txt').read_text(), 'Original')
        self.assertIn('Disk write failed', '\n'.join(logs.output))

    def test_collision_defaults_reject_and_legacy_suffix_requires_explicit_opt_in(self):
        self.write('a.txt','Original');self.write('Subject/a.txt','Occupied')
        plan=OrganizationPlan(str(self.root),[self.action('a.txt','Subject/a.txt')])
        self.assertEqual(len(Validator(self.root).validate_plan(plan).rejected_actions),1)
        report=Validator(self.root,allow_suffix_on_collision=True).validate_plan(plan)
        self.assertEqual(report.valid_actions[0].dst_rel,'Subject/a (1).txt')
        self.assertEqual((self.root/'Subject/a.txt').read_text(),'Occupied')

    def test_complete_apply_and_undo_verify_contents_and_persist_hashes(self):
        self.write('a.txt', 'Original A');self.write('b.txt', 'Original B')
        actions = [self.action(n, 'Subject/'+n) for n in ('a.txt','b.txt')]
        result = Executor(self.root, self.journal).apply(actions)
        self.assertEqual((result.succeeded,result.verified,result.failed), (2,2,0))
        batch = self.journal.get_latest_batch()
        moves = [op for op in batch.ops if op.op_type=='move']
        self.assertTrue(all(op.sha256 == file_hash(Path(op.dst_abs)) for op in moves))
        undone = UndoEngine(self.root,self.journal).undo_batch(batch.id)
        self.assertFalse(undone.errors)
        self.assertEqual((self.root/'a.txt').read_text(),'Original A')
        self.assertFalse((self.root/'Subject').exists())

    def test_changed_preview_aborts_whole_batch_before_any_write(self):
        a=self.write('a.txt','Original A');self.write('b.txt','Original B')
        actions=[self.action(n,'Subject/'+n) for n in ('a.txt','b.txt')]
        a.write_text('Changed after preview')
        result=Executor(self.root,self.journal).apply(actions)
        self.assertEqual((result.succeeded,result.failed,result.skipped),(0,1,1))
        self.assertFalse((self.root/'Subject').exists())
        self.assertIsNone(self.journal.get_latest_batch())
        self.assertTrue((self.root/'b.txt').exists())

    def test_source_replacement_and_destination_collision_preserve_all_inputs(self):
        a=self.write('a.txt','Original A');action=self.action('a.txt','Subject/a.txt')
        # Keep the old inode allocated so a fast recreate cannot reuse it in CI.
        a.rename(self.root/'retired-a.txt');a.write_text('Original A')
        self.assertNotEqual(identity(a.stat()), action.source_identity)
        result=Executor(self.root,self.journal).apply([action])
        self.assertEqual(result.failed,1)
        action=self.action('a.txt','Subject/a.txt');self.write('Subject/a.txt','Occupied')
        result=Executor(self.root,self.journal).apply([action])
        self.assertEqual(result.failed,1)
        self.assertEqual(a.read_text(),'Original A')
        self.assertEqual((self.root/'Subject/a.txt').read_text(),'Occupied')

    def test_new_application_marker_blocks_apply_after_preview(self):
        self.write('a.txt','Original');(self.root/'Subject').mkdir()
        action=self.action('a.txt','Subject/a.txt')
        self.write('Subject/SupportPackages/info.txt','Application data')
        result=Executor(self.root,self.journal).apply([action])
        self.assertEqual(result.failed,1)
        self.assertTrue((self.root/'a.txt').exists())

    def test_solution_projects_and_installer_prerequisites_share_scanner_protection(self):
        self.write('a.txt','Original')
        for folder in ('Solution', 'ISSetupPrerequisites'):
            (self.root/folder).mkdir()
        self.write('Solution/App.csproj','IDE project')
        for folder in ('Solution', 'ISSetupPrerequisites'):
            with self.subTest(folder=folder):
                result=Executor(self.root,self.journal).apply([self.action('a.txt',folder+'/a.txt')])
                self.assertEqual(result.failed,1)
                self.assertTrue((self.root/'a.txt').exists())

    def test_undo_preserves_a_destination_that_became_an_application_project(self):
        self.write('a.txt','Original')
        result=Executor(self.root,self.journal).apply([self.action('a.txt','Subject/a.txt')])
        self.write('Subject/App.csproj','New project')
        undone=UndoEngine(self.root,self.journal).undo_batch(result.batch_id)
        self.assertTrue(undone.errors)
        self.assertEqual((self.root/'Subject/a.txt').read_text(),'Original')

    def test_duplicate_sources_in_approved_map_abort_before_mutation(self):
        self.write('a.txt','Original')
        actions=[self.action('a.txt','First/a.txt'),self.action('a.txt','Second/a.txt')]
        result=Executor(self.root,self.journal).apply(actions)
        self.assertEqual(result.succeeded,0)
        self.assertFalse((self.root/'First').exists())

    def test_cancellation_during_pre_move_hash_preserves_source(self):
        self.write('a.txt','Original');action=self.action('a.txt','Subject/a.txt')
        with patch('app.core.executor.file_hash',side_effect=InterruptedError('stop')):
            result=Executor(self.root,self.journal).apply([action])
        self.assertEqual((result.succeeded,result.failed,result.skipped),(0,0,1))
        self.assertTrue((self.root/'a.txt').exists())
        self.assertFalse((self.root/'Subject').exists())

    def test_cancellation_after_move_finishes_verification_then_stops(self):
        self.write('a.txt','A');self.write('b.txt','B');cancelled=False
        def move_and_cancel(source,destination):
            nonlocal cancelled
            safe_move(source,destination);cancelled=True
        with patch('app.core.executor.safe_move',side_effect=move_and_cancel):
            result=Executor(self.root,self.journal,cancelled=lambda:cancelled).apply([
                self.action(n,'Subject/'+n) for n in ('a.txt','b.txt')])
        self.assertEqual((result.succeeded,result.verified,result.skipped),(1,1,1))
        self.assertTrue((self.root/'b.txt').exists())
        undone=UndoEngine(self.root,self.journal).undo_batch(result.batch_id)
        self.assertFalse(undone.errors)

    def test_failed_post_move_verification_stays_in_journal_and_undo_preserves_changes(self):
        self.write('a.txt','Original');self.write('b.txt','Untouched')
        actions=[self.action(n,'Subject/'+n) for n in ('a.txt','b.txt')]
        def corrupt_move(source,destination):
            safe_move(source,destination);destination.write_text('Changed at destination')
        with patch('app.core.executor.safe_move',side_effect=corrupt_move):
            result=Executor(self.root,self.journal).apply(actions)
        self.assertEqual((result.succeeded,result.verified,result.failed),(0,0,1))
        self.assertEqual(result.skipped,1)
        self.assertEqual((self.root/'b.txt').read_text(),'Untouched')
        batch=self.journal.get_latest_batch();move=batch.ops[-1]
        self.assertTrue(move.succeeded)
        self.assertIn('verification failed',move.error_msg)
        undone=UndoEngine(self.root,self.journal).undo_batch(batch.id)
        self.assertTrue(undone.errors)
        self.assertEqual((self.root/'Subject/a.txt').read_text(),'Changed at destination')

    def test_undo_refuses_overwriting_an_original_location(self):
        self.write('a.txt','Original');result=Executor(self.root,self.journal).apply([self.action('a.txt','Subject/a.txt')])
        self.write('a.txt','New file')
        undone=UndoEngine(self.root,self.journal).undo_batch(result.batch_id)
        self.assertTrue(undone.errors)
        self.assertEqual((self.root/'a.txt').read_text(),'New file')
        self.assertEqual((self.root/'Subject/a.txt').read_text(),'Original')

    def test_cross_volume_copy_verifies_and_cleans_up_failed_copy(self):
        source=self.write('a.txt','Original');dest=self.root/'b.txt'
        with patch('app.core.executor.os.link',side_effect=OSError(errno.EXDEV,'cross volume')), \
             patch.object(Path,'rename',side_effect=OSError(errno.EXDEV,'cross volume')):
            safe_move(source,dest)
        self.assertEqual(dest.read_text(),'Original');self.assertFalse(source.exists())
        source.write_text('Another original')
        with patch('app.core.executor.os.link',side_effect=OSError(errno.EXDEV,'cross volume')), \
             patch.object(Path,'rename',side_effect=OSError(errno.EXDEV,'cross volume')), \
             patch('app.core.executor.shutil.copyfileobj',side_effect=OSError('copy failed')):
            with self.assertRaises(OSError):safe_move(source,self.root/'c.txt')
        self.assertEqual(source.read_text(),'Another original');self.assertFalse((self.root/'c.txt').exists())

    def test_old_journal_gets_hash_column_without_losing_operations(self):
        self.journal.close()
        # A minimal historical schema without the added checksum column.
        old=self.base/'old.db'
        with closing(sqlite3.connect(old)) as c:
            c.executescript('''CREATE TABLE journal_batch(id INTEGER PRIMARY KEY,batch_uuid TEXT UNIQUE NOT NULL,
            root TEXT NOT NULL,applied_at REAL NOT NULL,action_count INTEGER DEFAULT 0,undone INTEGER DEFAULT 0);
            CREATE TABLE journal_op(id INTEGER PRIMARY KEY,batch_id INTEGER NOT NULL,op_order INTEGER NOT NULL,
            op_type TEXT NOT NULL,src_abs TEXT NOT NULL,dst_abs TEXT NOT NULL,src_existed INTEGER DEFAULT 1,
            dst_existed_pre INTEGER DEFAULT 0,succeeded INTEGER DEFAULT 0,error_msg TEXT DEFAULT '',undone INTEGER DEFAULT 0);''')
            c.execute('INSERT INTO journal_batch VALUES(1,?,?,0,1,0)',('historical',str(self.root)))
            c.execute("INSERT INTO journal_op(batch_id,op_order,op_type,src_abs,dst_abs,succeeded) VALUES(1,0,'move',?,?,1)",
                      (str(self.root/'a.txt'),str(self.root/'Subject/a.txt')))
            c.commit()
        migrated=Journal(old);self.addCleanup(migrated.close)
        columns={r[1] for r in migrated.connection().execute('PRAGMA table_info(journal_op)')}
        self.assertIn('sha256',columns)
        historical=migrated.get_batch_by_id(1)
        self.assertEqual(len(historical.ops),1)
        self.assertEqual(historical.ops[0].sha256,'')
        self.assertTrue(historical.ops[0].succeeded)


class ApprovalAndPreviewTests(DirectoryCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_arrival_watcher_rejects_a_source_changed_during_assignment(self):
        from app.config import AppConfig
        from app.ui.main_window import MainWindow
        source=self.write('new.txt','Original subject');(self.root/'Notes').mkdir()
        def assign(*args,**kwargs):
            source.write_text('Changed during model inference')
            return {'new.txt':'Notes'}
        runtime=SimpleNamespace(available=True,backend_label='Test AI',assign_files=assign,shutdown=lambda:None)
        window=MainWindow(SimpleNamespace(config=AppConfig(self.base/'config'),runtime=runtime,journal=self.journal))
        self.addCleanup(window.deleteLater)
        window._index.folders={'Notes':'Existing notes'}
        with patch.object(window,'_start') as start:
            window._ask_model(self.root,[source])
        kind,operation=start.call_args.args
        self.assertEqual(kind,'watch')
        moved=operation(lambda *a:None,lambda *a:None,lambda:False)
        self.assertEqual(moved,[])
        self.assertTrue(source.exists())
        self.assertFalse((self.root/'Notes/new.txt').exists())

    def test_cancelled_confirmation_does_nothing_and_approved_map_uses_verified_executor(self):
        from app.config import AppConfig
        from app.ui.main_window import MainWindow
        from PySide6.QtWidgets import QMessageBox
        runtime=SimpleNamespace(available=True,backend_label='Test AI')
        window=MainWindow(SimpleNamespace(config=AppConfig(self.base/'config'),runtime=runtime,journal=self.journal))
        self.addCleanup(window.deleteLater)
        self.write('a.txt','Original');window.plan=OrganizationPlan(str(self.root),[self.action('a.txt','Subject/a.txt')])
        with patch.object(window,'_start') as start, patch.object(QMessageBox,'question',return_value=QMessageBox.Cancel):
            window._apply()
            start.assert_not_called()
        self.assertTrue((self.root/'a.txt').exists())
        with patch.object(window,'_start') as start, patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            window._apply()
        kind,operation=start.call_args.args
        self.assertEqual(kind,'apply')
        result=operation(lambda *args:None,lambda *args:None,lambda:False)
        self.assertEqual((result.verified,result.failed),(1,0))
        self.assertEqual((self.root/'Subject/a.txt').read_text(),'Original')
        self.assertFalse(UndoEngine(self.root,self.journal).undo_batch(result.batch_id).errors)

    def test_preview_edits_keep_source_snapshot_and_mark_user_provenance(self):
        from app.core.scanner import Scanner
        from app.ui.preview_panel import PreviewPanel
        self.write('a.txt','Original A');self.write('b.txt','Original B')
        panel=PreviewPanel();self.addCleanup(panel.deleteLater)
        plan=OrganizationPlan(str(self.root),[PlanAction('1','move','a.txt','Subject/a.txt')])
        panel.set_plan(Scanner(self.root).scan(),plan)
        snapshot=plan.actions[0].source_identity
        self.assertEqual(snapshot,identity((self.root/'a.txt').stat()))
        actions=[PlanAction('1','move','a.txt','Edited/a.txt',source_identity=snapshot),
                 PlanAction('2','move','b.txt','Edited/b.txt')]
        self.assertTrue(panel._commit(actions))
        self.app.processEvents()
        self.assertTrue(panel.plan.review['user_edited'])
        self.assertEqual(panel.plan.actions[0].source_identity,snapshot)
        self.assertIsNotNone(panel.plan.actions[1].source_identity)
        self.write('b.txt','Changed after edit')
        result=Executor(self.root,self.journal).apply(panel.plan.actions)
        self.assertEqual(result.succeeded,0)
        self.assertFalse((self.root/'Edited').exists())

    def _new_folder_panel(self,actions):
        from app.core.scanner import Scanner
        from app.ui.preview_panel import PreviewPanel
        panel=PreviewPanel();self.addCleanup(panel.deleteLater)
        panel.set_plan(Scanner(self.root).scan(),OrganizationPlan(str(self.root),actions))
        rejected=[];panel.editRejected.connect(rejected.append)
        return panel,rejected

    def test_right_click_offers_new_folder_for_files_only(self):
        self.write('a.txt','Receipt');(self.root/'Old').mkdir();self.write('Old/b.txt','Kept')
        panel,_=self._new_folder_panel([])
        model=panel.models[1]
        menu=panel._file_menu(model.index_of(model._find_node('a.txt')))
        self.addCleanup(menu.deleteLater)
        self.assertEqual([a.text() for a in menu.actions()],['Move to new folder…'])
        self.assertIsNone(panel._file_menu(model.index_of(model._find_node('Old'))))
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QContextMenuEvent
        tree=panel.trees[1];tree.resize(400,300);tree.expandAll()
        file_index=model.index_of(model._find_node('a.txt'));tree.setCurrentIndex(file_index)
        shown=[]
        def menu_for(index):
            menu=panel._file_menu(index)
            if menu is None:
                return None
            menu.deleteLater()  # A real menu would wait for a click; record where it opened instead.
            return SimpleNamespace(exec=lambda pos:shown.append(index.data(Qt.UserRole).rel_path),deleteLater=lambda:None)
        tree.file_menu=menu_for
        # The menu key uses the selected row; a click on empty space shows no file menu.
        tree.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Keyboard,QPoint(1,1)))
        tree.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse,tree.visualRect(file_index).center()))
        tree.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse,QPoint(5,290)))
        self.assertEqual(shown,['a.txt','a.txt'])
        self.assertIsNone(panel.trees[0].file_menu)

    def test_rename_selects_a_files_name_without_its_extension(self):
        for name in ['report.final.pdf','notes']:
            self.write(name,'Text')
        (self.root/'Old.Projects').mkdir();self.write('Old.Projects/b.txt','Kept')
        from PySide6.QtWidgets import QAbstractItemDelegate
        panel,_=self._new_folder_panel([])
        panel.show();self.app.processEvents()
        model,tree=panel.models[1],panel.trees[1]
        expected={'report.final.pdf':'report.final','notes':'notes','Old.Projects':'Old.Projects'}
        for path,selected in expected.items():
            with self.subTest(path=path):
                # Open the editor the way a double-click does; the view itself selects all text first.
                index=model.index_of(model._find_node(path))
                tree.setCurrentIndex(index);tree.edit(index);self.app.processEvents()
                editor=tree.indexWidget(index)
                self.assertEqual(editor.text(),path)
                self.assertEqual(editor.selectedText(),selected)
                tree.closeEditor(editor,QAbstractItemDelegate.EndEditHint.RevertModelCache)
                self.app.processEvents()

    def test_new_folder_moves_the_file_there_and_apply_creates_it(self):
        from PySide6.QtCore import Qt
        self.write('a.txt','Receipt');self.write('b.txt','Other')
        panel,rejected=self._new_folder_panel([])
        with patch('app.ui.preview_panel.QInputDialog.getText',return_value=('  Receipts  ',True)):
            panel._ask_new_folder(panel.models[1]._find_node('a.txt'))
        self.app.processEvents()
        self.assertEqual(rejected,[])
        self.assertEqual([(a.src_rel,a.dst_rel) for a in panel.plan.actions],[('a.txt','Receipts/a.txt')])
        folder=panel.models[1]._find_node('Receipts')
        self.assertEqual(folder.diff_state,'created_dir')
        self.assertEqual(panel.trees[1].currentIndex().data(Qt.UserRole).rel_path,'Receipts/a.txt')
        result=Executor(self.root,self.journal).apply(panel.plan.actions)
        self.assertEqual(result.succeeded,1)
        self.assertEqual((self.root/'Receipts/a.txt').read_text(),'Receipt')
        self.assertTrue((self.root/'b.txt').exists())

    def test_new_folder_for_an_already_proposed_file_goes_beside_its_new_place(self):
        self.write('a.txt','Receipt')
        panel,_=self._new_folder_panel([PlanAction('1','move','a.txt','Subject/a.txt')])
        self.assertTrue(panel.move_to_new_folder(panel.models[1]._find_node('Subject/a.txt'),'2026'))
        self.app.processEvents()
        self.assertEqual([(a.src_rel,a.dst_rel) for a in panel.plan.actions],[('a.txt','Subject/2026/a.txt')])

    def test_new_folder_rejects_taken_or_invalid_names_without_changing_the_plan(self):
        self.write('a.txt','Receipt');self.write('notes','A file without an extension');(self.root/'Taxes').mkdir()
        panel,rejected=self._new_folder_panel([])
        node=panel.models[1]._find_node('a.txt')
        for name in ['taxes','Notes','bad/name','CON']:
            with self.subTest(name=name):
                self.assertFalse(panel.move_to_new_folder(node,name))
        with patch('app.ui.preview_panel.QInputDialog.getText',return_value=('Ignored',False)):
            panel._ask_new_folder(node)
        self.app.processEvents()
        self.assertEqual(panel.plan.actions,[])
        self.assertEqual(len(rejected),4)
        self.assertIn('already in this folder',rejected[0])
        self.assertIn("can't be used as a folder name",rejected[2])


if __name__ == '__main__':
    unittest.main()
