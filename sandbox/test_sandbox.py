"""Workspace and broker tests; real Docker tests are explicitly enabled by env."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.events import AgentEventType
from agent.loop import Agent
from google.genai import types
from sandbox.docker_backend import DockerSandbox, SandboxError
from sandbox.workspace import Workspace, allowed_path, host_path, snapshot, encode_files, decode_files
from sandbox.policy import inspect_shell
from safety.inspection import CommandTier
from ui.presentation import approval_handler


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_snapshot_excludes_credentials_and_generated_directories(self):
        (self.root / 'main.py').write_text('code')
        for name in ('.env', '.env.local', 'private.pem', 'secret.key', '.npmrc'):
            (self.root / name).write_text('secret')
        (self.root / '.aws').mkdir()
        (self.root / '.aws' / 'credentials').write_text('secret')
        self.assertEqual(snapshot(self.root), {'main.py': b'code'})

    def test_path_escapes_devices_and_ads_are_rejected(self):
        for name in ('../outside', '/etc/passwd', 'a/../../outside', 'C:/Windows/file',
                     'a\\..\\outside', '.env', 'src/.env.local', 'NUL', 'con.txt',
                     'a/file:stream', 'a/file.', 'a/file ', 'a/*.txt'):
            with self.subTest(name=name):
                self.assertFalse(allowed_path(name))
                with self.assertRaises(ValueError):
                    host_path(self.root, name)
        self.assertTrue(allowed_path('**/*.py', allow_patterns=True))

    def test_new_edit_delete_are_staged_until_apply(self):
        (self.root / 'edit.txt').write_bytes(b'old')
        (self.root / 'delete.txt').write_bytes(b'delete')
        workspace = Workspace(self.root)
        workspace.current = {'edit.txt': b'new', 'nested/new.txt': b'created'}
        self.assertEqual((self.root / 'edit.txt').read_bytes(), b'old')
        self.assertEqual(len(workspace.apply()), 3)
        self.assertEqual((self.root / 'edit.txt').read_bytes(), b'new')
        self.assertEqual((self.root / 'nested/new.txt').read_bytes(), b'created')
        self.assertFalse((self.root / 'delete.txt').exists())
        self.assertEqual(workspace.pending(), [])

    def test_conflict_prevents_all_writes(self):
        (self.root / 'b.txt').write_bytes(b'old')
        workspace = Workspace(self.root)
        workspace.current = {'a.txt': b'new', 'b.txt': b'agent edit'}
        (self.root / 'b.txt').write_bytes(b'user edit')
        with self.assertRaisesRegex(ValueError, 'Host file changed'):
            workspace.apply()
        self.assertFalse((self.root / 'a.txt').exists())
        self.assertEqual((self.root / 'b.txt').read_bytes(), b'user edit')

    def test_apply_replaces_hardlink_without_modifying_other_name(self):
        outside = self.root / 'original.txt'
        outside.write_bytes(b'original')
        os.link(outside, self.root / 'linked.txt')
        workspace = Workspace(self.root)
        workspace.current['linked.txt'] = b'changed'
        workspace.apply()
        self.assertEqual(outside.read_bytes(), b'original')
        self.assertEqual((self.root / 'linked.txt').read_bytes(), b'changed')

    def test_symlinks_are_not_snapshotted_or_applied_through(self):
        (self.root / 'real').mkdir()
        try:
            (self.root / 'link').symlink_to(self.root / 'real', target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation is unavailable for this Windows account')
        workspace = Workspace(self.root)
        workspace.current['link/new.txt'] = b'escape'
        with self.assertRaises(ValueError):
            workspace.apply()

    def test_snapshot_size_and_malicious_exports_are_rejected(self):
        (self.root / 'big').write_bytes(b'12345')
        with patch('sandbox.workspace.MAX_BYTES', 4):
            with self.assertRaises(ValueError):
                snapshot(self.root)
        with self.assertRaises(ValueError):
            decode_files({'../outside': 'YQ=='})
        self.assertEqual(decode_files(encode_files({'ok.txt': b'\x00binary'})), {'ok.txt': b'\x00binary'})


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = DockerSandbox(self.temp.name)

    def test_startup_failure_never_falls_back_to_host(self):
        with patch.object(self.backend, '_docker', side_effect=SandboxError('engine unavailable')):
            with self.assertRaisesRegex(SandboxError, 'No host tool execution'):
                self.backend.start()
            with self.assertRaises(SandboxError):
                self.backend.invoke('write_file', {'path': 'x', 'content': 'bad'})
        self.assertFalse((Path(self.temp.name) / 'x').exists())

    def test_container_has_no_host_mounts_and_security_flags_are_present(self):
        with patch.object(self.backend, '_docker', side_effect=['linux', 'image', 'volume', 'container']), \
                patch.object(self.backend, '_request', return_value={'ok': True}):
            # Capture Docker arguments independently from actual engine access.
            with patch.object(self.backend, '_docker', side_effect=['linux', 'image', 'volume', 'container']) as docker:
                self.backend.start()
        args = docker.call_args_list[-1].args
        for expected in ('--read-only', '--cap-drop', '--network', 'none', '--memory', '--pids-limit'):
            self.assertIn(expected, args)
        self.assertIn('no-new-privileges=true', args)
        self.assertFalse(any('type=bind' in str(arg) or 'docker.sock' in str(arg) for arg in args))
        self.assertNotIn('-e', args)

    def test_denied_command_never_reaches_worker(self):
        token = approval_handler.set(lambda *args: False)
        try:
            with patch.object(self.backend, '_request') as request:
                self.assertIn('Cancelled', self.backend.execute('python main.py'))
                request.assert_not_called()
        finally:
            approval_handler.reset(token)

    def test_timeout_kills_container_and_exports_stopped_volume(self):
        token = approval_handler.set(lambda *args: True)
        try:
            with patch.object(self.backend, '_request', return_value={'output': 'timeout', 'timed_out': True}), \
                    patch.object(self.backend, 'cancel') as cancel, \
                    patch.object(self.backend, 'refresh_stopped_volume') as export:
                self.assertEqual(self.backend.execute('sleep 10', 1), 'timeout')
                cancel.assert_called_once()
                export.assert_called_once()
        finally:
            approval_handler.reset(token)

    def test_unapplied_changes_saved_as_recovery_manifest(self):
        self.backend.workspace.current['new.txt'] = b'pending'
        with patch.object(self.backend, '_docker'):
            self.backend.close()
        manifest = json.loads(self.backend.recovery_path.read_text())
        self.assertEqual(decode_files(manifest['current']), {'new.txt': b'pending'})
        self.assertFalse((Path(self.temp.name) / 'new.txt').exists())
        self.backend.close()  # Closing twice must be harmless.

    def test_shell_policy_uses_posix_rules_and_checks_compound_commands(self):
        self.assertEqual(inspect_shell('node --check src/app.js').tier, CommandTier.SAFE)
        self.assertEqual(inspect_shell('ls; touch changed').tier, CommandTier.PROMPT)
        self.assertEqual(inspect_shell('echo $(touch changed)').tier, CommandTier.PROMPT)
        self.assertEqual(inspect_shell('Get-Content file').tier, CommandTier.PROMPT)

    def test_agent_routes_all_file_execution_to_backend(self):
        backend = Mock()
        backend.invoke.return_value = 'sandbox result'
        call = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
            role='model', parts=[types.Part(function_call=types.FunctionCall(name='read_file', args={'path': 'x'}))]))])
        done = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
            role='model', parts=[types.Part.from_text(text='Done')]))])
        with patch('agent.loop.LLMClient') as client, patch('agent.loop.TOOL_MAP', {'read_file': Mock()}) as tools:
            client.return_value.context.pruned_total = 0
            client.return_value.send.side_effect = [(call, 0), (done, 0)]
            events = list(Agent(backend=backend).run('read'))
            tools['read_file'].assert_not_called()
        backend.invoke.assert_called_once_with('read_file', {'path': 'x'})
        self.assertFalse([e for e in events if e.type == AgentEventType.AGENT_ERROR])


@unittest.skipUnless(os.getenv('ICE_RUN_DOCKER_TESTS') == '1', 'Set ICE_RUN_DOCKER_TESTS=1 to run real Docker isolation tests')
class DockerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'example.py').write_text('print("hello")\n')
        (self.root / '.env').write_text('GEMINI_API_KEY=not-for-container')
        self.backend = DockerSandbox(self.root)
        self.backend.start()
        self.token = approval_handler.set(lambda *args: True)

    def tearDown(self):
        approval_handler.reset(self.token)
        self.backend.close()
        self.temp.cleanup()

    def test_file_tools_staging_apply_and_host_path_rejection(self):
        self.assertIn('hello', self.backend.invoke('read_file', {'path': 'example.py'}))
        self.assertIn('Success', self.backend.invoke('edit_file', {'path': 'example.py', 'old_string': 'hello', 'new_string': 'sandbox'}))
        self.assertIn('hello', (self.root / 'example.py').read_text())
        self.assertIn('example.py', self.backend.invoke('glob', {'pattern': '**/*.py'}))
        self.assertIn('sandbox', self.backend.invoke('grep', {'pattern': 'sandbox'}))
        self.assertIn('example.py', self.backend.invoke('list_directories', {}))
        self.backend.invoke('write_file', {'path': 'nested/new.txt', 'content': 'new'})
        self.assertFalse((self.root / 'nested/new.txt').exists())
        for path in ('../outside', '/etc/passwd', str(self.root / '.env')):
            with self.assertRaises(SandboxError):
                self.backend.invoke('read_file', {'path': path})
        self.assertEqual(len(self.backend.apply()), 2)
        self.assertIn('sandbox', (self.root / 'example.py').read_text())
        self.assertEqual((self.root / 'nested/new.txt').read_text(), 'new')

    def test_runtime_nonroot_readonly_no_network_no_secrets_and_no_docker_socket(self):
        self.assertEqual(self.backend.execute('id -u'), '10001')
        self.assertIn('hello', self.backend.execute('python example.py'))
        self.assertIn('absent', self.backend.execute("python -c \"import os; print(os.getenv('GEMINI_API_KEY', 'absent'))\""))
        self.assertIn('absent', self.backend.execute("test ! -e /workspace/.env && test ! -e /var/run/docker.sock && echo absent"))
        result = self.backend.execute("touch /app/should-not-exist")
        self.assertIn('Exit code:', result)
        result = self.backend.execute("python -c \"import socket; s=socket.socket(); s.settimeout(1); print(s.connect_ex(('1.1.1.1',443)))\"")
        self.assertTrue(result.strip().isdigit())
        self.assertNotEqual(result.strip(), '0')
        self.backend.execute('echo outside-marker > /tmp/outside.txt; ln -s /tmp /workspace/link')
        with self.assertRaises(SandboxError):
            self.backend.invoke('read_file', {'path': 'link/outside.txt'})
        self.assertTrue(self.backend.invoke('grep', {'pattern': 'outside-marker'}).startswith('No matches found'))

    def test_timeout_stops_all_execution_and_preserves_completed_writes(self):
        result = self.backend.execute('echo preserved > checkpoint.txt; sleep 20', 1)
        self.assertIn('timed out', result)
        self.assertTrue(self.backend.stopped)
        self.assertIn('checkpoint.txt', self.backend.workspace.current)
        with self.assertRaises(SandboxError):
            self.backend.invoke('read_file', {'path': 'example.py'})


if __name__ == '__main__':
    unittest.main()
