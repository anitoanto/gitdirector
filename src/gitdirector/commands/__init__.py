from importlib.metadata import version
from typing import Optional

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from ..repo import RepoStatus

__version__ = version("gitdirector")

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


def _format_size(size: Optional[int]) -> Text:
    if size is None:
        return Text("—", style="bright_black")
    for unit, threshold in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= threshold:
            return Text(f"{size / threshold:.1f} {unit}", style="dim")
    return Text(f"{size} B", style="dim")


def _changes_text(staged: bool, unstaged: bool) -> Text:
    if staged and unstaged:
        return Text("staged+unstaged", style="yellow")
    elif staged:
        return Text("staged", style="cyan")
    elif unstaged:
        return Text("unstaged", style="yellow")
    return Text("—", style="bright_black")


def _path_text(path: str) -> Text:
    col_width = max(10, console.width * 2 // 9 - 6)
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
