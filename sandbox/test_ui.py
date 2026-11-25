"""Staged sandbox results remain reviewable before explicit host apply."""
import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from agent.events import AgentEvent
from sandbox.docker_backend import DockerSandbox
from ui.workbench import Workbench


class SandboxUITests(unittest.IsolatedAsyncioTestCase):
    async def wait_until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait(), 5)

    async def test_staged_edit_reaches_changes_view_then_apply_updates_host(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            root = Path(directory)
            (root / 'main.py').write_bytes(b'old\n')
            backend = DockerSandbox(root)

            class Agent:
                def __init__(self):
                    self.backend = backend

                def run(self, message, stream=False):
                    yield AgentEvent.agent_start(message)
                    yield AgentEvent.tool_call_start('edit_file', {'path': 'main.py'})
                    backend._accept_snapshot({'main.py': b'new\n'})
                    yield AgentEvent.tool_call_complete('edit_file', 'Success: staged edit')
                    yield AgentEvent.agent_end('done')

            ui = Workbench(Agent(), input=pipe, output=DummyOutput())
            running = asyncio.create_task(ui.app.run_async())
            try:
                await self.wait_until(lambda: ui.app.is_running)
                pipe.send_text('edit\r')
                await self.wait_until(lambda: ui.task is not None and ui.task.done())
                self.assertEqual((root / 'main.py').read_bytes(), b'old\n')
                self.assertEqual(len(ui.changes.files), 1)
                self.assertIn('+new', ui.transcript.entries[-1].diff)
                pipe.send_text('/sandbox\r')
                await self.wait_until(lambda: 'pending files' in ui.notice)
                pipe.send_text('/apply\r')
                await self.wait_until(lambda: ui.notice == 'Applied 1 files to the host workspace')
                self.assertEqual((root / 'main.py').read_bytes(), b'new\n')
                self.assertEqual(backend.workspace.pending(), [])
                pipe.send_bytes(b'\x04')
                await asyncio.wait_for(running, 5)
            finally:
                if not running.done():
                    ui.app.exit()
                    await running


class RecoveryTests(unittest.TestCase):
    def test_saved_manifest_can_be_recovered_with_conflict_checks(self):
        from sandbox.__main__ import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_bytes(b'old\n')
            backend = DockerSandbox(root)
            backend.workspace.current['main.py'] = b'new\n'
            backend.close()
            self.assertEqual((root / 'main.py').read_bytes(), b'old\n')
            with patch('sys.argv', ['sandbox', 'recover', str(backend.recovery_path)]), contextlib.redirect_stdout(io.StringIO()):
                main()
            self.assertEqual((root / 'main.py').read_bytes(), b'new\n')


if __name__ == '__main__':
    unittest.main()
