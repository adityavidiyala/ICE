"""
safety/inspection.py — Compound command inspection for ICE.

Splits chained shell commands and checks every segment independently
against dangerous and safe pattern libraries. A compound command is only
as safe as its least safe part.
"""
import re
import shlex
from dataclasses import dataclass
from enum import Enum


# ── Tier classification ────────────────────────────────────────────────────────

class CommandTier(Enum):
    """Safety tier assigned to a command after inspection."""
    BLOCKED  = "blocked"   # Hard-blocked — never execute
    PROMPT   = "prompt"    # Requires explicit user approval
    SAFE     = "safe"      # Auto-execute (read-only / known-safe)


@dataclass
class InspectionResult:
    tier: CommandTier
    reason: str                    # Human-readable explanation
    segments: list[str]            # Individual segments the command was split into
    flagged_segment: str | None    # The specific segment that caused the tier


# ── Dangerous patterns (BLOCKED tier) ─────────────────────────────────────────
# These are hard-blocked regardless of any approval policy.

DANGEROUS_PATTERNS: list[str] = [
    # Unix filesystem destruction
    r"rm\s+(-rf?|--recursive)\s+[/~]",
    r"rm\s+-rf?\s+\*",
    r"rmdir\s+[/~]",
    # Windows filesystem destruction
    r"rd\s+/s\s+/q\s+[a-zA-Z]:\\",        # rd /s /q C:\
    r"del\s+/f\s+/s\s+/q\s+[a-zA-Z]:\\",  # del /f /s /q C:\
    r"format\s+[a-zA-Z]:",                 # format C:
    r"Remove-Item\s+-Recurse\s+-Force\s+[a-zA-Z]:\\",  # PowerShell nuke
    # Disk operations
    r"dd\s+if=",
    r"mkfs",
    r"fdisk",
    r"parted",
    r"diskpart",
    # System control
    r"shutdown",
    r"reboot",
    r"halt",
    r"poweroff",
    r"init\s+[06]",
    r"Stop-Computer",
    r"Restart-Computer",
    # Permission changes on root
    r"chmod\s+(-R\s+)?777\s+[/~]",
    r"chown\s+-R\s+.*\s+[/~]",
    # Network exposure
    r"nc\s+-l",
    r"netcat\s+-l",
    # Code execution piped from network
    r"curl\s+.*\|\s*(bash|sh|python|powershell)",
    r"wget\s+.*\|\s*(bash|sh|python|powershell)",
    r"iwr\s+.*\|\s*iex",           # PowerShell: Invoke-WebRequest | Invoke-Expression
    r"Invoke-Expression",          # PowerShell IEX — common malware vector
    r"iex\s*[\(\$]",
    # Fork bomb
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;",
    # Registry destruction
    r"reg\s+delete\s+HKLM",
    r"Remove-Item\s+.*HKLM:",
]

# ── Safe patterns (SAFE tier) ─────────────────────────────────────────────────
# Commands that are read-only or harmless. Each segment must match one to be SAFE.

SAFE_PATTERNS: list[str] = [
    # Directory listing / navigation
    r"^(ls|dir|pwd|cd|echo)([\s$]|$)",
    # File reading (not writing)
    r"^(cat|head|tail|less|more|type)([\s$]|$)",
    r"^(wc|file|stat|du|df)([\s$]|$)",
    # Search & locate
    r"^(locate|which|whereis|find)([\s$]|$)",
    r"^(grep|rg|ripgrep|ag)([\s$]|$)",
    # Git read-only
    r"^git\s+(status|log|diff|show|branch|remote|tag|stash list)([\s$]|$)",
    # Package info (read-only)
    r"^(npm|yarn|pnpm)\s+(list|ls|outdated)([\s$]|$)",
    r"^pip\s+(list|show|freeze)([\s$]|$)",
    r"^python\s+-m\s+pip\s+(list|show|freeze)([\s$]|$)",
    # Text processing (read-only)
    r"^(cut|sort|uniq|tr|diff|comm)([\s$]|$)",
    # System info
    r"^(date|cal|uptime|whoami|id|groups|hostname|uname)([\s$]|$)",
    r"^(env|printenv|set)$",
    r"^(ps|top|htop|pgrep|tasklist)([\s$]|$)",
    # Python / node read-only execution
    r"^python\s+--version",
    r"^python3?\s+--version",
    r"^node\s+--version",
    # PowerShell read-only cmdlets
    r"^Get-(Content|ChildItem|Item|Location|Command|Help|Process|Service|Variable)([\s$]|$)",
    r"^(Write-Host|Write-Output|Write-Information)([\s$]|$)",
    r"^Select-(Object|String)([\s$]|$)",
    r"^Where-Object([\s$]|$)",
    r"^Format-(Table|List|Wide)([\s$]|$)",
    r"^Measure-(Object|Command)([\s$]|$)",
    r"^Test-Path([\s$]|$)",
]

# Shell constructs that can conceal writes/execution inside a "safe" prefix
_UNSAFE_CONSTRUCTS = ('>', '<', '$', '`', '&', '{', '}', '(', ')', '@', '\x00')

# Operators that chain segments
_COMMAND_SEPARATORS = {';', '&&', '||', '|'}


# ── Segment splitting ──────────────────────────────────────────────────────────

def split_command_segments(command: str) -> list[str]:
    """Preserve PowerShell quotes and newline boundaries in simple commands.

    This is a conservative scanner, not a full PowerShell parser. Escapes,
    comments and malformed operators require approval rather than guessing.
    """
    segments = []
    start = i = 0
    quote = None
    while i < len(command):
        char = command[i]
        if char == '`' or (ord(char) < 32 and char not in '\r\n\t'):
            raise ValueError("Unsupported escape or control character")
        if quote:
            if char == quote:
                if i + 1 < len(command) and command[i + 1] == quote:
                    i += 2
                    continue
                quote = None
        elif char in "\"'":
            quote = char
        elif char == '#':
            raise ValueError("Comments require PowerShell parsing")
        elif char in ';|\r\n' or command[i:i + 2] == '&&':
            segment = command[start:i].strip()
            if segment:
                segments.append(segment)
            elif char not in '\r\n':
                raise ValueError("Missing command between operators")
            size = 2 if command[i:i + 2] in {'&&', '||', '\r\n'} else 1
            i += size
            start = i
            continue
        i += 1
    if quote:
        raise ValueError("Unbalanced quotes")
    tail = command[start:].strip()
    if tail:
        segments.append(tail)
    elif segments and command.rstrip().endswith(('|', '&')):
        raise ValueError("Missing command after operator")
    return segments


def _known_options(args, flags, values=()):
    """Allow only recognized read-only options; everything after -- is a path."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            return True
        if arg.startswith('-'):
            option = arg.split('=', 1)[0]
            if arg in flags:
                pass
            elif option in values:
                if '=' not in arg:
                    i += 1
                    if i >= len(args) or args[i].startswith('-'):
                        return False
            else:
                return False
        i += 1
    return True


def _safe_git(args):
    if not args:
        return False
    action, rest = args[0], args[1:]
    if action == 'branch':
        flags = {'--list', '-a', '-r', '--all', '--remotes', '-v', '-vv', '--verbose', '--show-current'}
        return (all(arg in flags for arg in rest) or
                ('--list' in rest and _known_options(rest, flags)))
    if action == 'remote':
        return rest in ([], ['-v'], ['--verbose']) or (
            bool(rest) and rest[0] == 'get-url' and
            _known_options(rest[1:], {'--all', '--push'})
        )
    if action == 'tag':
        return not rest or (rest[0] in {'--list', '-l'} and
                            _known_options(rest[1:], {'--list', '-l', '-n'}))
    if action == 'stash':
        return bool(rest) and rest[0] == 'list' and _known_options(rest[1:], {'--oneline', '--no-color'})
    if action not in {'status', 'log', 'diff', 'show'}:
        return False
    flags = {'--short', '-s', '--branch', '-b', '--porcelain', '--porcelain=v1', '--porcelain=v2',
             '--oneline', '--stat', '--shortstat', '--numstat', '--name-only', '--name-status',
             '--cached', '--staged', '--check', '--no-color', '--color=never', '--quiet',
             '--exit-code', '--no-ext-diff', '--no-textconv', '--patch', '-p', '--summary',
             '--graph', '--decorate', '--all', '--reverse'}
    values = {'--max-count', '-n', '--format', '--pretty', '--author', '--since', '--until',
              '--grep', '--date', '--untracked-files', '--ignored', '--unified', '-U'}
    # Common short numeric forms used for log limits and diff context.
    rest = ['--max-count=' + arg[1:] if action == 'log' and re.fullmatch(r'-\d+', arg)
            else '--unified=' + arg[2:] if re.fullmatch(r'-U\d+', arg) else arg for arg in rest]
    return _known_options(rest, flags, values)


# ── Per-segment classifiers ────────────────────────────────────────────────────

def is_dangerous_command(command: str) -> bool:
    """Return True if the command matches any hard-blocked dangerous pattern."""
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def _is_safe_segment(segment: str) -> bool:
    """Return True only if the segment is demonstrably read-only / harmless."""
    # Any shell construct that can hide a write makes the segment unsafe
    if any(c in segment for c in _UNSAFE_CONSTRUCTS):
        return False
    try:
        args = shlex.split(segment, posix=False)
    except ValueError:
        return False
    if not args:
        return False
    name = args[0].lower()
    if name == 'git':
        return _safe_git(args[1:])
    if name in {'python', 'python3', 'node'}:
        if args[1:] == ['--version']:
            return True
        if name in {'python', 'python3'} and args[1:3] == ['-m', 'pip']:
            return len(args) >= 4 and args[3] in {'list', 'show', 'freeze'} and _known_options(
                args[4:], {'--local', '--user', '--all', '--verbose'}, {'--format'})
        return False
    if name == 'pip':
        return len(args) >= 2 and args[1] in {'list', 'show', 'freeze'} and _known_options(
            args[2:], {'--local', '--user', '--all', '--verbose'}, {'--format'})
    if name in {'find', 'measure-command', 'where-object'}:
        return False  # These can execute expressions or perform writes.
    if name == 'sort':
        return not any(arg.lower().startswith('/o') for arg in args[1:]) and _known_options(
            args[1:], {'-n', '-r', '-u', '-f', '-b', '-h', '-V', '--numeric-sort', '--reverse', '--unique'})
    if name in {'rg', 'ripgrep', 'ag', 'grep'}:
        return _known_options(args[1:], {'-n', '-i', '-l', '-L', '-v', '-r', '-R', '-F', '-E',
            '-w', '-x', '-c', '-q', '--files', '--hidden', '--line-number', '--ignore-case',
            '--files-with-matches', '--fixed-strings', '--no-config', '--no-heading'},
            {'-e', '-g', '--glob', '--regexp', '-A', '-B', '-C', '--max-count', '-m'})
    if name == 'get-help' and any(arg.lower().startswith('-online') for arg in args[1:]):
        return False
    for pattern in SAFE_PATTERNS:
        if re.search(pattern, segment, re.IGNORECASE):
            return True
    return False


def is_safe_command(command: str) -> bool:
    """Return True only when every segment of the command is safe."""
    # Check the full pipeline too: splitting must not hide curl | shell patterns.
    if is_dangerous_command(command):
        return False
    try:
        segments = split_command_segments(command)
    except ValueError:
        return False  # Can't parse → can't vouch for it

    return bool(segments) and all(_is_safe_segment(s) for s in segments)


# ── Top-level inspection entry point ──────────────────────────────────────────

def inspect_command(command: str) -> InspectionResult:
    """Classify a full (possibly compound) command into a safety tier.

    Returns an InspectionResult with the tier, a human-readable reason,
    the split segments, and which segment triggered the decision.
    """
    if is_dangerous_command(command):
        return InspectionResult(CommandTier.BLOCKED, "Matches a dangerous pattern.", [command], command)
    try:
        segments = split_command_segments(command)
    except ValueError as exc:
        return InspectionResult(
            tier=CommandTier.PROMPT,
            reason=f"Could not safely parse command: {exc}",
            segments=[command],
            flagged_segment=command,
        )

    # 1. Check every segment for hard-blocked patterns
    for seg in segments:
        if is_dangerous_command(seg):
            return InspectionResult(
                tier=CommandTier.BLOCKED,
                reason=f"Matches a dangerous pattern — execution permanently blocked.",
                segments=segments,
                flagged_segment=seg,
            )

    # 2. If every segment is safe, auto-approve
    all_safe = bool(segments) and all(_is_safe_segment(s) for s in segments)
    if all_safe:
        return InspectionResult(
            tier=CommandTier.SAFE,
            reason="All segments match safe/read-only patterns.",
            segments=segments,
            flagged_segment=None,
        )

    # 3. Otherwise, the command needs user approval
    # Find the first segment that is NOT safe to surface in the prompt
    flagged = next((s for s in segments if not _is_safe_segment(s)), command)
    return InspectionResult(
        tier=CommandTier.PROMPT,
        reason=f"Contains a mutating or unrecognised segment that requires approval.",
        segments=segments,
        flagged_segment=flagged,
    )
