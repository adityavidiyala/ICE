"""Temporary network installation, cleanup, and local setup guidance."""
import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.dependencies import install_plan, local_instructions
from sandbox.docker_backend import DockerSandbox, SandboxError
from ui.presentation import approval_handler


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_npm_uses_lockfile_and_disables_scripts_by_default(self):
        (self.root / 'package.json').write_text('{"dependencies": {}}')
        first = install_plan(self.root)
        self.assertEqual(first['argv'][1], 'install')
        self.assertIn('--ignore-scripts', first['argv'])
        (self.root / 'package-lock.json').write_text('{}')
        second = install_plan(self.root)
        self.assertEqual(second['argv'][1], 'ci')
        self.assertNotEqual(first['digest'], second['digest'])
        self.assertNotIn('--ignore-scripts', install_plan(self.root, allow_scripts=True)['argv'])

    def test_python_uses_project_environment_and_wheels(self):
        (self.root / 'requirements.txt').write_text('six==1.17.0\n')
        plan = install_plan(self.root)
        self.assertEqual(plan['manager'], 'pip')
        self.assertEqual(plan['argv'][0], '/workspace/.venv/bin/python')
        self.assertIn('--only-binary=:all:', plan['argv'])
        self.assertNotIn('--only-binary=:all:', install_plan(self.root, allow_scripts=True)['argv'])

    def test_invalid_missing_ambiguous_and_escaping_plans_fail_before_approval(self):
        with self.assertRaises(ValueError):
            install_plan(self.root)
        (self.root / 'package.json').write_text('{}')
        (self.root / 'requirements.txt').write_text('six')
        with self.assertRaisesRegex(ValueError, 'Both'):
            install_plan(self.root)
        for manager, path in [('other', '.'), ('npm', '../outside'), ('pip', '/etc'), ('npm', '.env')]:
            with self.subTest(manager=manager, path=path), self.assertRaises(ValueError):
                install_plan(self.root, manager, path)

    def test_windows_instructions_use_manifests_and_real_scripts(self):
        files = {'web/package.json': b'{"scripts":{"dev":"vite"}}',
                 'api/requirements.txt': b'flask', 'api/main.py': b'print(1)'}
        result = local_instructions(self.root, files)
        self.assertIn('npm install', result)
        self.assertIn('npm run dev', result)
        self.assertIn('.venv\\Scripts\\python.exe -m pip install', result)
        self.assertIn('.venv\\Scripts\\python.exe main.py', result)


class InstallBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = DockerSandbox(self.temp.name)
        self.backend.running = True
        self.plan = {'manager': 'npm', 'path': '.', 'command': 'npm install --ignore-scripts', 'digest': 'digest'}

    @contextlib.contextmanager
    def mocked_install(self, response=None, error=None):
        operations = []
        def docker(*args, **kwargs):
            operations.append(args)
            if args[0] == 'inspect':
                return '{"none":{}}'
            return ''
        def request(payload, *args, **kwargs):
            if payload['operation'] == 'install_plan':
                self.assertFalse(self.backend.network_enabled)
                return {'plan': self.plan}
            self.assertTrue(self.backend.network_enabled)
            if error:
                raise error
            return response or {'output': 'installed', 'exit_code': 0}
        token = approval_handler.set(lambda *args: True)
        try:
            with patch.object(self.backend, '_docker', side_effect=docker), \
                    patch.object(self.backend, '_request', side_effect=request), \
                    patch.object(self.backend, '_refresh'):
                yield operations
        finally:
            approval_handler.reset(token)

    def test_success_uses_same_volume_and_restores_verified_offline_container(self):
        with self.mocked_install() as operations:
            output = self.backend.install()
        self.assertIn('Dependencies installed', output)
        launches = [args for args in operations if args[0] == 'run']
        self.assertEqual([args[args.index('--network') + 1] for args in launches], ['bridge', 'none'])
        self.assertTrue(all(f'type=volume,source={self.backend.volume},target=/workspace' in args for args in launches))
        self.assertFalse(self.backend.network_enabled)
        self.assertFalse(self.backend.installing)
        self.assertTrue(self.backend.running)

    def test_denial_never_changes_network_or_executes_install(self):
        token = approval_handler.set(lambda *args: False)
        try:
            with patch.object(self.backend, '_request', return_value={'plan': self.plan}) as request, \
                    patch.object(self.backend, '_docker') as docker:
                result = self.backend.install()
                self.assertIn('denied', result)
                self.assertEqual(request.call_count, 1)
                docker.assert_not_called()
        finally:
            approval_handler.reset(token)

    def test_package_failure_and_timeout_both_restore_offline(self):
        for response in ({'output': 'failed', 'exit_code': 1},
                         {'output': 'timeout', 'exit_code': -9, 'timed_out': True}):
            with self.subTest(response=response), self.mocked_install(response):
                self.assertTrue(self.backend.install().startswith('Error:'))
                self.assertFalse(self.backend.network_enabled)
                self.assertFalse(self.backend.stopped)

    def test_transport_failure_still_restores_offline(self):
        with self.mocked_install(error=SandboxError('worker failed')):
            with self.assertRaisesRegex(SandboxError, 'worker failed'):
                self.backend.install()
            self.assertFalse(self.backend.network_enabled)
            self.assertTrue(self.backend.running)

    def test_failed_offline_verification_stops_execution(self):
        with self.mocked_install(), patch.object(self.backend, '_verify_offline', side_effect=SandboxError('not offline')):
            with self.assertRaisesRegex(SandboxError, 'Sandbox stopped'):
                self.backend.install()
            self.assertTrue(self.backend.stopped)
            self.assertFalse(self.backend.installing)

    def test_cancellation_does_not_restart_execution(self):
        def cancelled(*args, **kwargs):
            self.backend.cancel()
            return {'output': 'stopped', 'exit_code': -9}
        with self.mocked_install() as operations, patch.object(self.backend, 'refresh_stopped_volume'):
            def dispatch(payload, *args, **kwargs):
                return {'plan': self.plan} if payload['operation'] == 'install_plan' else cancelled()
            with patch.object(self.backend, '_request', side_effect=dispatch):
                self.assertIn('Cancelled', self.backend.install())
            self.assertTrue(self.backend.stopped)
            launches = [args for args in operations if args[0] == 'run']
            self.assertEqual(len(launches), 1)


@unittest.skipUnless(os.getenv('ICE_RUN_DOCKER_TESTS') == '1', 'Enable Docker tests explicitly')
class InstallDockerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.backend = DockerSandbox(self.root)
        self.backend.start()
        self.token = approval_handler.set(lambda *args: True)

    def tearDown(self):
        approval_handler.reset(self.token)
        self.backend.close()
        self.temp.cleanup()

    def offline(self):
        self.backend._verify_offline()
        self.assertFalse(self.backend.network_enabled)

    def test_npm_installs_then_runs_offline_and_does_not_copy_dependencies(self):
        self.backend.invoke('write_file', {'path': 'package.json', 'content': json.dumps({
            'name': 'ice-install-test', 'version': '1.0.0',
            'dependencies': {'is-number': '7.0.0'},
            'scripts': {'postinstall': 'touch install-script-ran'}
        })})
        output = self.backend.install('npm', timeout=180)
        self.assertIn('Dependencies installed', output, output)
        self.offline()
        result = self.backend.execute("node -e \"console.log(require('is-number')(42)); console.log(require.resolve('is-number'))\"")
        self.assertIn('true', result)
        self.assertIn('/workspace/node_modules/is-number', result)
        self.assertNotIn('install-script-ran', self.backend.workspace.current)
        self.assertIn('package-lock.json', self.backend.workspace.current)
        self.backend.apply()
        self.assertTrue((self.root / 'package.json').exists())
        self.assertFalse((self.root / 'node_modules').exists())

    def test_pip_installs_to_venv_and_python_imports_offline(self):
        self.backend.invoke('write_file', {'path': 'requirements.txt', 'content': 'six==1.17.0\n'})
        output = self.backend.install('pip', timeout=180)
        self.assertIn('Dependencies installed', output, output)
        self.offline()
        result = self.backend.execute("python -c \"import six,sys; print(six.__version__); print(sys.prefix)\"")
        self.assertIn('1.17.0', result)
        self.assertIn('/workspace/.venv', result)
        self.backend.apply()
        self.assertFalse((self.root / '.venv').exists())

    def test_failed_install_restores_offline_and_worker_ignores_project_shadow_modules(self):
        self.backend.invoke('write_file', {'path': 'package.json', 'content': json.dumps({
            'name': 'ice-failure-test', 'version': '1.0.0',
            'dependencies': {'ice-package-that-does-not-exist-abcdef123456': '0.0.0'}
        })})
        self.assertTrue(self.backend.install('npm', timeout=120).startswith('Error:'))
        self.offline()
        self.backend.invoke('write_file', {'path': 'sitecustomize.py', 'content': 'raise RuntimeError("shadowed")'})
        self.backend.invoke('write_file', {'path': 'sandbox/__init__.py', 'content': 'raise RuntimeError("shadowed")'})
        self.assertIn('package.json', self.backend.invoke('list_directories', {}))


if __name__ == '__main__':
    unittest.main()
