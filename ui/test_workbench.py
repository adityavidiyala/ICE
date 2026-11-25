"""Exercise the actual application, worker handoff, and collapsed transcript."""
import asyncio
import io
import unittest
import tempfile
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from agent.events import AgentEvent
from tools.run_command import run_command
from ui.presentation import present_change, change_handler, approval_handler
from ui.transcript import Transcript, ToolRow
from ui.workbench import Workbench


class TranscriptTests(unittest.TestCase):
    def text(self, model, width=100):
        buffer = io.StringIO()
        Console(file=buffer, width=width).print(model.render(width))
        return buffer.getvalue()

    def test_plans_are_compact_and_details_are_recoverable(self):
        model = Transcript()
        model.handle(AgentEvent.agent_start("Build a game"))
        model.handle(AgentEvent.tool_call_start("plan", {"action": "add", "content": "Build the board"}))
        model.handle(AgentEvent.tool_call_complete("plan", "Plan (0/1 completed):\n[ ] [secret-id] Build the board"))
        text = self.text(model)
        self.assertIn("Build the board", text)
        self.assertNotIn("secret-id", text)
        self.assertNotIn("action=", text)
        model.toggle_details()
        self.assertIn("secret-id", self.text(model))

    def test_errors_expand_and_source_tags_remain_literal(self):
        model = Transcript()
        model.handle(AgentEvent.tool_call_start("run_command", {"command": "demo"}))
        model.handle(AgentEvent.tool_call_complete("run_command", "[green] details\nExit code: 1"))
        self.assertEqual(model.entries[0].status, "Error")
        self.assertIn("[green] details", self.text(model))

    def test_stream_is_updated_once_without_duplicate_blocks(self):
        model = Transcript()
        model.handle(AgentEvent.text_delta("Hello "))
        model.handle(AgentEvent.text_delta("world"))
        model.handle(AgentEvent.text_complete("Hello world"))
        self.assertEqual(self.text(model).count("Hello world"), 1)

    def test_real_file_changes_use_ui_hook_without_printing_previews(self):
        from tools.write_file import write_file
        from tools.edit_file import edit_file
        changes = []
        token = change_handler.set(lambda *args: changes.append(args))
        try:
            with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()) as output:
                path = str(Path(directory) / "test.txt")
                self.assertTrue(write_file(path, "old\n").startswith("Success"))
                self.assertTrue(edit_file(path, "old", "new").startswith("Success"))
                self.assertEqual(Path(path).read_text(), "new\n")
                self.assertEqual(output.getvalue(), "")
            self.assertTrue(changes[0][2])
            self.assertIn("+new", changes[1][1])
        finally:
            change_handler.reset(token)

    def test_approved_command_executes_once_and_safe_command_prints_no_logs(self):
        token = approval_handler.set(lambda command, result: True)
        try:
            with patch("tools.run_command.subprocess.run") as execute, redirect_stdout(io.StringIO()) as output:
                execute.return_value.stdout = "ok"
                execute.return_value.stderr = ""
                execute.return_value.returncode = 0
                self.assertEqual(run_command("python demo.py"), "ok")
                self.assertEqual(execute.call_count, 1)
                self.assertEqual(run_command("git status"), "ok")
                self.assertEqual(output.getvalue(), "")
        finally:
            approval_handler.reset(token)


class WorkbenchTests(unittest.IsolatedAsyncioTestCase):
    async def wait_until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait(), timeout=5)

    async def test_scrollback_keyboard_wheel_and_follow_during_new_output(self):
        with create_pipe_input() as pipe:
            ui = Workbench(object(), input=pipe, output=DummyOutput())
            ui.transcript.entries.append({"text": "\n".join(f"message {i:03}" for i in range(120))})
            running = asyncio.create_task(ui.app.run_async())
            try:
                await self.wait_until(lambda: ui.log_window.render_info is not None)
                bottom = ui.log_window.vertical_scroll
                self.assertGreater(bottom, 0)
                pipe.send_bytes(b"\x1b[5~")  # PageUp
                await self.wait_until(lambda: not ui.follow and ui.log_window.vertical_scroll < bottom)
                top = ui.log_window.vertical_scroll
                ui.transcript.entries.append({"text": "new message while reading earlier messages"})
                ui.app.invalidate()
                await self.wait_until(lambda: ui.log_lines > 124)
                self.assertEqual(ui.log_window.vertical_scroll, top)
                # SGR wheel-up event over the transcript, through the input parser.
                pipe.send_bytes(b"\x1b[<64;10;5M")
                await self.wait_until(lambda: ui.log_window.vertical_scroll < top)
                pipe.send_bytes(b"\x1b[1;5F")  # Ctrl+End
                await self.wait_until(lambda: ui.follow and ui.log_window.vertical_scroll == ui.max_scroll)
                pipe.send_bytes(b"\x1b[1;2A")  # Shift+Up
                await self.wait_until(lambda: not ui.follow)
                previous = ui.scroll_top
                pipe.send_bytes(b"\x1b[1;2B")  # Shift+Down
                await self.wait_until(lambda: ui.scroll_top > previous)
                pipe.send_bytes(b"\x1b[1;5H")  # Ctrl+Home
                await self.wait_until(lambda: ui.log_window.vertical_scroll == 0)
                pipe.send_bytes(b"\x1b[6~")  # PageDown
                await self.wait_until(lambda: ui.log_window.vertical_scroll > 0)
                self.assertEqual(ui.app.layout.current_control, ui.input.control)
                pipe.send_bytes(b"\x04")
                await asyncio.wait_for(running, timeout=5)
            finally:
                if not running.done():
                    ui.app.exit()
                    await running

    async def test_welcome_uses_original_block_logo(self):
        from ui.logo import ICE_LOGO
        with create_pipe_input() as pipe:
            ui = Workbench(object(), input=pipe, output=DummyOutput())
            text = "".join(value for _, value in ui.log_fragments())
            for line in ICE_LOGO.splitlines():
                self.assertIn(line, text)
            self.assertIn("Interactive Coding Environment", text)

    async def test_keyboard_submission_streaming_and_clean_exit(self):
        class Agent:
            def run(self, message, stream=False):
                yield AgentEvent.agent_start(message)
                yield AgentEvent.text_delta("Hello ")
                yield AgentEvent.text_delta("world")
                yield AgentEvent.text_complete("Hello world")
                yield AgentEvent.tool_call_start("edit_file", {"path": "demo.py"})
                present_change("demo.py", "-old\n+new\n")
                yield AgentEvent.tool_call_complete("edit_file", "Success: Replaced one line")
                yield AgentEvent.agent_end("Hello world")

        with create_pipe_input() as pipe:
            ui = Workbench(Agent(), input=pipe, output=DummyOutput())
            running = asyncio.create_task(ui.app.run_async())
            try:
                await self.wait_until(lambda: ui.app.is_running)
                pipe.send_text("hello\r")
                await self.wait_until(lambda: ui.task is not None and ui.task.done())
                self.assertEqual(len([e for e in ui.transcript.entries if isinstance(e, ToolRow)]), 1)
                self.assertEqual(ui.transcript.entries[-1].diff, "-old\n+new\n")
                self.assertFalse(ui.transcript.busy)
                pipe.send_bytes(b"\x0f")
                await self.wait_until(lambda: ui.transcript.entries[-1].expanded)
                pipe.send_bytes(b"\x04")
                await asyncio.wait_for(running, timeout=5)
            finally:
                if not running.done():
                    ui.app.exit()
                    await running

    async def test_command_approval_stays_inside_application_and_denial_prevents_execution(self):
        class Agent:
            def run(self, message, stream=False):
                yield AgentEvent.agent_start(message)
                yield AgentEvent.tool_call_start("run_command", {"command": "python demo.py"})
                result = run_command("python demo.py")
                yield AgentEvent.tool_call_complete("run_command", result)
                yield AgentEvent.agent_end("done")

        with create_pipe_input() as pipe, patch("tools.run_command.subprocess.run") as execute:
            ui = Workbench(Agent(), input=pipe, output=DummyOutput())
            running = asyncio.create_task(ui.app.run_async())
            try:
                await self.wait_until(lambda: ui.app.is_running)
                pipe.send_text("run\r")
                await self.wait_until(lambda: ui.pending_approval is not None)
                pipe.send_text("n\r")
                await self.wait_until(lambda: ui.task.done())
                execute.assert_not_called()
                self.assertIn("Cancelled", ui.transcript.entries[-1].output)
                pipe.send_bytes(b"\x04")
                await asyncio.wait_for(running, timeout=5)
            finally:
                ui.resolve_approval(False)
                if not running.done():
                    ui.app.exit()
                    await running


if __name__ == "__main__":
    unittest.main()
