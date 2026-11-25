"""Windows system clipboard bridge without interpolating clipboard text into commands."""
import subprocess
from prompt_toolkit.clipboard import Clipboard, ClipboardData


class SystemClipboard(Clipboard):
    def _run(self, script, text=None):
        script = "[Console]::InputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); $ErrorActionPreference = 'Stop'; " + script
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                input=text, capture_output=True, text=True, encoding="utf-8",
                timeout=5, check=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return result.stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("System clipboard unavailable. Try your terminal's paste command.") from exc

    def set_data(self, data):
        self._run("Set-Clipboard -Value ([Console]::In.ReadToEnd())", data.text)

    def get_data(self):
        return ClipboardData(self._run("[Console]::Out.Write((Get-Clipboard -Raw))"))
