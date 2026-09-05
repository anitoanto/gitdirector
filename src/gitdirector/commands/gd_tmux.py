import click

from ..manager import RepositoryManager, describe_resolution_failure
from . import print_error
from .completion import complete_repository_names


def register(cli: click.Group):
    @cli.command()
    @click.argument("target", metavar="PATH|NAME", shell_complete=complete_repository_names)
    @click.argument("command")
    @click.option(
        "--description",
        "-d",
        "description",
        default=None,
        help="Description shown in the dashboard's Sessions tab",
    )
    def gd_tmux(target: str, command: str, description: str | None):
        """Run a command in a new background session for a repository

        Prints the new session name on stdout so scripts can capture it. The
        session self-destructs when the command exits, so redirect output you
        need to keep: "make test 2>&1 | tee /tmp/run.log".
        """
        if not command.strip():
            print_error("Command must not be empty")
            raise SystemExit(1)

        manager = RepositoryManager()
        repo_path, matches, path_attempted = manager.resolve_repository_target(target)
        if repo_path is None:
            print_error(describe_resolution_failure(target, matches, path_attempted))
            raise SystemExit(1)

        from ..integrations.tmux import (
            TmuxError,
            create_tmux_session,
            kill_tmux_session,
            launch_command_in_tmux_session,
        )

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
            print_error(f"tmux command failed: {exc}")
            raise SystemExit(1) from exc
        except BaseException:
            if session_name is not None:
                kill_tmux_session(session_name)
            raise
