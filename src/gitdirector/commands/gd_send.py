from __future__ import annotations

import click

from ..integrations.tmux import send_key_to_session, send_text_to_session
from . import require_gd_session_name
from .completion import complete_session_names

# tmux key names accepted by --key. A fixed list keeps an arbitrary string
# from being interpreted as a key by tmux.
SUPPORTED_KEYS = ("C-c", "C-d", "C-z", "C-l", "Enter", "Escape", "Tab", "Up", "Down")


def register(cli: click.Group):
    @cli.command("gd-send")
    @click.argument("session_name", metavar="SESSION", shell_complete=complete_session_names)
    @click.argument("text", required=False)
    @click.option("--enter", is_flag=True, help="Press Enter after the text")
    @click.option(
        "--key",
        type=click.Choice(SUPPORTED_KEYS),
        help="Send a key instead of text",
    )
    def gd_send(session_name: str, text: str | None, enter: bool, key: str | None) -> None:
        """Send text or a key to a live session

        SESSION is the full session name shown in the dashboard's Sessions
        tab. Text is pasted as-is; add --enter to submit it.
        """
        session_name = require_gd_session_name(session_name)

        if key is not None:
            if text is not None:
                raise click.ClickException("TEXT cannot be used with --key")
            if enter:
                raise click.ClickException("--enter cannot be used with --key")
            ok = send_key_to_session(session_name, key)
            action = f"key {key}"
        else:
            if text is None:
                raise click.ClickException("TEXT or --key is required")
            ok = send_text_to_session(session_name, text, enter=enter)
            action = "text and Enter" if enter else "text"

        if not ok:
            raise click.ClickException(
                f"session {session_name!r} is not running or tmux send failed"
            )

        click.echo(f"Sent {action} to {session_name}")
