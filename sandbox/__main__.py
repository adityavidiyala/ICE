"""Build the sandbox image or recover a saved working copy."""
import argparse
import json
import subprocess
from pathlib import Path
from sandbox.workspace import Workspace, decode_files


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('build')
    recover = sub.add_parser('recover')
    recover.add_argument('manifest', type=Path)
    args = parser.parse_args()
    if args.action == 'build':
        root = Path(__file__).resolve().parent.parent
        raise SystemExit(subprocess.call(['docker', 'build', '-t', 'ice-sandbox:local',
                                          '-f', str(root / 'sandbox' / 'Dockerfile'), str(root)]))
    data = json.loads(args.manifest.read_text(encoding='utf-8'))
    root = Path(data['root']).resolve()
    if args.manifest.resolve().parent != root / '.ice-sandbox' / 'sessions':
        raise ValueError('Recovery manifest must be inside the recorded workspace .ice-sandbox/sessions directory')
    workspace = Workspace(data['root'])
    workspace.baseline = decode_files(data['baseline'])
    workspace.current = decode_files(data['current'])
    if data.get('volume'):
        import re
        from sandbox.docker_backend import DockerSandbox
        if not re.fullmatch(r'ice-[0-9a-f]{32}-workspace', data['volume']):
            raise ValueError('Invalid recovery volume name')
        backend = DockerSandbox(root, data['image'])
        backend.workspace = workspace
        backend.volume = data['volume']
        backend.refresh_stopped_volume()
    files = workspace.apply()
    print(f'Applied {len(files)} recovered file changes to {workspace.root}')


if __name__ == '__main__':
    main()
