import click

from ..manager import RepositoryManager
from . import console


def register(cli: click.Group):
    @cli.command()
    @click.argument("name")
    def cd(name: str):
        """Open or switch to a tmux session rooted at a tracked repository."""
        manager = RepositoryManager()
        matches = [r for r in manager.config.repositories if r.name == name]

        if not matches:
            console.print(f"\n  [red]No tracked repository named: {name}[/red]\n")
            raise SystemExit(1)

        if len(matches) > 1:
            paths_list = "\n".join(f"  {p}" for p in matches)
            console.print(
                f"\n  [red]Multiple repositories named '{name}' — use the full path:[/red]\n"
                f"{paths_list}\n"
            )
            raise SystemExit(1)

        try:
            from ..integrations.tmux import open_in_tmux
        except ImportError:
            console.print(
                "\n  [red]libtmux is required for the cd command.[/red]\n"
                "  Install it with: [dim]pip install libtmux[/dim]\n"
            )
            raise SystemExit(1)

        open_in_tmux(name, matches[0])
