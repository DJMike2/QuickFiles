"""Fast protected-path checks must give the same answers as pathlib and notice changes."""
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys
import tempfile
import unittest
from unittest import mock

from app.core import blocked_paths


class BlockedPathTests(unittest.TestCase):
    def test_containment_matches_pathlib(self):
        cases = [('/usr', '/usr'), ('/usr/lib/x', '/usr'), ('/usrlocal/x', '/usr'), ('/home/a', '/home/a/b'),
                 ('/', '/'), ('/etc', '/'), ('/var/log', '/var/lib')]
        for path, root in cases:
            with self.subTest(path=path, root=root):
                self.assertEqual(blocked_paths._within(PurePosixPath(path), PurePosixPath(root)),
                                 PurePosixPath(path).is_relative_to(root))

    def test_windows_containment_ignores_case_like_pathlib(self):
        with mock.patch.object(sys, 'platform', 'win32'):
            for path, root in [('C:/Windows/System32', 'c:/windows'), ('C:/WindowsApps', 'C:/Windows'),
                               ('D:/Data', 'C:/')]:
                with self.subTest(path=path, root=root):
                    self.assertEqual(blocked_paths._within(PureWindowsPath(path), PureWindowsPath(root)),
                                     PureWindowsPath(path).is_relative_to(root))

    def test_changed_roots_are_noticed_and_resolution_failures_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'first').mkdir()
            (root/'second').mkdir()
            with mock.patch.object(blocked_paths, 'blocked_roots', return_value=[root/'first']):
                self.assertTrue(blocked_paths.is_blocked(root/'first'/'a.txt'))
                self.assertFalse(blocked_paths.is_blocked(root/'second'/'a.txt'))
            with mock.patch.object(blocked_paths, 'blocked_roots', return_value=[root/'second']):
                self.assertTrue(blocked_paths.is_blocked(root/'second'/'a.txt'))
            with mock.patch.object(blocked_paths, 'resolve_safe', side_effect=OSError('unreadable')):
                self.assertTrue(blocked_paths.is_blocked(root/'second'/'a.txt'))


if __name__ == '__main__':
    unittest.main()
