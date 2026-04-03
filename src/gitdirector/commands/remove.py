from pathlib import Path

import click

from ..manager import RepositoryManager
from . import console


def register(cli: click.Group):
    @cli.command()
    @click.argument("target", type=click.Path(exists=False))
    @click.option("--discover", is_flag=True, help="Recursively discover repositories to remove")
    def remove(target: str, discover: bool):
        manager = RepositoryManager()
        success, message, repos = manager.remove_repository(Path(target), discover=discover)

        # If path-based lookup failed and the target looks like a plain name, try by name.
        # Treat the following as paths (not names): contains a separator, is '.' or '..', is
        # absolute, starts with '~', or refers to an existing filesystem entry.
        if not success and not discover:
            path_obj = Path(target)
            is_path_like = (
                "/" in target
                or "\\" in target
                or target in (".", "..")
                or path_obj.is_absolute()
                or target.startswith("~")
                or path_obj.exists()
            )
            if not is_path_like:
                success, message, repos = manager.remove_by_name(target)

        console.print()
        if success:
            console.print(f"  {message}")
            if repos:
                for repo_path in repos:
                    console.print(f"  [yellow]-[/yellow] {repo_path}")
        else:
            console.print(f"  [red]{message}[/red]")
            console.print()
            raise SystemExit(1)
        console.print()
