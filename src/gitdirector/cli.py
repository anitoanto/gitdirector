from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib.metadata import version
from pathlib import Path
from typing import Optional

import click
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .manager import RepositoryManager
from .repo import Repository, RepositoryInfo, RepoStatus

__version__ = version("gitdirector")

console = Console(highlight=False)

_STATUS_COLOR = {
    RepoStatus.UP_TO_DATE: "green",
    RepoStatus.BEHIND: "yellow",
    RepoStatus.AHEAD: "cyan",
    RepoStatus.DIVERGED: "red",
    RepoStatus.UNKNOWN: "bright_black",
}

_STATUS_LABEL = {
    RepoStatus.UP_TO_DATE: "up to date",
    RepoStatus.BEHIND: "behind",
    RepoStatus.AHEAD: "ahead",
    RepoStatus.DIVERGED: "diverged",
    RepoStatus.UNKNOWN: "unknown",
}


def _status_text(status: RepoStatus) -> Text:
    color = _STATUS_COLOR.get(status, "white")
    label = _STATUS_LABEL.get(status, status.value)
    return Text(label, style=color)


def _format_size(size: Optional[int]) -> Text:
    if size is None:
        return Text("—", style="bright_black")
    for unit, threshold in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= threshold:
            return Text(f"{size / threshold:.1f} {unit}", style="dim")
    return Text(f"{size} B", style="dim")


def _changes_text(staged: bool, unstaged: bool) -> Text:
    if staged and unstaged:
        return Text("staged+unstaged", style="yellow")
    elif staged:
        return Text("staged", style="cyan")
    elif unstaged:
        return Text("unstaged", style="yellow")
    return Text("—", style="bright_black")


def _path_text(path: str) -> Text:
    col_width = max(10, console.width * 2 // 9 - 6)
    if len(path) > col_width:
        path = "\u2026" + path[-(col_width - 1) :]
    return Text(path, justify="right")


def _build_repo_table(results: list) -> Table:
    table = _repo_table()
    for info in sorted(results, key=lambda r: r.name.lower()):
        table.add_row(
            info.name,
            _status_text(info.status),
            info.branch or "—",
            _changes_text(info.staged, info.unstaged),
            info.last_updated or "—",
            _format_size(info.size),
            _path_text(str(info.path)),
        )
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


def _repo_table() -> Table:
    table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        show_header=True,
        header_style="bold",
        show_edge=False,
        padding=(0, 1),
    )
    table.add_column("REPOSITORY", ratio=2)
    table.add_column("SYNC", no_wrap=True, ratio=1)
    table.add_column("BRANCH", style="dim", no_wrap=True, ratio=1)
    table.add_column("CHANGES", no_wrap=True, ratio=1)
    table.add_column("LAST COMMIT", style="dim", no_wrap=True, ratio=1)
    table.add_column("SIZE", style="dim", no_wrap=True, ratio=1, justify="right")
    table.add_column("PATH", style="dim", ratio=2, no_wrap=True, justify="right")
    return table


def show_help():
    console.print()
    console.print(
        f" [bold white]GITDIRECTOR[/bold white]  "
        f"[dim]v{__version__} - Manage multiple git repositories[/dim]\n"
    )

    console.print(" [dim]Commands[/dim]\n")

    cmd_table = Table(
        box=None,
        show_header=False,
        show_edge=False,
        padding=(0, 2),
        expand=False,
    )
    cmd_table.add_column("cmd", style="white", no_wrap=True)
    cmd_table.add_column("desc", style="dim")

    for cmd, desc in [
        ("add PATH [--discover]", "Add a repository or discover all repos under a path"),
        ("remove PATH [--discover]", "Remove a repository or all repos under a path"),
        ("list", "List all tracked repositories"),
        ("status", "Show status summary and per-repo details"),
        ("pull", "Pull latest changes for all tracked repositories"),
        ("help", "Show this help message"),
    ]:
        cmd_table.add_row(cmd, desc)

    console.print(cmd_table)

    console.print()


class _HelpGroup(click.Group):
    def format_help(self, ctx, formatter):
        show_help()


@click.group(cls=_HelpGroup, invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        show_help()


@cli.command()
@click.argument("path", type=click.Path(exists=False))
@click.option("--discover", is_flag=True, help="Recursively discover repositories")
def add(path: str, discover: bool):
    manager = RepositoryManager()
    success, message, repos, skipped = manager.add_repository(Path(path), discover=discover)

    console.print()
    if success:
        if discover:
            console.print(f"  {message}")
            for repo_path in repos:
                console.print(f"  [green]+[/green] {repo_path}")
            for repo_path in skipped:
                console.print(
                    f"  [dim yellow]\\[skipped][/dim yellow] "
                    f"[bright_black]{repo_path}[/bright_black]"
                )
        else:
            console.print(f"  [green]+[/green] {message}")
    else:
        console.print(f"  [red]{message}[/red]")
        console.print()
        raise SystemExit(1)
    console.print()


@cli.command()
@click.argument("path", type=click.Path(exists=False))
@click.option("--discover", is_flag=True, help="Recursively discover repositories to remove")
def remove(path: str, discover: bool):
    manager = RepositoryManager()
    success, message, repos = manager.remove_repository(Path(path), discover=discover)

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


@cli.command(name="list")
def list_repos():
    manager = RepositoryManager()
    paths = sorted(manager.config.repositories, key=lambda p: p.name.lower())

    console.print()
    if not paths:
        console.print("  [dim]No repositories tracked[/dim]\n")
        return

    with Live(console=console, refresh_per_second=12, transient=False) as live:
        with ThreadPoolExecutor(max_workers=manager.config.max_workers) as executor:
            futures = {executor.submit(manager.get_repository_status, path): path for path in paths}
            remaining = len(futures)
            live.update(
                Group(
                    _repo_table(),
                    Spinner("dots", text=f"  [dim]checking {remaining} repositories...[/dim]"),
                )
            )
            results = []
            for future in as_completed(futures):
                remaining -= 1
                results.append(future.result())
                table = _build_repo_table(results)
                if remaining > 0:
                    live.update(
                        Group(table, Spinner("dots", text=f"  [dim]{remaining} remaining...[/dim]"))
                    )
                else:
                    live.update(table)

    console.print()
    total = len(paths)
    noun = "repository" if total == 1 else "repositories"
    console.print(f" [green]{total} {noun}[/green]\n")


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


@cli.command()
def status():
    manager = RepositoryManager()
    paths = sorted(manager.config.repositories, key=lambda p: p.name.lower())

    console.print()
    if not paths:
        console.print("  [dim]No repositories tracked[/dim]\n")
        return

    results = []
    with Live(console=console, refresh_per_second=12, transient=False) as live:
        with ThreadPoolExecutor(max_workers=manager.config.max_workers) as executor:
            futures = {executor.submit(manager.get_repository_status, path): path for path in paths}
            remaining = len(futures)
            live.update(Spinner("dots", text=f"  [dim]checking {remaining} repositories...[/dim]"))
            for future in as_completed(futures):
                remaining -= 1
                results.append(future.result())
                display = _build_dirty_display(results)
                if remaining > 0:
                    live.update(
                        Group(
                            display, Spinner("dots", text=f"  [dim]{remaining} remaining...[/dim]")
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


@cli.command()
def pull():
    manager = RepositoryManager()
    paths = sorted(manager.config.repositories, key=lambda p: p.name.lower())

    console.print()
    if not paths:
        console.print("  [dim]No repositories tracked[/dim]\n")
        return

    failed_count = 0
    success_count = 0

    with Live(console=console, refresh_per_second=12, transient=False) as live:
        with ThreadPoolExecutor(max_workers=manager.config.max_workers) as executor:
            futures = {executor.submit(_pull_one, path): path for path in paths}
            remaining = len(futures)
            live.update(
                Group(
                    _pull_table(),
                    Spinner("dots", text=f"  [dim]pulling {remaining} repositories...[/dim]"),
                )
            )
            results = []
            for future in as_completed(futures):
                remaining -= 1
                results.append(future.result())
                table, success_count, failed_count = _build_pull_table(results)
                if remaining > 0:
                    live.update(
                        Group(table, Spinner("dots", text=f"  [dim]{remaining} remaining...[/dim]"))
                    )
                else:
                    live.update(table)

    console.print()
    if failed_count:
        noun = "repository" if failed_count == 1 else "repositories"
        console.print(f" [red]{failed_count} {noun} failed[/red]\n")
        raise SystemExit(1)
    else:
        noun = "repository" if success_count == 1 else "repositories"
        console.print(f" [green]{success_count} {noun}[/green]\n")


@cli.command()
def help():
    show_help()


def main():
    try:
        cli()
    except Exception as e:
        console.print(f"\n  [red]Error:[/red] {str(e)}\n")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
