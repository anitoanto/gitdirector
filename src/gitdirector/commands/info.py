from pathlib import Path

import click
from rich.text import Text

from ..info import RepoInfoResult, gather_repo_info
from ..manager import RepositoryManager
from . import (
    MUTED,
    CommandError,
    console,
    count_noun,
    display_path,
    error_console,
    print_json,
    print_rows,
    summary_line,
)
from .completion import complete_repository_names


def _number(value: int | None) -> Text:
    return Text("-", style=MUTED) if value is None else Text(f"{value:,}")


def _render(result: RepoInfoResult, path: Path) -> None:
    console.print(
        Text.assemble((path.name, "bold"), "  ", (display_path(path), MUTED)), soft_wrap=True
    )
    console.print(
        summary_line(
            count_noun(result.total_files, "file"),
            count_noun(result.total_lines, "line"),
            count_noun(result.total_tokens, "token"),
            f"depth {result.max_depth}",
        )
    )
    if result.file_types:
        console.print()
        print_rows(
            ("EXTENSION", "FILES", "LINES", "TOKENS"),
            (
                (
                    Text(ft.extension, style="cyan"),
                    _number(ft.count),
                    _number(ft.line_count),
                    _number(ft.token_count),
                )
                for ft in result.file_types
            ),
            right=frozenset({1, 2, 3}),
        )


def _as_json(result: RepoInfoResult, path: Path) -> dict:
    return {
        "name": path.name,
        "path": str(path),
        "files": result.total_files,
        "lines": result.total_lines,
        "tokens": result.total_tokens,
        "max_depth": result.max_depth,
        "extensions": [
            {
                "extension": ft.extension,
                "files": ft.count,
                "lines": ft.line_count,
                "tokens": ft.token_count,
            }
            for ft in result.file_types
        ],
    }


def register(cli: click.Group):
    @cli.command()
    @click.argument("target", metavar="PATH|NAME", shell_complete=complete_repository_names)
    @click.option("--full", is_flag=True, help="List every file extension, not just the top 10")
    @click.option("--json", "as_json", is_flag=True, help="Print JSON instead of text")
    def info(target: str, full: bool, as_json: bool):
        """Count files, lines, and tokens in a repository

        PATH|NAME is a tracked repository (a unique part of its name is
        enough) or the path of any git repository. Files ignored by git are
        skipped; tokens are counted with tiktoken's cl100k_base encoding.
        """
        repo_path, matches, _ = RepositoryManager().resolve_repository_target(
            target, allow_untracked_git_path=True, fuzzy_names=True
        )
        if repo_path is None:
            if not matches:
                raise CommandError(f"No repository matches '{target}'")
            listing = "\n".join(f"  {match}" for match in matches)
            raise CommandError(
                f"Several repositories match '{target}'; be more specific:\n{listing}"
            )

        with error_console.status(f"Counting {repo_path.name}…"):
            result = gather_repo_info(repo_path, full=full)
        if as_json:
            print_json(_as_json(result, repo_path))
        else:
            _render(result, repo_path)
