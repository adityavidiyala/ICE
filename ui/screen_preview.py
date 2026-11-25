"""Capture actual application screens offline: python -m ui.screen_preview."""
import asyncio
import io
from pathlib import Path
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console
from rich.style import Style
from rich.text import Text
from agent.events import AgentEvent
from ui.workbench import Workbench


class PreviewOutput(DummyOutput):
    def get_size(self):
        return Size(rows=34, columns=112)


class DemoAgent:
    def run(self, message, stream=False):
        yield AgentEvent.agent_start(message)
        calls = [
            ("list_directories", {"path": "."}, "src/\nmain.py\ntests/"),
            ("plan", {"action": "add", "content": "Build and verify the game"}, "Plan (0/1 completed):\n[ ] [step-123] Build and verify the game"),
            ("write_file", {"path": "tic-tac-toe/index.html"}, "Success: Wrote 66 lines to tic-tac-toe/index.html"),
            ("write_file", {"path": "tic-tac-toe/style.css"}, "Success: Wrote 128 lines to tic-tac-toe/style.css"),
            ("run_command", {"command": "node --check tic-tac-toe/script.js"}, "Command executed successfully with no output."),
        ]
        for name, args, result in calls:
            yield AgentEvent.tool_call_start(name, args)
            yield AgentEvent.tool_call_complete(name, result)
        yield AgentEvent.token_usage(7536, 4)
        text = "The game is ready.\n\n- Two-player board with win detection\n- Score tracking and reset button\n- Responsive layout\n\nOpen `tic-tac-toe/index.html` to play. JavaScript syntax check passed."
        yield AgentEvent.text_delta(text)
        yield AgentEvent.text_complete(text)
        yield AgentEvent.agent_end(text)


def capture(ui, filename):
    ui.app._redraw()
    screen = ui.app.renderer.last_rendered_screen
    output = Text()
    from PIL import Image, ImageDraw, ImageFont
    font_path = Path("C:/Windows/Fonts/consola.ttf")
    font = ImageFont.truetype(str(font_path), 17) if font_path.exists() else ImageFont.load_default()
    image = Image.new("RGB", (1160, 750), "#0d0e10")
    draw = ImageDraw.Draw(image)
    for y in range(34):
        for x in range(112):
            cell = screen.data_buffer[y][x]
            attrs = ui.app._merged_style.get_attrs_for_style_str(cell.style)
            color = "#" + attrs.color if len(attrs.color or "") == 6 else "#b9bec7"
            background = "#" + attrs.bgcolor if len(attrs.bgcolor or "") == 6 else None
            output.append(cell.char or " ", style=Style(color=color, bgcolor=background, bold=attrs.bold))
            if background:
                draw.rectangle((20 + x * 10, 10 + y * 21, 30 + x * 10, 31 + y * 21), fill=background)
            draw.text((20 + x * 10, 10 + y * 21), cell.char or " ", font=font, fill=color)
        output.append("\n")
    console = Console(file=io.StringIO(), width=112, record=True)
    console.print(output, end="", soft_wrap=True)
    console.save_svg(filename, title="ICE")
    image.save(str(Path(filename).with_suffix(".png")))


async def preview():
    with create_pipe_input() as pipe:
        ui = Workbench(DemoAgent(), input=pipe, output=PreviewOutput())
        running = asyncio.create_task(ui.app.run_async())
        try:
            while not ui.app.is_running:
                await asyncio.sleep(0.01)
            capture(ui, "results/ice-welcome-preview.svg")
            pipe.send_text("Build a tic tac toe game using HTML, CSS and JavaScript.\r")
            while ui.task is None or not ui.task.done():
                if running.done():
                    await running
                await asyncio.sleep(0.01)
            capture(ui, "results/ice-ui-preview.svg")
        finally:
            if not running.done():
                ui.app.exit()
            await running


if __name__ == "__main__":
    asyncio.run(preview())
    print("Saved actual welcome and transcript screens to results/ice-*-preview.svg and .png")
