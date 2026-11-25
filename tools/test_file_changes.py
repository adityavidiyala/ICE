"""Verify automatic file changes retain previews and validation."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console
from tools.edit_file import edit_file
from tools.write_file import write_file


class FileChangeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.output = io.StringIO()
        console = Console(file=self.output, width=100)
        for target in ("tools.write_file._console", "tools.edit_file._console"):
            patcher = patch(target, console)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("builtins.input", side_effect=AssertionError("Unexpected approval prompt"))
        self.input_mock = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.input_mock.assert_not_called()

    def test_create_nested_file_without_approval(self):
        path = self.root / "nested" / "new.txt"
        result = write_file(str(path), "hello\n")
        self.assertTrue(result.startswith("Success:"))
        self.assertEqual(path.read_text(encoding="utf-8"), "hello\n")
        self.assertIn("New file", self.output.getvalue())

    def test_overwrite_retains_preview_before_writing(self):
        path = self.root / "existing.txt"
        path.write_text("old\n", encoding="utf-8")
        with patch("tools.write_file.render_diff") as preview:
            def check(diff, preview_path, console):
                self.assertEqual(path.read_text(encoding="utf-8"), "old\n")
                self.assertIn("+new", diff)
            preview.side_effect = check
            result = write_file(str(path), "new\n")
            preview.assert_called_once()
        self.assertTrue(result.startswith("Success:"))
        self.assertEqual(path.read_text(encoding="utf-8"), "new\n")

    def test_edit_retains_preview_before_writing(self):
        path = self.root / "edit.txt"
        path.write_text("hello world\n", encoding="utf-8")
        with patch("tools.edit_file.render_diff") as preview:
            def check(diff, preview_path, console):
                self.assertEqual(path.read_text(encoding="utf-8"), "hello world\n")
                self.assertIn("+hello ICE", diff)
            preview.side_effect = check
            result = edit_file(str(path), "world", "ICE")
            preview.assert_called_once()
        self.assertTrue(result.startswith("Success:"))
        self.assertEqual(path.read_text(encoding="utf-8"), "hello ICE\n")

    def test_identical_write_does_not_rewrite_file(self):
        path = self.root / "same.txt"
        path.write_text("same\n", encoding="utf-8")
        with patch.object(Path, "write_text") as write:
            result = write_file(str(path), "same\n")
        self.assertTrue(result.startswith("No changes"))
        write.assert_not_called()

    def test_ambiguous_edit_is_rejected_until_replace_all(self):
        path = self.root / "repeat.txt"
        path.write_text("word word\n", encoding="utf-8")
        result = edit_file(str(path), "word", "new")
        self.assertTrue(result.startswith("Error:"))
        self.assertEqual(path.read_text(encoding="utf-8"), "word word\n")
        result = edit_file(str(path), "word", "new", replace_all=True)
        self.assertTrue(result.startswith("Success:"))
        self.assertEqual(path.read_text(encoding="utf-8"), "new new\n")


if __name__ == "__main__":
    unittest.main()
