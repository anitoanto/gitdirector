"""Tests for TUI sessions tab, search/sort, and refresh behaviour."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual import events
from textual.widgets import DataTable, Input, Static, TabbedContent, TextArea

from gitdirector.commands.tui import GitDirectorConsole, SortMenuScreen
from gitdirector.commands.tui.app_sessions import (
    _MIN_SESSIONS_ID_WIDTH,
    _SESSIONS_REPO_WIDTH,
    _resolve_sessions_layout,
)
from gitdirector.integrations.tmux.core import _repo_session_name_segment

from .._timeouts import SYNC_TIMEOUT
from .conftest import (
    SAMPLE_SESSIONS,
    _make_info,
    _mock_manager,
    patch_sessions,
    sample_sessions,
)


def _session_row_lines(app, table, row_key: str) -> list[str]:
    """Return the plain text lines of a composed sessions row."""
    cell = table.get_cell(row_key, app._sess_col_keys[0])
    return [line.rstrip() for line in cell.plain.split("\n")]


class TestSessionsTab:
    async def test_sessions_table_exists(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as _:
            table = app.query_one("#sessions-table", DataTable)
            assert table
            assert table.display is False

    async def test_sessions_table_uses_a_single_composed_column(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as _:
            table = app.query_one("#sessions-table", DataTable)
            assert len(table.columns) == 1
            header = table.columns[app._sess_col_keys[0]].label.plain
            for title in ("Status", "Session", "Repository", "Session ID"):
                assert title in header

    async def test_tab_switching_via_action(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await pilot.pause()
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "sessions"

    async def test_tab_switching_back_to_repos(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await pilot.pause()
            app.action_tab_repos()
            await pilot.pause()
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "repos"

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=[])
    async def test_no_sessions_shows_message(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            no_msg = app.query_one("#no-sessions-message", Static)
            table = app.query_one("#sessions-table", DataTable)
            assert no_msg.display is True
            assert table.display is False

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
            {"session_name": "gd/beta/claude/1", "repo": "beta", "purpose": "claude"},
        ],
    )
    async def test_sessions_populated(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 2

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
        ],
    )
    async def test_sessions_status_bar_singular(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            status_text = app.query_one("#status-bar", Static).content
            assert "1 active session" in status_text

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
            {"session_name": "gd/beta/claude/1", "repo": "beta", "purpose": "claude"},
            {"session_name": "gd/gamma/copilot/1", "repo": "gamma", "purpose": "copilot"},
        ],
    )
    async def test_sessions_status_bar_plural(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            status_text = app.query_one("#status-bar", Static).content
            assert "3 active sessions" in status_text

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
        ],
    )
    async def test_session_row_select_attaches(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._suspend_and_attach = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            table.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            app._suspend_and_attach.assert_called_once_with("gd/alpha/shell/1")

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=[])
    async def test_sessions_no_sessions_status(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            status_text = app.query_one("#status-bar", Static).content
            assert "No active sessions" in status_text

    async def test_get_active_table_repos(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as _:
            table = app._get_active_table()
            assert table.id == "repo-table"

    async def test_get_active_table_sessions(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)):
            app._active_tab = "sessions"
            table = app._get_active_table()
            assert table.id == "sessions-table"

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_key_1_switches_to_repos(self, _mock_sessions):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await pilot.pause()
            await pilot.press("1")
            await pilot.pause()
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "repos"

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=[])
    async def test_key_2_switches_to_sessions(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("2")
            await app.workers.wait_for_complete()
            await pilot.pause()
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "sessions"

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
            {"session_name": "gd/beta/claude/1", "repo": "beta", "purpose": "claude"},
        ],
    )
    async def test_cursor_navigation_on_sessions_tab(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            table.focus()
            await pilot.pause()
            initial_row = table.cursor_coordinate.row
            await pilot.press("j")
            assert table.cursor_coordinate.row == initial_row + 1
            await pilot.press("k")
            assert table.cursor_coordinate.row == initial_row

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {
                "session_name": "gd/alpha/shell/1",
                "repo": "alpha",
                "purpose": "shell",
                "description": "-",
            },
        ],
    )
    async def test_sessions_table_cell_values(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            row_key = "gd/alpha/shell/1"
            lines = _session_row_lines(app, table, row_key)
            assert "shell" in lines[0]
            assert "alpha" in lines[0]
            assert lines[0].endswith("gd/alpha/shell/1")
            # The description lives on its own full-width second line.
            assert lines[1].strip() == "no description"
            assert lines[2] == ""


class TestSessionsSearchAndSort:
    @patch_sessions()
    async def test_search_filters_sessions_by_repo(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "alpha"
            app._apply_sessions_filter_and_sort()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 1

    @patch_sessions()
    async def test_search_filters_sessions_by_purpose(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "claude"
            app._apply_sessions_filter_and_sort()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 1

    @patch_sessions()
    async def test_search_filters_sessions_by_session_name(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "gd/gamma"
            app._apply_sessions_filter_and_sort()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 1

    @patch_sessions()
    async def test_search_no_match_sessions(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "zzz_no_match"
            app._apply_sessions_filter_and_sort()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 0
            assert table.display is True

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {"session_name": "gd/gamma/copilot/1", "repo": "gamma", "purpose": "copilot"},
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
            {"session_name": "gd/beta/claude/1", "repo": "beta", "purpose": "claude"},
        ],
    )
    async def test_sessions_are_grouped_by_repo(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            assert [str(key.value) for key in table.rows] == [
                "gd/alpha/shell/1",
                "gd/beta/claude/1",
                "gd/gamma/copilot/1",
            ]
            status_text = app.query_one("#status-bar", Static).content
            assert "sort:" not in status_text

    @patch_sessions()
    async def test_sessions_status_bar_with_filter(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "alpha"
            app._apply_sessions_filter_and_sort()
            status_text = app.query_one("#status-bar", Static).content
            assert "1 of 3" in status_text
            assert "filter:" in status_text

    @patch_sessions()
    async def test_status_refresh_does_not_resort_rows_when_sorted_by_status(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            app._sessions_sort_column = 0
            app._sessions_sort_reverse = False
            app._session_statuses = {
                "gd/alpha/shell/1": "running",
                "gd/beta/claude/1": "idle",
                "gd/gamma/copilot/1": "idle",
            }
            app._apply_sessions_filter_and_sort()

            table = app.query_one("#sessions-table", DataTable)
            table.move_cursor(row=0)
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/alpha/shell/1"

            app._session_statuses = {
                "gd/alpha/shell/1": "idle",
                "gd/beta/claude/1": "running",
                "gd/gamma/copilot/1": "idle",
            }
            app._active_tab = "sessions"
            generation = app._next_sessions_snapshot_generation()
            app._apply_sessions_snapshot(
                generation,
                sample_sessions(),
                app._session_statuses,
                False,
            )
            await pilot.pause()

            table.move_cursor(row=0)
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/alpha/shell/1"

    @patch_sessions()
    async def test_sort_is_off_on_sessions_tab(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            # Sessions are grouped by repo; there is no sort menu here.
            assert not isinstance(app.screen, SortMenuScreen)
            assert app.check_action("sort", ()) is False

    @patch_sessions()
    async def test_search_on_sessions_tab_via_input(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("slash")
            search_bar = app.query_one("#search-bar", Input)
            search_bar.value = "beta"
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 1

    @patch_sessions()
    async def test_search_submit_on_sessions_tab_hides_input_and_filters(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("slash")
            search_bar = app.query_one("#search-bar", Input)
            search_bar.value = "beta"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            container = app.query_one("#search-container")
            table = app.query_one("#sessions-table", DataTable)
            assert container.display is False
            assert table.row_count == 1

    @patch_sessions()
    async def test_search_escape_on_sessions_tab_clears_live_search(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("slash")
            search_bar = app.query_one("#search-bar", Input)
            search_bar.value = "beta"
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            container = app.query_one("#search-container")
            table = app.query_one("#sessions-table", DataTable)
            assert container.display is False
            assert app._search_query == ""
            assert table.row_count == 3


class TestBuildSessionsLoadedStatus:
    async def test_no_sessions_no_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            assert app._build_sessions_loaded_status(0, 0) == "No active sessions"

    async def test_single_session(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            msg = app._build_sessions_loaded_status(1, 1)
            assert "1 active session" in msg
            assert "sessions" not in msg.split("1 active ")[1].split(" ")[0]

    async def test_multiple_sessions(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            msg = app._build_sessions_loaded_status(3, 3)
            assert "3 active sessions" in msg

    async def test_with_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            app._search_query = "alpha"
            msg = app._build_sessions_loaded_status(1, 3)
            assert "1 of 3" in msg
            assert "filter: 'alpha'" in msg


class TestTabRestorationAfterSuspend:
    @patch_sessions()
    async def test_spurious_tab_reset_redirected_back_to_sessions(self, _mock):
        """When TabbedContent resets to repos after suspend, the guard redirects back."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._active_tab == "sessions"

            app._resume_target_tab = "sessions"
            app._active_tab = "sessions"

            app.query_one("#tabs", TabbedContent).active = "repos"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "sessions"
            assert app._active_tab == "sessions"
            assert app._resume_target_tab == "sessions"

    @patch_sessions()
    async def test_guard_persists_when_no_spurious_event(self, _mock):
        """If no spurious tab reset happens, guard persists harmlessly."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            app._resume_target_tab = "sessions"
            await pilot.pause()

            assert app._active_tab == "sessions"
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "sessions"

    @patch_sessions()
    async def test_mismatched_tab_action_is_ignored_while_restore_pending(self, _mock):
        """A tab action for the wrong tab must not break the pending restore."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            app._resume_target_tab = "sessions"

            await pilot.press("1")
            await pilot.pause()

            assert app._resume_target_tab == "sessions"
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "sessions"

    @patch_sessions()
    async def test_resume_hook_restores_target_tab_and_clears_guard(self, _mock):
        """The app resume hook restores the target tab and then clears the guard."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._load_sessions = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._load_sessions.reset_mock()

            tabs = app.query_one("#tabs", TabbedContent)
            tabs.active = "repos"
            await pilot.pause()

            app._resume_target_tab = "sessions"
            app._active_tab = "sessions"
            app._handle_app_resume(app)
            await pilot.pause()
            await pilot.pause()

            assert tabs.active == "sessions"
            assert app._active_tab == "sessions"
            assert app._resume_target_tab is None
            app._load_sessions.assert_called_once()

    @patch_sessions()
    async def test_resume_tab_activation_guard_suppresses_reload(self, _mock):
        """The one-shot resume activation guard must suppress a duplicate reload."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._load_sessions = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._load_sessions.reset_mock()

            app._resume_tab_activation_guard = "sessions"

            event = MagicMock()
            event.pane.id = "sessions"
            app.on_tabbed_content_tab_activated(event)

            assert app._resume_tab_activation_guard is None
            app._load_sessions.assert_not_called()

    @patch_sessions()
    async def test_resume_restores_selected_session_row(self, _mock_list):
        """Returning from a session keeps the same session row selected."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#sessions-table", DataTable)
            table.move_cursor(row=1)
            await pilot.pause()

            with patch("gitdirector.integrations.tmux.attach_tmux_session"):
                with patch("sys.stdout"):
                    with patch("termios.tcflush"):
                        app._suspend_and_attach("gd/beta/claude/1")

            table.move_cursor(row=0)
            await pilot.pause()

            with patch("termios.tcflush"):
                app._handle_app_resume(app)

            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/beta/claude/1"

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=[])
    async def test_resume_restores_selected_repo_row(self, _mock_sessions, _mock_all):
        """Returning from a repo-opened session keeps the same repo row selected."""
        repos = [
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
            _make_info("gamma", Path("/tmp/gamma")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.manager.get_repository_status.reset_mock()

            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=2)
            await pilot.pause()

            with patch("gitdirector.integrations.tmux.attach_tmux_session"):
                with patch("sys.stdout"):
                    with patch("termios.tcflush"):
                        app._suspend_and_attach("gd/beta/shell/1", Path("/tmp/beta"))

            table.move_cursor(row=0)
            await pilot.pause()

            with patch("termios.tcflush"):
                app._handle_app_resume(app)

            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "/tmp/beta"
            app.manager.get_repository_status.assert_called_once_with(Path("/tmp/beta"), fetch=True)

    async def test_resume_from_sessions_refreshes_backing_repo_row(self):
        beta = _make_info("beta", Path("/tmp/beta"))
        session_name = f"gd/{_repo_session_name_segment(beta.path)}/claude/1"
        session_entries = [
            {
                "session_name": session_name,
                "repo": "beta",
                "repo_slug": _repo_session_name_segment(beta.path),
                "purpose": "claude",
                "description": "-",
            }
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager([beta])
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        with patch(
            "gitdirector.integrations.tmux.list_all_gd_sessions", return_value=session_entries
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.pause()
                app.manager.get_repository_status.reset_mock()

                app.action_tab_sessions()
                await app.workers.wait_for_complete()
                await pilot.pause()

                with patch("gitdirector.integrations.tmux.attach_tmux_session"):
                    with patch("sys.stdout"):
                        with patch("termios.tcflush"):
                            app._suspend_and_attach(session_name)

                with patch("termios.tcflush"):
                    app._handle_app_resume(app)

                await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()

                app.manager.get_repository_status.assert_called_once_with(beta.path, fetch=True)

    @patch_sessions()
    async def test_repos_tab_guard_redirects_wrong_tab(self, _mock):
        """Guard for repos tab redirects a spurious sessions switch back."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app._resume_target_tab = "repos"

            app.query_one("#tabs", TabbedContent).active = "sessions"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "repos"
            assert app._active_tab == "repos"
            assert app._resume_target_tab == "repos"

    @patch_sessions()
    async def test_guard_survives_multiple_spurious_resets(self, _mock):
        """Guard keeps redirecting even after multiple spurious tab resets."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            app._resume_target_tab = "sessions"
            app._active_tab = "sessions"

            app.query_one("#tabs", TabbedContent).active = "repos"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.query_one("#tabs", TabbedContent).active == "sessions"

            app.query_one("#tabs", TabbedContent).active = "repos"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "sessions"
            assert app._resume_target_tab == "sessions"

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_repo_table_restore_falls_back_to_saved_row_position(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
            _make_info("gamma", Path("/tmp/gamma")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=3)
            await pilot.pause()

            app._capture_resume_selection("repos")
            app._repo_paths = [repos[0].path, repos[1].path]
            app._results = {
                str(repos[0].path): repos[0],
                str(repos[1].path): repos[1],
            }
            app._apply_filter_and_sort()
            await pilot.pause()

            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert table.cursor_coordinate.row == 2
            assert str(row_key.value) == str(repos[1].path)

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=[])
    async def test_repo_table_refresh_preserves_selected_row(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
            _make_info("gamma", Path("/tmp/gamma")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=1)
            await pilot.pause()

            app._sort_reverse = True
            app._apply_filter_and_sort()
            await pilot.pause()

            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == str(repos[0].path)
            assert table.cursor_coordinate.row == 3

    @patch_sessions()
    async def test_sessions_table_restore_falls_back_to_saved_row_position(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#sessions-table", DataTable)
            table.move_cursor(row=1)
            await pilot.pause()

            app._capture_resume_selection("sessions")
            # The selected session (beta) is gone; keep the mocked tmux
            # source consistent so any refresh sees the same two sessions.
            remaining = [SAMPLE_SESSIONS[0], SAMPLE_SESSIONS[2]]
            _mock_list.side_effect = lambda *_args, **_kwargs: sample_sessions(remaining)
            app._sessions_entries = sample_sessions(remaining)
            app._apply_sessions_filter_and_sort()
            await pilot.pause()

            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert table.cursor_coordinate.row == 1
            assert str(row_key.value) == "gd/gamma/copilot/1"

    @patch_sessions()
    async def test_sessions_table_restore_clears_saved_selection_when_empty(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#sessions-table", DataTable)
            table.move_cursor(row=1)
            await pilot.pause()

            app._capture_resume_selection("sessions")
            app._sessions_entries = []
            app._apply_sessions_filter_and_sort()
            await pilot.pause()

            assert table.row_count == 0
            assert app._resume_selection_tab is None
            assert app._resume_selection_key is None
            assert app._resume_selection_row is None

    @patch_sessions()
    async def test_sessions_table_refresh_preserves_selected_row(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#sessions-table", DataTable)
            table.move_cursor(row=1)
            await pilot.pause()

            app._apply_sessions_filter_and_sort()
            await pilot.pause()

            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/beta/claude/1"

    async def test_with_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            app._search_query = "test"
            msg = app._build_sessions_loaded_status(2, 5)
            assert "2 of 5" in msg
            assert "filter: 'test'" in msg
            assert "sort:" not in msg

    async def test_esc_clear_search_hint_shown(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            app._search_query = "test"
            msg = app._build_sessions_loaded_status(1, 3)
            assert "[esc] clear search" in msg

    async def test_esc_clear_search_hint_not_shown_without_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            msg = app._build_sessions_loaded_status(3, 3)
            assert "[esc] clear search" not in msg


class TestSessionsRefreshOnReturn:
    async def test_status_poll_reconciles_rows_when_it_supersedes_full_load(self):
        full_load_started = threading.Event()
        release_full_load = threading.Event()
        calls = 0

        def list_sessions():
            nonlocal calls
            calls += 1
            if calls == 1:
                full_load_started.set()
                assert release_full_load.wait(SYNC_TIMEOUT)
                return []
            return sample_sessions()

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        with patch("gitdirector.integrations.tmux.list_all_gd_sessions", side_effect=list_sessions):
            async with app.run_test(size=(120, 30)) as pilot:
                app._active_tab = "sessions"
                stale_worker = app._load_sessions()
                await asyncio.wait_for(
                    asyncio.to_thread(full_load_started.wait), timeout=SYNC_TIMEOUT
                )

                app._monitor.entries = sample_sessions
                app._monitor.statuses = lambda: {}
                app._poll_session_statuses()
                await pilot.pause()

                release_full_load.set()
                await stale_worker.wait()
                await pilot.pause()

                table = app.query_one("#sessions-table", DataTable)
                assert table.row_count == len(SAMPLE_SESSIONS)
                assert [entry["session_name"] for entry in app._sessions_entries] == [
                    entry["session_name"] for entry in SAMPLE_SESSIONS
                ]

    async def test_stale_empty_refresh_does_not_replace_newer_sessions(self):
        first_started = threading.Event()
        release_first = threading.Event()
        calls = 0

        def list_sessions():
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                assert release_first.wait(SYNC_TIMEOUT)
                return []
            return sample_sessions()

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        with patch("gitdirector.integrations.tmux.list_all_gd_sessions", side_effect=list_sessions):
            async with app.run_test(size=(120, 30)) as pilot:
                # Only the active Sessions tab paints the table; off-tab
                # loads just refresh the cache.
                app._active_tab = "sessions"
                stale_worker = app._load_sessions()
                await asyncio.wait_for(asyncio.to_thread(first_started.wait), timeout=SYNC_TIMEOUT)

                current_worker = app._load_sessions()
                await current_worker.wait()
                await pilot.pause()

                release_first.set()
                await stale_worker.wait()
                await pilot.pause()

                table = app.query_one("#sessions-table", DataTable)
                assert table.row_count == len(SAMPLE_SESSIONS)
                assert [entry["session_name"] for entry in app._sessions_entries] == [
                    entry["session_name"] for entry in SAMPLE_SESSIONS
                ]

    @patch_sessions()
    async def test_sessions_tab_opens_from_cache_without_reloading(self, _mock):
        """The list is loaded at startup and kept current on every tab, so
        switching to the Sessions tab paints from the cache instantly
        instead of showing a load."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            # Loaded in the background while the Repos tab was active...
            assert app._active_tab == "repos"
            assert app._sessions_loaded is True
            assert [e["session_name"] for e in app._sessions_entries] == [
                e["session_name"] for e in SAMPLE_SESSIONS
            ]
            # ...without touching the (hidden) table or the status bar.
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 0

            app._load_sessions = MagicMock()
            await pilot.press("2")
            await pilot.pause()

            assert app._active_tab == "sessions"
            assert table.row_count == len(SAMPLE_SESSIONS)
            app._load_sessions.assert_not_called()

    @patch_sessions()
    async def test_off_tab_poll_updates_cache_for_next_tab_visit(self, _mock):
        """A session created while on the Repos tab is picked up by the
        background poll and shows on the next switch, with no load."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._active_tab == "repos"

            new_entry = {
                "session_name": "gd/delta/shell/1",
                "repo": "delta",
                "repo_slug": "delta",
                "purpose": "shell",
                "description": "-",
            }
            app._monitor.entries = lambda: sample_sessions() + [dict(new_entry)]
            app._monitor.statuses = lambda: {}
            app._poll_session_statuses()
            await pilot.pause()

            assert "gd/delta/shell/1" in {e["session_name"] for e in app._sessions_entries}
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 0

            app._load_sessions = MagicMock()
            await pilot.press("2")
            await pilot.pause()

            assert table.row_count == len(SAMPLE_SESSIONS) + 1
            assert "gd/delta/shell/1" in {str(k.value) for k in table.rows}
            app._load_sessions.assert_not_called()

    async def test_sessions_tab_loads_when_nothing_cached_yet(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._load_sessions = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._load_sessions.reset_mock()
            assert app._sessions_loaded is False

            await pilot.press("2")
            await pilot.pause()

            app._load_sessions.assert_called_once_with()

    async def test_suspend_and_attach_refreshes_sessions_tab(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._active_tab = "sessions"
        app._load_sessions = MagicMock()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        with patch("gitdirector.integrations.tmux.attach_tmux_session"):
            with patch("sys.stdout"):
                app._suspend_and_attach("gd-test-session")
        assert app._active_tab == "sessions"

    async def test_the_deck_is_built_before_suspending(self):
        """The terminal goes straight from the console to the finished deck."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._active_tab = "sessions"
        app._load_sessions = MagicMock()
        order = []
        app.suspend = MagicMock(
            return_value=MagicMock(
                __enter__=MagicMock(side_effect=lambda *_: order.append("suspend")),
                __exit__=MagicMock(return_value=False),
            )
        )

        def prepare(session):
            order.append("prepare")
            return "gd/deck/1-a"

        with (
            patch("gitdirector.integrations.tmux.prepare_attach", side_effect=prepare),
            patch("gitdirector.integrations.tmux.attach_tmux_session") as attach,
            patch("sys.stdout"),
        ):
            app._suspend_and_attach("gd/repo/claude/1")
        assert order == ["prepare", "suspend"]
        assert attach.call_args.kwargs["deck"] == "gd/deck/1-a"

    async def test_a_failed_prepare_leaves_the_attach_to_report_it(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._active_tab = "sessions"
        app._load_sessions = MagicMock()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        with (
            patch("gitdirector.integrations.tmux.prepare_attach", side_effect=RuntimeError("x")),
            patch("gitdirector.integrations.tmux.attach_tmux_session") as attach,
            patch("sys.stdout"),
        ):
            app._suspend_and_attach("gd/repo/claude/1")
        assert attach.call_args.kwargs["deck"] is None

    async def test_suspend_and_attach_failure_still_resumes_app(self):
        """An attach failure must not escape ``suspend`` (Textual never resumes)."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._active_tab = "sessions"
        app._load_sessions = MagicMock()
        suspend_exit = MagicMock(return_value=False)
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=suspend_exit)
        )
        app._update_status = MagicMock()
        stdout = MagicMock()
        with patch(
            "gitdirector.integrations.tmux.attach_tmux_session",
            side_effect=RuntimeError("tmux exploded"),
        ):
            with patch("sys.stdout", stdout), patch.dict("os.environ", {}, clear=True):
                app._suspend_and_attach("gd-test-session")

        # The context manager completed normally: no exception reached it.
        suspend_exit.assert_called_once()
        assert suspend_exit.call_args.args == (None, None, None)
        # The manually entered alt screen was left and the cursor restored
        # while stdout still pointed at the terminal, so no black screen.
        written = "".join(call.args[0] for call in stdout.write.call_args_list)
        assert "\033[?1049h" in written
        assert written.endswith("\033[?25h\033[?1049l")
        app._update_status.assert_called_once_with("tmux attach failed: tmux exploded")
        assert app._active_tab == "sessions"
        assert app._session_status_tracking_paused is False

    async def test_suspend_and_attach_success_does_not_leave_alt_screen(self):
        """tmux leaves the alt screen itself; only the cursor is restored."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._active_tab = "sessions"
        app._load_sessions = MagicMock()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        app._update_status = MagicMock()
        stdout = MagicMock()
        with patch("gitdirector.integrations.tmux.attach_tmux_session"):
            with patch("sys.stdout", stdout), patch.dict("os.environ", {}, clear=True):
                app._suspend_and_attach("gd-test-session")
        written = "".join(call.args[0] for call in stdout.write.call_args_list)
        assert written.endswith("\033[?25h")
        assert "\033[?1049l" not in written
        app._update_status.assert_not_called()

    @pytest.mark.parametrize("fails", [False, True])
    async def test_the_console_frame_covers_the_way_back(self, fails):
        """The display held by tmux's exit is covered by the console, then released."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._active_tab = "sessions"
        app._load_sessions = MagicMock()
        app._current_frame = MagicMock(return_value="<frame>")
        app.call_after_refresh = MagicMock()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        app._update_status = MagicMock()
        stdout = MagicMock()
        attach = RuntimeError("tmux exploded") if fails else None
        with patch("gitdirector.integrations.tmux.attach_tmux_session", side_effect=attach):
            with patch("sys.stdout", stdout), patch.dict("os.environ", {}, clear=True):
                app._suspend_and_attach("gd-test-session")
        written = "".join(call.args[0] for call in stdout.write.call_args_list)
        if fails:
            assert "<frame>" not in written
            assert written.endswith("\033[?2026l\033[?25h\033[?1049l")
        else:
            assert written.endswith("\033[?2026h\033[?1049h\033[?25l<frame>")
        app.call_after_refresh.assert_called_once_with(app._release_held_frame)

    async def test_suspend_keeps_repository_cache_valid(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._repos_cache_updated_at = 123.0
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        with patch("gitdirector.integrations.tmux.attach_tmux_session"):
            with patch("sys.stdout"):
                app._suspend_and_attach("gd-test-session")
        assert app._repos_cache_updated_at == 123.0

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    @patch("gitdirector.commands.tui.app_repos.monotonic", return_value=1800.0)
    async def test_switching_to_repos_reloads_when_cache_expired(self, _mock_time, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.manager.get_repository_status.reset_mock()
            app._repos_cache_updated_at = 0.0
            app.action_tab_sessions()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.action_tab_repos()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.manager.get_repository_status.assert_called_once()

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    @patch("gitdirector.commands.tui.app_repos.monotonic", return_value=1799.0)
    async def test_switching_to_repos_uses_cache_within_ttl(self, _mock_time, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.manager.get_repository_status.reset_mock()
            app._repos_cache_updated_at = 0.0
            app.action_tab_sessions()
            await pilot.pause()
            app.action_tab_repos()
            await pilot.pause()
            await pilot.pause()
            app.manager.get_repository_status.assert_not_called()

    async def test_input_changed_routes_to_sessions_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._active_tab = "sessions"
        app._sessions_entries = sample_sessions()
        app._apply_sessions_filter_and_sort = MagicMock()
        event = MagicMock()
        event.input.id = "search-bar"
        event.value = "test"
        app.on_input_changed(event)
        assert app._search_query == "test"
        app._apply_sessions_filter_and_sort.assert_called_once()

    async def test_input_changed_routes_to_repos_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._active_tab = "repos"
        app._apply_filter_and_sort = MagicMock()
        event = MagicMock()
        event.input.id = "search-bar"
        event.value = "test"
        app.on_input_changed(event)
        assert app._search_query == "test"
        app._apply_filter_and_sort.assert_called_once()


class TestNoReopenAfterReturn:
    """Input that arrives while a session opens must not open it again on return."""

    @staticmethod
    def _mouse(event_cls, table: DataTable):
        x, y = table.region.offset + (4, 1)
        return event_cls(
            widget=None,
            x=x,
            y=y,
            delta_x=0,
            delta_y=0,
            button=1,
            shift=False,
            meta=False,
            ctrl=False,
            screen_x=x,
            screen_y=y,
        )

    async def _open_with_input_during_build(self, stale_input) -> MagicMock:
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            table.focus()
            await pilot.pause()

            pending = list(stale_input(table))

            def prepare(_session_name):
                # The driver keeps reading the terminal while the deck is built.
                while pending:
                    app.post_message(pending.pop(0))
                return None

            with (
                patch("gitdirector.integrations.tmux.prepare_attach", side_effect=prepare),
                patch("gitdirector.integrations.tmux.attach_tmux_session") as attach,
                patch("sys.stdout"),
                patch("termios.tcflush"),
            ):
                await pilot.press("enter")
                for _ in range(5):
                    await pilot.pause()
                await app.workers.wait_for_complete()
                await pilot.pause()
        return attach

    @patch_sessions()
    async def test_second_click_of_a_double_click(self, _mock_list):
        attach = await self._open_with_input_during_build(
            lambda table: [
                self._mouse(events.MouseDown, table),
                self._mouse(events.MouseUp, table),
            ]
        )
        assert attach.call_count == 1

    @patch_sessions()
    async def test_repeated_enter(self, _mock_list):
        attach = await self._open_with_input_during_build(
            lambda _table: [events.Key("enter", "\r")]
        )
        assert attach.call_count == 1

    @patch_sessions()
    async def test_press_released_inside_the_session(self, _mock_list):
        """Its release went to tmux; the next motion must not complete a click."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            app._mouse_down_widget = table
            with (
                patch("gitdirector.integrations.tmux.attach_tmux_session") as attach,
                patch("sys.stdout"),
                patch("termios.tcflush"),
            ):
                app._suspend_and_attach("gd/alpha/shell/1")
                attach.reset_mock()
                await pilot.pause()
                # What Textual synthesizes on the first motion after return.
                app.post_message(self._mouse(events.MouseUp, table))
                for _ in range(3):
                    await pilot.pause()
            attach.assert_not_called()

    @patch_sessions()
    async def test_input_after_return_still_opens(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.query_one("#sessions-table", DataTable).focus()
            await pilot.pause()
            with (
                patch("gitdirector.integrations.tmux.attach_tmux_session") as attach,
                patch("sys.stdout"),
                patch("termios.tcflush"),
            ):
                await pilot.press("enter")
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
            assert attach.call_count == 2


class TestMouseAfterReturn:
    """The mouse must work on the console once a session is left."""

    def test_resume_leaves_the_terminal_replies_to_textual(self):
        # Textual has restarted input and queried the terminal by the time
        # the resume hook runs; the in-band resize report that tells it mouse
        # coordinates are in pixels may already be waiting.
        import os
        import tty

        report = b"\x1b[48;40;150;640;1200t"
        master, slave = os.openpty()
        try:
            tty.setraw(slave)
            os.write(master, report)
            app = GitDirectorConsole()
            app._resume_target_tab = "sessions"
            app.call_after_refresh = MagicMock()
            with patch("sys.stdin", MagicMock(fileno=MagicMock(return_value=slave))):
                app._handle_app_resume(app)
            os.set_blocking(slave, False)
            assert os.read(slave, 64) == report
            app.call_after_refresh.assert_called_once()
        finally:
            os.close(master)
            os.close(slave)

    @patch_sessions()
    async def test_capture_left_by_a_press_released_inside_the_session(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.capture_mouse(app.query_one("#sessions-table", DataTable))
            with (
                patch("gitdirector.integrations.tmux.attach_tmux_session"),
                patch("sys.stdout"),
                patch("termios.tcflush"),
            ):
                app._suspend_and_attach("gd/alpha/shell/1")
            app._handle_app_resume(app)
            for _ in range(3):
                await pilot.pause()
            assert app.mouse_captured is None

            await pilot.click("#nav-repos")
            await pilot.pause()
            assert app._active_tab == "repos"


class TestRemoveSessionUpdatesTable:
    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    @patch_sessions()
    async def test_do_remove_updates_sessions_table(self, _mock_list, _mock_kill):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 3
            app._do_remove(True, "gd/alpha/shell/1")
            await pilot.pause()
            assert table.row_count == 2
            _mock_kill.assert_called_once_with("gd/alpha/shell/1")

    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    @patch_sessions()
    async def test_do_remove_removes_from_sessions_entries(self, _mock_list, _mock_kill):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._do_remove(True, "gd/beta/claude/1")
            await pilot.pause()
            remaining = [e["session_name"] for e in app._sessions_entries]
            assert "gd/beta/claude/1" not in remaining
            assert len(remaining) == 2

    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    @patch_sessions()
    async def test_do_remove_updates_status_bar(self, _mock_list, _mock_kill):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._do_remove(True, "gd/alpha/shell/1")
            await pilot.pause()
            status_text = app.query_one("#status-bar", Static).content
            assert "2 active sessions" in status_text

    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
        ],
    )
    async def test_do_remove_last_session_shows_no_sessions(self, _mock_list, _mock_kill):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._do_remove(True, "gd/alpha/shell/1")
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 0

    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    @patch_sessions()
    async def test_do_remove_not_confirmed_does_nothing(self, _mock_list, _mock_kill):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._do_remove(False, "gd/alpha/shell/1")
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 3
            _mock_kill.assert_not_called()

    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
        ],
    )
    async def test_do_remove_updates_sessions_table_only(self, _mock_list_all, _mock_kill):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sessions_entries = [
                {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
            ]
            app._do_remove(True, "gd/alpha/shell/1")
            await pilot.pause()
            assert app._sessions_entries == []
            _mock_kill.assert_called_once_with("gd/alpha/shell/1")


class TestSessionDescription:
    @patch_sessions()
    async def test_description_shows_below_the_columns(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sessions_entries[0]["description"] = "fix the flaky test"
            app._apply_sessions_filter_and_sort()
            table = app.query_one("#sessions-table", DataTable)
            lines = _session_row_lines(app, table, app._sessions_entries[0]["session_name"])
            assert lines[1].strip() == "fix the flaky test"

    def test_long_session_names_wrap_after_a_slash(self):
        from gitdirector.commands.tui.app_sessions import _wrap_session_name

        name = "gd/noa-browser-extension_ntbdq/claude-dangerously-skip-permissions/1"
        lines = _wrap_session_name(name, 40)
        assert "".join(lines) == name
        assert all(len(line) <= 40 for line in lines)
        assert lines[0] == "gd/noa-browser-extension_ntbdq/"

    @patch_sessions()
    async def test_description_line_uses_placeholder_when_unset(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            assert _session_row_lines(app, table, "gd/alpha/shell/1")[1].strip() == "no description"

    @patch_sessions()
    async def test_search_matches_description(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sessions_entries[0]["description"] = "alpha only"
            app._sessions_entries[1]["description"] = "shared"
            app._sessions_entries[2]["description"] = "shared"
            app._search_query = "alpha only"
            app._apply_sessions_filter_and_sort()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 1
            table.move_cursor(row=0)
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/alpha/shell/1"

    @patch_sessions()
    async def test_long_description_wraps_to_max_width(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            long_text = "this is a long description that should wrap over multiple lines " * 3
            long_text = long_text.strip()
            for entry in app._sessions_entries:
                entry["description"] = long_text
            app._apply_sessions_filter_and_sort()
            table = app.query_one("#sessions-table", DataTable)
            layout = _resolve_sessions_layout(app._sessions_entries, app.size.width)
            lines = _session_row_lines(app, table, "gd/alpha/shell/1")
            # The description wraps in full below the columns instead of truncating.
            assert len(lines) > 3
            assert " ".join(line.strip() for line in lines[1:]).strip() == long_text
            for line in lines:
                assert len(line) <= layout.cell_width

    def test_resolve_sessions_layout_repo_width_is_independent_of_names(self):
        short = _resolve_sessions_layout([{"purpose": "shell", "repo": "a"}], 160)
        long = _resolve_sessions_layout(
            [{"purpose": "shell", "repo": "a-very-long-repository-name-indeed"}], 160
        )
        empty = _resolve_sessions_layout([], 160)

        assert short.repo == long.repo == empty.repo == _SESSIONS_REPO_WIDTH

    def test_resolve_sessions_layout_gives_the_session_id_the_remaining_width(self):
        entries = [{"purpose": "shell", "repo": "alpha"}]
        narrow = _resolve_sessions_layout(entries, 60)
        wide = _resolve_sessions_layout(entries, 160)

        assert narrow.session_id < wide.session_id
        assert narrow.session_id >= _MIN_SESSIONS_ID_WIDTH
        assert wide.total <= 160

    @patch_sessions()
    async def test_sessions_column_spans_the_resolved_width(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#sessions-table", DataTable)
            layout = _resolve_sessions_layout(app._sessions_entries, app.size.width)
            column = table.columns[app._sess_col_keys[0]]
            assert column.width == layout.cell_width
            assert "Session ID" in column.label.plain

    @patch("gitdirector.integrations.tmux.core._set_session_description")
    @patch("gitdirector.integrations.tmux.core._get_session_description", return_value="-")
    @patch_sessions()
    async def test_d_key_opens_description_editor(self, _mock_list, _mock_get_desc, _mock_set_desc):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            table.focus()
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            from gitdirector.commands.tui.screens.sessions import (
                EditSessionDescriptionScreen,
            )

            assert isinstance(app.screen, EditSessionDescriptionScreen)

    async def test_description_editor_expands_for_wrapped_text(self):
        from gitdirector.commands.tui.screens.sessions import EditSessionDescriptionScreen

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = EditSessionDescriptionScreen("gd/alpha/shell/1", "-")
            result: list[str | None] = []
            app.push_screen(screen, callback=result.append)
            await pilot.pause()

            description_input = screen.query_one("#description-input", TextArea)
            initial_height = description_input.size.height
            description = "long description " * 30
            description_input.text = description
            await pilot.pause()

            assert description_input.size.height > initial_height
            await pilot.press("enter")
            await pilot.pause()
            assert result == [description.strip()]

    @patch("gitdirector.integrations.tmux.core._set_session_description")
    @patch("gitdirector.integrations.tmux.core._get_session_description", return_value="-")
    @patch_sessions()
    async def test_handle_description_edit_persists_value(
        self, _mock_list, _mock_get_desc, mock_set_desc
    ):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._handle_description_edit("gd/alpha/shell/1", "  ready to ship  ")
            await pilot.pause()
            mock_set_desc.assert_called_once_with("gd/alpha/shell/1", "  ready to ship  ")
            matching = [e for e in app._sessions_entries if e["session_name"] == "gd/alpha/shell/1"]
            assert matching[0]["description"] == "  ready to ship  "

    @patch("gitdirector.integrations.tmux.core._set_session_description")
    @patch("gitdirector.integrations.tmux.core._get_session_description", return_value="-")
    @patch_sessions()
    async def test_handle_description_edit_empty_resets_to_placeholder(
        self, _mock_list, _mock_get_desc, mock_set_desc
    ):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._handle_description_edit("gd/alpha/shell/1", "")
            await pilot.pause()
            mock_set_desc.assert_called_once_with("gd/alpha/shell/1", "")
            matching = [e for e in app._sessions_entries if e["session_name"] == "gd/alpha/shell/1"]
            assert matching[0]["description"] == "-"

    @patch("gitdirector.integrations.tmux.core._get_session_description", return_value="-")
    @patch_sessions()
    async def test_action_edit_session_description_noop_on_other_tabs(
        self, _mock_list, _mock_get_desc
    ):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.push_screen = MagicMock()
            app.action_tab_repos()
            await pilot.pause()
            app.action_edit_session_description()
            app.push_screen.assert_not_called()


class TestRepoGroups:
    def _entry(self, name: str, repo: str) -> dict[str, str]:
        _, slug, purpose, _ = name.split("/")
        return {
            "session_name": name,
            "repo": repo,
            "repo_slug": slug,
            "purpose": purpose,
            "description": "-",
        }

    def test_each_session_knows_its_place_in_its_repo(self):
        from gitdirector.commands.tui.app_sessions import _repo_positions

        entries = [
            self._entry("gd/alpha/shell/1", "alpha"),
            self._entry("gd/alpha/claude-auto/1", "alpha"),
            self._entry("gd/alpha/codex/1", "alpha"),
            self._entry("gd/beta/shell/1", "beta"),
            self._entry("gd/gamma/shell/1", "gamma"),
            self._entry("gd/gamma/shell/2", "gamma"),
        ]
        assert list(_repo_positions(entries).values()) == [
            "first",
            "middle",
            "last",
            "only",
            "first",
            "last",
        ]

    def test_sessions_of_a_repo_sit_together_in_a_stable_order(self):
        from gitdirector.commands.tui.app_sessions import _session_order

        entries = [
            self._entry("gd/beta/shell/1", "beta"),
            self._entry("gd/alpha/shell/10", "alpha"),
            self._entry("gd/alpha/claude-auto/1", "alpha"),
            self._entry("gd/alpha/shell/2", "alpha"),
        ]
        ordered = [entry["session_name"] for entry in sorted(entries, key=_session_order)]
        assert ordered == [
            "gd/alpha/claude-auto/1",
            "gd/alpha/shell/2",
            "gd/alpha/shell/10",
            "gd/beta/shell/1",
        ]

    def _lines(self, guide=None, position: str = "only") -> list[str]:
        from gitdirector.commands.tui.app_sessions import (
            _render_session_row,
            _resolve_sessions_layout,
        )
        from gitdirector.commands.tui.constants import TablePalette

        palette = TablePalette(success="green", yellow="yellow", muted="grey50", primary="magenta")
        entry = self._entry("gd/alpha/shell/1", "alpha")
        layout = _resolve_sessions_layout([entry], 100)
        row, height = _render_session_row(entry, layout, palette, position=position, guide=guide)
        lines = row.plain.split("\n")
        assert len(lines) == height
        assert all(len(line) == layout.cell_width for line in lines)
        return lines

    def _guides(self, *names_and_repos):
        from gitdirector.commands.tui.app_sessions import _repo_positions, _row_guides

        entries = [self._entry(name, repo) for name, repo in names_and_repos]
        return _row_guides(entries, _repo_positions(entries))

    def test_a_group_is_held_in_one_bracket(self):
        from gitdirector.commands.tui.app_sessions import RowGuide

        guides = self._guides(
            ("gd/alpha/shell/1", "alpha"),
            ("gd/beta/shell/1", "beta"),
            ("gd/beta/shell/2", "beta"),
            ("gd/beta/shell/3", "beta"),
            ("gd/gamma/shell/1", "gamma"),
        )
        assert list(guides.values()) == [
            # The group's first row opens the bracket on a line of its own.
            RowGuide(),
            RowGuide("│", "│", lead="╭"),
            RowGuide("│", "│"),
            RowGuide("│", "│", "╰"),
            RowGuide(),
        ]

    def test_a_group_at_the_top_opens_on_its_own_first_line(self):
        from gitdirector.commands.tui.app_sessions import RowGuide

        guides = self._guides(("gd/beta/shell/1", "beta"), ("gd/beta/shell/2", "beta"))
        assert list(guides.values()) == [RowGuide("╭", "│"), RowGuide("│", "│", "╰")]

    def test_rows_draw_their_bracket_marks(self):
        from gitdirector.commands.tui.app_sessions import RowGuide

        first = self._lines(RowGuide("│", "│"), position="first")
        assert first[0].startswith(" │ alpha")
        assert all(line.startswith(" │") for line in first[1:])
        last = self._lines(RowGuide("│", "│", "╰"), position="last")
        assert "alpha" not in last[0][:30]
        assert last[-1].startswith(" ╰") and last[-2].startswith(" │")
        opening = self._lines(RowGuide("│", "│", lead="╭"), position="first")
        assert opening[0].startswith(" ╭") and opening[0][2:].strip() == ""
        assert opening[1].startswith(" │ alpha")
        assert self._lines()[-1].strip() == ""

    def test_the_opening_line_is_part_of_the_group(self):
        guides = self._guides(
            ("gd/alpha/shell/1", "alpha"),
            ("gd/beta/shell/1", "beta"),
            ("gd/beta/shell/2", "beta"),
        )
        # The row above a group ends on its usual single blank line.
        assert len(self._lines(guides["gd/alpha/shell/1"])) == len(self._lines())

    def test_a_lone_session_has_no_bracket(self):
        only = self._lines()
        assert only[0].startswith("   alpha")
        assert not any(mark in "".join(only) for mark in "╭│╰")


class TestSessionsFitTheWidth:
    @pytest.mark.parametrize("width", [80, 120, 160])
    async def test_there_is_nothing_to_scroll_sideways(self, width):
        from gitdirector.integrations.tmux.core import session_entry

        many = [
            session_entry(f"gd/repo{i % 4}_aaaaa/shell/{i}", f"repo{i % 4}", "")
            for i in range(1, 14)
        ]
        with patch(
            "gitdirector.integrations.tmux.list_all_gd_sessions",
            side_effect=lambda *_a, **_k: [dict(entry) for entry in many],
        ):
            app = GitDirectorConsole()
            app.manager = _mock_manager()
            async with app.run_test(size=(width, 30)) as pilot:
                app.action_tab_sessions()
                await app.workers.wait_for_complete()
                await pilot.pause()
                table = app.query_one("#sessions-table", DataTable)
                # Enough rows for a vertical scrollbar, which takes a column.
                assert table.show_vertical_scrollbar
                assert table.virtual_size.width <= table.scrollable_content_region.width
                assert table.max_scroll_x == 0
