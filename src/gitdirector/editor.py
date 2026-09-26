"""Opening a repository or group folder in VS Code.

VS Code must start as if the user had opened it themselves: nothing of the
console's process (its virtualenv, tmux, working directory, agent session or
``GITDIRECTOR_*`` settings) may reach it, or its terminals would carry the
trace. On macOS ``open -a`` hands the folder to LaunchServices, which starts
the app from the login environment and passes none of ours. The ``code`` CLI
inherits the environment it is given, so it gets a scrubbed one.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .integrations.tmux.session_env import sanitized_environ
from .launch_context import neutral_directory

_MAC_APP = "Visual Studio Code"
# Inherited from the console, they would make VS Code's terminals think they run in tmux.
_TMUX_VARIABLES = frozenset({"TMUX", "TMUX_PANE"})
_OWN_PREFIXES = ("GITDIRECTOR_", "GD_")


def _mac_app_installed() -> bool:
    return sys.platform == "darwin" and any(
        (apps / f"{_MAC_APP}.app").exists()
        for apps in (Path("/Applications"), Path.home() / "Applications")
    )


def vscode_command(path: Path) -> list[str] | None:
    """The command that opens *path* in VS Code, or ``None`` when it isn't installed."""
    if _mac_app_installed():
        return ["open", "-a", _MAC_APP, str(path)]
    code = shutil.which("code")
    if code:
        return [code, str(path)]
    return None


def launch_environment() -> dict[str, str]:
    """What the ``code`` CLI may inherit: the session scrub, less our own names."""
    return {
        name: value
        for name, value in sanitized_environ().items()
        if name not in _TMUX_VARIABLES and not name.startswith(_OWN_PREFIXES)
    }


def open_in_vscode(path: Path) -> None:
    command = vscode_command(path)
    if command is None:
        raise FileNotFoundError("VS Code not found; install its 'code' command")
    result = subprocess.run(
        command,
        env=launch_environment(),
        cwd=neutral_directory(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        start_new_session=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"exit status {result.returncode}")


__all__ = ["launch_environment", "open_in_vscode", "vscode_command"]
