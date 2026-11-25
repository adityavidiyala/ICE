import subprocess
from safety.inspection import inspect_command, CommandTier
from ui.presentation import approval_handler


def run_command(command: str, timeout: int = 120) -> str:
    """Execute a system command in the terminal (PowerShell).

    Every command — including compound ones chained with ; && || | — is
    inspected segment-by-segment before execution:

      BLOCKED  → Hard-blocked, never executed (matches a dangerous pattern).
      PROMPT   → Pauses and asks the user for explicit y/n approval.
      SAFE     → Auto-executed (all segments are read-only / known-safe).

    Args:
        command: The shell command to run.
        timeout: Maximum seconds to wait before killing the process (default 120).
    """
    result = inspect_command(command)

    # ── Tier 1: BLOCKED ───────────────────────────────────────────────────────
    if result.tier == CommandTier.BLOCKED:
        lines = [
            "🚫 Command BLOCKED by safety filters.",
            f"   Reason  : {result.reason}",
        ]
        if result.flagged_segment:
            lines.append(f"   Segment : {result.flagged_segment!r}")
        return "\n".join(lines)

    # ── Tier 2: PROMPT — requires explicit user approval ──────────────────────
    if result.tier == CommandTier.PROMPT:
        handler = approval_handler.get()
        if handler is not None:
            if not handler(command, result):
                return "Cancelled: User denied permission to run command."
        elif not _request_approval(command, result):
            return "Cancelled: User denied permission to run command."

    # Keep execution output in the tool result; the UI owns its presentation.
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
        )
        output = proc.stdout
        if proc.stderr:
            output += f"\n--- stderr ---\n{proc.stderr}"
        if proc.returncode != 0:
            output += f"\nExit code: {proc.returncode}"
        return output.strip() or "Command executed successfully with no output."
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds. It may be waiting for input — rewrite it to be non-interactive."
    except Exception as e:
        return f"Error executing command: {e}"


def _request_approval(command, result):
    """Fallback for use outside the interactive application."""
    print("Command requires approval:")
    print(command)
    print(result.reason)
    try:
        return input("Approve? (y/n): ").strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False
