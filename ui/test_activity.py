"""Activity animation and its placement in the actual terminal screen."""
import asyncio
import unittest

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from agent.events import AgentEvent
from ui.activity import activity_fragments
from ui.workbench import Workbench


class ActivityTests(unittest.TestCase):
    def test_spinner_and_highlight_advance_without_changing_label(self):
        first = activity_fragments("Thinking…", 0)
        second = activity_fragments("Thinking…", 0.12)
        self.assertNotEqual(first[0][1], second[0][1])
        self.assertNotEqual([style for style, _ in first[1:-1]],
                            [style for style, _ in second[1:-1]])
        self.assertIn("Thinking…", "".join(value for _, value in first))


class ActivityScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_indicator_above_composer_changes_with_tools_and_hides_when_done(self):
        with create_pipe_input() as pipe:
            ui = Workbench(object(), input=pipe, output=DummyOutput())
            ui.transcript.handle(AgentEvent.agent_start("hello"))
            running = asyncio.create_task(ui.app.run_async())
            try:
                for _ in range(100):
                    if ui.app.renderer.last_rendered_screen is not None:
                        break
                    await asyncio.sleep(0.01)
                ui.app._redraw()
                screen = ui.app.renderer.last_rendered_screen
                lines = ["".join(screen.data_buffer[y][x].char for x in range(80)) for y in range(40)]
                activity_y = next(i for i, line in enumerate(lines) if "Thinking" in line)
                composer_y = next(i for i, line in enumerate(lines) if "›" in line)
                self.assertEqual(composer_y - activity_y, 2)  # Composer border lies between them.
                ui.transcript.handle(AgentEvent.tool_call_start("edit_file", {"path": "a.txt"}))
                self.assertIn("Editing", "".join(text for _, text in ui.activity_fragments()))
                ui.pending_approval = object()
                self.assertEqual(ui.activity_fragments(), [])
                ui.pending_approval = None
                ui.transcript.handle(AgentEvent.agent_end("done"))
                self.assertEqual(ui.activity_fragments(), [])
            finally:
                ui.app.exit()
                await running


if __name__ == "__main__":
    unittest.main()
