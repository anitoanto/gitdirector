from pathlib import Path

import click

from ..info import RepoInfoResult, gather_repo_info
from ..manager import RepositoryManager
from . import console, print_error
from .completion import complete_repository_names


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
    @click.argument("target", metavar="PATH|NAME", shell_complete=complete_repository_names)
    @click.option(
        "--full",
        is_flag=True,
        default=False,
        help="List every file extension instead of the top 10",
    )
    def info(target: str, full: bool):
        """Show file, line, and token statistics for a repository

        PATH|NAME is a tracked repository, or the path of any git repository.
        """
        manager = RepositoryManager()
        repo_path, matches, _path_attempted = manager.resolve_repository_target(
            target,
            allow_untracked_git_path=True,
            fuzzy_names=True,
        )
        if repo_path is None:
            if not matches:
                print_error(f"Repository '{target}' not found")
            else:
                listing = "\n".join(f"  {match}" for match in matches)
                print_error(f"Multiple repositories match '{target}':\n{listing}")
            raise SystemExit(1)

        result = gather_repo_info(repo_path, full=full)
        _render_info_cli(result, repo_path.name, repo_path)
