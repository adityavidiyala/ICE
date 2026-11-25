"""Directory observations and planner feedback must be sufficient to act on."""
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.list_directories import list_directories
from ui.transcript import ToolRow


class ExplorationTests(unittest.TestCase):
    def test_empty_workspace_is_explicit_to_model_and_user(self):
        with tempfile.TemporaryDirectory() as directory:
            result = list_directories(directory)
        self.assertIn("No visible files or directories", result)
        row = ToolRow("list_directories", {"path": directory}, 0, output=result)
        self.assertEqual(row.summary(), "No visible files or directories")

    def test_hidden_entries_are_not_claimed_to_be_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / ".env").write_text("hidden")
            (Path(directory) / "node_modules").mkdir()
            result = list_directories(directory)
        self.assertIn("No visible files or directories", result)
        self.assertIn("hidden and excluded entries omitted", result)
        self.assertNotIn(".env", result)
        self.assertNotIn("node_modules", result)

    def test_new_files_appear_on_fresh_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIn("No visible files or directories", list_directories(directory))
            (Path(directory) / "index.html").write_text("hello")
            result = list_directories(directory)
        self.assertIn("index.html", result)
        self.assertNotIn("No visible files or directories", result)

    def test_empty_subdirectory_does_not_make_root_summary_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "src").mkdir()
            result = list_directories(directory)
        self.assertIn("src/", result)
        row = ToolRow("list_directories", {}, 0, output=result)
        self.assertNotEqual(row.summary(), "No visible files or directories")

    def test_missing_directory_remains_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            result = list_directories(str(Path(directory) / "not-created"))
        self.assertTrue(result.startswith("Error: Path does not exist"))

    def test_invalid_plan_id_returns_valid_ids_without_marking_work_complete(self):
        planner = importlib.import_module("tools.plan")
        with patch.object(planner, "_steps", {}), patch.object(planner, "_status", {}):
            planner.plan("add", "Implement the game")
            step_id = planner.snapshot()["steps"][0]["id"]
            result = planner.plan("complete", id="1")
            self.assertTrue(result.startswith("Error:"))
            self.assertIn(step_id, result)
            self.assertIn("Implement the game", result)
            self.assertEqual(planner.snapshot()["completed"], 0)
            self.assertIn("Completed step", planner.plan("complete", id=step_id))
            self.assertEqual(planner.snapshot()["completed"], 1)


if __name__ == "__main__":
    unittest.main()
