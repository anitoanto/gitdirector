"""Shared console, formatting, and orchestration helpers for the CLI commands."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TypeVar

import click
from rich import box
from rich.console import Console, RenderableType
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .. import version_check
from ..integrations.tmux.core import _parse_gd_session_name
from ..repo import RepoStatus

T = TypeVar("T")

#: ``-h`` works everywhere, like most modern CLIs.
CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

#: Command output. Errors and notices go to :data:`error_console` so that
#: ``SESSION=$(gitdirector gd-tmux ...)`` captures exactly what was asked for.
console = Console(highlight=False)
error_console = Console(highlight=False, stderr=True, style="red")

# How long a command waits at exit for a slow update check before giving up.
_UPDATE_NOTICE_WAIT_SECS = 1.0
_UPDATE_NOTICE_FLAG = "update_notice_printed"


def get_version() -> str:
    return version_check.get_installed_version()


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
        error_console.print(f"\n{notice}\n", style="yellow")


def print_update_notice() -> None:
    """Check for a newer release now and print a notice if there is one."""
    if not _claim_update_notice(click.get_current_context(silent=True)):
        return
    _emit_update_notice(version_check.get_update_notice())


def schedule_update_notice(ctx: click.Context) -> None:
    """Run the release check alongside the command and print at exit.

    The check hits the network when its cache is cold, so it runs in a
    thread while the command does its work and is only awaited briefly once
    the command has finished.
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


def print_error(message: str) -> None:
    """Print a failure message to stderr: red headline, detail lines as they are.

    Detail lines are typically paths. They are printed without wrapping so a
    long path is never broken across lines and stays copyable.
    """
    headline, _, details = message.partition("\n")
    error_console.print(f"Error: {headline}")
    if details:
        error_console.print(details, soft_wrap=True, style="none")


def require_gd_session_name(name: str) -> str:
    """Validate a ``gd/<repo>/<purpose>/<N>`` session name for a CLI argument.

    Refusing anything else means a typo can never be routed to a different
    session through tmux's prefix matching.
    """
    if _parse_gd_session_name(name) is None:
        raise click.ClickException(
            f"expected a gd session name of the form gd/<repo>/<purpose>/<N>; got {name!r}"
        )
    return name


def run_concurrently(
    paths: Iterable[Path],
    task: Callable[[Path], T],
    *,
    max_workers: int,
    verb: str,
    on_error: Callable[[Path, Exception], T],
    render: Callable[[list[T], int], RenderableType] | None = None,
    transient: bool = True,
) -> list[T]:
    """Run *task* over *paths* in a thread pool with a live progress display.

    *on_error* turns an exception raised by *task* into a result so one bad
    repository never aborts the whole run. *render* may replace the default
    spinner with a renderable built from the results so far and the number
    of repositories still pending.
    """
    paths = list(paths)
    results: list[T] = []

    def default_render(done: list[T], remaining: int) -> RenderableType:
        if not done:
            text = f"  [dim]{verb} {remaining} repositories...[/dim]"
        elif remaining:
            text = f"  [dim]{len(done)} done, {remaining} remaining...[/dim]"
        else:
            text = "  [dim]done[/dim]"
        return Spinner("dots", text=text)

    render = render or default_render
    with Live(
        console=console,
        refresh_per_second=12,
        transient=transient,
        vertical_overflow="visible",
    ) as live:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(task, path): path for path in paths}
            remaining = len(futures)
            live.update(render(results, remaining))
            for future in as_completed(futures):
                remaining -= 1
                path = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(on_error(path, exc))
                live.update(render(results, remaining))
    return results


def count_noun(count: int, noun: str, plural: str | None = None) -> str:
    return f"{count} {noun if count == 1 else plural or noun + 's'}"


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


def _format_size(size: int | None) -> Text:
    if size is None:
        return Text("—", style="bright_black")
    for unit, threshold in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= threshold:
            return Text(f"{size / threshold:.1f} {unit}", style="dim")
    return Text(f"{size} B", style="dim")


def _changes_text(staged: bool, unstaged: bool) -> Text:
    if staged and unstaged:
        return Text("staged+unstaged", style="yellow")
    if staged:
        return Text("staged", style="cyan")
    if unstaged:
        return Text("unstaged", style="yellow")
    return Text("—", style="bright_black")


def _path_text(path: str) -> Text:
    col_width = max(10, console.width * 2 // 9 - 6)
    if len(path) > col_width:
        path = "…" + path[-(col_width - 1) :]
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
    table.add_column("REPOSITORY", ratio=2)
    table.add_column("SYNC", no_wrap=True, ratio=1)
    table.add_column("BRANCH", style="dim", no_wrap=True, ratio=1)
    table.add_column("CHANGES", no_wrap=True, ratio=1)
    table.add_column("LAST COMMIT", style="dim", no_wrap=True, ratio=1)
    table.add_column("SIZE", style="dim", no_wrap=True, ratio=1, justify="right")
    table.add_column("PATH", style="dim", ratio=2, no_wrap=True, justify="right")
    return table


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
