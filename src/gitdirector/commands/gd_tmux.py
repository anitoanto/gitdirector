from pathlib import Path

import click

from ..manager import RepositoryManager
from . import console
from .completion import complete_repository_names


def _resolve_repo(target: str) -> Path | None:
    """Resolve *target* to a tracked repository path.

    Tries the value as a filesystem path first. If no path-based match is
    found and the value does not look path-like, falls back to looking it up
    by repository name. Prints a rich error and returns ``None`` on missing
    or ambiguous targets.
    """
    manager = RepositoryManager()
    repo_path, matches, path_attempted = manager.resolve_repository_target(target)
    if repo_path is not None:
        return repo_path
    if path_attempted:
        console.print(f"\n  [red]No tracked repository at path: {target}[/red]\n")
        return None
    if not matches:
        console.print(f"\n  [red]No tracked repository named: {target}[/red]\n")
        return None
    if len(matches) > 1:
        paths_list = "\n".join(f"  {p}" for p in matches)
        console.print(
            f"\n  [red]Multiple repositories named '{target}' — use the full path:[/red]\n"
            f"{paths_list}\n"
        )
        return None
    return None


def register(cli: click.Group):
    @cli.command()
    @click.argument("target", metavar="PATH|NAME", shell_complete=complete_repository_names)
    @click.argument("command")
    @click.option(
        "--description",
        "-d",
        "description",
        default=None,
        help="Description stored on the session and shown in the TUI Sessions tab.",
    )
    def gd_tmux(target: str, command: str, description: str | None):
        """Create a gd tmux session for a repository and run a command in it."""
        if not command.strip():
            console.print("\n  [red]Command must not be empty.[/red]\n")
            raise SystemExit(1)

        repo_path = _resolve_repo(target)
        if repo_path is None:
            raise SystemExit(1)

        try:
            from ..integrations.tmux import (
                TmuxError,
                create_tmux_session,
                kill_tmux_session,
                launch_command_in_tmux_session,
            )
        except ImportError:
            console.print(
                "\n  [red]The tmux integration is unavailable for the gd-tmux command.[/red]\n"
                "  Reinstall gitdirector or check your installation.\n"
            )
            raise SystemExit(1)

        session_name: str | None = None
        try:
            session_name = create_tmux_session(
                repo_path.name, repo_path, purpose="shell", description=description
            )
            click.echo(session_name)
            launch_command_in_tmux_session(session_name, command)
        except TmuxError as exc:
            if session_name is not None:
                kill_tmux_session(session_name)
            console.print(f"\n  [red]tmux command failed:[/red] {exc}\n")
            raise SystemExit(1) from exc
        except BaseException:
            if session_name is not None:
                kill_tmux_session(session_name)
            raise
