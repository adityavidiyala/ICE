"""One JSON request per container-side worker process. Never imports LLM clients."""
import contextlib
import io
import json
import os
import sys
import subprocess
import signal
import tempfile
import time
from pathlib import Path
from sandbox.dependencies import install_plan

from sandbox.workspace import snapshot, encode_files, decode_files, host_path, allowed_path

ROOT = Path('/workspace')


def tool_path(value):
    if value in {'.', '/workspace', '/workspace/'}:
        return str(ROOT)
    path = Path(value)
    if path.is_absolute():
        try:
            value = path.relative_to(ROOT).as_posix()
        except ValueError:
            raise ValueError("File tools are restricted to /workspace")
    return str(host_path(ROOT, value))


def handle(request):
    operation = request['operation']
    if operation == 'seed':
        for name, data in decode_files(request['files']).items():
            target = host_path(ROOT, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return {'ok': True}
    if operation == 'snapshot':
        return {'files': encode_files(snapshot(ROOT))}
    if operation == 'install_plan':
        return {'plan': install_plan(ROOT, request.get('manager', 'auto'),
                                    request.get('path', '.'), request.get('allow_scripts', False))}
    if operation in {'command', 'install'}:
        import resource
        installing = operation == 'install'
        timeout = min(900 if installing else 300, max(1, int(request.get('timeout', 120))))
        directory = ROOT
        argv = ['/bin/bash', '--noprofile', '--norc', '-c', request.get('command', '')]
        if installing:
            plan = install_plan(ROOT, request.get('manager', 'auto'), request.get('path', '.'),
                                request.get('allow_scripts', False))
            if plan['digest'] != request.get('digest'):
                raise ValueError('Dependency manifests changed after approval. Request a fresh install.')
            directory = ROOT / plan['path']
            argv = plan['argv']
        env = {'PATH': '/workspace/.venv/bin:/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp',
               'LANG': 'C.UTF-8', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0',
               'PIP_CONFIG_FILE': '/dev/null', 'PYTHONUNBUFFERED': '1', 'CI': '1'}
        if installing:
            env['PATH'] = '/usr/local/bin:/usr/bin:/bin'
            if plan['manager'] == 'pip' and (ROOT / '.venv').is_symlink():
                raise ValueError('The project virtual environment must not be a symlink.')
        if (ROOT / '.venv' / 'pyvenv.cfg').is_file():
            env['VIRTUAL_ENV'] = str(ROOT / '.venv')
        def limits():
            maximum = (128 if installing else 1) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            timed_out = False
            # Python installation always uses a project-local environment. The
            # worker itself stays on the image's isolated system interpreter.
            setup = [['/usr/local/bin/python', '-I', '-m', 'venv', '--clear', str(ROOT / '.venv')]] if installing and plan['manager'] == 'pip' else []
            deadline = time.monotonic() + timeout
            for command in setup + [argv]:
                process = subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, start_new_session=True, preexec_fn=limits, env=env)
                try:
                    process.wait(timeout=max(0.1, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                if timed_out or process.returncode:
                    break
            stdout.seek(0)
            stderr.seek(0)
            output = stdout.read(1024 * 1024).decode('utf-8', errors='replace')
            error = stderr.read(1024 * 1024).decode('utf-8', errors='replace')
        if error:
            output += '\n--- stderr ---\n' + error
        if process.returncode:
            output += f'\nExit code: {process.returncode}'
        if timed_out:
            output = f'Error: Command timed out after {timeout}s. Sandbox execution stopped.'
        return {'output': output.strip() or 'Command executed successfully with no output.',
                'timed_out': timed_out, 'exit_code': process.returncode}
    if operation != 'tool':
        raise ValueError("Unknown worker operation")
    from tools import ICE_TOOLS
    from ui.presentation import change_handler
    tools = {tool.__name__: tool for tool in ICE_TOOLS if tool.__name__ not in {'plan', 'run_command', 'install_dependencies'}}
    name = request['name']
    if name not in tools:
        raise ValueError("Unknown sandbox file tool")
    args = dict(request['arguments'])
    args['path'] = tool_path(args.get('path', '.'))
    if name == 'glob':
        pattern = args.get('pattern', '')
        if not allowed_path(pattern, allow_patterns=True):
            raise ValueError("Glob pattern escapes or names an excluded path")
    token = change_handler.set(lambda *args: None)
    try:
        return {'output': tools[name](**args)}
    finally:
        change_handler.reset(token)


def main():
    try:
        os.chdir(ROOT)
        request = json.loads(sys.stdin.read())
        with contextlib.redirect_stdout(io.StringIO()):
            result = handle(request)
        result['ok'] = True
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)}
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    main()
