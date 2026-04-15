from pathlib import Path

import click

from ..info import RepoInfoResult, gather_repo_info
from ..manager import RepositoryManager
from . import console


def _render_info_cli(result: RepoInfoResult, name: str, path: Path) -> None:
    console.print()
    console.print(f"  [bold white]{name}[/bold white]")
    console.print(f"  [dim]{path}[/dim]")
    console.print()
    console.print(f"  [dim]Files[/dim]      [bold white]{result.total_files:,}[/bold white]")
    console.print(f"  [dim]Lines[/dim]      [bold white]{result.total_lines:,}[/bold white]")
    console.print(f"  [dim]Tokens[/dim]     [bold white]{result.total_tokens:,}[/bold white]")
    console.print(f"  [dim]Max Depth[/dim]  [bold white]{result.max_depth}[/bold white]")
    console.print()

    if result.file_types:
        console.print(
            f"  [dim]{'EXTENSION':<12} {'FILES':>6}   {'LINES':>8}   {'TOKENS':>10}[/dim]"
        )
        for ft in result.file_types:
            lines_str = f"{ft.line_count:,}" if ft.line_count is not None else "-"
            tokens_str = f"{ft.token_count:,}" if ft.token_count is not None else "-"
            console.print(
                f"  [cyan]{ft.extension:<12}[/cyan] [white]{ft.count:>6}[/white]"
                f"   [dim]{lines_str:>8}[/dim]"
                f"   [dim]{tokens_str:>10}[/dim]"
            )
    console.print()


def register(cli: click.Group):
    @cli.command()
    @click.argument("target", metavar="PATH|NAME")
    @click.option(
        "--full",
        is_flag=True,
        default=False,
        help="Show all file extensions without the top 10 cap.",
    )
    def info(target: str, full: bool):
        """Show file statistics for a repository."""
        manager = RepositoryManager()
        path = Path(target).resolve()

        if path.is_dir() and (path / ".git").is_dir():
            repo_path = path
        else:
            repos = manager.config.repositories
            exact = [r for r in repos if r.name == target]
            if exact:
                matches = exact
            else:
                matches = [r for r in repos if target.lower() in r.name.lower()]

            if not matches:
                console.print(f"\n  [red]Repository '{target}' not found[/red]\n")
                raise SystemExit(1)
            if len(matches) > 1:
                console.print(f"\n  [red]Multiple repositories match '{target}':[/red]")
                for m in matches:
                    console.print(f"    {m}")
                console.print()
                raise SystemExit(1)
            repo_path = matches[0]

        result = gather_repo_info(repo_path, full=full)
        _render_info_cli(result, repo_path.name, repo_path)
