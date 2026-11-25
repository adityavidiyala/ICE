"""Thread-local hooks for keeping tool output inside the terminal application."""
from contextvars import ContextVar

change_handler = ContextVar("ice_change_handler", default=None)
approval_handler = ContextVar("ice_approval_handler", default=None)
change_record_handler = ContextVar("ice_change_record_handler", default=None)


def present_change(path, diff="", created=False):
    handler = change_handler.get()
    if handler is None:
        return False
    handler(path, diff, created)
    return True


def record_file_change(path, before, after, created=False):
    """Record completed writes separately from proposed previews."""
    handler = change_record_handler.get()
    if handler is not None:
        from pathlib import Path
        handler(str(Path(path).resolve()), before, after, created)
