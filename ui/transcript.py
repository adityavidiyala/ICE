"""Compact terminal transcript. Colors are deliberately limited to accents."""
import re
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Group
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from agent.events import AgentEventType as Event
from ui.display import _preview

TEXT = "#b9bec7"
MUTED = "#777e89"
BORDER = "#444b55"
ACCENT = "#9cb9c4"
ERROR = "#c38b8b"


def prose(value):
    """Render prose without heading boxes, bright code palettes, or markup injection."""
    rows = []
    in_code = False
    for line in value.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            rows.append(Text("  " + line, style=TEXT))
            continue
        heading = bool(re.match(r"^#{1,6}\s", line))
        line = re.sub(r"^#{1,6}\s+", "", line)
        line = re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)
        text = Text(style=TEXT)
        for part in re.split(r"(`[^`]+`|\*\*[^*]+\*\*)", line):
            if part.startswith("**") and part.endswith("**"):
                text.append(part[2:-2], style="bold")
            elif part.startswith("`") and part.endswith("`"):
                text.append(part[1:-1], style=ACCENT)
            else:
                text.append(part, style="bold" if heading else None)
        rows.append(text)
    return Group(*rows)


def headline(args):
    for key in ("path", "command", "pattern", "action"):
        if key in args:
            return str(args[key]).replace("\n", " ")[:160]
    return ""


@dataclass
class ToolRow:
    name: str
    args: dict
    started: float
    elapsed: float = 0
    output: str = ""
    status: str = "Running"
    diff: str = ""
    expanded: bool = False

    def summary(self):
        if self.name == "list_directories":
            lines = self.output.splitlines()
            if len(lines) > 1 and lines[1].strip().startswith("No visible files or directories"):
                return "No visible files or directories"
        if self.name == "plan":
            action = self.args.get("action", "list")
            if action == "add":
                return str(self.args.get("content", "Step added"))
            listing = re.search(r"Plan \(([^)]+)\)", self.output)
            return listing.group(1) if listing else self.output.split("\n")[0]
        if self.name == "read_file":
            return f"Read {len(self.output.splitlines())} lines"
        if self.name in {"write_file", "edit_file"}:
            return self.output.split("\n")[0].removeprefix("Success: ")
        if self.name == "run_command":
            match = re.search(r"Exit code: (\d+)", self.output)
            return f"exit {match.group(1)}" if match else f"{len(self.output.splitlines())} lines of output"
        return f"{len(self.output.splitlines())} lines of output"


class Transcript:
    def __init__(self):
        self.entries = []
        self.active = None
        self.streaming = None
        self.busy = False
        self.tokens = 0
        self.started = 0
        self.failed = False

    def handle(self, event):
        kind, data = event.type, event.data
        if kind == Event.AGENT_START:
            self.started = time.monotonic()
            self.busy = True
            self.failed = False
            self.entries.append({"user": data["message"]})
        elif kind == Event.TEXT_DELTA:
            if self.streaming is None:
                self.streaming = {"text": ""}
                self.entries.append(self.streaming)
            self.streaming["text"] += data["content"]
        elif kind == Event.TEXT_COMPLETE:
            if self.streaming is None:
                self.entries.append({"text": data["content"]})
            else:
                self.streaming["text"] = data["content"]
            self.streaming = None
        elif kind == Event.TOOL_CALL_START:
            self.streaming = None
            self.active = ToolRow(data["name"], dict(data["arguments"]), time.monotonic())
            self.entries.append(self.active)
        elif kind == Event.TOOL_CALL_COMPLETE:
            if self.active:
                row = self.active
                row.output = str(data["output"])
                row.elapsed = time.monotonic() - row.started
                failed = row.output.startswith(("Error", "Tool execution failed", "Cancelled", "🚫"))
                failed = failed or bool(re.search(r"Exit code: [1-9]\d*", row.output))
                failed = failed or "--- stderr ---" in row.output
                row.status = "Error" if failed else "Done"
                row.expanded = failed
                self.active = None
        elif kind == Event.TOKEN_USAGE:
            self.tokens = data["total_tokens"]
        elif kind == Event.AGENT_ERROR:
            self.failed = True
            self.entries.append({"error": data["error"]})
        elif kind == Event.CONTEXT_PRUNED:
            self.entries.append({"note": f"Context compacted · {data['n_pruned']} old outputs cleared"})
        elif kind == Event.AGENT_END:
            self.busy = False
            self.streaming = None

    def change(self, path, diff, created):
        if self.active:
            self.active.diff = diff

    def toggle_details(self):
        rows = [entry for entry in self.entries if isinstance(entry, ToolRow)]
        if rows:
            rows[-1].expanded = not rows[-1].expanded

    def render(self, width=100):
        parts = []
        for entry in self.entries:
            if isinstance(entry, ToolRow):
                table = Table.grid(expand=True, padding=(0, 1))
                table.add_column(ratio=1, overflow="ellipsis", no_wrap=True)
                table.add_column(width=7)
                table.add_column(width=6, justify="right")
                failed = entry.status == "Error"
                label = Text("× " if failed else "✓ " if entry.status == "Done" else "· ",
                             style=ERROR if failed else MUTED)
                label.append(entry.name, style=TEXT)
                detail = headline(entry.args)
                if detail:
                    label.append("  " + detail, style=MUTED)
                elapsed = time.monotonic() - entry.started if entry.status == "Running" else entry.elapsed
                table.add_row(label, Text(entry.status, style=ERROR if failed else MUTED),
                              Text(f"{elapsed:.1f}s", style=MUTED))
                parts.append(table)
                if entry.output:
                    parts.append(Text("  └ " + _preview(entry.summary(), 180, 1), style=MUTED))
                if entry.expanded:
                    parts.append(Padding(Text(_preview(entry.diff or entry.output, 12000, 100),
                                                   style=TEXT), (0, 4)))
            elif "user" in entry:
                parts.extend([Text(""), Text("You", style=MUTED),
                              Text(entry["user"], style=TEXT), Text("")])
            elif "text" in entry:
                parts.extend([Text(""), prose(entry["text"]), Text("")])
            elif "error" in entry:
                parts.append(Text(entry["error"], style=ERROR))
            else:
                parts.append(Text(entry["note"], style=MUTED))
        return Group(*parts)
