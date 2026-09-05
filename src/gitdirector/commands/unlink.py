from pathlib import Path

import click

from ..manager import RepositoryManager, describe_resolution_failure
from . import console, print_error
from .completion import complete_repository_names


def register(cli: click.Group):
    @cli.command()
    @click.argument(
        "target",
        metavar="PATH|NAME",
        type=click.Path(exists=False),
        shell_complete=complete_repository_names,
    )
    @click.option("--discover", is_flag=True, help="Stop tracking every repository under PATH")
    def unlink(target: str, discover: bool):
        """Stop tracking a repository, or every repository under a directory"""
        manager = RepositoryManager()
        if discover:
            success, message, repos = manager.remove_repository(Path(target), discover=True)
        else:
            repo_path, matches, path_attempted = manager.resolve_repository_target(target)
            if repo_path is None:
                success = False
                message = describe_resolution_failure(target, matches, path_attempted)
                repos = []
            else:
                success, message, repos = manager.remove_repository(repo_path)

        if not success:
            print_error(message)
            raise SystemExit(1)
        console.print()
        for repo_path in repos:
            console.print(f"  [yellow]-[/yellow] {repo_path}")
        console.print()
