"""Small widget adjustments shared by the console's tabs."""

from __future__ import annotations

from textual import events
from textual.widgets import DataTable


class ConsoleTable(DataTable):
    """A row table whose hover highlight only ever sits under the pointer."""

    async def _on_click(self, event: events.Click) -> None:
        # Textual would run DataTable's own handler again after this one.
        event.prevent_default()
        await super()._on_click(event)
        # DataTable turns the hover highlight back on for any click, so a click
        # below the last row would light up whichever row was hovered last.
        if "row" not in event.style.meta:
            self._set_hover_cursor(False)
