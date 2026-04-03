"""tmux integration via libtmux."""

import os
import re
from pathlib import Path

import libtmux


def _safe_session_name(name: str) -> str:
    """Replace characters that are invalid in tmux session names (. and :)."""
    return re.sub(r"[.:]", "-", name)


def open_in_tmux(repo_name: str, path: Path) -> None:
    """Open or switch to a tmux session rooted at *path*.

    Re-uses an existing session when one with the same sanitised name already
    exists.  When called from inside tmux the current client is switched to
    that session.  When called from outside tmux the current process is
    replaced by ``tmux attach-session`` so the terminal is taken over
    correctly.
    """
    session_name = _safe_session_name(repo_name)
    server = libtmux.Server()

    if server.has_session(session_name):
        sessions = server.sessions.filter(session_name=session_name)
        session = sessions[0]
    else:
        session = server.new_session(
            session_name=session_name,
            start_directory=str(path),
            attach=False,
        )

    if os.environ.get("TMUX"):
        # Already inside tmux: switch the current client to the target session.
        server.switch_client(session.session_name)
    else:
        # Outside tmux: replace this process so the terminal is fully handed
        # over to tmux attach-session.
        os.execvp("tmux", ["tmux", "attach-session", "-t", session.session_name])
