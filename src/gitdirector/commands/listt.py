import click
from rich.text import Text

from ..manager import RepositoryManager
from ..repo import RepositoryInfo, RepoStatus
from . import (
    ATTENTION,
    MUTED,
    console,
    count_noun,
    display_path,
    failed_status,
    format_size,
    is_missing,
    print_json,
    print_rows,
    repository_json,
    run_concurrently,
    status_text,
    summary_line,
)


def gather_statuses(*, fetch: bool, include_size: bool) -> list[RepositoryInfo]:
    """Status of every tracked repository, sorted by name."""
    manager = RepositoryManager()
    config = manager.config
    paths = sorted(config.repositories, key=lambda path: (path.name.lower(), str(path)))
    if not paths:
        return []
    return run_concurrently(
        paths,
        lambda path: manager.get_repository_status(path, fetch=fetch, include_size=include_size),
        max_workers=config.max_workers,
        verb="Fetching" if fetch else "Checking",
        on_error=failed_status,
    )


def _summary(results: list[RepositoryInfo]) -> Text:
    behind = sum(info.status in (RepoStatus.BEHIND, RepoStatus.DIVERGED) for info in results)
    ahead = sum(info.status in (RepoStatus.AHEAD, RepoStatus.DIVERGED) for info in results)
    changed = sum(info.staged or info.unstaged for info in results)
    missing = sum(is_missing(info) for info in results)
    parts: list[tuple[str, str] | str] = [count_noun(len(results), "repository", "repositories")]
    if missing:
        parts.append((f"{missing} missing", ATTENTION))
    if behind:
        parts.append((f"{behind} to pull", ATTENTION))
    if ahead:
        parts.append((f"{ahead} to push", ATTENTION))
    if changed:
        parts.append((f"{changed} with changes", ATTENTION))
    if len(parts) == 1:
        parts.append(("all clean and in sync", MUTED))
    return summary_line(*parts)


def register(cli: click.Group):
    @cli.command(name="list")
    @click.option(
        "--no-fetch", is_flag=True, help="Compare with the origin refs on disk; skip the network"
    )
    @click.option("--json", "as_json", is_flag=True, help="Print JSON instead of a table")
    def list_repos(no_fetch: bool, as_json: bool):
        """List repositories with sync state and changes

        Fetches each repository's branch from origin first (concurrently, up
        to max_workers at a time) unless --no-fetch is given.
        """
        results = gather_statuses(fetch=not no_fetch, include_size=True)
        if as_json:
            print_json([repository_json(info) for info in results])
            return
        if not results:
            console.print("No repositories tracked. Add one with: gitdirector link PATH")
            return
        print_rows(
            ("REPOSITORY", "BRANCH", "STATUS", "LAST COMMIT", "SIZE", "PATH"),
            (
                (
                    Text(info.name, style="bold"),
                    Text(info.branch or "-", style=MUTED),
                    status_text(info),
                    Text(info.last_updated or "-", style=MUTED),
                    format_size(info.size),
                    Text(display_path(info.path), style=MUTED),
                )
                for info in results
            ),
            right=frozenset({4}),
            fit_last=True,
        )
        console.print()
        console.print(_summary(results))
