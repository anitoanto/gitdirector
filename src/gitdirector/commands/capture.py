"""The ``gd-capture`` command: dump the current scrollback of a live gd session.

gd-tmux sessions are ephemeral — they self-destruct when the command exits —
so this only works for *running* sessions. For finished sessions, the only
option is to redirect the command's own output to a file:

    gitdirector gd-tmux myrepo "make test 2>&1 | tee /tmp/run.log"

The session is addressed by its full ``gd/<repo>/<purpose>/<N>`` name so it is
unambiguous even when several sessions for the same repo are running.
"""

from __future__ import annotations

import click

from ..integrations.tmux import capture_pane
from . import require_gd_session_name
from .completion import complete_session_names


def register(cli: click.Group):
    @cli.command()
    @click.argument("session_name", metavar="SESSION", shell_complete=complete_session_names)
    @click.option(
        "-n",
        "--lines",
        "lines",
        type=int,
        default=200,
        show_default=True,
        help="Number of trailing lines to print (ignored with --full)",
    )
    @click.option("--full", is_flag=True, help="Print the entire scrollback history")
    def gd_capture(session_name: str, lines: int, full: bool) -> None:
        """Print the scrollback of a live session

        SESSION is the full session name shown in the dashboard's Sessions
        tab, e.g. gd/myrepo/shell/1. Only running sessions can be captured;
        a finished gd-tmux session has already destroyed itself.
        """
        session_name = require_gd_session_name(session_name)

        if lines <= 0 and not full:
            raise click.ClickException("--lines must be a positive integer")

        try:
            content = capture_pane(session_name, lines=lines, full=full)
        except Exception as exc:
            raise click.ClickException(f"failed to capture session: {exc}") from exc

        if content is None:
            raise click.ClickException(
                f"session {session_name!r} is not running or tmux capture failed"
            )

        click.echo(content, nl=False)
