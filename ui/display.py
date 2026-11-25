from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.padding import Padding

from agent.events import AgentEvent, AgentEventType
from config import Config
from ui.console import console


def _preview(value: str, max_chars: int = 800, max_lines: int = 20) -> str:
    """Bound terminal output by both characters and lines."""
    clipped = "\n".join(value[:max_chars].splitlines()[:max_lines])
    if len(value) > max_chars or len(value.splitlines()) > max_lines:
        clipped += "\n...[output truncated for display]"
    return clipped


def _argument_summary(args: dict) -> str:
    """Keep file payloads out of the UI without changing tool arguments."""
    parts = []
    for key, value in args.items():
        if key in {"content", "old_string", "new_string"} and isinstance(value, str):
            parts.append(f"{key}=<{len(value):,} characters, {len(value.splitlines())} lines>")
        else:
            parts.append(f"{key}={value!r}")
    return _preview(", ".join(parts), max_chars=400, max_lines=5)


# ── Plan renderer ─────────────────────────────────────────────────────────────

_PLAN_MARKERS = {
    "completed": ("✔", "green",  "dim strike"),
    "pending":   ("☐", "yellow", ""),
}

def _render_plan_table(output_text: str) -> Table | None:
    """Parse the plan listing text into a Rich table. Returns None if no steps."""
    lines = output_text.strip().splitlines()
    # Expect lines like: "  [x] [abc12345] Do something"
    rows = [l for l in lines if l.strip().startswith("[")]
    if not rows:
        return None

    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(no_wrap=True, width=2)   # marker
    table.add_column(style="dim", no_wrap=True, width=10)  # id
    table.add_column(ratio=1)                 # content

    for row in rows:
        row = row.strip()
        # Pattern: [x] [id] content  OR  [ ] [id] content
        done = row.startswith("[x]")
        marker_sym, marker_style, content_style = _PLAN_MARKERS["completed" if done else "pending"]
        # Strip the two bracket groups
        rest = row[3:].strip()           # remove [x] or [ ]
        if rest.startswith("["):
            bracket_end = rest.index("]")
            step_id = rest[1:bracket_end]
            content = rest[bracket_end + 1:].strip()
        else:
            step_id = ""
            content = rest
        table.add_row(
            Text(marker_sym, style=marker_style),
            Text(f"[{step_id}]", style="dim"),
            Text(content, style=content_style),
        )
    return table


def render_banner():
    """Renders the ICE welcome banner in the terminal."""
    banner_text = """
    █▀ ██████╗█████████╗
    ██╗██╔═══╝██╔════╝
    ██║██║    █████╗  
    ██║██║    ██╔══╝  
    ██║╚██████╗███████╗
    ╚═╝ ╚═════╝╚══════╝
    Interactive Coding Environment v0.1.0
    """
    from pathlib import Path
    console.print(Text(banner_text, style="bold cyan"))
    console.print(Text(f"  Model  {Config.MODEL}\n  Folder {Path.cwd()}", style="dim"))
    console.print(Text("\n  /help commands  ·  /expand last tool  ·  ctrl+c interrupt  ·  ctrl+d quit\n", style="blue"))


def render_event(event: AgentEvent):
    """Dispatches a single AgentEvent to the appropriate Rich renderer."""
    if event.type == AgentEventType.AGENT_START:
        label = Text("\n❯ ", style="bold cyan")
        label.append(event.data['message'], style="default")
        console.print(label)

    elif event.type == AgentEventType.TOOL_CALL_START:
        tool_name = event.data['name']
        args = event.data['arguments']
        console.print(Text(f"\n◇ {tool_name}", style="bold cyan"))

        # For run_command, preview the safety tier of the command before it runs
        if tool_name == 'run_command' and 'command' in args:
            from safety.inspection import inspect_command, CommandTier
            cmd = args['command']
            result = inspect_command(cmd)
            tier_styles = {
                CommandTier.SAFE:    ("🟢", "green",  "SAFE"),
                CommandTier.PROMPT:  ("🟡", "yellow", "NEEDS APPROVAL"),
                CommandTier.BLOCKED: ("🔴", "red",    "BLOCKED"),
            }
            icon, colour, label = tier_styles[result.tier]
            preview = Text(f"   {icon} {label}  ", style=colour)
            preview.append(_preview(repr(cmd), 400, 5), style="dim")
            console.print(preview)
            if len(result.segments) > 1:
                segments = "\n".join(f"[{i}] {seg}" for i, seg in enumerate(result.segments, 1))
                console.print(Text(_preview(segments, 400, 5), style="dim"))
        else:
            console.print(Text(_argument_summary(args), style="dim"))

    elif event.type == AgentEventType.TOOL_CALL_COMPLETE:
        tool_name = event.data['name']
        out = str(event.data['output'])

        # Plan tool gets its own rich table renderer
        if tool_name == 'plan':
            header_line = out.splitlines()[0] if out.splitlines() else ""
            plan_table = _render_plan_table(out)
            title = Text("📋 ", style="bold yellow")
            title.append("Plan", style="bold yellow")
            if header_line.startswith("Plan ("):
                title.append(f"  {header_line[5:]}", style="dim")
            if plan_table:
                console.print(Padding(plan_table, (0, 2)))
            else:
                console.print(Padding(Text(_preview(out)), (0, 2)))

        else:
            # Generic tool result — truncate if too long
            console.print(Padding(Text(_preview(out), style="dim"), (0, 2)))

    elif event.type == AgentEventType.TEXT_COMPLETE:
        console.print(Text("\n◆ ICE", style="bold cyan"))
        console.print(Padding(Markdown(event.data['content']), (0, 2)))

    elif event.type == AgentEventType.TOKEN_USAGE:
        tokens = event.data['total_tokens']
        turn   = event.data['turn']
        # Colour-code based on how close we are to the prune threshold (80k)
        from context.manager import PRUNE_THRESHOLD
        pct = tokens / PRUNE_THRESHOLD
        colour = "green" if pct < 0.6 else "yellow" if pct < 0.9 else "red"
        bar_width = 20
        filled = min(bar_width, int(pct * bar_width))
        bar = "█" * filled + "░" * (bar_width - filled)
        console.print(
            f"  [dim]turn {turn}  [{colour}]{bar}[/{colour}]  "
            f"[{colour}]{tokens:,}[/{colour}][dim] / {PRUNE_THRESHOLD:,} tokens[/dim]"
        )

    elif event.type == AgentEventType.CONTEXT_PRUNED:
        n       = event.data['n_pruned']
        before  = event.data['tokens_before']
        console.print(
            f"\n  [bold yellow]✂  Context pruned[/bold yellow]  "
            f"[dim]cleared {n} old tool output(s)  ({before:,} tokens → compacted)[/dim]"
        )

    elif event.type == AgentEventType.AGENT_ERROR:
        console.print(Text(f"\n⚠️  Error: {event.data['error']}", style="bold red"))
