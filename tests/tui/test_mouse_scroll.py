"""The console's mouse scrolls vertically only."""

from pathlib import Path

from textual import events
from textual.widgets import DataTable

from gitdirector.commands.tui import GitDirectorConsole
from gitdirector.commands.tui.app import _is_horizontal_mouse_scroll

from .conftest import _make_info, _mock_manager


def _scroll(cls, **modifiers):
    return cls(
        None, 5, 5, 0, 0, 0, modifiers.get("shift", False), False, modifiers.get("ctrl", False)
    )


def test_sideways_and_modified_wheel_events_are_horizontal():
    assert _is_horizontal_mouse_scroll(_scroll(events.MouseScrollLeft))
    assert _is_horizontal_mouse_scroll(_scroll(events.MouseScrollRight))
    assert _is_horizontal_mouse_scroll(_scroll(events.MouseScrollDown, shift=True))
    assert _is_horizontal_mouse_scroll(_scroll(events.MouseScrollUp, ctrl=True))
    assert not _is_horizontal_mouse_scroll(_scroll(events.MouseScrollDown))
    assert not _is_horizontal_mouse_scroll(_scroll(events.MouseScrollUp))


async def test_the_repo_table_fits_its_width_so_never_scrolls_sideways():
    long_name = "very-long-repository-name-" * 4
    app = GitDirectorConsole()
    app.manager = _mock_manager([_make_info(long_name, Path("/tmp") / long_name)])
    async with app.run_test(size=(60, 20)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#repo-table", DataTable)
        # Long names truncate inside the laid-out row instead of widening it.
        assert table.max_scroll_x == 0
        await pilot.hover("#repo-table", offset=(5, 3))
        for _ in range(3):
            app.post_message(_scroll(events.MouseScrollRight))
            app.post_message(_scroll(events.MouseScrollDown, shift=True))
        await pilot.pause()
        assert table.scroll_x == 0


async def test_one_wheel_notch_scrolls_one_row():
    app = GitDirectorConsole()
    app.manager = _mock_manager([_make_info(f"repo{i}", Path(f"/tmp/r{i}")) for i in range(40)])
    async with app.run_test(size=(100, 20)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#repo-table", DataTable)
        assert table.max_scroll_y > 1
        widget, _ = app.screen.get_widget_at(*table.region.offset + (5, 3))
        widget.post_message(_scroll(events.MouseScrollDown))
        await pilot.pause()
        assert table.scroll_y == 1
