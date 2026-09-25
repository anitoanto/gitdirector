from __future__ import annotations

from pathlib import Path

import click

from . import CommandError, require_gd_session_name
from .completion import complete_session_names
from .sessions import tmux_errors


def register(cli: click.Group):
    @cli.command("gd-screenshot")
    @click.argument("session_name", metavar="SESSION", shell_complete=complete_session_names)
    @click.argument("path", type=click.Path(dir_okay=False, path_type=Path))
    def gd_screenshot(session_name: str, path: Path) -> None:
        """Save a live session's screen as a PNG image

        Draws exactly what the session shows right now (layout, colours, and
        cursor) into PATH, which must end in .png; an existing file is
        replaced. Prints the saved path. gd-capture's text is usually enough;
        this is for when the layout or colours matter.

        \b
        Example:
          gitdirector gd-screenshot "$SESSION" /tmp/session.png
        """
        from ..config import Config
        from ..integrations.tmux import capture_screen
        from ..screenshot import render_png, terminal_theme
        from ..ui_theme import resolve_panel_theme

        require_gd_session_name(session_name)
        if path.suffix.lower() != ".png":
            raise click.UsageError("PATH must end in .png")
        path = path.expanduser().absolute()
        if not path.parent.is_dir():
            raise CommandError(f"directory does not exist: {path.parent}")

        with tmux_errors("Couldn't read the session"):
            screen = capture_screen(session_name)
        if screen is None:
            raise CommandError(f"session {session_name!r} is not running")

        colors = resolve_panel_theme(Config().theme)
        try:
            render_png(screen, path, terminal_theme(colors.background, colors.foreground))
        except OSError as exc:
            raise CommandError(f"couldn't write {path}: {exc.strerror or exc}") from exc
        click.echo(str(path))
