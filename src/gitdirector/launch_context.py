"""Keep the directory gitdirector was started from out of its sessions' reach.

A process's working directory is readable by any other process of the same
user (``lsof -d cwd``), and tmux hands sessions a path to several processes
that would otherwise carry it: the tmux server inherits the working
directory of the client that forked it, and ``tmux list-clients`` leads from
inside any session to the attached client and on to its parent, the console.
Every tmux client therefore runs from a neutral directory, and the
long-lived, attaching commands restart themselves there before they start.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

_LAUNCH_ENV_NAMES = ("PWD", "OLDPWD")


def neutral_directory() -> str:
    home = Path.home()
    return str(home) if home.is_dir() else "/"


def _in_launch_context() -> bool:
    try:
        cwd = os.getcwd()
    except OSError:
        return True
    return cwd != neutral_directory() or any(name in os.environ for name in _LAUNCH_ENV_NAMES)


def leave_launch_directory(args: Sequence[str] | None = None) -> None:
    """Re-exec this command from :func:`neutral_directory` without ``PWD``/``OLDPWD``.

    Changing directory alone is not enough: the environment a process was
    started with stays readable for its lifetime. *args* replaces the
    command-line arguments, for callers that resolved a relative path the
    new working directory would break. A no-op without a terminal (scripts,
    tests) and once already neutral, so it can never loop.
    """
    if not sys.stdin.isatty() or not _in_launch_context():
        return
    env = {name: value for name, value in os.environ.items() if name not in _LAUNCH_ENV_NAMES}
    argv = list(sys.argv[1:] if args is None else args)
    os.chdir(neutral_directory())
    os.execve(sys.executable, [sys.executable, "-m", "gitdirector", *argv], env)
