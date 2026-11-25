"""Clipboard transport and keyboard integration without touching the user's clipboard."""
import asyncio
import subprocess
import unittest
from unittest.mock import Mock, patch

from prompt_toolkit.clipboard import ClipboardData, InMemoryClipboard
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ui.clipboard import SystemClipboard
from ui.workbench import Workbench


class ClipboardTests(unittest.TestCase):
    def test_text_is_stdin_data_and_never_executable_script(self):
        text = 'hello\n$(unexpected) `code` "quotes" [green]'
        with patch("ui.clipboard.subprocess.run") as run:
            SystemClipboard().set_data(ClipboardData(text))
        self.assertEqual(run.call_args.kwargs["input"], text)
        self.assertNotIn(text, run.call_args.args[0][-1])
        self.assertIn("ReadToEnd", run.call_args.args[0][-1])

    def test_paste_preserves_unicode_and_newlines(self):
        text = "hello λ\nsecond line\n"
        with patch("ui.clipboard.subprocess.run", return_value=Mock(stdout=text)):
            self.assertEqual(SystemClipboard().get_data().text, text)

    def test_clipboard_failure_is_actionable(self):
        with patch("ui.clipboard.subprocess.run", side_effect=subprocess.TimeoutExpired("powershell", 5)):
            with self.assertRaisesRegex(RuntimeError, "clipboard unavailable"):
                SystemClipboard().get_data()


class ClipboardKeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def wait_until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait(), 5)

    async def test_paste_copy_input_response_and_chat_via_keyboard(self):
        clipboard = InMemoryClipboard()
        clipboard.set_text("pasted first line\nsecond line")
        with create_pipe_input() as pipe:
            ui = Workbench(object(), input=pipe, output=DummyOutput(), clipboard=clipboard)
            ui.transcript.entries.extend([{"user": "my request"}, {"text": "assistant response"}])
            running = asyncio.create_task(ui.app.run_async())
            try:
                await self.wait_until(lambda: ui.app.is_running)
                pipe.send_bytes(b"\x16")  # Ctrl+V
                await self.wait_until(lambda: ui.input.text == "pasted first line\nsecond line")
                self.assertIsNone(ui.task)  # Pasted newlines must never submit a prompt.
                pipe.send_bytes(b"\x01\x03")  # Ctrl+A, Ctrl+C
                await self.wait_until(lambda: ui.notice == "Copied selection")
                self.assertEqual(clipboard.get_data().text, ui.input.text)
                pipe.send_bytes(b"\x1b[17~")  # F6
                await self.wait_until(lambda: clipboard.get_data().text == "assistant response")
                pipe.send_bytes(b"\x1b[18~")  # F7
                await self.wait_until(lambda: "my request" in clipboard.get_data().text)
                self.assertIn("assistant response", clipboard.get_data().text)
                self.assertNotIn("\x1b[", clipboard.get_data().text)
                pipe.send_bytes(b"\x04")
                await asyncio.wait_for(running, 5)
            finally:
                if not running.done():
                    ui.app.exit()
                    await running


if __name__ == "__main__":
    unittest.main()
