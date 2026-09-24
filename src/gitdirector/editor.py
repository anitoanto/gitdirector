"""Opening a repository or group folder in VS Code."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_MAC_APP = "Visual Studio Code"
# Inherited from the console, they would make VS Code's terminals think they run in tmux.
_TMUX_VARIABLES = ("TMUX", "TMUX_PANE")


def vscode_command(path: Path) -> list[str] | None:
    """The command that opens *path* in VS Code, or ``None`` when it isn't installed."""
    code = shutil.which("code")
    if code:
        return [code, str(path)]
    if sys.platform == "darwin":
        for apps in (Path("/Applications"), Path.home() / "Applications"):
            if (apps / f"{_MAC_APP}.app").exists():
                return ["open", "-a", _MAC_APP, str(path)]
    return None


def open_in_vscode(path: Path) -> None:
    command = vscode_command(path)
    if command is None:
        raise FileNotFoundError("VS Code not found; install its 'code' command")
    env = {key: value for key, value in os.environ.items() if key not in _TMUX_VARIABLES}
    result = subprocess.run(
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        start_new_session=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"exit status {result.returncode}")


__all__ = ["open_in_vscode", "vscode_command"]
