"""tmux integration via subprocess."""

import os
import re
import subprocess
from pathlib import Path


def _safe_session_name(name: str) -> str:
    """Replace characters that are invalid in tmux session names (. and :)."""
    return re.sub(r"[.:]", "-", name)


def _session_exists(session_name: str) -> bool:
    """Check if a tmux session with the given name exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def prepare_tmux_session(repo_name: str, path: Path) -> str:
    """Ensure a tmux session exists for *repo_name* and return the session name.

    Creates the session (detached) if it doesn't already exist.
    """
    session_name = _safe_session_name(repo_name)

    if not _session_exists(session_name):
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name, "-c", str(path)],
            check=True,
        )

    return session_name


def attach_tmux_session(session_name: str) -> None:
    """Attach to an existing tmux session, blocking until detach/exit."""
    if os.environ.get("TMUX"):
        subprocess.run(["tmux", "switch-client", "-t", session_name])
    else:
        subprocess.run(["tmux", "attach-session", "-t", session_name])


def open_in_tmux(repo_name: str, path: Path) -> None:
    """Open or switch to a tmux session rooted at *path*."""
    session_name = prepare_tmux_session(repo_name, path)
    attach_tmux_session(session_name)
