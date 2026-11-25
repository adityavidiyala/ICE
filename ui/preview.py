"""Render a deterministic ICE transcript without an API key or network call.

Run: python -m ui.preview
"""
from io import StringIO
from unittest.mock import patch

from rich.console import Console

from agent.events import AgentEvent
from ui.display import render_banner
from ui.terminal import TerminalUI


def main():
    console = Console(file=StringIO(), force_terminal=True, width=90, record=True)
    ui = TerminalUI()
    with patch("ui.terminal.console", console), patch("ui.display.console", console):
        render_banner()
        console.print("❯ ICE Check the project and explain what you found.", markup=False)
        try:
            for event in [
                AgentEvent.agent_start("demo"),
                AgentEvent.text_delta("I'll check the "),
                AgentEvent.text_delta("**project files** first."),
                AgentEvent.text_complete("I'll check the **project files** first."),
                AgentEvent.tool_call_start("read_file", {"path": "main.py"}),
                AgentEvent.tool_call_complete("read_file", "1| from ui.prompt import run_repl\n2| run_repl()"),
                AgentEvent.token_usage(2450, 2),
                AgentEvent.text_delta("The entry point starts ICE's interactive terminal.\n\n"),
                AgentEvent.text_delta("- Streaming responses\n- Compact tool output\n- Cyan and blue ICE theme"),
                AgentEvent.text_complete("The entry point starts ICE's interactive terminal."),
                AgentEvent.agent_end("done"),
            ]:
                ui.handle(event)
        finally:
            ui.close()
    console.save_svg("results/ice-ui-preview.svg", title="ICE terminal preview")
    print("Terminal-mode rendering passed. Preview: results/ice-ui-preview.svg")


if __name__ == "__main__":
    main()
