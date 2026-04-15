import subprocess

import click

from ..config import Config
from ..integrations.tmux import list_all_gd_sessions
from . import console


def _list_gd_sessions() -> list[str]:
    """List all tmux sessions using the shared GitDirector session parser."""
    return [entry["session_name"] for entry in list_all_gd_sessions()]


def _kill_session(name: str) -> bool:
    result = subprocess.run(
        ["tmux", "kill-session", "-t", name],
        capture_output=True,
    )
    return result.returncode == 0


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
        console.print(f"  [red]✕[/red] {p}")
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
        console.print(f"  [dim]•[/dim] {s}")
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
            console.print(f"  [red]Failed to kill:[/red] {s}")

    console.print()
    console.print(f"  [green]Killed {killed} session(s).[/green]")
    console.print()
