import click

from ..config import Config
from . import console


def register(cli: click.Group):
    @cli.command()
    def autoclean():
        _autoclean_links()


def _autoclean_links():
    config = Config()
    broken = [p for p in config.repositories if not p.exists()]

    if not broken:
        console.print()
        console.print("  [green]All links are valid.[/green]")
        console.print()
        return

    console.print()
    console.print(f"  Found [yellow]{len(broken)}[/yellow] broken link(s):\n")
    for p in broken:
        console.print(f"  [red]✕[/red] {p}", soft_wrap=True)
    console.print()

    if not click.confirm("  Remove these broken links?"):
        console.print()
        console.print("  [dim]Cancelled.[/dim]")
        console.print()
        return

    config.remove_repositories(broken)

    console.print()
    console.print(f"  [green]Removed {len(broken)} broken link(s).[/green]")
    console.print()
