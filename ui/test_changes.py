"""Review real completed edits after subsequent tools have run."""
import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from agent.events import AgentEvent
from tools.write_file import write_file
from tools.edit_file import edit_file
from ui.changes import ChangeStore, numbered_diff
from ui.presentation import change_record_handler, change_handler
from ui.workbench import Workbench


def rendered(value):
    buffer = io.StringIO()
    Console(file=buffer, width=120).print(value)
    return buffer.getvalue()


class ChangeTests(unittest.TestCase):
    def test_combined_and_individual_diffs_after_multiple_edits(self):
        store = ChangeStore()
        store.record("example.py", "a\n", "b\n")
        store.record("example.py", "b\n", "c\n")
        self.assertIn("2 edits", rendered(store.render()))
        combined = rendered(store.render("1"))
        self.assertIn("- a", combined)
        self.assertIn("+ c", combined)
        self.assertNotIn("+ b", combined)
        self.assertIn("+ b", rendered(store.render("1 1")))
        self.assertIn("- b", rendered(store.render("1 2")))

    def test_line_numbers_for_replacement_and_addition(self):
        output = rendered(numbered_diff("--- a/x\n+++ b/x\n@@ -12,2 +12,3 @@\n same\n-old\n+new\n+extra\n"))
        self.assertIn("   12    12   same", output)
        self.assertIn("   13       - old", output)
        self.assertIn("         13 + new", output)
        self.assertIn("         14 + extra", output)

    def test_reverted_file_keeps_history_and_all_view_keeps_files(self):
        store = ChangeStore()
        store.record("first.py", "old\n", "new\n")
        store.record("first.py", "new\n", "old\n")
        store.record("second.py", "", "created\n", True)
        self.assertIn("No net line changes", rendered(store.render("1")))
        output = rendered(store.render("all"))
        self.assertIn("first.py", output)
        self.assertIn("second.py", output)
        self.assertIn("+ created", output)
        self.assertEqual(len(next(iter(store.files.values())).revisions), 2)

    def test_empty_invalid_and_long_diffs(self):
        store = ChangeStore()
        self.assertIn("No completed file changes", rendered(store.render()))
        store.record("long.txt", "", "".join(f"line {i}\n" for i in range(200)), True)
        for selection in ("0", "-1", "2", "1 0", "1 2", "1 x", "1 1 1"):
            self.assertIn("Unknown file/edit number", rendered(store.render(selection)))
        self.assertIn("line 199", rendered(store.render("1")))

    def test_real_file_tools_record_only_successful_changes(self):
        store = ChangeStore()
        record_token = change_record_handler.set(store.record)
        preview_token = change_handler.set(lambda *args: None)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / "file.txt")
                self.assertTrue(write_file(path, "old\n").startswith("Success"))
                self.assertTrue(edit_file(path, "old", "new").startswith("Success"))
                write_file(path, "new\n")  # Identical content must not add a revision.
                edit_file(path, "missing", "ignored")
                with patch.object(Path, "write_text", side_effect=OSError("disk full")):
                    self.assertTrue(write_file(path, "failed\n").startswith("Error"))
                file = next(iter(store.files.values()))
                self.assertEqual(len(file.revisions), 2)
                self.assertEqual(file.baseline, "")
                self.assertEqual(file.latest, "new\n")
                self.assertTrue(file.revisions[0].created)
                self.assertEqual(Path(path).read_text(), "new\n")
        finally:
            change_record_handler.reset(record_token)
            change_handler.reset(preview_token)


class ChangeNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def wait_until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait(), 5)

    async def test_review_after_other_tool_and_return_to_chat(self):
        class Agent:
            def run(self, message, stream=False):
                yield AgentEvent.agent_start(message)
                yield AgentEvent.tool_call_start("write_file", {"path": path})
                yield AgentEvent.tool_call_complete("write_file", write_file(path, "first\n"))
                yield AgentEvent.tool_call_start("edit_file", {"path": path})
                yield AgentEvent.tool_call_complete("edit_file", edit_file(path, "first", "second"))
                yield AgentEvent.tool_call_start("run_command", {"command": "mock check"})
                yield AgentEvent.tool_call_complete("run_command", "ok")
                yield AgentEvent.agent_end("done")

        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            path = str(Path(directory) / "demo.txt")
            ui = Workbench(Agent(), input=pipe, output=DummyOutput())
            running = asyncio.create_task(ui.app.run_async())
            try:
                await self.wait_until(lambda: ui.app.is_running)
                pipe.send_text("make edits\r")
                await self.wait_until(lambda: ui.task is not None and ui.task.done())
                task = ui.task
                pipe.send_text("/changes\r")
                await self.wait_until(lambda: ui.changes_view == "")
                self.assertIn("demo.txt", rendered(ui.changes.render()))
                pipe.send_text("/changes 1 1\r")
                await self.wait_until(lambda: ui.changes_view == "1 1")
                fragments = "".join(item[1] for item in ui.log_fragments())
                self.assertIn("+ first", fragments)
                pipe.send_text("/changes 1\r")
                await self.wait_until(lambda: ui.changes_view == "1")
                self.assertIn("+ second", rendered(ui.changes.render("1")))
                pipe.send_text("/chat\r")
                await self.wait_until(lambda: ui.changes_view is None)
                self.assertIs(ui.task, task)  # Local commands never call the model.
                pipe.send_bytes(b"\x04")
                await asyncio.wait_for(running, 5)
            finally:
                if not running.done():
                    ui.app.exit()
                    await running


if __name__ == "__main__":
    unittest.main()
