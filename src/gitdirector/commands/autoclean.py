import click

from ..config import Config
from ..integrations.tmux import _list_sessions, kill_tmux_session
from . import console


def _list_gd_sessions() -> list[str]:
    return [s for s in _list_sessions() if s.startswith("gd/")]


def _kill_session(name: str) -> bool:
    return kill_tmux_session(name)


def register(cli: click.Group):
    @cli.command()
    @click.argument("target", type=click.Choice(["links", "sessions"]))
    def autoclean(target: str):
        if target == "links":
            _autoclean_links()
        elif target == "sessions":
            _autoclean_sessions()


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


def _autoclean_sessions():
    sessions = _list_gd_sessions()

    if not sessions:
        console.print()
        console.print("  [green]No gitdirector tmux sessions found.[/green]")
        console.print()
        return

    console.print()
    console.print(f"  Found [yellow]{len(sessions)}[/yellow] gitdirector tmux session(s):\n")
    for s in sessions:
        console.print(f"  [dim]•[/dim] {s}", soft_wrap=True)
    console.print()

    if not click.confirm(f"  Kill all {len(sessions)} session(s)?"):
        console.print()
        console.print("  [dim]Cancelled.[/dim]")
        console.print()
        return

    killed = 0
    for s in sessions:
        if _kill_session(s):
            killed += 1
        else:
            console.print(f"  [red]Failed to kill:[/red] {s}", soft_wrap=True)

    console.print()
    console.print(f"  [green]Killed {killed} session(s).[/green]")
    console.print()
