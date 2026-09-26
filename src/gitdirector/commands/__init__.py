"""Shared output and orchestration helpers for the CLI commands.

Conventions every command follows:

* Results go to stdout, everything else (errors, prompts, progress, the update
  notice) to stderr, so ``$(gitdirector ...)`` and pipes see only results.
* Progress spinners only draw on a terminal.
* Tables are fitted to the terminal; piped, they are never truncated.
* ``--json`` prints a stable machine-readable document instead of a table.
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import click
from rich.console import Console, RenderableType
from rich.table import Table
from rich.text import Text

from .. import version_check
from ..repo import MISSING_REPOSITORY_MESSAGE, RepositoryInfo, RepoStatus

if TYPE_CHECKING:
    from ..manager import RepositoryManager

T = TypeVar("T")

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 100}

console = Console(highlight=False)
error_console = Console(highlight=False, stderr=True)

# Styles shared with the console's design language.
ATTENTION = "bold yellow"
SUCCESS = "bold green"
# Work goes on in a subagent: the same green, the ◐ glyph tells them apart.
PENDING = SUCCESS
DANGER = "bold red"
MUTED = "dim"

# How long a command waits at exit for a slow update check before giving up.
_UPDATE_NOTICE_WAIT_SECS = 1.0
_UPDATE_NOTICE_FLAG = "update_notice_printed"


def get_version() -> str:
    return version_check.get_installed_version()


class CommandError(click.ClickException):
    """A failure printed as ``Error: <headline>`` with unwrapped detail lines."""

    def show(self, file=None) -> None:
        print_error(self.message)


def print_error(message: str) -> None:
    """Print a failure to stderr: red ``Error:`` headline, detail lines as they are.

    Detail lines are typically paths, printed without wrapping so they stay
    copyable.
    """
    headline, _, details = message.partition("\n")
    error_console.print(Text.assemble(("Error: ", DANGER), headline), soft_wrap=True)
    if details:
        error_console.print(details, soft_wrap=True, markup=False)


def _claim_update_notice(ctx: click.Context | None) -> bool:
    """Return True the first time a notice is claimed for this invocation."""
    if ctx is None:
        return True
    root = ctx.find_root()
    if root.meta.get(_UPDATE_NOTICE_FLAG):
        return False
    root.meta[_UPDATE_NOTICE_FLAG] = True
    return True


def _emit_update_notice(notice: str | None) -> None:
    if notice:
        error_console.print(f"\n{notice}", style="yellow")


def print_update_notice() -> None:
    """Check for a newer release now and print a notice if there is one."""
    if not _claim_update_notice(click.get_current_context(silent=True)):
        return
    _emit_update_notice(version_check.get_update_notice())


def schedule_update_notice(ctx: click.Context) -> None:
    """Run the release check alongside the command and print at exit.

    The check hits the network when its cache is cold, so it runs in a
    thread while the command works and is only awaited briefly at the end.
    """
    if not _claim_update_notice(ctx):
        return
    result: dict[str, str | None] = {}

    def check() -> None:
        try:
            result["notice"] = version_check.get_update_notice()
        except Exception:  # never let the notice break a command
            result["notice"] = None

    worker = threading.Thread(target=check, name="gitdirector-update-check", daemon=True)
    worker.start()

    def finish() -> None:
        worker.join(timeout=_UPDATE_NOTICE_WAIT_SECS)
        _emit_update_notice(result.get("notice"))

    ctx.call_on_close(finish)


def require_gd_session_name(name: str) -> str:
    """Validate a ``gd/<repo>/<purpose>/<N>`` session name for a CLI argument.

    Refusing anything else means a typo can never be routed to a different
    session through tmux's prefix matching.
    """
    from ..integrations.tmux.core import _parse_gd_session_name

    if _parse_gd_session_name(name) is None:
        raise CommandError(
            f"expected a session name like gd/<repo>/<purpose>/<N>, got {name!r}\n"
            "Run 'gitdirector sessions' to list live sessions."
        )
    return name


def resolve_repository(target: str, manager: RepositoryManager | None = None) -> Path:
    """The tracked repository *target* names (a path or a directory name)."""
    from ..manager import RepositoryManager, describe_resolution_failure

    manager = manager or RepositoryManager()
    repo_path, matches, path_attempted = manager.resolve_repository_target(target)
    if repo_path is None:
        raise CommandError(describe_resolution_failure(target, matches, path_attempted))
    return repo_path


def tracked_repositories(targets: Sequence[str], manager: RepositoryManager) -> list[Path]:
    """*targets* resolved, or every tracked repository; sorted by name."""
    paths = (
        {resolve_repository(target, manager) for target in targets}
        if targets
        else manager.config.repositories
    )
    return sorted(paths, key=lambda path: (path.name.lower(), str(path)))


def run_concurrently(
    paths: Iterable[Path],
    task: Callable[[Path], T],
    *,
    max_workers: int,
    verb: str,
    on_error: Callable[[Path, Exception], T],
) -> list[T]:
    """Run *task* over *paths* in a thread pool behind a progress spinner.

    Results come back in the order of *paths*. *on_error* turns an exception
    raised by *task* into a result, so one bad repository never aborts the run.
    """
    paths = list(paths)
    results: list[T | None] = [None] * len(paths)
    total = len(paths)
    noun = count_noun(total, "repository", "repositories")
    with error_console.status(f"{verb} {noun}…") as status:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, total))) as executor:
            futures = {executor.submit(task, path): index for index, path in enumerate(paths)}
            for done, future in enumerate(as_completed(futures), start=1):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    results[index] = on_error(paths[index], exc)
                status.update(f"{verb} {noun}… {done}/{total}")
    return results  # type: ignore[return-value]


def count_noun(count: int, noun: str, plural: str | None = None) -> str:
    return f"{count:,} {noun if count == 1 else plural or noun + 's'}"


def display_path(path: Path | str) -> str:
    """*path* with the home directory shortened to ``~``."""
    text = str(path)
    home = str(Path.home())
    if text == home or text.startswith(home + os.sep):
        return "~" + text[len(home) :]
    return text


def print_json(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))


def summary_line(*parts: tuple[str, str] | str) -> Text:
    """``3 repositories · 1 behind · 2 changed``: plain parts or (text, style)."""
    text = Text()
    for part in parts:
        if text:
            text.append(" · ", style=MUTED)
        text.append(*((part, "") if isinstance(part, str) else part))
    return text


def print_rows(
    headers: Sequence[str],
    rows: Iterable[Sequence[str | Text]],
    *,
    right: frozenset[int] = frozenset(),
    fit_last: bool = False,
) -> None:
    """Print a borderless table with bold headers, like ``docker ps`` or ``gh``.

    On a terminal, *fit_last* cuts the last column (a path) from the left
    so each row stays on one line; piped output is never truncated.
    """
    rows = [[cell if isinstance(cell, Text) else Text(cell) for cell in row] for row in rows]
    if fit_last and console.is_terminal and rows:
        used = sum(
            max(len(headers[i]), *(row[i].cell_len for row in rows)) + 2
            for i in range(len(headers) - 1)
        )
        room = console.width - used
        if room < 12:
            headers = headers[:-1]
            rows = [row[:-1] for row in rows]
        else:
            for row in rows:
                row[-1] = Text(fit_left(row[-1].plain, room), style=row[-1].style)
    table = Table(
        box=None, show_edge=False, pad_edge=False, padding=(0, 2, 0, 0), header_style="bold"
    )
    for index, header in enumerate(headers):
        table.add_column(header, no_wrap=True, justify="right" if index in right else "left")
    for row in rows:
        table.add_row(*row)
    emit(table)


def emit(renderable: RenderableType) -> None:
    """Print a table-like *renderable* to stdout without trailing padding.

    Piped, it is laid out as wide as it needs: a script never reads a cell
    that was wrapped or cut to fit an imaginary 80-column screen.
    """
    target = console
    if not console.is_terminal:
        target = Console(file=io.StringIO(), width=100_000, highlight=False)
    with target.capture() as capture:
        target.print(renderable)
    console.file.write("".join(line.rstrip() + "\n" for line in capture.get().splitlines()))


def fit_left(text: str, width: int) -> str:
    """Cut *text* from the left to *width* cells, keeping a path's informative tail."""
    if len(text) <= width:
        return text
    if width <= 1:
        return "…"[:width]
    return "…" + text[-(width - 1) :]


def format_size(size: int | None) -> Text:
    if size is None:
        return Text("-", style=MUTED)
    for unit, threshold in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= threshold:
            return Text(f"{size / threshold:.1f} {unit}")
    return Text(f"{size} B")


def _count(items: list[str] | None) -> str:
    return f"{len(items)} " if items else ""


def is_missing(info: RepositoryInfo) -> bool:
    return info.message == MISSING_REPOSITORY_MESSAGE


def status_parts(info: RepositoryInfo) -> list[tuple[str, str]]:
    """The console's status wording: ``↑1 to push · 2 staged · 5 changed``."""
    parts: list[tuple[str, str]] = []
    if info.status in (RepoStatus.AHEAD, RepoStatus.DIVERGED):
        parts.append((f"↑{info.ahead or ''} to push", ATTENTION))
    if info.status in (RepoStatus.BEHIND, RepoStatus.DIVERGED):
        parts.append((f"↓{info.behind or ''} to pull", ATTENTION))
    if info.staged:
        parts.append((f"{_count(info.staged_files)}staged", SUCCESS))
    if info.unstaged:
        parts.append((f"{_count(info.unstaged_files)}changed", ATTENTION))
    if is_missing(info):
        parts.append(("missing", ATTENTION))
    elif info.status is RepoStatus.UNKNOWN:
        label = "no remote branch" if info.message.startswith("No origin/") else "sync unknown"
        parts.append((label, MUTED))
    if info.sync_stale:
        parts.append(("offline", MUTED))
    return parts


def status_text(info: RepositoryInfo) -> Text:
    return summary_line(*status_parts(info)) or Text("clean", style=MUTED)


def repository_json(info: RepositoryInfo) -> dict[str, Any]:
    return {
        "name": info.name,
        "path": str(info.path),
        "branch": info.branch,
        "sync": info.status.value,
        "ahead": info.ahead,
        "behind": info.behind,
        "offline": info.sync_stale,
        "staged": info.staged_files or [],
        "unstaged": info.unstaged_files or [],
        "last_commit": info.last_updated,
        "last_commit_timestamp": info.last_commit_timestamp,
        "size": info.size,
        "message": info.message,
    }


def failed_status(path: Path, exc: Exception) -> RepositoryInfo:
    return RepositoryInfo(path, path.name, RepoStatus.UNKNOWN, None, str(exc))


def confirm(prompt: str, *, default: bool) -> bool:
    """Ask on stderr; without a terminal to ask on, tell the user about ``--yes``."""
    try:
        return click.confirm(prompt, default=default, err=True)
    except click.Abort:
        if sys.stdin.isatty():
            raise
        raise CommandError("no answer to the confirmation prompt; pass --yes to skip it") from None
