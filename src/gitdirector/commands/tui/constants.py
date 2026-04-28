"""Constants and helper functions for the TUI."""

from __future__ import annotations

from textual.binding import Binding

from ...repo import RepositoryInfo, RepoStatus

_STATUS_LABEL = {
    RepoStatus.UP_TO_DATE: "up to date",
    RepoStatus.BEHIND: "behind",
    RepoStatus.AHEAD: "ahead",
    RepoStatus.DIVERGED: "diverged",
    RepoStatus.UNKNOWN: "unknown",
}


def _changes_label(info: RepositoryInfo) -> str:
    if info.staged and info.unstaged:
        return "staged+unstaged"
    if info.staged:
        return "staged"
    if info.unstaged:
        return "unstaged"
    return "—"


_SORT_COLUMN_NAMES = {
    0: "Repository",
    1: "Sync",
    2: "Branch",
    3: "Changes",
    4: "Last Commit",
    5: "Sessions",
    6: "Path",
}

_DEFAULT_SORT_COLUMN = 0

_STATUS_ORDER = {
    RepoStatus.UP_TO_DATE: 0,
    RepoStatus.AHEAD: 1,
    RepoStatus.BEHIND: 2,
    RepoStatus.DIVERGED: 3,
    RepoStatus.UNKNOWN: 4,
}

_SESSIONS_SORT_COLUMN_NAMES = {
    0: "Status",
    1: "Session",
    2: "Repository",
    3: "Session Name",
}

_DEFAULT_SESSIONS_SORT_COLUMN = 3

_SESSION_STATUS_POLL_INTERVAL_SECS = 10

_PANELS_SORT_COLUMN_NAMES = {
    0: "Name",
    1: "TMUX",
    2: "Layout",
    3: "Panes",
    4: "Status",
}

_DEFAULT_PANELS_SORT_COLUMN = 0

_PANEL_STATUS_LABEL = {
    "active": "[green]● active[/green]",
    "empty": "[dim]○ empty[/dim]",
}

_SESSION_STATUS_LABEL = {
    "waiting": "[bold yellow]● waiting[/bold yellow]",
    "running": "[green]● running[/green]",
    "idle": "[dim]○ idle[/dim]",
}

_SESSION_STATUS_ORDER = {
    "waiting": 0,
    "running": 1,
    "idle": 2,
}


_MODAL_CSS = """
    #menu-container {
        width: 50%;
        height: auto;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    #menu-title {
        text-align: center;
        padding: 1 1 0 1;
        color: $text;
    }
    #menu-branch {
        text-align: center;
        padding: 0 1 1 1;
        color: $text-muted;
    }
    #action-menu {
        width: 1fr;
        height: auto;
        border: none;
        padding: 1 2;
        margin: 1 0;
    }
    #menu-hint {
        text-align: center;
        padding: 1 1 1 1;
        color: $text-muted;
    }
"""

_MODAL_BINDINGS = [
    Binding("escape", "cancel", "Esc close", show=True),
    Binding("j", "cursor_down", "↓", show=False),
    Binding("k", "cursor_up", "↑", show=False),
]
