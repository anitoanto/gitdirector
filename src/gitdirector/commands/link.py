from pathlib import Path

import click

from ..manager import RepositoryManager
from . import console, print_error


def _print_skipped(skipped: list[Path]) -> None:
    for repo_path in skipped:
        console.print(
            f"  [dim yellow]\\[skipped][/dim yellow] [bright_black]{repo_path}[/bright_black]"
        )


def register(cli: click.Group):
    @cli.command()
    @click.argument("path", type=click.Path(exists=False))
    @click.option("--discover", is_flag=True, help="Track every repository found under PATH")
    def link(path: str, discover: bool):
        """Track a repository, or every repository under a directory"""
        manager = RepositoryManager()
        success, message, repos, skipped = manager.add_repository(Path(path), discover=discover)

        if not success:
            print_error(message)
            raise SystemExit(1)
        console.print()

        if discover:
            console.print(f"  {message}")
        for repo_path in repos:
            console.print(f"  [green]+[/green] {repo_path}")
        _print_skipped(skipped)
        console.print()
