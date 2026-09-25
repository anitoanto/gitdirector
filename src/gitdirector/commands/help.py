"""``gitdirector help`` and the top-level ``--help`` page.

Descriptions come from each command's docstring, so the overview can never
drift from what ``gitdirector COMMAND --help`` says.
"""

import click
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from . import MUTED, console, emit, get_version, print_update_notice

# Commands grouped the way people look for them; anything registered but
# not listed here lands in a trailing "Other" section.
_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Repositories", ("link", "unlink", "list", "status", "pull", "info", "autoclean")),
    ("Sessions", ("console", "cd", "sessions", "panel")),
    ("Headless sessions", ("gd-tmux", "gd-capture", "gd-screenshot", "gd-send", "gd-kill")),
    ("Setup", ("doctor", "completion", "reset", "help")),
)

_OPTIONS = (("-V, --version", "Show the version and exit"), ("-h, --help", "Show help and exit"))


def _usage(command: click.Command) -> str:
    arguments = [p.make_metavar(None) for p in command.params if isinstance(p, click.Argument)]
    return " ".join([command.name or "", *arguments])


def show_help(cli: click.Group | None = None) -> None:
    print_update_notice()
    cli = cli or click.get_current_context().find_root().command
    assert isinstance(cli, click.Group)

    remaining = dict(cli.commands)
    sections = [
        (
            title,
            [
                (_usage(c), c.get_short_help_str(limit=200))
                for n in names
                if (c := remaining.pop(n, None))
            ],
        )
        for title, names in _SECTIONS
    ]
    if remaining:
        sections.append(
            ("Other", [(_usage(c), c.get_short_help_str(limit=200)) for c in remaining.values()])
        )
    sections.append(("Options", list(_OPTIONS)))
    width = max(len(usage) for _, rows in sections for usage, _ in rows)

    console.print(Text.assemble(("GITDIRECTOR", "bold"), " ", (get_version(), MUTED)))
    console.print(cli.help or "")
    console.print()
    console.print(Text.assemble(("Usage: ", "bold"), "gitdirector COMMAND [ARGS]..."))
    for title, rows in sections:
        if not rows:
            continue
        table = Table.grid(padding=(0, 2, 0, 0))
        table.add_column(no_wrap=True, min_width=width)
        table.add_column(style=MUTED)
        for usage, description in rows:
            table.add_row(usage, description)
        console.print()
        console.print(Text(title, style="bold"))
        emit(Padding(table, (0, 0, 0, 2)))
    console.print()
    console.print(Text("Run 'gitdirector COMMAND --help' for a command's options.", style=MUTED))


def register(cli: click.Group):
    @cli.command()
    def help():
        """Show this overview"""
        show_help(cli)
