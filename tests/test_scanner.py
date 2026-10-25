"""Native scanner integration and subprocess lifecycle contracts (no AI needed)."""
import fnmatch
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.core.scanner import Scanner, scanner_binary


class NativeScannerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scanner ü-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, relative, data=b"example"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_metadata_unicode_directories_and_stable_parallel_order(self):
        for name, data in (("z.TXT", b"example"), ("a.txt", b""),
                           ("Nested/résumé 🐈.pdf", b"example"), ("Nested/Deep/data.csv", b"example")):
            self.write(name, data)
        (self.root / "Empty").mkdir()
        expected = ["a.txt", "z.TXT", "Nested/résumé 🐈.pdf", "Nested/Deep/data.csv"]
        for threads in (1, 4):
            scanner = Scanner(self.root, threads=threads)
            inventory = scanner.scan()
            self.assertEqual(scanner.native_threads, threads)
            self.assertEqual([e.rel_path for e in inventory.entries], expected)
            self.assertEqual(inventory.directories, ["Empty", "Nested", "Nested/Deep"])
            self.assertEqual(inventory.error_count, 0)
            for entry in inventory.entries:
                path = self.root / entry.rel_path
                self.assertEqual(entry.abs_path, str(path))
                self.assertEqual(entry.size_bytes, path.stat().st_size)
                self.assertAlmostEqual(entry.modified_ts, path.stat().st_mtime, places=4)
                self.assertEqual(entry.depth, len(path.relative_to(self.root).parts) - 1)
            self.assertTrue(inventory.entries[0].is_empty)
            self.assertEqual(inventory.entries[1].extension, ".txt")
            self.assertEqual(inventory.entries[2].file_type_bucket, "PDFs")

    def test_exclusions_project_pruning_and_protected_names(self):
        for name in ("keep.txt", "skip.tmp", ".hidden.txt", "~$draft.docx", "desktop.ini",
                     "ntuser.dat.LOG1", "Hidden.tmp/inside.txt", ".hidden/inside.txt",
                     "Project/package.json", "Project/source.js", "Git/.git/HEAD",
                     "Git/source.py", "Solution/App.csproj", "Solution/code.cs",
                     "ISSetupPrerequisites/install.exe", "Normal/file.txt"):
            self.write(name)
        inventory = Scanner(self.root, exclude_patterns=["*.tmp"], threads=4).scan()
        self.assertEqual([e.rel_path for e in inventory.entries], ["keep.txt", "Normal/file.txt"])
        self.assertEqual(inventory.protected_projects, ["Git", "ISSetupPrerequisites", "Project", "Solution"])
        self.assertEqual(inventory.directories, ["Git", "ISSetupPrerequisites", "Normal", "Project", "Solution"])
        self.assertEqual(inventory.skipped_count, 5)
        self.assertEqual(inventory.error_count, 0)

    def test_glob_patterns_follow_fnmatch_for_files_and_directories(self):
        names = ["a.txt", "ab.txt", "abc.txt", "b.txt", "cat.md", "file1.txt", "file9.txt",
                 "résumé.txt", "[a].txt", "hello.TXT", "a.b.c"]
        for name in names:
            self.write(name)
        for pattern in ("?.txt", "[ab]*", "[!a]*", "file[0-5].txt", "*a*b*", "[[]a]*", "*.TXT", "[]", "*.*.*"):
            with self.subTest(pattern=pattern):
                result = Scanner(self.root, exclude_patterns=[pattern]).scan()
                expected = sorted(name for name in names if not fnmatch.fnmatch(name, pattern))
                self.assertEqual([e.filename for e in result.entries], expected)

    def test_links_and_nonregular_files_are_skipped(self):
        self.write("keep.txt")
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "external.txt"
            target.write_text("outside")
            try:
                (self.root / "linked.txt").symlink_to(target)
                (self.root / "linked-folder").symlink_to(outside, target_is_directory=True)
                (self.root / "cycle").symlink_to(self.root, target_is_directory=True)
            except OSError:
                self.skipTest("Symlink creation is unavailable for this account")
            if sys.platform != "win32":
                import os
                os.mkfifo(self.root / "pipe")
            inventory = Scanner(self.root, threads=4).scan()
            self.assertEqual([e.rel_path for e in inventory.entries], ["keep.txt"])
            self.assertEqual(inventory.directories, [])

    def test_blocked_subtree_is_pruned(self):
        self.write("keep.txt")
        self.write("Blocked/private.txt")
        with patch("app.core.scanner.blocked_roots", return_value=[self.root / "Blocked"]):
            inventory = Scanner(self.root).scan()
        self.assertEqual([e.rel_path for e in inventory.entries], ["keep.txt"])
        self.assertEqual(inventory.directories, [])

    def test_file_cap_is_stable_across_threads_and_reports_skipped(self):
        for name in ("z.txt", "a.txt", "folder/b.txt", "folder/c.txt"):
            self.write(name)
        for threads in (1, 4):
            inventory = Scanner(self.root, max_files=2, threads=threads).scan()
            self.assertEqual([e.rel_path for e in inventory.entries], ["a.txt", "z.txt"])
            self.assertEqual(inventory.skipped_count, 2)

    @unittest.skipUnless(sys.platform == "win32", "Windows file attributes")
    def test_windows_system_and_cloud_files_are_skipped(self):
        import ctypes
        system = self.write("system.txt")
        cloud = self.write("cloud.txt")
        self.write("keep.txt")
        set_attributes = ctypes.windll.kernel32.SetFileAttributesW
        self.assertTrue(set_attributes(str(system), 0x4))
        self.assertTrue(set_attributes(str(cloud), 0x1000))
        try:
            inventory = Scanner(self.root).scan()
            self.assertEqual([e.rel_path for e in inventory.entries], ["keep.txt"])
            self.assertEqual(inventory.skipped_count, 2)
        finally:
            set_attributes(str(system), 0x80)
            set_attributes(str(cloud), 0x80)

    def test_deep_paths_and_extensionless_files(self):
        relative = "/".join(["long-folder-name-" + str(i) * 10 for i in range(12)]) + "/README"
        self.write(relative)
        inventory = Scanner(self.root).scan()
        self.assertEqual([e.rel_path for e in inventory.entries], [relative])
        self.assertEqual(inventory.entries[0].extension, "")

    def test_cancel_during_scan_reaps_child(self):
        for index in range(500):
            self.write(f"file-{index:04}.txt")
        cancelled = False
        launched = []
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            launched.append(child)
            return child

        def progress(*_):
            nonlocal cancelled
            cancelled = True

        with patch("app.core.scanner.subprocess.Popen", side_effect=launch):
            with self.assertRaises(InterruptedError):
                Scanner(self.root, progress_cb=progress, cancelled=lambda: cancelled).scan()
        self.assertEqual(len(launched), 1)
        self.assertIsNotNone(launched[0].poll())
        self.assertTrue(launched[0].stdout.closed)

    def test_cancel_before_scan_and_missing_binary(self):
        with patch("app.core.scanner.subprocess.Popen") as launch:
            with self.assertRaises(InterruptedError):
                Scanner(self.root, cancelled=lambda: True).scan()
            launch.assert_not_called()
        scanner = Scanner(self.root)
        scanner.binary_path = self.root / "missing"
        with self.assertRaisesRegex(OSError, "build_scanner.py"):
            scanner.scan()

    def test_benchmark_uses_native_binary_from_another_cwd(self):
        self.write("keep.txt")
        benchmark = Path(__file__).resolve().parent / "benchmark_scan.py"
        result = subprocess.run([sys.executable, "-B", str(benchmark), str(self.root),
                                 "--repeat", "2", "--threads", "4"], cwd=self.root,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.count("Files scanned: 1"), 2)
        self.assertIn(str(scanner_binary()), result.stdout)
        self.assertIn("Threads: 4", result.stdout)


class ScannerProtocolTests(unittest.TestCase):
    def test_cancellation_while_native_stdout_is_stalled(self):
        released = threading.Event()

        class Pipe(BytesIO):
            def read1(self, _):
                released.wait(2)
                return b""

        class Process:
            returncode = None
            stdout = Pipe()

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = -15
                released.set()

            def wait(self, **_):
                return self.returncode

        child = Process()
        with tempfile.TemporaryDirectory() as root, \
                patch("app.core.scanner.subprocess.Popen", return_value=child):
            started = time.monotonic()
            with self.assertRaises(InterruptedError):
                Scanner(Path(root), cancelled=lambda: time.monotonic() - started > 0.1).scan()
            self.assertLess(time.monotonic() - started, 1)
        self.assertIsNotNone(child.poll())
        self.assertTrue(child.stdout.closed)

    def test_native_failure_and_truncated_output_do_not_return_partial_inventory(self):
        header = {"t": "H", "protocol": 1, "threads": 1}
        summary = {"t": "S", "skipped": 0, "errors": 0}
        for records, code in (([header], 0), ([header, summary], 3),
                              ([{"t": "H", "protocol": 99}], 0), ([summary], 0)):
            with self.subTest(records=records, code=code), tempfile.TemporaryDirectory() as root:
                class Process:
                    returncode = code
                    stdout = BytesIO(b"".join(json.dumps(r).encode() + b"\n" for r in records))

                    def poll(self):
                        return self.returncode

                with patch("app.core.scanner.subprocess.Popen", return_value=Process()):
                    with self.assertRaises(OSError):
                        Scanner(Path(root)).scan()


if __name__ == "__main__":
    unittest.main()
