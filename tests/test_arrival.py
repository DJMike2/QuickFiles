"""Folder memory from a previous scan, without the model or the disk."""
from types import SimpleNamespace
import unittest

from app.core.arrival import ScanIndex, is_ignored


class ArrivalTests(unittest.TestCase):
    def test_scanned_folders_keep_a_short_description(self):
        inventory = SimpleNamespace(
            directories=["Notes", "Photos", "Project"],
            protected_projects=["Project"],
            entries=[
                SimpleNamespace(rel_path="Notes/meeting.txt", filename="meeting.txt"),
                SimpleNamespace(rel_path="Photos/holiday.txt", filename="holiday.txt"),
                SimpleNamespace(rel_path="Project/source.js", filename="source.js"),
            ],
        )
        index = ScanIndex()
        index.learn(inventory)
        self.assertEqual(index.folders["Notes"], "meeting.txt")
        self.assertEqual(index.folders["Photos"], "holiday.txt")
        self.assertNotIn("Project", index.folders)

    def test_a_moved_file_is_remembered_with_its_folder(self):
        index = ScanIndex()
        index.folders = {"Notes": "existing folder"}
        index.add("Notes", "standup.txt")
        self.assertEqual(index.folders["Notes"], "standup.txt")

    def test_partial_downloads_and_scan_excludes_are_ignored(self):
        self.assertTrue(is_ignored("report.pdf.crdownload", []))
        self.assertTrue(is_ignored("notes.tmp", ["*.tmp"]))
        self.assertFalse(is_ignored("notes.txt", ["*.tmp"]))


if __name__ == "__main__":
    unittest.main()