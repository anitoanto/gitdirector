"""The ``gd-capture`` command: dump the current scrollback of a live gd session.

gd-tmux sessions are ephemeral — they self-destruct when the command
exits — so this only works for *running* sessions. For finished
sessions, the only option is to redirect the command's own output to a
file (``--description``-style metadata aside):

    gitdirector gd-tmux myrepo "make test" 2>&1 | tee /tmp/run.log

This command targets a session by its full gd/ name (``gd/repo/purpose/N``)
so it is unambiguous even when several sessions for the same repo are
running. Pass the full name verbatim, or list candidates with
``gitdirector console`` → Sessions tab.
"""

from __future__ import annotations

import click

from ..integrations.tmux import capture_pane
from ..integrations.tmux.core import _parse_gd_session_name


def _resolve_session_name(name: str) -> str:
    """Validate that *name* looks like a gd session and return it unchanged.

    gd-capture refuses anything that doesn't match the ``gd/{repo}/{purpose}/{N}``
    shape — that way a typo can't be silently routed to the wrong session.
    """
    parsed = _parse_gd_session_name(name)
    if parsed is None:
        raise click.BadParameter(
            f"expected a gd session name of the form gd/<repo>/<purpose>/<N>; got {name!r}"
        )
    return name


def register(cli: click.Group):
    @cli.command()
    @click.argument("session_name", metavar="SESSION")
    @click.option(
        "--lines",
        "-n",
        "lines",
        type=int,
        default=200,
        show_default=True,
        help="Number of trailing lines to capture (ignored when --full is set).",
    )
    @click.option(
        "--full",
        is_flag=True,
        default=False,
        help="Capture the entire scrollback history instead of the last --lines.",
    )
    def gd_capture(session_name: str, lines: int, full: bool) -> None:
        """Print the current scrollback of a live gd tmux session.

        \b
        SESSION is the full session name as shown in the TUI Sessions tab,
        e.g. ``gd/myrepo/shell/1``. Only running sessions can be
        captured — finished gd-tmux sessions self-destruct, so for those
        you must have redirected the command's output to a file.

        Output is plain text on stdout (pipeable). Errors go to stderr.
        """
        try:
            session_name = _resolve_session_name(session_name)
        except click.BadParameter as exc:
            raise click.ClickException(str(exc)) from exc

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
