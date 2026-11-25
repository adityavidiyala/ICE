"""Host-side Docker broker. The container never receives host mounts or API credentials."""
import json
import subprocess
import threading
import uuid
from pathlib import Path

from safety.inspection import CommandTier
from sandbox.policy import inspect_shell
from sandbox.workspace import Workspace, encode_files, decode_files
from ui.presentation import approval_handler, record_file_change, present_change
from utils.diff import compute_diff
from sandbox.dependencies import local_instructions
from safety.inspection import InspectionResult

# Ignore project-local modules and sitecustomize when launching the trusted worker.
WORKER_BOOTSTRAP = "import sys,runpy; sys.path.insert(0,'/app'); runpy.run_module('sandbox.worker',run_name='__main__')"


class SandboxError(RuntimeError):
    pass


class DockerSandbox:
    def __init__(self, root, image='ice-sandbox:local'):
        self.workspace = Workspace(root)
        self.image = image
        self.session = uuid.uuid4().hex
        self.container = 'ice-' + self.session
        self.volume = self.container + '-workspace'
        self.running = False
        self.stopped = False
        self.recovery_path = None
        self.lock = threading.RLock()
        self.resources_created = False
        self.closed = False
        self.snapshot_incomplete = False
        self.stop_requested = threading.Event()
        self.network_enabled = False
        self.installing = False

    def _docker(self, *args, data=None, timeout=30):
        try:
            result = subprocess.run(['docker', *args], input=data, text=True, encoding='utf-8',
                                    errors='replace', capture_output=True, timeout=timeout,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxError(f'Docker operation failed: {exc}') from exc
        if result.returncode:
            raise SandboxError((result.stderr or result.stdout).strip()[:2000])
        return result.stdout

    def start(self):
        try:
            if self._docker('info', '--format', '{{.OSType}}').strip() != 'linux':
                raise SandboxError('Docker must use Linux containers.')
            self._docker('image', 'inspect', self.image)
        except SandboxError as exc:
            raise SandboxError('Sandbox unavailable. Start Docker Desktop (Linux containers), then run '
                               '`python -m sandbox build` once. No host tool execution was attempted.\n' + str(exc)) from exc
        try:
            self._docker('volume', 'create', self.volume)
            self.resources_created = True
            self._launch('none')
            self._request({'operation': 'seed', 'files': encode_files(self.workspace.baseline)})
        except Exception:
            self.close()
            raise

    def _launch(self, network, lifetime=None):
        if self.stop_requested.is_set():
            raise SandboxError('Sandbox was stopped by the user.')
        self.network_enabled = network != 'none'
        self._docker('run', '-d', '--name', self.container,
                         '--network', network, '--read-only', '--cap-drop', 'ALL',
                         '--security-opt', 'no-new-privileges=true', '--user', '10001:10001',
                         '--pids-limit', '128', '--memory', '1g', '--cpus', '2',
                         '--tmpfs', '/tmp:rw,nosuid,noexec,size=128m,mode=1777',
                         '--mount', f'type=volume,source={self.volume},target=/workspace',
                         '--workdir', '/workspace', self.image, 'sleep',
                         str(lifetime or 960) if network != 'none' else 'infinity')
        self.running = True
        if self.stop_requested.is_set():
            self.cancel()
            raise SandboxError('Sandbox was stopped by the user.')

    def _remove_container(self):
        try:
            self._docker('rm', '-f', self.container, timeout=15)
        except SandboxError as exc:
            if 'no such container' not in str(exc).lower():
                raise
        self.running = False
        self.network_enabled = False

    def _verify_offline(self):
        networks = json.loads(self._docker('inspect', '--format', '{{json .NetworkSettings.Networks}}', self.container))
        if set(networks) != {'none'}:
            raise SandboxError('Cannot verify that container networking is disabled.')

    def _request(self, payload, timeout=30):
        if not self.running or self.stopped:
            raise SandboxError('Sandbox is stopped. Restart ICE to start a new sandbox; host execution is disabled.')
        result = self._docker('exec', '-i', self.container, 'python', '-I', '-c', WORKER_BOOTSTRAP,
                              data=json.dumps(payload), timeout=timeout)
        try:
            response = json.loads(result)
        except ValueError as exc:
            raise SandboxError('Invalid worker response; no host execution attempted.') from exc
        if not response.get('ok'):
            raise SandboxError(response.get('error', 'Sandbox worker failed'))
        return response

    def _refresh(self):
        files = decode_files(self._request({'operation': 'snapshot'})['files'])
        self._accept_snapshot(files)

    def _accept_snapshot(self, files):
        before = self.workspace.current
        for name in sorted(before.keys() | files.keys()):
            if before.get(name) != files.get(name):
                old = before.get(name, b'').decode('utf-8', errors='replace')
                new = files.get(name, b'').decode('utf-8', errors='replace')
                present_change(str(self.workspace.root / name), compute_diff(old, new, name), name not in before)
                record_file_change(str(self.workspace.root / name), old, new, name not in before)
        self.workspace.current = files

    def refresh_stopped_volume(self):
        """Export after killing the container; no sandbox process can race this copy."""
        try:
            result = self._docker('run', '--rm', '--network', 'none', '--read-only',
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges=true',
                '--user', '10001:10001', '--pids-limit', '32', '--memory', '256m',
                '--mount', f'type=volume,source={self.volume},target=/workspace,readonly',
                '-i', self.image, 'python', '-I', '-c', WORKER_BOOTSTRAP,
                data=json.dumps({'operation': 'snapshot'}))
            payload = json.loads(result)
            if not payload.get('ok'):
                raise SandboxError(payload.get('error', 'Snapshot export failed'))
            self._accept_snapshot(decode_files(payload['files']))
            self.snapshot_incomplete = False
        except Exception:
            self.snapshot_incomplete = True
            raise

    def invoke(self, name, arguments):
        with self.lock:
            if name == 'run_command':
                return self.execute(arguments['command'], arguments.get('timeout', 120))
            if name == 'install_dependencies':
                return self.install(**arguments)
            response = self._request({'operation': 'tool', 'name': name, 'arguments': arguments})
            self._refresh()
            return response['output']

    def execute(self, command, timeout=120):
        result = inspect_shell(command)
        if result.tier == CommandTier.BLOCKED:
            return 'Error: Command BLOCKED by sandbox command policy. ' + result.reason
        if result.tier == CommandTier.PROMPT:
            approve = approval_handler.get()
            if approve is None or not approve(command, result):
                return 'Cancelled: User denied permission to run command.'
        timeout = min(300, max(1, int(timeout)))
        try:
            response = self._request({'operation': 'command', 'command': command, 'timeout': timeout}, timeout + 10)
            if response.get('timed_out'):
                self.cancel()
                self.refresh_stopped_volume()
            else:
                self._refresh()
            return response['output']
        except SandboxError:
            self.cancel()
            try:
                self.refresh_stopped_volume()
            except Exception:
                pass
            raise

    def install(self, manager='auto', path='.', timeout=300, allow_scripts=False):
        """One approved network window; kill all processes at both transitions."""
        with self.lock:
            timeout = min(900, max(1, int(timeout)))
            plan = self._request({'operation': 'install_plan', 'manager': manager, 'path': path,
                                  'allow_scripts': allow_scripts})['plan']
            reason = ('Temporary GENERAL INTERNET access. '
                      + ('Install/build scripts ALLOWED. ' if allow_scripts else
                         'Install/build scripts disabled. ')
                      + 'Other tools pause; networking turns off afterward.')
            permission = InspectionResult(CommandTier.PROMPT, reason, [plan['command']], plan['command'])
            approve = approval_handler.get()
            if approve is None or not approve(f"In /workspace/{plan['path']}: {plan['command']}", permission):
                return 'Cancelled: Dependency installation denied. Container networking remains disabled.'
            if self.stop_requested.is_set():
                return 'Cancelled: Sandbox was stopped before installation.'
            self.installing = True
            response = None
            try:
                # Replacing the same container retains the workspace volume while
                # preventing older background processes from receiving internet access.
                self._remove_container()
                self._launch('bridge', lifetime=timeout + 30)
                response = self._request({'operation': 'install', 'manager': plan['manager'],
                    'path': plan['path'], 'digest': plan['digest'], 'allow_scripts': allow_scripts,
                    'timeout': timeout}, timeout + 15)
            finally:
                try:
                    self._remove_container()  # Also kills background install scripts.
                    if not self.stop_requested.is_set():
                        self._launch('none')
                        self._verify_offline()
                        self._refresh()
                    else:
                        self.refresh_stopped_volume()
                except Exception as exc:
                    self.cancel()
                    raise SandboxError('Could not restore verified offline execution. Sandbox stopped; '
                                       'no further tools will run. ' + str(exc)) from exc
                finally:
                    self.installing = False
            if self.stop_requested.is_set():
                return 'Cancelled: Dependency installation stopped. Networking is disabled; restart ICE for further execution.'
            if response.get('timed_out'):
                return 'Error: Dependency installation timed out. Networking is disabled again.'
            output = response['output']
            if response.get('exit_code'):
                return ('Error: Dependency installation failed. Networking is disabled again.\n' + output +
                        ('\nA package may require install/build scripts; request explicit allow_scripts approval if needed.'
                         if not allow_scripts else ''))
            return (output + '\nDependencies installed inside Docker. Networking is disabled again.\n'
                    'Python commands use /workspace/.venv automatically; Node uses project node_modules.\n'
                    'Use /apply for source files, then install dependencies separately on Windows.')

    def local_run_instructions(self):
        return local_instructions(self.workspace.root, self.workspace.current)

    def apply(self):
        with self.lock:
            if self.stopped and self.resources_created:
                self.refresh_stopped_volume()
            return self.workspace.apply()

    def cancel(self):
        # Kill the container, not just `docker exec`: detached children must stop too.
        self.stopped = True
        self.stop_requested.set()
        if self.running:
            try:
                self._docker('kill', self.container, timeout=10)
                self.network_enabled = False
            except SandboxError:
                pass

    def close(self):
        if self.closed:
            return
        if self.stopped and self.resources_created:
            try:
                self.refresh_stopped_volume()
            except Exception:
                pass
        # Preserve broker snapshots so exiting cannot silently discard unapplied work.
        if self.workspace.pending() or self.snapshot_incomplete:
            folder = self.workspace.root / '.ice-sandbox' / 'sessions'
            for part in (folder.parent, folder):
                if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
                    raise SandboxError(f'Cannot save recovery data through symlink/junction: {part}. Docker volume retained: {self.volume}')
            folder.mkdir(parents=True, exist_ok=True)
            self.recovery_path = folder / (self.session + '.json')
            if self.recovery_path.exists() or self.recovery_path.is_symlink():
                raise SandboxError('Recovery path already exists; Docker volume retained: ' + self.volume)
            self.recovery_path.write_text(json.dumps({
                'root': str(self.workspace.root), 'baseline': encode_files(self.workspace.baseline),
                'current': encode_files(self.workspace.current),
                'volume': self.volume if self.snapshot_incomplete else None, 'image': self.image,
            }), encoding='utf-8')
        if not self.resources_created:
            self.closed = True
            return
        try:
            self._docker('rm', '-f', self.container, timeout=15)
        except SandboxError:
            pass
        if not self.snapshot_incomplete:
            try:
                self._docker('volume', 'rm', self.volume, timeout=15)
            except SandboxError:
                pass
        self.running = False
        self.closed = True

    def description(self):
        status = 'stopped (restart ICE for more execution)' if self.stopped else 'running'
        network = 'temporarily on (dependency installation)' if self.network_enabled else 'off'
        return f'Docker sandbox {status} · network {network} · {len(self.workspace.pending())} pending files · use /apply to update host files'
