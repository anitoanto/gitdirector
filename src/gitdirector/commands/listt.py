import click

from ..manager import RepositoryManager
from ..repo import RepositoryInfo, RepoStatus
from . import _build_repo_table, console, count_noun, run_concurrently


def register(cli: click.Group):
    @cli.command(name="list")
    def list_repos():
        """List tracked repositories with their sync status"""
        manager = RepositoryManager()
        paths = sorted(manager.config.repositories, key=lambda p: p.name.lower())

        console.print()
        if not paths:
            console.print("  [dim]No repositories linked[/dim]\n")
            return

        results = run_concurrently(
            paths,
            lambda path: manager.get_repository_status(path, fetch=True, include_size=True),
            max_workers=manager.config.max_workers,
            verb="checking",
            on_error=lambda path, exc: RepositoryInfo(
                path, path.name, RepoStatus.UNKNOWN, None, str(exc)
            ),
        )

        console.print(_build_repo_table(results))
        console.print()
        console.print(f" [green]{count_noun(len(paths), 'repository', 'repositories')}[/green]\n")
