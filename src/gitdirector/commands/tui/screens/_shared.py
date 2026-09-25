"""Shared helpers and generic modal screens used across the TUI."""

from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..constants import _MODAL_BINDINGS, _SORT_COLUMN_NAMES
from .card import ShortcutKeys, card_css, key_hints, menu_row


def _render_ansi_output(output: str) -> Text:
    return Text.from_ansi(output)


class ConfirmScreen(ShortcutKeys, ModalScreen[bool]):
    """A yes/no question; No is highlighted, so a stray Enter changes nothing."""

    BINDINGS = _MODAL_BINDINGS

    CSS = card_css("ConfirmScreen", width=56)

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__()
        self.message = message
        self.detail = detail
        self._shortcuts = {"n": "no", "y": "yes"}

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static(self.message, id="menu-title")
            yield Static(self.detail, id="menu-branch")
            yield OptionList(
                Option(menu_row(Text("No", style="bold"), "", "n"), id="no"),
                Option(menu_row(Text("Yes", style="bold"), "", "y"), id="yes"),
                id="action-menu",
            )
            yield Static(key_hints(("y", "yes"), ("n", "no"), ("esc", "cancel")), id="menu-hint")

    def on_mount(self) -> None:
        self.query_one("#action-menu", OptionList).focus()

    def _choose(self, option_id: str) -> None:
        self.dismiss(option_id == "yes")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._choose(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_cursor_down(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_up()


class SortMenuScreen(ShortcutKeys, ModalScreen[tuple | None]):
    """Pick the sort order; choosing the current one again flips its direction."""

    BINDINGS = _MODAL_BINDINGS

    CSS = card_css("SortMenuScreen", width=48)

    def __init__(
        self, current_column: int, current_reverse: bool, column_names: dict[int, str] | None = None
    ) -> None:
        super().__init__()
        self._current_column = current_column
        self._current_reverse = current_reverse
        self._column_names = column_names or _SORT_COLUMN_NAMES
        self._shortcuts = {
            str(number): f"sort:{idx}"
            for number, idx in enumerate(self._column_names, start=1)
            if number < 10
        }

    def compose(self) -> ComposeResult:
        arrow = "▼ descending" if self._current_reverse else "▲ ascending"
        current = self._column_names.get(self._current_column, "")
        with Vertical(id="menu-container"):
            yield Static("Sort by", id="menu-title")
            yield Static(f"{escape(current)} {arrow}", id="menu-branch")
            items: list[Option] = []
            for number, (idx, name) in enumerate(self._column_names.items(), start=1):
                if idx == self._current_column:
                    label = Text.assemble(("● ", "bold"), (name, "bold"))
                    detail = Text("▼" if self._current_reverse else "▲", style="bold")
                else:
                    label = Text.assemble(("○ ", "dim"), (name, ""))
                    detail = Text()
                key = str(number) if number < 10 else ""
                items.append(Option(menu_row(label, detail, key), id=f"sort:{idx}"))
            yield OptionList(*items, id="action-menu")
            yield Static(
                key_hints(("↑↓", "select"), ("⏎", "sort, again to reverse"), ("esc", "close")),
                id="menu-hint",
            )

    def on_mount(self) -> None:
        menu = self.query_one("#action-menu", OptionList)
        menu.focus()
        menu.highlighted = list(self._column_names).index(self._current_column)

    def _choose(self, option_id: str) -> None:
        col_idx = int(option_id.split(":")[1])
        if col_idx == self._current_column:
            self.dismiss((col_idx, not self._current_reverse))
        else:
            self.dismiss((col_idx, False))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._choose(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_up()


__all__ = ["ConfirmScreen", "SortMenuScreen", "_render_ansi_output"]
