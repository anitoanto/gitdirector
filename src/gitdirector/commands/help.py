"""``gitdirector help`` and the top-level ``--help`` page.

Descriptions come from each command's docstring, so the overview can never
drift from what ``gitdirector <command> --help`` says.
"""

import click
from rich.table import Table
from rich.text import Text

from . import console, get_version, print_update_notice

# Commands grouped the way people look for them; anything registered but
# not listed here lands in a trailing "Other" section.
_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Repositories", ("link", "unlink", "list", "status", "pull", "info", "autoclean")),
    ("Sessions", ("console", "cd", "gd-tmux", "gd-capture", "gd-send")),
    ("Setup", ("doctor", "completion", "reset", "help")),
)


def _usage(command: click.Command) -> str:
    return " ".join(
        [
            command.name,
            *(p.make_metavar(None) for p in command.params if isinstance(p, click.Argument)),
        ]
    ).strip()


def show_help(cli: click.Group | None = None) -> None:
    print_update_notice()
    cli = cli or click.get_current_context().find_root().command
    assert isinstance(cli, click.Group)

    console.print()
    console.print(
        f" [bold]GITDIRECTOR[/bold] [dim]{get_version()}[/dim]  "
        f"{cli.help or 'Manage many git repositories and agent sessions from one place.'}"
    )
    console.print()
    console.print(" [bold]Usage:[/bold] gitdirector [OPTIONS] COMMAND [ARGS]...")
    console.print()

    remaining = dict(cli.commands)
    sections = [
        (title, [remaining.pop(name) for name in names if name in remaining])
        for title, names in _SECTIONS
    ]
    if remaining:
        sections.append(("Other", list(remaining.values())))

    for title, commands in sections:
        if not commands:
            continue
        table = Table(box=None, show_header=False, show_edge=False, padding=(0, 2), expand=False)
        table.add_column("cmd", no_wrap=True, min_width=32)
        table.add_column("desc", style="dim")
        for command in commands:
            table.add_row(Text(_usage(command), style="bold"), command.get_short_help_str(limit=90))
        console.print(f" [bold]{title}[/bold]")
        console.print(table)
        console.print()

    console.print(" [bold]Options:[/bold]")
    console.print("   -V, --version      Show the version and exit")
    console.print("   -h, --help         Show this message and exit")
    console.print()
    console.print(" [dim]Run 'gitdirector COMMAND --help' for more information on a command.[/dim]")
    console.print()


def register(cli: click.Group):
    @cli.command()
    def help():
        """Show this overview of commands"""
        show_help(cli)
