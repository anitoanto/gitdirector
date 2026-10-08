"""Opening a repository or group folder in the user's editor.

The editor is the ``editor`` command in ``config.yaml`` (``code`` unless
set), run with the folder as its argument. It must start as if the user had
opened it themselves: nothing of the console's process (its virtualenv, tmux,
working directory, agent session or ``GITDIRECTOR_*`` settings) may reach it,
or its terminals would carry the trace. On macOS the default's app bundle is
handed to LaunchServices with ``open -a``, which starts it from the login
environment and passes none of ours; any other command inherits the
environment it is given, so it gets a scrubbed one.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Config
from .integrations.tmux.session_env import sanitized_environ
from .launch_context import neutral_directory

DEFAULT_EDITOR = Config.DEFAULT_EDITOR
# The app bundle behind a bare command, for a launch that carries no trace of us.
_MAC_APPS = {"code": "Visual Studio Code"}
# Inherited from the console, they would make the editor's terminals think they run in tmux.
_TMUX_VARIABLES = frozenset({"TMUX", "TMUX_PANE"})
_OWN_PREFIXES = ("GITDIRECTOR_", "GD_")


def editor_setting() -> str:
    """The configured editor command line, or the default when the config cannot be read."""
    try:
        return Config().editor
    except Exception:
        return DEFAULT_EDITOR


def _argv(command: str | None) -> list[str]:
    try:
        return shlex.split(command if command is not None else editor_setting())
    except ValueError:
        return []


def editor_label(command: str | None = None) -> str:
    """The command's name, as shown in menus: ``code``, ``cursor``…"""
    argv = _argv(command)
    return Path(argv[0]).name if argv else DEFAULT_EDITOR


def _mac_app_installed(app: str) -> bool:
    return sys.platform == "darwin" and any(
        (apps / f"{app}.app").exists()
        for apps in (Path("/Applications"), Path.home() / "Applications")
    )


def editor_command(path: Path, command: str | None = None) -> list[str] | None:
    """The command that opens *path* in the editor, or ``None`` when it isn't installed."""
    argv = _argv(command)
    if not argv:
        return None
    app = _MAC_APPS.get(argv[0]) if len(argv) == 1 else None
    if app and _mac_app_installed(app):
        return ["open", "-a", app, str(path)]
    executable = shutil.which(argv[0])
    if executable:
        return [executable, *argv[1:], str(path)]
    return None


def launch_environment() -> dict[str, str]:
    """What the editor may inherit: the session scrub, less our own names."""
    return {
        name: value
        for name, value in sanitized_environ().items()
        if name not in _TMUX_VARIABLES and not name.startswith(_OWN_PREFIXES)
    }


def open_in_editor(path: Path, command: str | None = None) -> None:
    command = editor_setting() if command is None else command
    argv = editor_command(path, command)
    if argv is None:
        raise FileNotFoundError(
            f"{editor_label(command)} not found; set `editor` in config.yaml to an installed command"
        )
    result = subprocess.run(
        argv,
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


__all__ = [
    "DEFAULT_EDITOR",
    "editor_command",
    "editor_label",
    "editor_setting",
    "launch_environment",
    "open_in_editor",
]
