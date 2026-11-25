"""Smoke-test for diff preview functionality — runs non-interactively."""
import sys
from pathlib import Path

# ── Test compute_diff and diff_stats ──────────────────────────────────────────
from utils.diff import compute_diff, diff_stats, render_diff
from rich.console import Console

console = Console()

old = "line one\nline two\nline three\n"
new = "line one\nline TWO (edited)\nline three\nline four (added)\n"

diff = compute_diff(old, new, "test_file.txt")
added, removed = diff_stats(diff)

assert diff != "", "diff should not be empty"
assert added == 2, f"expected 2 added, got {added}"
assert removed == 1, f"expected 1 removed, got {removed}"
print("PASS  compute_diff + diff_stats")

# Render to console (visual only, no assertion)
render_diff(diff, "test_file.txt", console)
print("PASS  render_diff (visual above)")

# ── Test identical content returns no diff ────────────────────────────────────
diff_empty = compute_diff(old, old, "test_file.txt")
assert diff_empty == "", "identical content should produce empty diff"
print("PASS  empty diff for identical content")

# ── Full import chain ─────────────────────────────────────────────────────────
from tools import ICE_TOOLS
from tools.write_file import write_file
from tools.edit_file import edit_file
from ui.prompt import run_repl

print("PASS  all imports OK")
print("Tools:", [t.__name__ for t in ICE_TOOLS])
print()
print("All diff tests passed!")
