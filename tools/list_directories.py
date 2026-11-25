from pathlib import Path

_SKIP_DIRS = {'node_modules', '__pycache__', '.git', '.venv', 'venv', '.next', 'dist', 'build'}


def list_directories(path: str = '.') -> str:
    """List the contents of a directory, showing files and subdirectories in a tree layout.

    Hidden entries and common generated directories are excluded. Inspect an
    unfamiliar workspace once; reuse the result until relevant contents change.
    A no-visible-entries result is sufficient to start creating project files.

    Args:
        path: Directory path to list (default: current directory).
    """
    dir_path = Path(path).resolve()

    if not dir_path.exists():
        return f"Error: Path does not exist: {path}"

    if not dir_path.is_file():
        # It's a directory — show a shallow tree (1 level deep by default)
        return _render_tree(dir_path, prefix="", depth=0, max_depth=2)
    else:
        return f"Error: Path is a file, not a directory: {path}"


def _render_tree(path: Path, prefix: str, depth: int, max_depth: int) -> str:
    lines: list[str] = []

    if depth == 0:
        lines.append(f"{path.name}/")

    try:
        items = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        lines.append(f"{prefix}  [permission denied]")
        return "\n".join(lines)

    # Separate dirs and files, skip hidden and junk dirs
    dirs = [i for i in items if i.is_dir() and i.name not in _SKIP_DIRS and not i.name.startswith('.')]
    files = [i for i in items if i.is_file() and not i.name.startswith('.')]
    visible = [item for item in dirs + files if not item.is_symlink()]

    if not visible:
        lines.append(f"{prefix}  No visible files or directories (hidden and excluded entries omitted).")
        return "\n".join(lines)

    for idx, item in enumerate(visible):
        is_last = idx == len(visible) - 1
        connector = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")

        if item.is_dir():
            lines.append(f"{prefix}{connector}{item.name}/")
            if depth < max_depth and item.name not in _SKIP_DIRS:
                subtree = _render_tree(item, child_prefix, depth + 1, max_depth)
                if subtree:
                    lines.append(subtree)
        else:
            # Show file size for context
            try:
                size = item.stat().st_size
                size_str = _fmt_size(size)
            except Exception:
                size_str = ""
            lines.append(f"{prefix}{connector}{item.name}  [{size_str}]")

    return "\n".join(lines)


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / 1024**2:.1f} MB"
