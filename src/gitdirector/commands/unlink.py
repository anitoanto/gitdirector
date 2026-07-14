from pathlib import Path

import click

from ..manager import RepositoryManager
from . import console
from .completion import complete_repository_names


def register(cli: click.Group):
    @cli.command()
    @click.argument(
        "target",
        metavar="PATH|NAME",
        type=click.Path(exists=False),
        shell_complete=complete_repository_names,
    )
    @click.option("--discover", is_flag=True, help="Unlink tracked repositories under PATH")
    def unlink(target: str, discover: bool):
        manager = RepositoryManager()
        if discover:
            success, message, repos = manager.remove_repository(Path(target), discover=True)
        else:
            repo_path, matches, path_attempted = manager.resolve_repository_target(target)
            if repo_path is not None:
                success, message, repos = manager.remove_repository(repo_path)
            elif path_attempted:
                success, message, repos = False, f"No tracked repository at path: {target}", []
            elif matches:
                paths_list = "\n".join(f"  {path}" for path in matches)
                success, message, repos = (
                    False,
                    f"Multiple repositories named '{target}' — use the full path:\n{paths_list}",
                    [],
                )
            else:
                success, message, repos = False, f"No tracked repository named: {target}", []

        console.print()
        if success:
            if repos:
                for repo_path in repos:
                    console.print(f"  [yellow]-[/yellow] {repo_path}")
        else:
            console.print(f"  [red]{message}[/red]")
            console.print()
            raise SystemExit(1)
        console.print()
