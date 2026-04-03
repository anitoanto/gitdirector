from concurrent.futures import ThreadPoolExecutor, as_completed

import click
from rich.console import Group
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from ..manager import RepositoryManager
from ..repo import RepositoryInfo
from . import console


def _build_dirty_display(results: list[RepositoryInfo]) -> Text:
    dirty_repos = sorted(
        [r for r in results if r.staged or r.unstaged], key=lambda r: r.name.lower()
    )
    output = Text()
    for repo in dirty_repos:
        output.append(f"  {repo.name}", style="bold white")
        output.append(f"  {repo.branch or '—'}\n", style="dim")
        if repo.staged_files:
            for f in repo.staged_files:
                output.append("    ")
                output.append("staged:", style="cyan")
                output.append(f"   {f}\n")
        if repo.unstaged_files:
            for f in repo.unstaged_files:
                output.append("    ")
                output.append("unstaged:", style="yellow")
                output.append(f" {f}\n")
        output.append("\n")
    return output


def register(cli: click.Group):
    @cli.command()
    def status():
        manager = RepositoryManager()
        paths = sorted(manager.config.repositories, key=lambda p: p.name.lower())

        console.print()
        if not paths:
            console.print("  [dim]No repositories linked[/dim]\n")
            return

        results = []
        with Live(console=console, refresh_per_second=12, transient=False) as live:
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
                    display = _build_dirty_display(results)
                    if remaining > 0:
                        live.update(
                            Group(
                                display,
                                Spinner("dots", text=f"  [dim]{remaining} remaining...[/dim]"),
                            )
                        )
                    else:
                        live.update(display)

        total = len(results)
        dirty = sum(1 for r in results if r.staged or r.unstaged)
        clean = total - dirty

        if not dirty:
            console.print("  [dim]All repositories are clean[/dim]")
            console.print()

        summary = Text(" ")
        summary.append(str(total), style="bold white")
        summary.append(" repositories", style="dim")
        summary.append("    ")
        summary.append(f"{clean} clean", style="green")
        if dirty:
            summary.append(f"    {dirty} changed", style="yellow")

        console.print(summary)
        console.print()
