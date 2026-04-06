"""tmux integration via subprocess."""

import os
import re
import subprocess
import unicodedata
from pathlib import Path

from faker import Faker

_fake = Faker()


def _alphanumeric_name(name: str) -> str:
    """Strip non-alphanumeric characters from a name."""
    return re.sub(r"[^a-zA-Z0-9]", "", name)


def slugify(text):
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = text.replace(" ", "-")
    text = re.sub(r"[^a-z-]", "", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def _make_session_name(repo_name: str) -> str:
    """Generate a unique tmux session name: gd-{alphanumeric}-{faker-slug}."""
    clean = _alphanumeric_name(repo_name)
    slug = f"{slugify(_fake.color_name())}-{slugify(_fake.city())}"
    return f"gd-{clean}-{slug}"


def _session_exists(session_name: str) -> bool:
    """Check if a tmux session with the given name exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def list_repo_sessions(repo_name: str) -> list[str]:
    """List all tmux sessions matching gd-{alphanumeric_repo}-*."""
    clean = _alphanumeric_name(repo_name)
    prefix = f"gd-{clean}-"
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    sessions = result.stdout.strip().split("\n")
    return sorted([s for s in sessions if s.startswith(prefix)])


def create_tmux_session(repo_name: str, path: Path) -> str:
    """Create a new detached tmux session with a unique name and return it."""
    for _ in range(10):
        session_name = _make_session_name(repo_name)
        if not _session_exists(session_name):
            break
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, "-c", str(path)],
        check=True,
    )
    return session_name


def kill_tmux_session(session_name: str) -> bool:
    """Kill a tmux session. Returns True on success."""
    result = subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def attach_tmux_session(session_name: str) -> None:
    """Attach to an existing tmux session, blocking until detach/exit."""
    if os.environ.get("TMUX"):
        subprocess.run(["tmux", "switch-client", "-t", session_name])
    else:
        subprocess.run(["tmux", "attach-session", "-t", session_name])


def open_in_tmux(repo_name: str, path: Path) -> None:
    """Create and attach to a new tmux session rooted at *path*."""
    session_name = create_tmux_session(repo_name, path)
    attach_tmux_session(session_name)
