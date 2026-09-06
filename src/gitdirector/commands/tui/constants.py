"""Constants and helper functions for the TUI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from textual.binding import Binding
from textual.color import Color

from ...repo import RepositoryInfo, RepoStatus
from ...ui_theme import readable_on

# Rows are Rich markup, which cannot reference theme variables, so table
# colours are resolved from the active theme into concrete values that are
# guaranteed to read against both the table surface and the highlighted row.
_CURSOR_TINT = 0.30
_MIN_CONTRAST = 4.5
_MUTED_CONTRAST = 3.5
# The yellow used to flag attention (sync drift, uncommitted changes, a
# waiting session, repo names). Fixed rather than taken from the theme so it
# stays yellow in every theme; only its lightness adapts for contrast.
_ATTENTION_YELLOW = "#ffd75f"
# The green used for something live (a running session, an active panel).
# Neon so it stands out at a glance, and fixed for the same reason as the
# yellow: a theme's own success colour can be teal or olive.
_LIVE_GREEN = "#39ff14"


def _variable_color(variables: Mapping[str, str], name: str, fallback: str) -> Color:
    try:
        return Color.parse(variables.get(name) or fallback)
    except Exception:
        return Color.parse(fallback)


def _markup_color(color: Color, source: str) -> str:
    # Rich understands "green" but not "ansi_green"; ANSI themes keep the
    # terminal's own palette on purpose.
    if color.ansi is not None:
        return source[5:] if source.startswith("ansi_") else source
    return color.hex6


@dataclass(frozen=True)
class TablePalette:
    """Colours for status cells, as Rich style strings."""

    success: str
    yellow: str
    muted: str
    primary: str

    def sync_label(self, status: RepoStatus) -> str:
        if status is RepoStatus.UP_TO_DATE:
            return "up to date"
        return f"[bold {self.yellow}]{status.value}[/]"

    def changes_label(self, info: RepositoryInfo) -> str:
        key = _changes_sort_key(info)
        if key == "—":
            return "—"
        return f"[bold {self.yellow}]{key}[/]"

    def group_label(self, text: str) -> str:
        return f"[bold {self.primary}]{text}[/]"

    def panel_status_label(self, state: str) -> str:
        if state == "active":
            return f"[{self.success}]● active[/]"
        return f"[{self.muted}]○ empty[/]"

    def session_status(self, status: str) -> tuple[str, str]:
        """``(label, style)`` for a composed sessions row."""
        if status == "waiting":
            return "● waiting", f"bold {self.yellow}"
        if status == "idle":
            return "○ idle", self.muted
        return "● running", self.success


def resolve_table_palette(variables: Mapping[str, str]) -> TablePalette:
    """Build the palette for a theme from its CSS variables.

    Every colour is checked against the plain surface and against the
    highlighted row (surface tinted with the primary colour) and nudged
    toward black or white until it clears the contrast threshold on both.
    """
    surface = _variable_color(variables, "surface", "#1e1e1e")
    primary = _variable_color(variables, "primary", "#5fd7ff")
    foreground = _variable_color(variables, "foreground", "#f0f0f0")
    # ANSI themes name terminal palette slots; their real values are unknown,
    # so no contrast math is possible and the slot names are used as-is.
    ansi_theme = any(c.ansi is not None for c in (surface, primary, foreground))
    if not ansi_theme:
        # A focused table tints its surface 5% toward the foreground.
        surface = surface.blend(foreground, 0.05)
    tint = surface if ansi_theme else surface.blend(primary, _CURSOR_TINT)

    def readable(name: str, fallback: str) -> str:
        source = variables.get(name) or fallback
        color = _variable_color(variables, name, fallback)
        return _markup_color(readable_on(color, surface, tint, minimum=_MIN_CONTRAST), source)

    if ansi_theme:
        success = "bright_green"
        yellow = "yellow"
        muted = "bright_black"
    else:
        success = _markup_color(
            readable_on(Color.parse(_LIVE_GREEN), surface, tint, minimum=_MIN_CONTRAST), ""
        )
        yellow = _markup_color(
            readable_on(Color.parse(_ATTENTION_YELLOW), surface, tint, minimum=_MIN_CONTRAST), ""
        )
        muted = _markup_color(
            readable_on(foreground.blend(surface, 0.45), surface, tint, minimum=_MUTED_CONTRAST),
            "",
        )

    return TablePalette(
        success=success,
        yellow=yellow,
        muted=muted,
        primary=readable("primary", "#5fd7ff"),
    )


def _changes_sort_key(info: RepositoryInfo) -> str:
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
    5: "Path",
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
    4: "Description",
}

_DEFAULT_SESSIONS_SORT_COLUMN = 3

# Column indexes for the Sessions tab. Kept here so the TUI mixins and
# the action handlers can refer to the same source of truth.
_SESSIONS_COL_STATUS = 0
_SESSIONS_COL_PURPOSE = 1
_SESSIONS_COL_REPO = 2
_SESSIONS_COL_SESSION_NAME = 3
_SESSIONS_COL_DESCRIPTION = 4

_SESSION_STATUS_POLL_INTERVAL_SECS = 1
_REPO_CACHE_TTL_SECS = 30 * 60

_PANELS_SORT_COLUMN_NAMES = {
    0: "Name",
    1: "TMUX",
    2: "Layout",
    3: "Panes",
    4: "Status",
}

_DEFAULT_PANELS_SORT_COLUMN = 0

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
