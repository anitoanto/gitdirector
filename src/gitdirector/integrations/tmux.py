"""tmux integration via subprocess."""

import os
import re
import shlex
import subprocess
import tempfile
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


def _make_agent_ready_marker() -> Path:
    """Create a unique marker path used to signal agent startup."""
    fd, raw_path = tempfile.mkstemp(prefix="gitdirector-agent-", suffix=".ready")
    os.close(fd)
    marker_path = Path(raw_path)
    try:
        marker_path.unlink()
    except FileNotFoundError:
        pass
    return marker_path


def launch_agent_in_tmux_session(session_name: str, agent_cmd: str) -> Path:
    """Launch an agent in *session_name* and return a startup marker path."""
    normalized_agent_cmd = shlex.join(shlex.split(agent_cmd))
    ready_marker = _make_agent_ready_marker()
    ready_marker_quoted = shlex.quote(str(ready_marker))
    cleanup_script = (
        f"touch {ready_marker_quoted} >/dev/null 2>&1 || true; "
        "clear; "
        f"{normalized_agent_cmd}; "
        "status=$?; "
        f"rm -f {ready_marker_quoted} >/dev/null 2>&1 || true; "
        "tmux detach-client >/dev/null 2>&1 || true; "
        f"tmux kill-session -t {shlex.quote(session_name)} >/dev/null 2>&1 || true; "
        "exit $status"
    )
    subprocess.run(
        [
            "tmux",
            "send-keys",
            "-t",
            session_name,
            f"sh -lc {shlex.quote(cleanup_script)}",
            "Enter",
        ],
        check=False,
    )
    return ready_marker


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

_AGENT_PURPOSES = frozenset({"opencode", "claude", "copilot", "codex"})

_SILENCE_THRESHOLD_SECS = 10


def _normalize_process_command(raw_args: str) -> str:
    token = raw_args.strip().split(" ", 1)[0]
    if not token:
        return ""
    return Path(token).name


def _get_process_snapshot() -> tuple[dict[int, list[int]], dict[int, str]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,args="],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}, {}

    children_by_parent: dict[int, list[int]] = {}
    commands_by_pid: dict[int, str] = {}
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        if match is None:
            continue
        pid = int(match.group(1))
        ppid = int(match.group(2))
        commands_by_pid[pid] = _normalize_process_command(match.group(3))
        children_by_parent.setdefault(ppid, []).append(pid)

    return children_by_parent, commands_by_pid


def _resolve_pane_command(
    pane_pid: int,
    fallback_command: str,
    children_by_parent: dict[int, list[int]],
    commands_by_pid: dict[int, str],
) -> str:
    descendants: list[tuple[int, int, str]] = []
    stack: list[tuple[int, int]] = [(pane_pid, 0)]
    seen: set[int] = set()
    while stack:
        pid, depth = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for child in children_by_parent.get(pid, []):
            child_depth = depth + 1
            stack.append((child, child_depth))
            command = commands_by_pid.get(child, "")
            if command:
                descendants.append((child_depth, child, command))

    if not descendants:
        return fallback_command

    non_shell_descendants = [
        descendant for descendant in descendants if descendant[2].lstrip("-") not in _SHELL_COMMANDS
    ]
    candidates = non_shell_descendants or descendants
    return max(candidates, key=lambda descendant: (descendant[0], descendant[1]))[2]


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
            "#{session_name}|#{window_bell_flag}|#{pane_current_command}|#{pane_dead}|#{window_activity}|#{pane_pid}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}

    pane_rows: list[tuple[str, bool, str, bool, int, int]] = []
    for line in result.stdout.strip().split("\n"):
        if not line or not line.startswith("gd/"):
            continue
        parts = line.split("|", 5)
        if len(parts) < 6:
            continue
        try:
            activity = int(parts[4])
        except (ValueError, IndexError):
            activity = 0
        try:
            pane_pid = int(parts[5])
        except (ValueError, IndexError):
            pane_pid = 0
        pane_rows.append(
            (
                parts[0],
                parts[1] == "1",
                parts[2],
                parts[3] == "1",
                activity,
                pane_pid,
            )
        )

    if not pane_rows:
        return {}

    children_by_parent, commands_by_pid = _get_process_snapshot()
    statuses: dict[str, dict[str, object]] = {}
    for session_name, bell, command, dead, activity, pane_pid in pane_rows:
        effective_command = command
        if pane_pid > 0:
            effective_command = _resolve_pane_command(
                pane_pid,
                command,
                children_by_parent,
                commands_by_pid,
            )
        statuses[session_name] = {
            "bell": bell,
            "command": effective_command,
            "dead": dead,
            "activity": activity,
        }
    return statuses


def resolve_pane_status(purpose: str, command: str, dead: bool, last_activity: int = 0) -> str:
    """Determine pane status from tmux info (without considering bell state).

    Returns either "running" or "idle".
    """
    if dead:
        return "idle"
    clean_cmd = command.lstrip("-")
    is_shell = clean_cmd in _SHELL_COMMANDS
    if is_shell:
        return "idle"
    if purpose in _AGENT_PURPOSES and clean_cmd in _AGENT_PURPOSES and last_activity > 0:
        elapsed = int(time.time()) - last_activity
        if elapsed >= _SILENCE_THRESHOLD_SECS:
            return "idle"
    return "running"
