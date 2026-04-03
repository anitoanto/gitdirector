from pathlib import Path

import click

from ..manager import RepositoryManager
from . import console


def register(cli: click.Group):
    @cli.command()
    @click.argument("path", type=click.Path(exists=False))
    @click.option("--discover", is_flag=True, help="Recursively discover repositories")
    def link(path: str, discover: bool):
        manager = RepositoryManager()
        success, message, repos, skipped = manager.add_repository(Path(path), discover=discover)

        console.print()
        if success:
            if discover:
                console.print(f"  {message}")
                for repo_path in repos:
                    console.print(f"  [green]+[/green] {repo_path}")
                for repo_path in skipped:
                    console.print(
                        f"  [dim yellow]\\[skipped][/dim yellow] "
                        f"[bright_black]{repo_path}[/bright_black]"
                    )
            else:
                for repo_path in repos:
                    console.print(f"  [green]+[/green] {repo_path}")
        else:
            console.print(f"  [red]{message}[/red]")
            console.print()
            raise SystemExit(1)
        console.print()
