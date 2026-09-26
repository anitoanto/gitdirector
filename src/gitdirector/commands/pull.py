import re
from pathlib import Path

import click
from rich.text import Text

from ..manager import RepositoryManager
from ..repo import Repository, is_git_repository
from . import (
    DANGER,
    MUTED,
    SUCCESS,
    confirm,
    console,
    count_noun,
    print_rows,
    run_concurrently,
    summary_line,
    tracked_repositories,
)
from .completion import complete_repository_names

_UPDATING_RE = re.compile(r"^Updating (\S+)", re.MULTILINE)
_CHANGED_RE = re.compile(r"^\s*(\d+ files? changed.*)$", re.MULTILINE)


def pull_repository(path: Path) -> tuple[str, bool, str]:
    name = path.name
    if not is_git_repository(path):
        return name, False, "path not found"
    try:
        ok, msg = Repository(path).pull()
    except Exception as exc:
        return name, False, str(exc)
    return name, ok, msg


def summarize_pull(ok: bool, output: str) -> str:
    """One line for a pull: ``up to date``, ``a1b2..c3d4 · 2 files changed``, or the error."""
    if not ok:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        # git prints the fetch banner and hints before the actual failure.
        errors = [line for line in lines if line.startswith(("fatal:", "error:"))]
        if errors:
            return errors[-1].removeprefix("fatal: ").removeprefix("error: ")
        return lines[0] if lines else "git pull failed"
    updating = _UPDATING_RE.search(output)
    if updating is None:
        return "up to date"
    changed = _CHANGED_RE.search(output)
    return updating.group(1) + (f" · {changed.group(1)}" if changed else "")


def register(cli: click.Group):
    @cli.command()
    @click.argument(
        "targets", metavar="[PATH|NAME]...", nargs=-1, shell_complete=complete_repository_names
    )
    @click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt")
    def pull(targets: tuple[str, ...], yes: bool):
        """Fast-forward pull repositories (all by default)

        Runs git pull --ff-only on each repository's current branch,
        concurrently. Never merges or rebases: a branch that has diverged is
        reported and left alone. Asks first when pulling every repository.
        Exits 1 if any pull failed.
        """
        manager = RepositoryManager()
        paths = tracked_repositories(targets, manager)
        if not paths:
            console.print("No repositories tracked. Add one with: gitdirector link PATH")
            return
        noun = count_noun(len(paths), "repository", "repositories")
        if not targets and not yes and not confirm(f"Pull {noun}?", default=True):
            return

        results = run_concurrently(
            paths,
            pull_repository,
            max_workers=manager.config.max_workers,
            verb="Pulling",
            on_error=lambda path, exc: (path.name, False, str(exc)),
        )
        print_rows(
            ("REPOSITORY", "RESULT"),
            (
                (
                    Text(name, style="bold"),
                    Text.assemble(
                        ("✓ " if ok else "✗ ", SUCCESS if ok else DANGER),
                        (summarize_pull(ok, message), "" if ok else DANGER),
                    ),
                )
                for name, ok, message in results
            ),
        )

        failed = sum(not ok for _, ok, _ in results)
        updated = sum(ok and _UPDATING_RE.search(message) is not None for _, ok, message in results)
        console.print()
        console.print(
            summary_line(
                noun,
                (f"{updated} updated", SUCCESS if updated else MUTED),
                *([(f"{failed} failed", DANGER)] if failed else []),
            )
        )
        if failed:
            raise SystemExit(1)
