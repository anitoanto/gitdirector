from pathlib import Path

import click
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .manager import RepositoryManager
from .repo import RepoStatus

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


def _repo_table() -> Table:
    table = Table(
        box=box.SIMPLE_HEAD,
        expand=True,
        show_header=True,
        header_style="bold",
        show_edge=False,
        padding=(0, 1),
    )
    table.add_column("REPOSITORY", ratio=4)
    table.add_column("STATUS", no_wrap=True, ratio=2)
    table.add_column("BRANCH", style="dim", no_wrap=True, ratio=2)
    table.add_column("DETAILS", style="dim", ratio=3)
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
    repos = manager.list_repositories()

    console.print()
    if not repos:
        console.print("  [dim]No repositories tracked[/dim]\n")
        return

    table = _repo_table()
    for repo in repos:
        table.add_row(
            repo.name,
            _status_text(repo.status),
            repo.branch or "—",
            repo.message or "",
        )

    console.print(table)
    console.print()


@cli.command()
def status():
    manager = RepositoryManager()
    repos = manager.list_repositories()

    console.print()
    if not repos:
        console.print("  [dim]No repositories tracked[/dim]\n")
        return

    total = len(repos)
    up_to_date = sum(1 for r in repos if r.status == RepoStatus.UP_TO_DATE)
    behind = sum(1 for r in repos if r.status == RepoStatus.BEHIND)
    ahead = sum(1 for r in repos if r.status == RepoStatus.AHEAD)
    diverged = sum(1 for r in repos if r.status == RepoStatus.DIVERGED)
    unknown = sum(1 for r in repos if r.status == RepoStatus.UNKNOWN)

    summary = Text(" ")
    summary.append(str(total), style="bold white")
    summary.append(" repositories", style="dim")
    summary.append("    ")
    summary.append(f"{up_to_date} up to date", style="green")
    if behind:
        summary.append(f"    {behind} behind", style="yellow")
    if ahead:
        summary.append(f"    {ahead} ahead", style="cyan")
    if diverged:
        summary.append(f"    {diverged} diverged", style="red")
    if unknown:
        summary.append(f"    {unknown} unknown", style="bright_black")

    console.print(summary)
    console.print()

    table = _repo_table()
    for repo in repos:
        table.add_row(
            repo.name,
            _status_text(repo.status),
            repo.branch or "—",
            repo.message or "",
        )

    console.print(table)
    console.print()


@cli.command()
def pull():
    manager = RepositoryManager()

    if manager.get_repository_count() == 0:
        console.print("\n  [dim]No repositories tracked[/dim]\n")
        return

    console.print("\n  [dim]Pulling repositories...[/dim]")
    success_results, failed_results = manager.pull_all()

    console.print()
    if success_results:
        for msg in success_results:
            console.print(f"  [green]+[/green] {msg}")

    if failed_results:
        console.print()
        for msg in failed_results:
            console.print(f"  [red]-[/red] {msg}")
        console.print()
        raise SystemExit(1)
    else:
        count = len(success_results)
        noun = "repository" if count == 1 else "repositories"
        console.print()
        console.print(f"  [green]{count} {noun} updated[/green]\n")


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
