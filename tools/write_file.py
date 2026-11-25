from pathlib import Path

from rich.console import Console

from utils.diff import compute_diff, render_diff
from ui.presentation import present_change, record_file_change
from rich.text import Text

_console = Console()


def write_file(path: str, content: str) -> str:
    """Write content to a file automatically, showing a change preview.

    For new files the content is previewed as a creation summary.
    For existing files a full unified diff is rendered before any change is applied.

    Args:
        path: Path to the file to write (created if it does not exist).
        content: Full content to write to the file.
    """
    file_path = Path(path)
    new_lines = len(content.splitlines())
    created = not file_path.exists()
    old_content = ""

    # ── New file: show creation summary ──────────────────────────────────
    if not file_path.exists():
        if not present_change(path, created=True):
            _console.print(Text(f"  New file · {path} · {new_lines} lines", style="#777e89"))

    # ── Existing file: show unified diff ──────────────────────────────────────
    else:
        try:
            old_content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading existing file for diff: {e}"

        diff = compute_diff(old_content, content, path)

        if not diff:
            return f"No changes — file content is identical to what already exists at {path}."

        if not present_change(path, diff):
            render_diff(diff, path, _console)


    # ── Apply ──────────────────────────────────────────────────────────────────
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        record_file_change(path, old_content, content, created)
        return f"Success: Wrote {new_lines} lines to {path}"
    except Exception as e:
        return f"Error writing file: {e}"
