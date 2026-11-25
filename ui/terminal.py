"""ICE's streamed transcript and transient activity display."""
import time
from collections import deque

from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.spinner import Spinner
from rich.text import Text

from agent.events import AgentEvent, AgentEventType as Event
from ui.console import console
from ui.display import _preview, render_event


class TerminalUI:
    def __init__(self):
        self.live = None
        self.text = ""
        self.started = 0
        self.tokens = 0
        self.turn = 0
        self.tools = 0
        self.failed = False
        self.recent = deque(maxlen=20)

    def close(self):
        if self.live is not None:
            self.live.stop()
            self.live = None

    def thinking(self):
        self.close()
        if console.is_terminal:
            self.live = Live(Spinner("dots", text=Text(" ICE is thinking…", style="cyan"),
                                     style="cyan"), console=console,
                             transient=True, refresh_per_second=10)
            self.live.start()

    def finish_text(self, fallback=""):
        streamed = bool(self.text)
        self.close()
        if not streamed and fallback:
            render_event(AgentEvent.text_complete(fallback))
        elif streamed and not console.is_terminal:
            render_event(AgentEvent.text_complete(self.text))
        self.text = ""

    def expand(self):
        if not self.recent:
            console.print(Text("  No tool output yet.", style="dim"))
            return
        name, output = self.recent[-1]
        console.print(Text(f"\n◇ {name} · details", style="cyan"))
        console.print(Padding(Text(_preview(output, 12000, 120)), (0, 2)))

    def handle(self, event):
        kind, data = event.type, event.data
        if kind == Event.AGENT_START:
            self.close()
            self.started = time.monotonic()
            self.tokens = self.turn = self.tools = 0
            self.failed = False
            self.text = ""
            # The submitted prompt is already present in the transcript.
            self.thinking()
        elif kind == Event.TEXT_DELTA:
            if not self.text:
                self.close()
                if console.is_terminal:
                    console.print(Text("\n◆ ICE", style="bold cyan"))
            self.text += data["content"]
            if console.is_terminal:
                body = Padding(Markdown(self.text), (0, 2))
                if self.live is None:
                    self.live = Live(body, console=console, refresh_per_second=15,
                                     vertical_overflow="visible")
                    self.live.start()
                else:
                    self.live.update(body)
        elif kind == Event.TEXT_COMPLETE:
            self.finish_text(data["content"])
            self.thinking()
        elif kind == Event.TOKEN_USAGE:
            self.tokens = data["total_tokens"]
            self.turn = data["turn"]
        elif kind == Event.TOOL_CALL_START:
            self.finish_text()
            self.tools += 1
            render_event(event)
            # Leave Live stopped while tools print previews or ask approval.
        elif kind == Event.TOOL_CALL_COMPLETE:
            output = str(data["output"])
            self.recent.append((data["name"], output))
            if data["name"] == "plan":
                render_event(event)
            else:
                is_error = output.startswith(("Error", "Tool execution failed", "Cancelled"))
                summary = _preview(output, 800 if is_error else 240, 10 if is_error else 3)
                console.print(Padding(Text(summary, style="red" if is_error else "dim"), (0, 2)))
            self.thinking()
        elif kind == Event.AGENT_ERROR:
            self.finish_text()
            self.failed = True
            render_event(event)
        elif kind == Event.AGENT_END:
            self.finish_text()
            elapsed = time.monotonic() - self.started
            status = "Stopped" if self.failed else "Done"
            console.print(Text(
                f"\n  {status} · {elapsed:.1f}s · {self.tools} tools · "
                f"{self.turn} turns · {self.tokens:,} tokens in last request\n", style="dim"))
        else:
            self.close()
            render_event(event)
