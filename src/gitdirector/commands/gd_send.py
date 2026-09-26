from __future__ import annotations

import click

from . import CommandError, require_gd_session_name
from .completion import complete_session_names
from .sessions import tmux_errors

# A fixed list keeps an arbitrary string from being interpreted as a key by tmux.
SUPPORTED_KEYS = ("C-c", "C-d", "C-z", "C-l", "Enter", "Escape", "Tab", "Up", "Down")


def register(cli: click.Group):
    @cli.command("gd-send")
    @click.argument("session_name", metavar="SESSION", shell_complete=complete_session_names)
    @click.argument("text", required=False)
    @click.option("--enter", is_flag=True, help="Press Enter after the text")
    @click.option("--key", type=click.Choice(SUPPORTED_KEYS), help="Send a key instead of text")
    def gd_send(session_name: str, text: str | None, enter: bool, key: str | None) -> None:
        """Type text or press a key in a live session

        TEXT is pasted as-is, as one paste even across lines; add --enter
        to submit it. --key sends one key,
        e.g. C-c to stop the foreground program.

        \b
        Examples:
          gitdirector gd-send "$SESSION" "run the tests" --enter
          gitdirector gd-send "$SESSION" --key C-c
        """
        from ..integrations.tmux import send_key_to_session, send_text_to_session

        require_gd_session_name(session_name)
        if key is not None:
            if text is not None or enter:
                raise click.UsageError("--key cannot be combined with TEXT or --enter")
            with tmux_errors("Couldn't send to the session"):
                ok = send_key_to_session(session_name, key)
            action = f"key {key}"
        else:
            if text is None:
                raise click.UsageError("pass TEXT or --key")
            with tmux_errors("Couldn't send to the session"):
                ok = send_text_to_session(session_name, text, enter=enter)
            action = "text and Enter" if enter else "text"
        if not ok:
            raise CommandError(f"session {session_name!r} is not running")
        click.echo(f"Sent {action} to {session_name}")
