import click
from rich.table import Table

from . import __version__, console


def show_help():
    console.print()
    console.print(
        f" [bold white]GITDIRECTOR[/bold white]  "
        f"[dim]v{__version__} - Manage multiple git repositories[/dim]\n"
    )

    console.print(" [dim]Commands[/dim]\n")

    cmd_table = Table(
        box=None,
        show_header=False,
        show_edge=False,
        padding=(0, 2),
        expand=False,
    )
    cmd_table.add_column("cmd", style="white", no_wrap=True)
    cmd_table.add_column("desc", style="dim")

    for cmd, desc in [
        ("link PATH [--discover]", "Link a repository or discover all repos under a path"),
        ("unlink PATH [--discover]", "Unlink a repository or all repos under a path"),
        ("list", "List all tracked repositories"),
        ("status", "Show status summary and per-repo details"),
        ("pull", "Pull latest changes for all tracked repositories"),
        ("cd NAME", "Open or switch to a tmux session for a repository"),
        ("console", "Interactive TUI for browsing and opening repositories"),
        ("autoclean links|sessions", "Clean broken links or stale tmux sessions"),
        ("help", "Show this help message"),
    ]:
        cmd_table.add_row(cmd, desc)

    console.print(cmd_table)

    console.print()


def register(cli: click.Group):
    @cli.command()
    def help():
        show_help()
