import os
import re
from pathlib import Path

# File extensions and names that are almost certainly binary / not worth grepping
_BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg',
    '.pdf', '.zip', '.tar', '.gz', '.rar', '.7z',
    '.exe', '.dll', '.so', '.dylib', '.bin', '.obj',
    '.mp3', '.mp4', '.wav', '.avi', '.mov',
    '.pyc', '.pyd', '.whl',
    '.ttf', '.otf', '.woff', '.woff2',
}

_SKIP_DIRS = {'node_modules', '__pycache__', '.git', '.venv', 'venv', '.next', 'dist', 'build'}


def _is_binary(path: Path) -> bool:
    if path.suffix.lower() in _BINARY_EXTENSIONS:
        return True
    try:
        with open(path, 'rb') as f:
            return b'\x00' in f.read(8192)
    except Exception:
        return True


def _walk_text_files(root: Path, max_files: int = 500) -> list[Path]:
    """Recursively collect text files, skipping known binary/junk dirs."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not (Path(dirpath) / d).is_symlink()]
        for name in filenames:
            if name.startswith('.'):
                continue
            fp = Path(dirpath) / name
            if not fp.is_symlink() and not _is_binary(fp):
                files.append(fp)
            if len(files) >= max_files:
                return files
    return files


def grep(pattern: str, path: str = '.', case_insensitive: bool = False) -> str:
    """Search files for a regex pattern and return matching lines with file paths and line numbers.

    Args:
        pattern: Regular expression pattern to search for.
        path: File or directory path to search in (default: current directory).
        case_insensitive: If True, perform a case-insensitive search.
    """
    search_path = Path(path).resolve()

    if not search_path.exists():
        return f"Error: Path does not exist: {path}"

    try:
        flags = re.IGNORECASE if case_insensitive else 0
        compiled = re.compile(pattern, flags)
    except re.error as e:
        return f"Error: Invalid regex pattern — {e}"

    files = [search_path] if search_path.is_file() else _walk_text_files(search_path)

    output_lines: list[str] = []
    total_matches = 0

    for fp in files:
        try:
            content = fp.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue

        file_header_added = False
        for i, line in enumerate(content.splitlines(), start=1):
            if compiled.search(line):
                total_matches += 1
                if not file_header_added:
                    try:
                        rel = fp.relative_to(search_path if search_path.is_dir() else search_path.parent)
                    except ValueError:
                        rel = fp
                    output_lines.append(f"── {rel} ──")
                    file_header_added = True
                output_lines.append(f"  {i}: {line}")

        if file_header_added:
            output_lines.append("")

    if not output_lines:
        return f'No matches found for pattern: "{pattern}"'

    output_lines.append(f"[{total_matches} match(es) across {len(files)} file(s) searched]")
    return "\n".join(output_lines)
