"""Conservative approval policy for the sandbox's POSIX shell."""
import re
import shlex
from safety.inspection import CommandTier, InspectionResult, is_dangerous_command, _safe_git


def inspect_shell(command):
    if is_dangerous_command(command):
        return InspectionResult(CommandTier.BLOCKED, "Matches a dangerous pattern.", [command], command)
    # No attempt to interpret expansions, pipelines, comments, or substitutions.
    if re.search(r'[\r\n;&|<>$`\\(){}!#\x00]', command):
        return InspectionResult(CommandTier.PROMPT, "Shell syntax requires approval.", [command], command)
    try:
        args = shlex.split(command)
    except ValueError:
        args = []
    safe = False
    if args:
        name, rest = args[0], args[1:]
        if name in {'pwd', 'ls', 'cat', 'head', 'tail', 'wc', 'echo', 'whoami'}:
            safe = True
        elif name == 'git':
            safe = _safe_git(rest)
        elif name in {'node', 'python', 'python3'} and rest == ['--version']:
            safe = True
        elif name == 'node' and len(rest) == 2 and rest[0] == '--check' and not rest[1].startswith('-'):
            safe = True
    return InspectionResult(CommandTier.SAFE if safe else CommandTier.PROMPT,
                            "Known read-only command." if safe else "Execution requires approval.",
                            [command], None if safe else command)
