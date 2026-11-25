from pathlib import Path

from rich.console import Console

from utils.diff import compute_diff, render_diff, diff_stats
from ui.presentation import present_change, record_file_change

_console = Console()


def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Edit a file by replacing an exact string match, with a diff preview before applying.

    Shows a unified diff of the proposed change, then applies it automatically.

    Args:
        path: Path to the file to edit.
        old_string: The exact string to find and replace (whitespace-sensitive).
        new_string: The string to substitute in place of old_string.
        replace_all: If True, replace every occurrence; otherwise only the first.
    """
    file_path = Path(path)

    if not file_path.exists():
        return f"Error: File not found at {path}"

    try:
        old_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"

    # ── Validate the match ─────────────────────────────────────────────────────
    occurrence_count = old_content.count(old_string)

    if occurrence_count == 0:
        return (
            "Error: old_string not found in file. "
            "It must match exactly, including whitespace and indentation."
        )
    if occurrence_count > 1 and not replace_all:
        return (
            f"Error: old_string found {occurrence_count} times. "
            "Provide more surrounding context to make it unique, or set replace_all=True."
        )

    # ── Compute new content ────────────────────────────────────────────────────
    replace_count = -1 if replace_all else 1
    new_content = old_content.replace(old_string, new_string, replace_count)

    # ── Diff preview ──────────────────────────────────────────────────────────
    diff = compute_diff(old_content, new_content, path)
    added, removed = diff_stats(diff)

    if not present_change(path, diff):
        render_diff(diff, path, _console)

    n_replaced = occurrence_count if replace_all else 1

    # ── Apply ──────────────────────────────────────────────────────────────────
    try:
        file_path.write_text(new_content, encoding="utf-8")
        record_file_change(path, old_content, new_content)
        return f"Success: Replaced {n_replaced} occurrence(s) in {path}  (+{added} -{removed} lines)"
    except Exception as e:
        return f"Error writing file: {e}"
