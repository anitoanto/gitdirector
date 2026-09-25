from __future__ import annotations

import click

from . import CommandError, require_gd_session_name
from .completion import complete_session_names
from .sessions import tmux_errors


def register(cli: click.Group):
    @cli.command("gd-capture")
    @click.argument("session_name", metavar="SESSION", shell_complete=complete_session_names)
    @click.option(
        "-n",
        "--lines",
        type=click.IntRange(min=1),
        default=200,
        show_default=True,
        help="Number of trailing lines to print",
    )
    @click.option("--full", is_flag=True, help="Print the entire scrollback instead")
    def gd_capture(session_name: str, lines: int, full: bool) -> None:
        """Print a live session's recent output

        SESSION is a full session name as printed by gd-tmux or listed by
        'gitdirector sessions'. A session's output is gone once its program
        exits, so this only works while it runs.
        """
        from ..integrations.tmux import capture_pane

        require_gd_session_name(session_name)
        with tmux_errors("Couldn't read the session"):
            content = capture_pane(session_name, lines=lines, full=full)
        if content is None:
            raise CommandError(f"session {session_name!r} is not running")
        click.echo(content, nl=False)
