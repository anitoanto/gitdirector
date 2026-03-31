from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .manager import RepositoryManager
from .repo import RepoStatus

console = Console()


def show_help():
    help_text = """
GitDirector - Manage multiple git repositories with ease

COMMANDS:
  add PATH [--discover]       Add repository to tracking
                              --discover: recursively find all repos in PATH

  remove PATH [--discover]    Remove repository from tracking
                              --discover: recursively remove all repos under PATH

  list                        List all tracked repositories with status

  status                      Show status of all tracked repositories

  pull                        Pull latest changes from all tracked repositories

  help                        Show this help message

EXAMPLES:
  gitdirector add /path/to/repo
  gitdirector add /path/to/folder --discover
  gitdirector remove /path/to/repo
  gitdirector remove /path/to/folder --discover
  gitdirector list
  gitdirector status
  gitdirector pull
"""
    console.print(help_text)


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
    success, message, repos = manager.add_repository(Path(path), discover=discover)

    if success:
        console.print(f"[green]{message}[/green]")
        if repos:
            for repo_path in repos:
                console.print(f"  [cyan]✓[/cyan] {repo_path}")
    else:
        console.print(f"[red]Error: {message}[/red]")
        raise SystemExit(1)


@cli.command()
@click.argument("path", type=click.Path(exists=False))
@click.option("--discover", is_flag=True, help="Recursively discover repositories to remove")
def remove(path: str, discover: bool):
    manager = RepositoryManager()
    success, message, repos = manager.remove_repository(Path(path), discover=discover)

    if success:
        console.print(f"[green]{message}[/green]")
        if repos:
            for repo_path in repos:
                console.print(f"  [yellow]✓[/yellow] {repo_path}")
    else:
        console.print(f"[red]Error: {message}[/red]")
        raise SystemExit(1)


@cli.command()
def list():
    manager = RepositoryManager()
    repos = manager.list_repositories()

    if not repos:
        console.print("[yellow]No repositories tracked[/yellow]")
        return

    table = Table(title="Tracked Repositories", show_header=True, header_style="bold cyan")
    table.add_column("Repository", style="cyan")
    table.add_column("Status")
    table.add_column("Branch", style="magenta")
    table.add_column("Details", style="dim")

    for repo in repos:
        status_color = _get_status_color(repo.status)
        status_text = f"[{status_color}]{repo.status.value}[/{status_color}]"

        table.add_row(
            repo.name,
            status_text,
            repo.branch or "N/A",
            repo.message or "",
        )

    console.print(table)


@cli.command()
def status():
    manager = RepositoryManager()
    repos = manager.list_repositories()

    if not repos:
        console.print("[yellow]No repositories tracked[/yellow]")
        return

    total = len(repos)
    up_to_date = sum(1 for r in repos if r.status == RepoStatus.UP_TO_DATE)
    behind = sum(1 for r in repos if r.status == RepoStatus.BEHIND)
    ahead = sum(1 for r in repos if r.status == RepoStatus.AHEAD)
    diverged = sum(1 for r in repos if r.status == RepoStatus.DIVERGED)
    unknown = sum(1 for r in repos if r.status == RepoStatus.UNKNOWN)

    summary = f"""
Total Repositories: {total}
Up to Date: {up_to_date}
Behind: {behind}
Ahead: {ahead}
Diverged: {diverged}
Unknown: {unknown}
"""

    console.print(Panel(summary.strip(), title="Repository Status Summary", border_style="cyan"))

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Repository", style="cyan")
    table.add_column("Status")
    table.add_column("Branch", style="magenta")
    table.add_column("Details")

    for repo in repos:
        status_color = _get_status_color(repo.status)
        status_text = f"[{status_color}]{repo.status.value}[/{status_color}]"

        table.add_row(
            repo.name,
            status_text,
            repo.branch or "N/A",
            repo.message or "",
        )

    console.print(table)


@cli.command()
def pull():
    manager = RepositoryManager()

    if manager.get_repository_count() == 0:
        console.print("[yellow]No repositories tracked[/yellow]")
        return

    console.print("[cyan]Pulling repositories...[/cyan]")
    success, failed = manager.pull_all()

    if success:
        console.print("[green]Successful pulls:[/green]")
        for msg in success:
            console.print(f"  [green]✓[/green] {msg}")

    if failed:
        console.print("[red]Failed pulls:[/red]")
        for msg in failed:
            console.print(f"  [red]✗[/red] {msg}")
        raise SystemExit(1)
    else:
        console.print("[green]All repositories pulled successfully[/green]")


@cli.command()
def help():
    show_help()


def _get_status_color(status: RepoStatus) -> str:
    colors = {
        RepoStatus.UP_TO_DATE: "green",
        RepoStatus.BEHIND: "yellow",
        RepoStatus.AHEAD: "cyan",
        RepoStatus.DIVERGED: "red",
        RepoStatus.UNKNOWN: "dim",
    }
    return colors.get(status, "white")


def main():
    try:
        cli()
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
