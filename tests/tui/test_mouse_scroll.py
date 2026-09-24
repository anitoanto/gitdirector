"""The console's mouse scrolls vertically only."""

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


async def test_a_wide_table_does_not_scroll_sideways_with_the_mouse():
    long_path = "/tmp/" + "very-long-directory-name/" * 12 + "repo"
    app = GitDirectorConsole()
    from pathlib import Path

    app.manager = _mock_manager([_make_info("repo", Path(long_path))])
    async with app.run_test(size=(60, 20)) as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#repo-table", DataTable)
        assert table.max_scroll_x > 0
        await pilot.hover("#repo-table", offset=(5, 3))
        for _ in range(3):
            app.post_message(_scroll(events.MouseScrollRight))
            app.post_message(_scroll(events.MouseScrollDown, shift=True))
        await pilot.pause()
        assert table.scroll_x == 0
        # The keyboard still reaches the rest of the row.
        table.focus()
        table.scroll_right(animate=False)
        await pilot.pause()
        assert table.scroll_x > 0
