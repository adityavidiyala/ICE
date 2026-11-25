from pathlib import Path

def read_file(path: str, offset: int = 1, limit: int = None) -> str:
    """Read the contents of a file and return it as text with line numbers.
    Args:
        path: Path to the file to read.
        offset: 1-indexed line number to start reading from.
        limit: Maximum number of lines to read.
    """
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return f"Error: File not found at {path}"

    try:
        content = file_path.read_text(encoding='utf-8')
        lines = content.splitlines()
        
        start_idx = max(0, offset - 1)
        end_idx = start_idx + limit if limit else len(lines)
        
        selected_lines = lines[start_idx:end_idx]
        formatted_lines = [f"{i + 1:6}|{line}" for i, line in enumerate(selected_lines, start=start_idx)]
        
        return "\n".join(formatted_lines)
    except Exception as e:
        return f"Error reading file: {e}"