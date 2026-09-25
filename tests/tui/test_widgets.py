"""Console table behaviour shared by every tab."""

from pathlib import Path

from textual.widgets import DataTable

from gitdirector.commands.tui import GitDirectorConsole
from gitdirector.commands.tui.widgets import ConsoleTable

from .conftest import _make_info, _mock_manager


async def test_every_tab_uses_the_console_table():
    app = GitDirectorConsole()
    app.manager = _mock_manager()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        for table_id in ("#repo-table", "#sessions-table", "#panels-table"):
            assert isinstance(app.query_one(table_id, DataTable), ConsoleTable)


async def test_a_click_below_the_rows_leaves_no_hover_highlight():
    app = GitDirectorConsole()
    app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
    async with app.run_test(size=(100, 30)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#repo-table", DataTable)
        # Hover the row, then click well below it.
        await pilot.hover("#repo-table", offset=(5, 1))
        assert table._show_hover_cursor
        await pilot.click("#repo-table", offset=(5, 15))
        await pilot.pause()
        assert not table._show_hover_cursor


async def test_a_focused_table_is_not_tinted():
    app = GitDirectorConsole()
    app.manager = _mock_manager()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        table = app.query_one("#repo-table", DataTable)
        table.focus()
        await pilot.pause()
        assert table.styles.background_tint.a == 0
