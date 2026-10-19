"""Constants and helper functions for the TUI."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from textual.binding import Binding
from textual.color import Color

from ...ui_theme import MIN_CONTRAST, highlight_backgrounds, readable_on

# Rows are Rich markup, which cannot reference theme variables, so table
# colours are resolved from the active theme into concrete values that are
# guaranteed to read on the table surface, a card's panel, and the
# highlighted row of either.
_MIN_CONTRAST = MIN_CONTRAST
# The yellow used to flag attention (sync drift, uncommitted changes, a
# waiting session, repo names). Fixed rather than taken from the theme so it
# stays yellow in every theme; only its lightness adapts for contrast.
_ATTENTION_YELLOW = "#ffd75f"
# The green used for something live (a running session, an active panel).
# Neon so it stands out at a glance, and fixed for the same reason as the
# yellow: a theme's own success colour can be teal or olive.
_LIVE_GREEN = "#39ff14"
# The red for a mode that drops permission prompts.
_DANGER_RED = "#ff2d2d"


def _variable_color(variables: Mapping[str, str], name: str, fallback: str) -> Color:
    try:
        return Color.parse(variables.get(name) or fallback)
    except Exception:
        return Color.parse(fallback)


def _markup_color(color: Color) -> str:
    return color.hex6


@dataclass(frozen=True)
class TablePalette:
    """Colours for status cells, as Rich style strings."""

    success: str
    yellow: str
    muted: str
    primary: str
    danger: str = "red"

    @property
    def pending(self) -> str:
        """Same green as ``success``: work goes on, in a subagent (the ◐ says so)."""
        return self.success

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
        if status == "pending":
            return "◐ pending", self.pending
        return "● running", self.success


def resolve_table_palette(variables: Mapping[str, str]) -> TablePalette:
    """Build the palette for a theme from its CSS variables.

    Every colour is checked against each background a row can have and
    nudged toward black or white until it clears the contrast threshold on
    all of them.
    """
    surface = _variable_color(variables, "surface", "#1e1e1e")
    primary = _variable_color(variables, "primary", "#5fd7ff")
    foreground = _variable_color(variables, "foreground", "#f0f0f0")
    panel = _variable_color(variables, "panel", surface.hex6)
    backgrounds = highlight_backgrounds(
        {"surface": surface.hex6, "panel": panel.hex6, "primary": primary.hex6}
    )

    def readable(color: Color) -> str:
        return _markup_color(readable_on(color, *backgrounds, minimum=_MIN_CONTRAST))

    return TablePalette(
        success=readable(Color.parse(_LIVE_GREEN)),
        yellow=readable(Color.parse(_ATTENTION_YELLOW)),
        muted=readable(foreground.blend(surface, 0.45)),
        primary=readable(primary),
        danger=readable(Color.parse(_DANGER_RED)),
    )


_SORT_COLUMN_NAMES = {
    0: "Repository",
    1: "Needs attention",
    2: "Branch",
    3: "Last commit",
    4: "Sessions",
}

_DEFAULT_SORT_COLUMN = 0

_SESSION_STATUS_POLL_INTERVAL_SECS = 1
_REPO_CACHE_TTL_SECS = 30 * 60
# Worktree-only re-read of every repository; no fetch, so cheap and offline.
_LOCAL_REFRESH_SECS = 15

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
    "pending": 2,
    "idle": 3,
}


# The card every popup shares: a quiet rounded frame, a header (title, meta
# on the right, a muted subtitle over a hairline), the body, and a line of
# key hints. See screens/card.py for the pieces that fill it.
_MODAL_CSS = """
    #menu-container {
        width: 64;
        max-width: 95%;
        height: auto;
        max-height: 95%;
        border: round $primary 45%;
        background: $panel;
        padding: 0;
    }
    #menu-header {
        height: auto;
        padding: 1 2 0 2;
    }
    #menu-title {
        width: 1fr;
        height: auto;
        padding: 1 2 0 2;
        color: $text;
        text-style: bold;
        text-align: left;
    }
    #menu-header #menu-title {
        padding: 0;
    }
    #menu-meta,
    #menu-stats,
    #result-status {
        width: auto;
        max-width: 60%;
        padding: 0;
    }
    #menu-branch,
    #description-session-name,
    #commit-result-message {
        height: auto;
        padding: 0 2 1 2;
        color: $text-muted;
        text-align: left;
        border-bottom: solid $foreground 10%;
    }
    #action-menu {
        height: auto;
        max-height: 30;
        border: none;
        padding: 1 1;
        margin: 0;
        background: $panel;
    }
    #action-menu:focus {
        border: none;
        background-tint: $foreground 0%;
    }
    #menu-hint {
        height: auto;
        padding: 1 2 1 2;
        text-align: left;
        color: $text-muted;
    }
    .card-body {
        height: auto;
        padding: 1 2 0 2;
    }
    .card-input {
        width: 1fr;
        height: auto;
        margin: 1 2 0 2;
        border: none;
        background: $surface;
        color: $text;
        padding: 0 1;
    }
    .card-input:focus {
        border: none;
        background-tint: $foreground 0%;
    }
    .card-actions {
        height: auto;
        border: none;
        padding: 1 1 0 1;
        background: $panel;
    }
    .card-actions:focus {
        border: none;
        background-tint: $foreground 0%;
    }
"""

_MODAL_BINDINGS = [
    Binding("escape", "cancel", "Esc close", show=True),
    Binding("j", "cursor_down", "↓", show=False),
    Binding("k", "cursor_up", "↑", show=False),
]
