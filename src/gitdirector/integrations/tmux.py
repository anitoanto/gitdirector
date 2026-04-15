"""tmux integration via subprocess."""

import os
import re
import subprocess
import time
from pathlib import Path


def _sanitize_repo_name(name: str) -> str:
    """Sanitize a repository name for use in tmux session names.

    Keeps lowercase alphanumeric characters and hyphens. Replaces everything
    else with ``-``, collapses consecutive hyphens, and strips leading/trailing
    hyphens.
    """
    name = name.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


def _list_sessions() -> list[str]:
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [s for s in result.stdout.strip().split("\n") if s]


def _next_n(prefix: str, sessions: list[str] | None = None) -> int:
    if sessions is None:
        sessions = _list_sessions()
    max_n = 0
    for s in sessions:
        if s.startswith(prefix):
            tail = s[len(prefix) :]
            if tail.isdigit():
                max_n = max(max_n, int(tail))
    return max_n + 1


def _make_session_name(repo_name: str, purpose: str = "shell") -> str:
    """Generate the next sequential session name: gd/{repo}/{purpose}/{N}."""
    clean = _sanitize_repo_name(repo_name)
    prefix = f"gd/{clean}/{purpose}/"
    n = _next_n(prefix)
    return f"{prefix}{n}"


def _session_exists(session_name: str) -> bool:
    """Check if a tmux session with the given name exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def list_repo_sessions(repo_name: str) -> list[str]:
    """List all tmux sessions for a given repository."""
    clean = _sanitize_repo_name(repo_name)
    prefix = f"gd/{clean}/"
    sessions = _list_sessions()
    return sorted([s for s in sessions if s.startswith(prefix)])


def list_all_gd_sessions() -> list[dict[str, str]]:
    """List all GitDirector tmux sessions (gd/ prefix).

    Returns a list of dicts with keys: session_name, repo, purpose.
    """
    sessions = _list_sessions()
    entries = []
    for s in sorted(sessions):
        if not s.startswith("gd/"):
            continue
        parts = s.split("/")
        if len(parts) < 4:
            continue
        repo = parts[1]
        purpose = parts[2]
        entries.append({"session_name": s, "repo": repo, "purpose": purpose})
    return entries


def create_tmux_session(repo_name: str, path: Path, purpose: str = "shell") -> str:
    """Create a new detached tmux session with a unique name and return it."""
    for _ in range(10):
        session_name = _make_session_name(repo_name, purpose)
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


_SHELL_COMMANDS = frozenset(
    {
        "zsh",
        "bash",
        "fish",
        "sh",
        "dash",
        "tcsh",
        "csh",
        "ksh",
    }
)

_SILENCE_THRESHOLD_SECS = 10


def get_all_session_statuses() -> dict[str, dict[str, object]]:
    """Query tmux for bell, foreground command, and dead status of all gd/ panes.

    Returns a dict keyed by session_name with:
      - bell: bool
      - command: str (foreground process name)
      - dead: bool
    """
    result = subprocess.run(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{session_name}|#{window_bell_flag}|#{pane_current_command}|#{pane_dead}|#{window_activity}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}

    statuses: dict[str, dict[str, object]] = {}
    for line in result.stdout.strip().split("\n"):
        if not line or not line.startswith("gd/"):
            continue
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        try:
            activity = int(parts[4])
        except (ValueError, IndexError):
            activity = 0
        statuses[parts[0]] = {
            "bell": parts[1] == "1",
            "command": parts[2],
            "dead": parts[3] == "1",
            "activity": activity,
        }
    return statuses


def resolve_pane_status(purpose: str, command: str, dead: bool, last_activity: int = 0) -> str:
    """Determine pane status from tmux info (without considering bell state).

    Returns one of: "running", "waiting", "idle".
    """
    if dead:
        return "idle"
    clean_cmd = command.lstrip("-")
    is_shell = clean_cmd in _SHELL_COMMANDS
    if is_shell:
        return "idle"
    if purpose != "shell" and last_activity > 0:
        elapsed = int(time.time()) - last_activity
        if elapsed >= _SILENCE_THRESHOLD_SECS:
            return "waiting"
    return "running"
