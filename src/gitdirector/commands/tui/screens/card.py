"""The card layout shared by the repository modals: launcher, git menu and info.

A card is a header (title with meta on the right, a muted subtitle under it),
a body, and a one-line footer of key hints, with hairline dividers between.
"""

from __future__ import annotations

from pathlib import Path

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import LoadingIndicator, Static
from textual.widgets.option_list import Option

from ..constants import _MODAL_CSS, TablePalette, resolve_table_palette
from ..repo_rows import display_path
from ..terminal_caps import strip_unsupported_css as _safe_css


def modal_screen_css(screen_name: str) -> str:
    """The dimmed backdrop a card sits centred on."""
    return f"{screen_name} {{ align: center middle; background: $background 60%; }}"


def card_css(screen_name: str, width: int | str = 64, extra: str = "") -> str:
    """The card's CSS for one screen.

    *screen_name* is the screen's class name, or a class selector
    (``.-loading-card``) for a base class that sets ``SCOPED_CSS = False``.
    """
    return _safe_css(
        modal_screen_css(screen_name)
        + _MODAL_CSS
        + f"{screen_name} #menu-container {{ width: {width}; }}"
        + extra
    )


def key_hints(*pairs: tuple[str, str]) -> str:
    """``↑↓ select   ⏎ open   esc close`` with the keys picked out."""
    return "   ".join(f"[bold $accent]{key}[/] {label}" for key, label in pairs)


def heading(label: str) -> Option:
    return Option(Text(label.upper(), style="bold dim"), disabled=True)


def spacer() -> Option:
    return Option("", disabled=True)


def card_subtitle(path: Path | None, status: Text | None = None) -> Text:
    """The path under the title, and the status on its own line when there is one."""
    text = Text(display_path(path) if path else "", style="dim")
    if status:
        text.append("\n")
        text.append_text(status)
    return text


def menu_row(left: Text | str, detail: Text | str = "", key: str = "") -> Table:
    """A menu line: *left*, *detail* pinned right, then the shortcut key.

    The one-column inset on both edges lines the text up under the card's
    title while the highlight still runs a column wider.
    """
    grid = Table.grid(expand=True, padding=(0, 1), pad_edge=True)
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    grid.add_column(justify="right", no_wrap=True, overflow="ellipsis")
    grid.add_column(justify="right", width=2, no_wrap=True)
    # A capital needs Shift; say so rather than rely on the letter's case.
    shown_key = f"⇧{key}" if len(key) == 1 and key.isupper() else key
    grid.add_row(
        Text.from_markup(left) if isinstance(left, str) else left,
        Text.from_markup(detail) if isinstance(detail, str) else detail,
        Text(shown_key, style="bold dim"),
    )
    return grid


def card_palette(app) -> TablePalette:
    """The console's resolved colours, or the theme's when shown elsewhere."""
    return getattr(app, "_palette", None) or resolve_table_palette(app.get_css_variables())


class ShortcutKeys:
    """A single key picks the menu line it is shown against.

    Screens list ``key -> option id`` in ``_shortcuts`` and act on the pick in
    ``_choose``; the key is shown in the line's right-hand column by
    :func:`menu_row`, so what is on screen is what works.
    """

    _shortcuts: dict[str, str] = {}

    def _choose(self, option_id: str) -> None:
        self.dismiss(option_id)

    def on_key(self, event) -> None:
        option_id = self._shortcuts.get(event.character or "")
        if option_id is None:
            return
        event.stop()
        event.prevent_default()
        self._choose(option_id)


class LoadingCard(ModalScreen[None]):
    """A card with a spinner while something runs; it closes itself when done."""

    DEFAULT_CLASSES = "-loading-card"
    # Textual scopes a screen's CSS under the concrete class, which would keep
    # these rules off every subclass; the class selector is specific enough.
    SCOPED_CSS = False

    CSS = card_css(
        ".-loading-card",
        width=56,
        extra="""
    .-loading-card LoadingIndicator {
        height: 1;
        margin: 1 2 0 2;
        color: $primary;
    }
    """,
    )

    def __init__(self, title: str, subtitle: str = "", hint: str = "working…") -> None:
        super().__init__()
        self.card_title = title
        self.card_subtitle = subtitle
        self.hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static(self.card_title, id="menu-title")
            yield Static(self.card_subtitle, id="menu-branch")
            yield LoadingIndicator()
            yield Static(self.hint, id="menu-hint")
