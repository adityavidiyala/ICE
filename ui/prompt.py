from pathlib import Path
from rich.text import Text
from agent.loop import Agent
from sandbox.docker_backend import DockerSandbox, SandboxError
from ui.console import console
from llm.credentials import ensure_api_key
from llm.model_catalog import select_gemini_model


def run_repl():
    """Start ICE with sandbox isolation required; never fall back to host tools."""
    from ui.workbench import Workbench
    backend = None
    agent = None
    try:
        if not ensure_api_key(console):
            return
        if not select_gemini_model(console):
            return
        console.print(Text("Preparing isolated workspace...", style="dim"))
        backend = DockerSandbox(Path.cwd())
        backend.start()
        agent = Agent(backend=backend)
        Workbench(agent).run()
    except (SandboxError, ValueError) as exc:
        console.print(Text(str(exc), style="red"))
    finally:
        if agent is not None:
            agent.client.client.close()
        if backend is not None:
            backend.close()
            if backend.recovery_path:
                console.print(Text(f"Unapplied changes saved to {backend.recovery_path}", style="dim"))
                console.print(Text("Recover later with: python -m sandbox recover <manifest-path>", style="dim"))
