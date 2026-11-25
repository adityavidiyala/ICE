"""Copy/filter a project and apply sandbox results with conflict checks."""
import base64
import os
import tempfile
import stat
from pathlib import Path, PurePosixPath

MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 5000
EXCLUDED = {'.git', '.env', '.aws', '.ssh', '.gnupg', '.codex', '.agents',
            '.azure', '.kube', '.docker', '.config', '.gemini', '.secrets',
            '.git-credentials', '.gitconfig', '.netrc', 'credentials.json', 'secrets.json',
            'service-account.json',
            'node_modules', '.venv', 'venv', '__pycache__', '.pytest_cache',
            '.ice-sandbox', '.npmrc', '.pypirc', 'credentials', 'id_rsa', 'id_ed25519'}


def allowed_path(relative, allow_patterns=False):
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or any(part in {'..', ''} for part in path.parts):
        return False
    if '\\' in relative or ':' in relative:
        return False
    for part in path.parts:
        low = part.lower()
        if part.endswith((' ', '.')) or any(char in part for char in '<>"|'):
            return False
        if not allow_patterns and any(char in part for char in '*?'):
            return False
        if low.split('.')[0] in {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}:
            return False
        if low in EXCLUDED or low.startswith('.env.') or low.endswith(('.env', '.pem', '.key', '.pfx', '.p12')):
            return False
    return True


def host_path(root, relative):
    if not allowed_path(relative):
        raise ValueError(f"Excluded or invalid workspace path: {relative}")
    root = Path(root).resolve()
    target = root.joinpath(*PurePosixPath(relative).parts)
    for part in [target, *target.parents]:
        if part == root:
            break
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError(f"Symlink/junction paths cannot be synchronized: {relative}")
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"Path escapes workspace: {relative}")
    return target


def snapshot(root):
    root = Path(root).resolve()
    files = {}
    size = 0
    for directory, names, filenames in os.walk(root, followlinks=False):
        names[:] = [name for name in names if allowed_path(
            (Path(directory) / name).relative_to(root).as_posix()) and
            not (Path(directory) / name).is_symlink() and
            not getattr(Path(directory) / name, 'is_junction', lambda: False)()]
        for name in filenames:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if not allowed_path(relative) or path.is_symlink() or not path.is_file():
                continue
            target = host_path(root, relative)
            size += target.stat().st_size
            if size > MAX_BYTES or len(files) >= MAX_FILES:
                raise ValueError("Workspace exceeds the sandbox limit (32 MiB / 5,000 files). Narrow the project directory.")
            files[relative] = target.read_bytes()
    return files


def encode_files(files):
    return {name: base64.b64encode(data).decode('ascii') for name, data in files.items()}


def decode_files(payload):
    if not isinstance(payload, dict) or len(payload) > MAX_FILES:
        raise ValueError("Invalid sandbox snapshot")
    files = {}
    size = 0
    for name, value in payload.items():
        if not allowed_path(name) or not isinstance(value, str):
            raise ValueError(f"Invalid sandbox snapshot path: {name}")
        data = base64.b64decode(value, validate=True)
        size += len(data)
        if size > MAX_BYTES:
            raise ValueError("Sandbox snapshot exceeds 32 MiB")
        files[name] = data
    if os.name == 'nt' and len({name.casefold() for name in files}) != len(files):
        raise ValueError('Sandbox snapshot contains Windows filename collisions')
    return files


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError("Workspace must be an existing directory")
        self.baseline = snapshot(self.root)
        self.current = dict(self.baseline)

    def pending(self):
        return sorted(name for name in self.baseline.keys() | self.current.keys()
                      if self.baseline.get(name) != self.current.get(name))

    def apply(self):
        names = self.pending()
        # Validate ALL paths and conflicts before the first host mutation.
        for name in names:
            target = host_path(self.root, name)
            actual = target.read_bytes() if target.is_file() else None
            if target.exists() and not target.is_file():
                raise ValueError(f"Cannot replace a directory: {name}")
            if actual != self.baseline.get(name):
                raise ValueError(f"Host file changed since snapshot: {name}. No changes applied.")
        completed = []
        try:
            for name in names:
                target = host_path(self.root, name)
                # Recheck immediately before writing, in case an editor changed it.
                actual = target.read_bytes() if target.is_file() else None
                if actual != self.baseline.get(name):
                    raise ValueError(f"Host file changed during apply: {name}")
                data = self.current.get(name)
                if data is None:
                    target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # Replace the directory entry rather than modifying a possibly
                    # hard-linked host inode. Never write through a host symlink.
                    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
                    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.ice-apply-', delete=False) as output:
                        temporary = Path(output.name)
                        output.write(data)
                    try:
                        host_path(self.root, name)
                        if mode is not None:
                            temporary.chmod(mode)
                        os.replace(temporary, target)
                    finally:
                        temporary.unlink(missing_ok=True)
                completed.append(name)
                if data is None:
                    self.baseline.pop(name, None)
                else:
                    self.baseline[name] = data
        except Exception as exc:
            raise RuntimeError(f"Apply stopped after {len(completed)} files; remaining changes are still pending: {exc}") from exc
        return completed
