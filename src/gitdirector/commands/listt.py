from concurrent.futures import ThreadPoolExecutor, as_completed

import click
from rich.live import Live
from rich.spinner import Spinner

from ..manager import RepositoryManager
from . import _build_repo_table, console


def register(cli: click.Group):
    @cli.command(name="list")
    def list_repos():
        manager = RepositoryManager()
        paths = sorted(manager.config.repositories, key=lambda p: p.name.lower())

        console.print()
        if not paths:
            console.print("  [dim]No repositories tracked[/dim]\n")
            return

        results = []
        with Live(
            console=console, refresh_per_second=12, transient=True, vertical_overflow="visible"
        ) as live:
            with ThreadPoolExecutor(max_workers=manager.config.max_workers) as executor:
                futures = {
                    executor.submit(manager.get_repository_status, path): path for path in paths
                }
                remaining = len(futures)
                live.update(
                    Spinner("dots", text=f"  [dim]checking {remaining} repositories...[/dim]")
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

        console.print(_build_repo_table(results))

        console.print()
        total = len(paths)
        noun = "repository" if total == 1 else "repositories"
        console.print(f" [green]{total} {noun}[/green]\n")
