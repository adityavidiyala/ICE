"""Manifest-based installation plans and Windows setup instructions."""
import hashlib
import json
import shlex
from pathlib import Path

from sandbox.workspace import host_path


def install_plan(root, manager='auto', path='.', allow_scripts=False):
    root = Path(root).resolve()
    directory = root if path in {'.', '/workspace', '/workspace/'} else host_path(root, path.removeprefix('/workspace/'))
    if not directory.is_dir():
        raise ValueError('Project directory does not exist')
    if manager not in {'auto', 'npm', 'pip'}:
        raise ValueError("Manager must be auto, npm or pip")
    has_npm = (directory / 'package.json').is_file()
    has_pip = (directory / 'requirements.txt').is_file()
    if manager == 'auto':
        if has_npm and has_pip:
            raise ValueError('Both package.json and requirements.txt exist. Choose npm or pip explicitly.')
        manager = 'npm' if has_npm else 'pip' if has_pip else ''
    if not manager or (manager == 'npm' and not has_npm) or (manager == 'pip' and not has_pip):
        raise ValueError('No matching manifest found. Create package.json or requirements.txt first.')
    manifests = ['package.json', 'npm-shrinkwrap.json', 'package-lock.json'] if manager == 'npm' else ['requirements.txt']
    digest = hashlib.sha256()
    for name in manifests:
        file = directory / name
        if file.exists():
            file = host_path(root, file.relative_to(root).as_posix())
            digest.update(name.encode() + b'\0' + file.read_bytes())
    if manager == 'npm':
        json.loads((directory / 'package.json').read_text(encoding='utf-8'))
        locked = any((directory / name).exists() for name in ('package-lock.json', 'npm-shrinkwrap.json'))
        argv = ['/usr/bin/npm', 'ci' if locked else 'install', '--no-audit', '--no-fund', '--cache', '/tmp/ice-npm-cache']
        if not allow_scripts:
            argv.append('--ignore-scripts')
    else:
        argv = ['/workspace/.venv/bin/python', '-I', '-m', 'pip', 'install', '--no-input',
                '--disable-pip-version-check', '--no-cache-dir', '-r', 'requirements.txt']
        if not allow_scripts:
            argv.append('--only-binary=:all:')
    return {'manager': manager, 'path': directory.relative_to(root).as_posix(),
            'argv': argv, 'command': shlex.join(argv), 'digest': digest.hexdigest(),
            'allow_scripts': bool(allow_scripts)}


def local_instructions(root, files):
    """Suggest commands from source manifests, never from Linux dependency folders."""
    root = Path(root)
    sections = []
    for name in sorted(files, key=lambda name: (len(Path(name).parts), name)):
        file = Path(name)
        if file.name not in {'package.json', 'requirements.txt'}:
            continue
        folder = root / file.parent
        lines = [f'Project: {folder}', f'  cd "{folder}"']
        if file.name == 'package.json':
            try:
                scripts = json.loads(files[name]).get('scripts', {})
            except (ValueError, AttributeError):
                scripts = {}
            lines.append('  npm install')
            script = next((key for key in ('dev', 'start', 'test') if key in scripts), None)
            if script:
                lines.append('  npm run ' + script)
        else:
            lines.extend(['  python -m venv .venv',
                          '  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt'])
            main = (file.parent / 'main.py').as_posix()
            if main in files:
                lines.append('  .\\.venv\\Scripts\\python.exe main.py')
        sections.append('\n'.join(lines))
    if not sections:
        html = next((name for name in files if Path(name).name == 'index.html'), None)
        return f'Open "{root / html}" in your browser.' if html else ''
    return ('Run locally on Windows:\n\n' + '\n\n'.join(sections) +
            '\n\nInstall locally once, and again when dependencies change. Docker dependencies are not copied by /apply.')
