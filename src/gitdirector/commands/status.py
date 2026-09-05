import click
from rich.console import Group
from rich.spinner import Spinner
from rich.text import Text

from ..manager import RepositoryManager
from ..repo import RepositoryInfo, RepoStatus
from . import console, run_concurrently


def _is_dirty(info: RepositoryInfo) -> bool:
    return info.staged or info.unstaged


def _build_dirty_display(results: list[RepositoryInfo]) -> Text:
    dirty_repos = sorted((r for r in results if _is_dirty(r)), key=lambda r: r.name.lower())
    output = Text()
    for repo in dirty_repos:
        output.append(f"  {repo.name}", style="bold white")
        output.append(f"  {repo.branch or '—'}\n", style="dim")
        for f in repo.staged_files or ():
            output.append("    ")
            output.append("staged:", style="cyan")
            output.append(f"   {f}\n")
        for f in repo.unstaged_files or ():
            output.append("    ")
            output.append("unstaged:", style="yellow")
            output.append(f" {f}\n")
        output.append("\n")
    return output


def _render_progress(results: list[RepositoryInfo], remaining: int):
    display = _build_dirty_display(results)
    if not results and remaining:
        return Spinner("dots", text=f"  [dim]checking {remaining} repositories...[/dim]")
    if remaining:
        return Group(display, Spinner("dots", text=f"  [dim]{remaining} remaining...[/dim]"))
    return display


def register(cli: click.Group):
    @cli.command()
    def status():
        """Show tracked repositories with uncommitted changes"""
        manager = RepositoryManager()
        paths = sorted(manager.config.repositories, key=lambda p: p.name.lower())

        console.print()
        if not paths:
            console.print("  [dim]No repositories linked[/dim]\n")
            return

        results = run_concurrently(
            paths,
            manager.get_repository_status,
            max_workers=manager.config.max_workers,
            verb="checking",
            on_error=lambda path, exc: RepositoryInfo(
                path, path.name, RepoStatus.UNKNOWN, None, str(exc)
            ),
            render=_render_progress,
            transient=False,
        )

        total = len(results)
        dirty = sum(1 for r in results if _is_dirty(r))
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
