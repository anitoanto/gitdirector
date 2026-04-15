from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from rich import box
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from ..manager import RepositoryManager
from ..repo import Repository
from . import console


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


def _pull_one(path: Path) -> tuple[str, bool, str]:
    name = path.name
    if not path.exists() or not (path / ".git").is_dir():
        return name, False, "path not found"
    try:
        repo = Repository(path)
        ok, msg = repo.pull()
        return name, ok, msg
    except Exception as e:
        return name, False, str(e)


def register(cli: click.Group):
    @cli.command()
    @click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
    def pull(yes):
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

        results = []
        with Live(
            console=console, refresh_per_second=12, transient=True, vertical_overflow="visible"
        ) as live:
            with ThreadPoolExecutor(max_workers=manager.config.max_workers) as executor:
                futures = {executor.submit(_pull_one, path): path for path in paths}
                remaining = len(futures)
                live.update(
                    Spinner("dots", text=f"  [dim]pulling {remaining} repositories...[/dim]")
                )
                for future in as_completed(futures):
                    remaining -= 1
                    results.append(future.result())
                    done = len(results)
                    if remaining > 0:
                        live.update(
                            Spinner(
                                "dots",
                                text=f"  [dim]{done} done, {remaining} remaining...[/dim]",
                            )
                        )
                    else:
                        live.update(Spinner("dots", text="  [dim]done[/dim]"))

        table, success_count, failed_count = _build_pull_table(results)
        console.print(table)

        console.print()
        if failed_count:
            noun = "repository" if failed_count == 1 else "repositories"
            console.print(f" [red]{failed_count} {noun} failed[/red]\n")
            raise SystemExit(1)
        else:
            noun = "repository" if success_count == 1 else "repositories"
            console.print(f" [green]{success_count} {noun}[/green]\n")
