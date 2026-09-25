"""``sessions`` and ``gd-kill``, plus the session start shared by ``cd`` and ``gd-tmux``."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import click
from rich.text import Text

from ..agents import AGENTS, AGENTS_BY_KEY, CLAUDE_MODES, AgentSpec
from . import (
    ATTENTION,
    MUTED,
    SUCCESS,
    CommandError,
    console,
    count_noun,
    print_json,
    print_rows,
    require_gd_session_name,
    summary_line,
)
from .completion import complete_session_names

_STATUS_STYLE = {
    "waiting": ("● waiting", ATTENTION),
    "running": ("● running", "green"),
    "idle": ("○ idle", MUTED),
}

AGENT_CHOICE = click.Choice([agent.key for agent in AGENTS])
MODE_CHOICE = click.Choice([mode.key for mode in CLAUDE_MODES])


@contextmanager
def tmux_errors(action: str) -> Iterator[None]:
    """Report a tmux failure as one plain sentence instead of a traceback."""
    from ..integrations.tmux.core import TmuxError, explain_tmux_failure

    try:
        yield
    except TmuxError as exc:
        raise CommandError(f"{action}: {explain_tmux_failure(exc)}") from exc


def resolve_agent(agent_key: str | None, mode: str | None) -> AgentSpec | None:
    if agent_key is None:
        if mode is not None:
            raise click.UsageError("--mode needs --agent")
        return None
    agent = AGENTS_BY_KEY[agent_key]
    if mode is not None and agent.mode(mode) is None:
        raise click.UsageError(f"--mode is not supported by {agent.label}")
    return agent


def start_session(
    repo_path: Path,
    *,
    command: str | None = None,
    agent: AgentSpec | None = None,
    mode: str | None = None,
    description: str | None = None,
    on_created=None,
) -> str:
    """Create a session in *repo_path* running a shell, *command*, or *agent*.

    *on_created* sees the name before the program starts. A failure after
    the session exists removes it again.
    """
    from ..integrations.tmux import (
        create_tmux_session,
        kill_tmux_session,
        launch_command_in_tmux_session,
    )

    if agent is not None:
        command = agent.launch_command_for(mode)
        purpose = agent.purpose_for(mode)
    else:
        purpose = "shell"
    session_name: str | None = None
    try:
        with tmux_errors("Couldn't start the session"):
            session_name = create_tmux_session(
                repo_path.name,
                repo_path,
                purpose=purpose,
                description=description,
                shell=command is None,
            )
            if on_created is not None:
                on_created(session_name)
            if command is not None:
                launch_command_in_tmux_session(session_name, command)
    except BaseException:
        if session_name is not None:
            kill_tmux_session(session_name)
        raise
    return session_name


def live_sessions() -> list[dict[str, Any]]:
    """Every live ``gd/*`` session with its status, as the Sessions tab shows it.

    One monitor sample: agents that report their own status are exact;
    anything else is judged from the pane as of this moment.
    """
    from ..integrations.tmux import TmuxMonitor

    monitor = TmuxMonitor()
    statuses = monitor.refresh()
    sessions = []
    for entry in monitor.entries() or []:
        name = entry["session_name"]
        sequence = int(name.rsplit("/", 1)[-1])
        sessions.append(
            {
                "session": name,
                "repo": entry["repo"],
                "purpose": entry["purpose"],
                "status": statuses.get(name, "idle"),
                "description": None if entry["description"] == "-" else entry["description"],
                "_order": (entry["repo"].lower(), entry["repo_slug"], entry["purpose"], sequence),
            }
        )
    sessions.sort(key=lambda session: session.pop("_order"))
    return sessions


def _print_sessions(sessions: list[dict[str, Any]]) -> None:
    rows = []
    previous_repo = None
    for session in sessions:
        label, style = _STATUS_STYLE.get(session["status"], (session["status"], ""))
        repo = session["repo"] if session["repo"] != previous_repo else ""
        previous_repo = session["repo"]
        rows.append(
            (
                Text(repo, style="bold"),
                Text(label, style=style),
                Text(session["session"]),
                Text(session["description"] or "-", style="" if session["description"] else MUTED),
            )
        )
    print_rows(("REPOSITORY", "STATUS", "SESSION", "DESCRIPTION"), rows)
    counts = {status: 0 for status in _STATUS_STYLE}
    for session in sessions:
        counts[session["status"]] = counts.get(session["status"], 0) + 1
    console.print()
    console.print(
        summary_line(
            count_noun(len(sessions), "session"),
            *(
                (f"{counts[status]} {status}", style)
                for status, style in (("waiting", ATTENTION), ("running", SUCCESS))
                if counts[status]
            ),
        )
    )


def register(cli: click.Group):
    @cli.command()
    @click.option("--json", "as_json", is_flag=True, help="Print JSON instead of a table")
    def sessions(as_json: bool):
        """List live sessions and their status

        waiting: the program needs you (a permission prompt, a question, a
        bell). running: it is working. idle: nothing is happening.
        """
        entries = live_sessions()
        if as_json:
            print_json(entries)
        elif not entries:
            console.print("No live sessions. Start one with: gitdirector cd PATH|NAME")
        else:
            _print_sessions(entries)

    @cli.command("gd-kill")
    @click.argument("session_name", metavar="SESSION", shell_complete=complete_session_names)
    def gd_kill(session_name: str):
        """End a live session and everything in it

        Prefer 'gd-send SESSION --key C-c' to stop a program gracefully.
        """
        from ..integrations.tmux import kill_tmux_session, sync_panel_tmux_config

        require_gd_session_name(session_name)
        if not kill_tmux_session(session_name):
            raise CommandError(f"session {session_name!r} is not running")
        sync_panel_tmux_config()
        click.echo(f"Killed {session_name}")
