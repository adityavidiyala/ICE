"""
plan.py — Session-scoped task planner tool for ICE.

State lives at module level so it persists across all tool calls
within a single agent session (the same Python process lifetime).
"""
import uuid
from typing import Optional

# --- Module-level session state ---
_steps: dict[str, str] = {}   # step_id -> content
_status: dict[str, str] = {}  # step_id -> 'pending' | 'completed'

PENDING = 'pending'
COMPLETED = 'completed'


def _listing() -> str:
    if not _steps:
        return "No plan steps."
    completed = sum(1 for s in _status.values() if s == COMPLETED)
    lines = [f"Plan ({completed}/{len(_steps)} completed):"]
    for step_id, content in _steps.items():
        marker = 'x' if _status[step_id] == COMPLETED else ' '
        lines.append(f"  [{marker}] [{step_id}] {content}")
    return "\n".join(lines)


def snapshot() -> dict:
    """Return a serialisable snapshot of the current plan (used by the UI renderer)."""
    steps = [
        {"id": sid, "content": content, "status": _status[sid]}
        for sid, content in _steps.items()
    ]
    return {
        "steps": steps,
        "completed": sum(1 for s in steps if s["status"] == COMPLETED),
        "total": len(steps),
    }


def plan(action: str, content: Optional[str] = None, id: Optional[str] = None) -> str:
    """Manage a multi-step task plan for the current session.

    Use this to track progress on complex tasks. Call 'add' to register each
    step at the start, 'complete' as each step finishes, 'list' to review
    progress, and 'clear' to reset. Reserve this tool for complex tasks that
    benefit from tracking. Use the exact IDs returned by 'add' or 'list' for
    'complete'; IDs are not sequential numbers. Mark steps complete only after
    carrying out their work.

    Args:
        action: One of 'add', 'complete', 'list', or 'clear'.
        content: The step description (required for 'add').
        id: Exact step ID returned by this tool (required for 'complete'; never guess).
    """
    action = action.lower().strip()

    if action == 'add':
        if not content:
            return "Error: 'content' is required for the 'add' action."
        step_id = str(uuid.uuid4())[:8]
        _steps[step_id] = content
        _status[step_id] = PENDING
        return f"Added step [{step_id}]: {content}\n\n{_listing()}"

    elif action == 'complete':
        if not id:
            return "Error: 'id' is required for the 'complete' action."
        if id not in _steps:
            return (f"Error: Plan step not found: '{id}'. Use an exact ID from the current plan below.\n\n"
                    f"{_listing()}")
        _status[id] = COMPLETED
        return f"Completed step [{id}]: {_steps[id]}\n\n{_listing()}"

    elif action == 'list':
        return _listing()

    elif action == 'clear':
        count = len(_steps)
        _steps.clear()
        _status.clear()
        return f"Cleared {count} plan step(s)."

    else:
        return f"Error: Unknown action '{action}'. Valid actions: add, complete, list, clear."
