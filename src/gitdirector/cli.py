from pathlib import Path

import click
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .manager import RepositoryManager
from .repo import Repository, RepoStatus

__version__ = "0.1.1"

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


def _changes_text(staged: bool, unstaged: bool) -> Text:
    if staged and unstaged:
        return Text("staged+unstaged", style="yellow")
    elif staged:
        return Text("staged", style="cyan")
    elif unstaged:
        return Text("unstaged", style="yellow")
    return Text("—", style="bright_black")


def _path_text(path: str) -> Text:
    # PATH column is ratio=4 out of total ratio=13 (3+1+1+2+2+4)
    # subtract 2 for the column's own padding (1 char each side)
    col_width = max(10, console.width * 4 // 13 - 5)
    if len(path) > col_width:
        path = "\u2026" + path[-(col_width - 1) :]
    return Text(path, justify="right")


def _repo_table() -> Table:
    table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        show_header=True,
        header_style="bold",
        show_edge=False,
        padding=(0, 1),
    )
    table.add_column("REPOSITORY", ratio=3)
    table.add_column("SYNC", no_wrap=True, ratio=1)
    table.add_column("BRANCH", style="dim", no_wrap=True, ratio=1)
    table.add_column("CHANGES", no_wrap=True, ratio=2)
    table.add_column("LAST COMMIT", style="dim", no_wrap=True, ratio=2)
    table.add_column("PATH", style="dim", ratio=4, no_wrap=True, justify="right")
    return table


def show_help():
    console.print()
    console.print(
        f" [bold white]GITDIRECTOR[/bold white]  [dim]v{__version__} - Manage multiple git repositories[/dim]\n"
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
    console.print(" [dim]Examples[/dim]\n")
    console.print("  [dim white]gitdirector add /path/to/repo[/dim white]")
    console.print("  [dim white]gitdirector add /path/to/folder --discover[/dim white]")
    console.print("  [dim white]gitdirector status[/dim white]")
    console.print("  [dim white]gitdirector pull[/dim white]")
    console.print()


@click.group(invoke_without_command=True)
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
                    f"  [dim yellow]\\[skipped][/dim yellow] [bright_black]{repo_path}[/bright_black]"
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


@cli.command()
def list():
    manager = RepositoryManager()
    paths = manager.config.repositories

    console.print()
    if not paths:
        console.print("  [dim]No repositories tracked[/dim]\n")
        return

    table = _repo_table()
    with Live(console=console, refresh_per_second=12, transient=False) as live:
        for path in paths:
            name = path.name
            live.update(Group(table, Spinner("dots", text=f"  [dim]checking {name}...[/dim]")))
            info = manager.get_repository_status(path)
            full_path = str(info.path)
            table.add_row(
                info.name,
                _status_text(info.status),
                info.branch or "—",
                _changes_text(info.staged, info.unstaged),
                info.last_updated or "—",
                _path_text(full_path),
                # full_path,
            )
            live.update(table)

    console.print()


@cli.command()
def status():
    manager = RepositoryManager()
    paths = manager.config.repositories

    console.print()
    if not paths:
        console.print("  [dim]No repositories tracked[/dim]\n")
        return

    repos = []
    with Live(console=console, refresh_per_second=12, transient=True) as live:
        for path in paths:
            live.update(Spinner("dots", text=f"  [dim]checking {path.name}...[/dim]"))
            info = manager.get_repository_status(path)
            repos.append(info)

    total = len(repos)
    dirty = sum(1 for r in repos if r.staged or r.unstaged)
    clean = total - dirty

    summary = Text(" ")
    summary.append(str(total), style="bold white")
    summary.append(" repositories", style="dim")
    summary.append("    ")
    summary.append(f"{clean} clean", style="green")
    if dirty:
        summary.append(f"    {dirty} changed", style="yellow")

    console.print(summary)
    console.print()

    # Show per-repo file-level changes
    dirty_repos = [r for r in repos if r.staged or r.unstaged]
    if dirty_repos:
        for repo in dirty_repos:
            console.print(
                f"  [bold white]{repo.name}[/bold white]  [dim]{repo.branch or '—'}[/dim]"
            )
            if repo.staged_files:
                for f in repo.staged_files:
                    console.print(f"    [cyan]staged:[/cyan]   {f}")
            if repo.unstaged_files:
                for f in repo.unstaged_files:
                    console.print(f"    [yellow]unstaged:[/yellow] {f}")
            console.print()
    else:
        console.print("  [dim]All repositories are clean[/dim]")
        console.print()

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


@cli.command()
def pull():
    manager = RepositoryManager()
    paths = manager.config.repositories

    console.print()
    if not paths:
        console.print("  [dim]No repositories tracked[/dim]\n")
        return

    table = _pull_table()
    failed_count = 0
    success_count = 0

    with Live(console=console, refresh_per_second=12, transient=False) as live:
        for path in paths:
            name = path.name
            live.update(Group(table, Spinner("dots", text=f"  [dim]pulling {name}...[/dim]")))
            if not path.exists() or not (path / ".git").is_dir():
                table.add_row(name, Text("path not found", style="red"))
                failed_count += 1
            else:
                try:
                    repo = Repository(path)
                    ok, msg = repo.pull()
                    if ok:
                        table.add_row(name, Text(msg, style="green"))
                        success_count += 1
                    else:
                        table.add_row(name, Text(msg, style="red"))
                        failed_count += 1
                except Exception as e:
                    table.add_row(name, Text(str(e), style="red"))
                    failed_count += 1
            live.update(table)

    console.print()
    if failed_count:
        noun = "repository" if failed_count == 1 else "repositories"
        console.print(f"  [red]{failed_count} {noun} failed[/red]\n")
        raise SystemExit(1)
    else:
        noun = "repository" if success_count == 1 else "repositories"
        console.print(f"  [green]{success_count} {noun} updated[/green]\n")


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
