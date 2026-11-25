"""Session-local history of completed file writes, including full numbered diffs."""
import re
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Group
from rich.text import Text

from ui.transcript import TEXT, MUTED, ACCENT
from utils.diff import compute_diff, diff_stats


@dataclass
class Revision:
    before: str
    after: str
    diff: str
    created: bool


@dataclass
class FileChanges:
    path: str
    baseline: str
    latest: str
    revisions: list = field(default_factory=list)
    net_diff: str = ""


def numbered_diff(diff):
    """Show original and updated line numbers beside each unified-diff record."""
    lines = [Text("  OLD   NEW   CHANGE", style=MUTED)]
    old = new = None
    for line in diff.splitlines():
        match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            old, new = map(int, match.groups())
            lines.append(Text(line, style=MUTED))
        elif old is None:
            continue  # File names are already in the viewer heading.
        elif line.startswith("-"):
            lines.append(Text(f"{old:5}       - {line[1:]}", style="#bb9393"))
            old += 1
        elif line.startswith("+"):
            lines.append(Text(f"      {new:5} + {line[1:]}", style="#9db8a3"))
            new += 1
        elif line.startswith(" "):
            lines.append(Text(f"{old:5} {new:5}   {line[1:]}", style=TEXT))
            old += 1
            new += 1
        else:
            lines.append(Text("            " + line, style=MUTED))
    return Group(*lines)


class ChangeStore:
    def __init__(self):
        self.files = {}

    def record(self, path, before, after, created=False):
        if before == after and not created:
            return
        key = str(Path(path).resolve())
        file = self.files.get(key)
        if file is None:
            file = FileChanges(key, before, after)
            self.files[key] = file
        file.latest = after
        file.revisions.append(Revision(before, after, compute_diff(before, after, key), created))
        file.net_diff = compute_diff(file.baseline, file.latest, key)

    def display_path(self, path):
        try:
            return str(Path(path).relative_to(Path.cwd()))
        except ValueError:
            return path

    def render_file(self, file, index, revision=None):
        if revision is None:
            diff = file.net_diff
            label = f"Combined session diff · {len(file.revisions)} edits"
        else:
            item = file.revisions[revision - 1]
            diff = item.diff
            label = f"Edit {revision}/{len(file.revisions)}" + (" · new file" if item.created else "")
        added, removed = diff_stats(diff)
        return Group(
            Text(f"\n{index}. {self.display_path(file.path)}", style=ACCENT),
            Text(f"{label} · +{added} -{removed}\n", style=MUTED),
            numbered_diff(diff) if diff else Text("No net line changes. Individual edits remain available.", style=MUTED),
        )

    def render(self, selection=""):
        parts = [Text("\nChanges · current session", style=ACCENT),
                 Text("/changes N — file diff · /changes N M — edit M\n"
                      "/changes all — all diffs · /changes — file list · /chat or Esc — return\n",
                      style=MUTED)]
        files = list(self.files.values())
        if not files:
            return Group(*parts, Text("No completed file changes recorded yet.", style=MUTED))
        if not selection:
            for i, file in enumerate(files, 1):
                added, removed = diff_stats(file.net_diff)
                parts.append(Text(f"{i:3}. {self.display_path(file.path)}  ·  {len(file.revisions)} edits  ·  +{added} -{removed}", style=TEXT))
            parts.append(Text("\nNumbers stay stable for this session. Sandbox changes remain staged until /apply.", style=MUTED))
        elif selection == "all":
            parts.extend(self.render_file(file, i) for i, file in enumerate(files, 1))
        else:
            try:
                numbers = selection.split()
                if len(numbers) not in (1, 2) or any(not n.isdecimal() for n in numbers):
                    raise ValueError
                index = int(numbers[0])
                if not 1 <= index <= len(files):
                    raise ValueError
                file = files[index - 1]
                revision = int(numbers[1]) if len(numbers) == 2 else None
                if revision is not None and not 1 <= revision <= len(file.revisions):
                    raise ValueError
                parts.append(self.render_file(file, index, revision))
            except ValueError:
                parts.append(Text("Unknown file/edit number. Use /changes to see the files, then /changes N or /changes N M.", style=MUTED))
        return Group(*parts)
