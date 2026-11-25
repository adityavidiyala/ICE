"""
utils/diff.py — Unified diff computation and Rich terminal rendering for ICE.

Used by write_file and edit_file to show a preview before applying changes.
"""
import difflib
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


def compute_diff(old_content: str, new_content: str, path: str) -> str:
    """Generate a unified diff string between old and new content."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff_lines = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="\n",
    ))
    # difflib preserves missing trailing newlines on source lines. Separate
    # those records and mark the missing newline so changes remain visible.
    return "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in diff_lines
    )


def diff_stats(diff: str) -> tuple[int, int]:
    """Return (lines_added, lines_removed) counts from a unified diff string."""
    added = removed = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("@@ "):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            added += 1
        elif in_hunk and line.startswith("-"):
            removed += 1
    return added, removed


def render_diff(diff: str, path: str, console: Console, max_lines: int = 12) -> None:
    """Print a syntax-highlighted unified diff panel to the given Rich console."""
    lines = diff.splitlines()
    truncated = False
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True

    added, removed = diff_stats(diff)

    # Build title: filename + +N -N stat
    title = Text()
    title.append(f" {path}", style="bold white")
    title.append("  ")
    title.append(f"+{added}", style="bold green")
    title.append(" ")
    title.append(f"-{removed}", style="bold red")

    body = "\n".join(lines)
    if truncated:
        body += f"\n… ({len(diff.splitlines()) - max_lines} more lines)"

    from rich.padding import Padding
    console.print(Text(f"  {path}  +{added} -{removed}", style="#777e89"))
    console.print(Padding(Text(body, style="#aeb5bf"), (0, 4)))


def ask_approval_for_change(action: str, path: str, console: Console) -> bool:
    """Print a simple approval prompt and return True if user typed 'y'."""
    console.print(
        f"\n[bold yellow]⚠️  Approval required[/bold yellow] — "
        f"[dim]{action}[/dim] [bold]{path}[/bold]"
    )
    try:
        choice = input("  Apply change? (y/n): ").strip().lower()
        return choice == "y"
    except (EOFError, KeyboardInterrupt):
        return False
