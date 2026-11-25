"""Regression coverage for bounded, literal tool rendering."""
import io
import unittest
from unittest.mock import patch

from rich.console import Console

from agent.events import AgentEvent
from ui.display import render_event


class DisplayTests(unittest.TestCase):
    def render(self, event):
        output = io.StringIO()
        console = Console(file=output, width=100, force_terminal=True,
                          color_system="standard", record=True)
        with patch("ui.display.console", console):
            render_event(event)
        return console.export_text(), output.getvalue()

    def test_large_write_payload_is_summarized_without_mutation(self):
        content = "SECRET_CODE_LINE\n" * 500
        args = {"path": "script.js", "content": content}
        text, _ = self.render(AgentEvent.tool_call_start("write_file", args))
        self.assertIn("script.js", text)
        self.assertIn("500 lines", text)
        self.assertNotIn("SECRET_CODE_LINE", text)
        self.assertLess(len(text), 300)
        self.assertEqual(args["content"], content)

    def test_edit_payloads_are_summarized(self):
        text, _ = self.render(AgentEvent.tool_call_start("edit_file", {
            "path": "a.py", "old_string": "old\n" * 100,
            "new_string": "new\n" * 200,
        }))
        self.assertIn("100 lines", text)
        self.assertIn("200 lines", text)
        self.assertNotIn("old\\n", text)

    def test_result_tags_remain_literal_and_do_not_enable_green_background(self):
        code = "array[i]; [b] [green] [on green] [/unmatched]"
        text, ansi = self.render(AgentEvent.tool_call_complete("read_file", code))
        self.assertIn(code, text)
        self.assertNotIn("\x1b[42m", ansi)
        self.assertNotIn("\x1b[32m", ansi)

    def test_argument_tags_remain_literal(self):
        text, _ = self.render(AgentEvent.tool_call_start("grep", {
            "pattern": "[i][green][/unmatched]",
        }))
        self.assertIn("[i][green][/unmatched]", text)

    def test_long_results_are_bounded_by_characters_and_lines(self):
        for content in ("x" * 10000, "x\n" * 500):
            with self.subTest(content_length=len(content)):
                text, _ = self.render(AgentEvent.tool_call_complete("read_file", content))
                self.assertIn("output truncated for display", text)
                self.assertLess(len(text.splitlines()), 26)

    def test_long_generic_arguments_are_bounded(self):
        text, _ = self.render(AgentEvent.tool_call_start("grep", {"pattern": "x" * 10000}))
        self.assertIn("output truncated for display", text)
        self.assertLess(len(text), 600)

    def test_command_preview_renders_tags_literally(self):
        text, _ = self.render(AgentEvent.tool_call_start("run_command", {
            "command": "echo '[green]'; echo '[/unmatched]'",
        }))
        self.assertIn("[green]", text)
        self.assertIn("[/unmatched]", text)


if __name__ == "__main__":
    unittest.main()
