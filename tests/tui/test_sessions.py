"""Tests for TUI sessions tab, search/sort, and refresh behaviour."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from textual.widgets import DataTable, Input, OptionList, Static, TabbedContent

from gitdirector.commands.tui import GitDirectorConsole, SortMenuScreen

from .conftest import SAMPLE_SESSIONS, _make_info, _mock_manager


class TestSessionsTab:
    async def test_sessions_table_exists(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as _:
            assert app.query_one("#sessions-table", DataTable)

    async def test_sessions_table_has_columns(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as _:
            table = app.query_one("#sessions-table", DataTable)
            assert len(table.columns) == 4

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
            assert no_msg.display is True

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
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
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
            ck = app._sess_col_keys
            row_key = "gd/alpha/shell/1"
            assert table.get_cell(row_key, ck[1]) == "shell"
            assert table.get_cell(row_key, ck[2]) == "alpha"
            assert table.get_cell(row_key, ck[3]) == "gd/alpha/shell/1"


class TestSessionsSearchAndSort:
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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

    @patch("gitdirector.integrations.tmux.get_all_session_statuses", return_value={})
    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {"session_name": "gd/gamma/copilot/1", "repo": "gamma", "purpose": "copilot"},
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
            {"session_name": "gd/beta/claude/1", "repo": "beta", "purpose": "claude"},
        ],
    )
    async def test_default_sessions_sort_is_session_name(self, _mock_list, _mock_status):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            table.move_cursor(row=0)
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/alpha/shell/1"
            status_text = app.query_one("#status-bar", Static).content
            assert "sort:" not in status_text

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_sort_sessions_by_repo(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sessions_sort_column = 2
            app._sessions_sort_reverse = False
            app._apply_sessions_filter_and_sort()
            table = app.query_one("#sessions-table", DataTable)
            ck = app._sess_col_keys
            assert table.get_cell("gd/alpha/shell/1", ck[2]) == "alpha"
            table.move_cursor(row=0)
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/alpha/shell/1"

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_sort_sessions_by_repo_descending(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sessions_sort_column = 2
            app._sessions_sort_reverse = True
            app._apply_sessions_filter_and_sort()
            table = app.query_one("#sessions-table", DataTable)
            table.move_cursor(row=0)
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/gamma/copilot/1"

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_sort_sessions_by_session_name(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sessions_sort_column = 3
            app._sessions_sort_reverse = False
            app._apply_sessions_filter_and_sort()
            table = app.query_one("#sessions-table", DataTable)
            table.move_cursor(row=0)
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/alpha/shell/1"

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_sort_sessions_combined_with_search(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "gd/"
            app._sessions_sort_column = 2
            app._sessions_sort_reverse = True
            app._apply_sessions_filter_and_sort()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 3
            table.move_cursor(row=0)
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/gamma/copilot/1"

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_sessions_status_bar_with_sort(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sessions_sort_column = 2
            app._sessions_sort_reverse = True
            app._apply_sessions_filter_and_sort()
            status_text = app.query_one("#status-bar", Static).content
            assert "sort:" in status_text
            assert "Repository" in status_text
            assert "\u25bc" in status_text

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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
                "gd/alpha/shell/1": {"command": "python", "dead": False},
                "gd/beta/claude/1": {"command": "zsh", "dead": False},
                "gd/gamma/copilot/1": {"command": "zsh", "dead": False},
            }
            app._apply_sessions_filter_and_sort()

            table = app.query_one("#sessions-table", DataTable)
            table.move_cursor(row=0)
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/alpha/shell/1"

            app._session_statuses = {
                "gd/alpha/shell/1": {"command": "zsh", "dead": False},
                "gd/beta/claude/1": {"command": "python", "dead": False},
                "gd/gamma/copilot/1": {"command": "zsh", "dead": False},
            }
            app._active_tab = "sessions"
            app._on_statuses_updated()
            await pilot.pause()

            table.move_cursor(row=0)
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert str(row_key.value) == "gd/alpha/shell/1"

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_sort_action_on_sessions_tab(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, SortMenuScreen)
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count == 4

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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

    @patch("gitdirector.integrations.tmux.get_all_session_statuses", return_value={})
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_search_submit_on_sessions_tab_hides_input_and_filters(
        self, _mock_list, _mock_status
    ):
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

    @patch("gitdirector.integrations.tmux.get_all_session_statuses", return_value={})
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_search_escape_on_sessions_tab_clears_live_search(self, _mock_list, _mock_status):
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

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_handle_sessions_sort_selection_none(self, _mock):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            original_col = app._sessions_sort_column
            original_rev = app._sessions_sort_reverse
            app._handle_sessions_sort_selection(None)
            assert app._sessions_sort_column == original_col
            assert app._sessions_sort_reverse == original_rev


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

    async def test_with_sort(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            app._sessions_sort_column = 2
            app._sessions_sort_reverse = True
            msg = app._build_sessions_loaded_status(3, 3)
            assert "sort: Repository \u25bc" in msg


class TestTabRestorationAfterSuspend:
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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
            app._repos_stale = True

            app.query_one("#tabs", TabbedContent).active = "repos"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "sessions"
            assert app._active_tab == "sessions"
            assert app._resume_target_tab == "sessions"

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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

    @patch("gitdirector.integrations.tmux.get_all_session_statuses", return_value={})
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_resume_restores_selected_session_row(self, _mock_list, _mock_status):
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

            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=1)
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

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_repos_tab_guard_redirects_wrong_tab(self, _mock):
        """Guard for repos tab redirects a spurious sessions switch back."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app._resume_target_tab = "repos"
            app._repos_stale = True

            app.query_one("#tabs", TabbedContent).active = "sessions"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "repos"
            assert app._active_tab == "repos"
            assert app._resume_target_tab == "repos"

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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
            table.move_cursor(row=2)
            await pilot.pause()

            app._capture_resume_selection("repos")
            app._results = {
                str(repos[0].path): repos[0],
                str(repos[1].path): repos[1],
            }
            app._apply_filter_and_sort()
            await pilot.pause()

            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert table.cursor_coordinate.row == 1
            assert str(row_key.value) == str(repos[1].path)

    @patch("gitdirector.integrations.tmux.get_all_session_statuses", return_value={})
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_sessions_table_restore_falls_back_to_saved_row_position(
        self, _mock_list, _mock_status
    ):
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
            app._sessions_entries = [SAMPLE_SESSIONS[0], SAMPLE_SESSIONS[2]]
            app._apply_sessions_filter_and_sort()
            await pilot.pause()

            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            assert table.cursor_coordinate.row == 1
            assert str(row_key.value) == "gd/gamma/copilot/1"

    @patch("gitdirector.integrations.tmux.get_all_session_statuses", return_value={})
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_sessions_table_restore_clears_saved_selection_when_empty(
        self, _mock_list, _mock_status
    ):
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

    async def test_with_filter_and_sort(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            app._search_query = "test"
            app._sessions_sort_column = 3
            app._sessions_sort_reverse = True
            msg = app._build_sessions_loaded_status(2, 5)
            assert "2 of 5" in msg
            assert "filter: 'test'" in msg
            assert "sort: Session Name \u25bc" in msg

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

    async def test_suspend_sets_repos_stale(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        with patch("gitdirector.integrations.tmux.attach_tmux_session"):
            with patch("sys.stdout"):
                app._suspend_and_attach("gd-test-session")
        assert app._repos_stale is True

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_switching_to_repos_reloads_when_stale(self, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.manager.get_repository_status.reset_mock()
            app._repos_stale = True
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.action_tab_repos()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._repos_stale is False
            app.manager.get_repository_status.assert_called_once()

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_switching_to_repos_no_reload_when_not_stale(self, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.manager.get_repository_status.reset_mock()
            app._repos_stale = False
            app.action_tab_sessions()
            await pilot.pause()
            app.action_tab_repos()
            await pilot.pause()
            app.manager.get_repository_status.assert_not_called()

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_suspend_and_attach_refreshes_repo(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        app._refresh_repo_for_path = MagicMock()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )
        with patch("gitdirector.integrations.tmux.attach_tmux_session"):
            with patch("sys.stdout"):
                app._suspend_and_attach("gd-test", Path("/tmp/alpha"))

    async def test_input_changed_routes_to_sessions_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._active_tab = "sessions"
        app._sessions_entries = list(SAMPLE_SESSIONS)
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


class TestRemoveSessionUpdatesTable:
    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
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

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=["gd/alpha/shell/1"])
    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
        ],
    )
    async def test_do_remove_updates_repo_table_session_count(
        self, _mock_list_all, _mock_kill, _mock_list_repo
    ):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sessions_cache[str(Path("/tmp/alpha"))] = 1
            app._update_row(repos[0], 1)
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            ck = app._col_keys
            assert table.get_cell(str(Path("/tmp/alpha")), ck[5]) == "1"
            app._sessions_entries = [
                {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
            ]
            app._do_remove(True, "gd/alpha/shell/1")
            await pilot.pause()
            assert table.get_cell(str(Path("/tmp/alpha")), ck[5]) == "—"
            assert app._sessions_cache[str(Path("/tmp/alpha"))] == 0
