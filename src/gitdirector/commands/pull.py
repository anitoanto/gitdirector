from pathlib import Path

import click
from rich import box
from rich.table import Table
from rich.text import Text

from ..manager import RepositoryManager
from ..repo import Repository, is_git_repository
from . import console, count_noun, run_concurrently


def _pull_table() -> Table:
    table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        show_header=True,
        header_style="bold",
        show_edge=False,
        padding=(0, 1),
    )
    table.add_column("REPOSITORY", ratio=3)
    table.add_column("RESULT", ratio=6)
    return table


def _build_pull_table(results: list) -> tuple[Table, int, int]:
    table = _pull_table()
    success_count = 0
    failed_count = 0
    for name, ok, msg in sorted(results, key=lambda r: r[0].lower()):
        if ok:
            table.add_row(name, Text(msg, style="green"))
            success_count += 1
        else:
            table.add_row(name, Text(msg, style="red"))
            failed_count += 1
    return table, success_count, failed_count


def pull_repository(path: Path) -> tuple[str, bool, str]:
    name = path.name
    if not is_git_repository(path):
        return name, False, "path not found"
    try:
        repo = Repository(path)
        ok, msg = repo.pull()
        return name, ok, msg
    except (OSError, ValueError) as exc:
        return name, False, str(exc)


def _pull_one(path: Path) -> tuple[str, bool, str]:
    try:
        return pull_repository(path)
    except Exception as exc:
        return path.name, False, str(exc)


def register(cli: click.Group):
    @cli.command()
    @click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt")
    def pull(yes):
        """Fast-forward pull every tracked repository"""
        manager = RepositoryManager()
        paths = sorted(manager.config.repositories, key=lambda p: p.name.lower())

        console.print()
        if not paths:
            console.print("  [dim]No repositories linked[/dim]\n")
            return

        console.print("  [bold]Command:[/bold] git pull --ff-only")
        console.print(f"  [bold]Repositories ({len(paths)}):[/bold]")
        for p in paths:
            console.print(f"    [dim]•[/dim] {p.name}")
        console.print()

        if not yes:
            if not click.confirm("  Proceed?", default=True):
                console.print("  [dim]Aborted[/dim]\n")
                return
            console.print()

        results = run_concurrently(
            paths,
            _pull_one,
            max_workers=manager.config.max_workers,
            verb="pulling",
            on_error=lambda path, exc: (path.name, False, str(exc)),
        )

        table, success_count, failed_count = _build_pull_table(results)
        console.print(table)

        console.print()
        if failed_count:
            failed = count_noun(failed_count, "repository", "repositories")
            console.print(f" [red]{failed} failed[/red]\n")
            raise SystemExit(1)
        console.print(
            f" [green]{count_noun(success_count, 'repository', 'repositories')}[/green]\n"
        )
