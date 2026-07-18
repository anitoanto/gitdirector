from __future__ import annotations

import click

from ..integrations.tmux import send_key_to_session, send_text_to_session
from ..integrations.tmux.core import _parse_gd_session_name

SUPPORTED_KEYS = ("C-c",)


def _resolve_session_name(name: str) -> str:
    parsed = _parse_gd_session_name(name)
    if parsed is None:
        raise click.BadParameter(
            f"expected a gd session name of the form gd/<repo>/<purpose>/<N>; got {name!r}"
        )
    return name


def register(cli: click.Group):
    @cli.command("gd-send")
    @click.argument("session_name", metavar="SESSION")
    @click.argument("text", required=False)
    @click.option("--enter", is_flag=True, help="Press Enter after sending text.")
    @click.option(
        "--key",
        type=click.Choice(SUPPORTED_KEYS),
        help="Send a supported key instead of text. Accepted: C-c.",
    )
    def gd_send(session_name: str, text: str | None, enter: bool, key: str | None) -> None:
        """Send text or a supported key to a live gd tmux session."""
        try:
            session_name = _resolve_session_name(session_name)
        except click.BadParameter as exc:
            raise click.ClickException(str(exc)) from exc

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
