import click

from ..config import Config
from ..repo import is_git_repository
from . import console, count_noun


def register(cli: click.Group):
    @cli.command()
    @click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt")
    def autoclean(yes: bool):
        """Stop tracking repositories that no longer exist"""
        _autoclean_links(confirm=not yes)


def _autoclean_links(*, confirm: bool = True) -> None:
    config = Config()
    # A path that still exists but is no longer a git checkout is just as
    # broken as a missing one: every other command would refuse it.
    broken = [p for p in config.repositories if not is_git_repository(p)]

    console.print()
    if not broken:
        console.print("  [green]All links are valid.[/green]")
        console.print()
        return

    console.print(f"  Found [yellow]{count_noun(len(broken), 'broken link')}[/yellow]:\n")
    for p in broken:
        console.print(f"  [red]✕[/red] {p}", soft_wrap=True)
    console.print()

    if confirm and not click.confirm("  Remove these broken links?"):
        console.print()
        console.print("  [dim]Cancelled.[/dim]")
        console.print()
        return

    removed = config.remove_repositories(broken)

    console.print()
    console.print(f"  [green]Removed {count_noun(removed, 'broken link')}.[/green]")
    console.print()
