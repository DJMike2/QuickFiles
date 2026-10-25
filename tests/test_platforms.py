"""Supported platform boundaries and direct native scanner execution."""
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from app.ai.binary_manager import BinaryManager
from app.ai.hardware_probe import probe
from app.config import app_data_path
from app.core.scanner import scanner_binary
from app.main import main
from app.platforms import require_supported_platform


class PlatformTests(unittest.TestCase):
    def test_windows_and_linux_select_their_own_scanner(self):
        for system, filename in (("win32", "fastscan.exe"), ("linux", "fastscan")):
            with self.subTest(system=system), patch("sys.platform", system):
                require_supported_platform()
                self.assertEqual(scanner_binary().name, filename)

    def test_other_platforms_are_rejected_before_setup_or_scanning(self):
        for system in ("darwin", "freebsd14"):
            with self.subTest(system=system), patch("sys.platform", system):
                for operation in (require_supported_platform, app_data_path, scanner_binary, probe,
                                  lambda: BinaryManager(Path("."), None)):
                    with self.assertRaisesRegex(RuntimeError, "only Windows and Linux"):
                        operation()
                errors = StringIO()
                with redirect_stderr(errors):
                    self.assertEqual(main(), 1)
                self.assertIn("only Windows and Linux", errors.getvalue())

    def test_current_platform_binary_runs_directly(self):
        binary = scanner_binary()
        with binary.open("rb") as stream:
            magic = stream.read(4)
        if sys.platform == "linux":
            self.assertEqual(magic, b"\x7fELF")
        else:
            self.assertEqual(magic[:2], b"MZ")
        result = subprocess.run([str(binary), "--version"], capture_output=True,
                                text=True, check=True, timeout=10)
        self.assertEqual(result.stdout.strip(), "quickfiles-fastscan 1")

    def test_build_tool_only_exposes_windows_and_linux_targets(self):
        builder = Path(__file__).resolve().parents[1] / "tools/build_scanner.py"
        result = subprocess.run([sys.executable, "-B", str(builder), "--help"],
                                capture_output=True, text=True, check=True)
        self.assertIn("{linux,windows}", result.stdout)
        result = subprocess.run([sys.executable, "-B", str(builder), "--target", "macos"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
