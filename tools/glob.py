import fnmatch
import os
from pathlib import Path

_SKIP_DIRS = {'node_modules', '__pycache__', '.git', '.venv', 'venv', '.next', 'dist', 'build'}


def glob(pattern: str, path: str = '.') -> str:
    """Find files matching a glob pattern. Supports ** for recursive search.

    Args:
        pattern: Glob pattern to match (e.g. '**/*.py', '*.txt', 'src/**/*.js').
        path: Directory path to search in (default: current directory).
    """
    search_path = Path(path).resolve()

    if not search_path.exists():
        return f"Error: Path does not exist: {path}"

    if not search_path.is_dir():
        return f"Error: Path is not a directory: {path}"

    try:
        # pathlib.glob handles ** natively
        matches = sorted(search_path.glob(pattern))
    except Exception as e:
        return f"Error: Glob failed — {e}"

    # Filter out skip dirs from results
    filtered = [
        p for p in matches
        if not any(part in _SKIP_DIRS for part in p.parts)
        and not p.is_symlink() and p.resolve().is_relative_to(search_path)
    ]

    if not filtered:
        return f'No files matched pattern: "{pattern}" in {path}'

    lines: list[str] = []
    for p in filtered:
        try:
            rel = p.relative_to(search_path)
        except ValueError:
            rel = p
        suffix = "/" if p.is_dir() else ""
        lines.append(f"{rel}{suffix}")

    lines.append(f"\n[{len(filtered)} match(es)]")
    return "\n".join(lines)
