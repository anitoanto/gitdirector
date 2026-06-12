"""Shared helpers and generic modal screens used across the TUI."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..constants import _MODAL_BINDINGS, _MODAL_CSS, _SORT_COLUMN_NAMES
from ..terminal_caps import strip_unsupported_css as _safe_css


def _render_ansi_output(output: str) -> Text:
    return Text.from_ansi(output)


class ConfirmScreen(ModalScreen[bool]):
    """Simple yes/no confirmation dialog."""

    BINDINGS = _MODAL_BINDINGS

    CSS = _safe_css(
        "ConfirmScreen { align: center middle; background: $panel 80%; hatch: right $primary 30%; }"
        + _MODAL_CSS
    )

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static(f"[bold white]{self.message}[/bold white]", id="menu-title")
            yield OptionList(
                Option("[dim]✗ No[/dim]", id="no"),
                Option("[white]✓[/white] [bold]Yes[/bold]", id="yes"),
                id="action-menu",
            )
            yield Static("↑↓/jk select    \\[enter] confirm    \\[esc] cancel", id="menu-hint")

    def on_mount(self) -> None:
        self.query_one("#action-menu", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_cursor_down(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_up()


class SortMenuScreen(ModalScreen[tuple | None]):
    """Modal for selecting the sort column and direction."""

    BINDINGS = _MODAL_BINDINGS

    CSS = _safe_css(
        "SortMenuScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(
        self, current_column: int, current_reverse: bool, column_names: dict[int, str] | None = None
    ) -> None:
        super().__init__()
        self._current_column = current_column
        self._current_reverse = current_reverse
        self._column_names = column_names or _SORT_COLUMN_NAMES

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static("[bold white]Sort by[/bold white]", id="menu-title")
            items: list[Option] = []
            for idx, name in self._column_names.items():
                if idx == self._current_column:
                    arrow = "▼" if self._current_reverse else "▲"
                    label = f"[cyan]● {name} {arrow}[/cyan]"
                else:
                    label = f"  {name}"
                items.append(Option(label, id=f"sort:{idx}"))
            yield OptionList(*items, id="action-menu")
            yield Static("↑↓/jk select    \\[enter] confirm    \\[esc] close", id="menu-hint")

    def on_mount(self) -> None:
        menu = self.query_one("#action-menu", OptionList)
        menu.focus()
        menu.highlighted = self._current_column

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        col_idx = int(event.option.id.split(":")[1])
        if col_idx == self._current_column:
            self.dismiss((col_idx, not self._current_reverse))
        else:
            self.dismiss((col_idx, False))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_up()


__all__ = ["ConfirmScreen", "SortMenuScreen", "_render_ansi_output"]
