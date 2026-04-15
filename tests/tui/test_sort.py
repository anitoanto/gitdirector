"""Tests for TUI sort functionality."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from textual.widgets import DataTable, Static

from gitdirector.commands.tui import GitDirectorConsole, SortMenuScreen
from gitdirector.repo import RepoStatus

from .conftest import _make_info, _mock_manager


class TestSort:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_by_name_ascending(self, _mock_sessions):
        repos = [
            _make_info("gamma", Path("/tmp/gamma")),
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sort_column = 0
            app._sort_reverse = False
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/alpha")
            table.move_cursor(row=2)
            assert app._get_selected_path() == Path("/tmp/gamma")

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_by_name_descending(self, _mock_sessions):
        repos = [
            _make_info("gamma", Path("/tmp/gamma")),
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sort_column = 0
            app._sort_reverse = True
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/gamma")
            table.move_cursor(row=2)
            assert app._get_selected_path() == Path("/tmp/alpha")

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_by_status(self, _mock_sessions):
        repos = [
            _make_info("c", Path("/tmp/c"), status=RepoStatus.UNKNOWN),
            _make_info("a", Path("/tmp/a"), status=RepoStatus.UP_TO_DATE),
            _make_info("b", Path("/tmp/b"), status=RepoStatus.BEHIND),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sort_column = 1
            app._sort_reverse = False
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/a")
            table.move_cursor(row=1)
            assert app._get_selected_path() == Path("/tmp/b")
            table.move_cursor(row=2)
            assert app._get_selected_path() == Path("/tmp/c")

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_by_branch(self, _mock_sessions):
        repos = [
            _make_info("a", Path("/tmp/a"), branch="main"),
            _make_info("b", Path("/tmp/b"), branch="develop"),
            _make_info("c", Path("/tmp/c"), branch="feature"),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sort_column = 2
            app._sort_reverse = False
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/b")

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_by_last_commit_uses_timestamp(self, _mock_sessions):
        repos = [
            _make_info(
                "old",
                Path("/tmp/old"),
                last_updated="3 months ago",
                last_commit_timestamp=1700000000,
            ),
            _make_info(
                "new",
                Path("/tmp/new"),
                last_updated="2 hours ago",
                last_commit_timestamp=1710000000,
            ),
            _make_info(
                "mid",
                Path("/tmp/mid"),
                last_updated="5 days ago",
                last_commit_timestamp=1705000000,
            ),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sort_column = 4
            app._sort_reverse = False
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/old")
            table.move_cursor(row=1)
            assert app._get_selected_path() == Path("/tmp/mid")
            table.move_cursor(row=2)
            assert app._get_selected_path() == Path("/tmp/new")

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_by_last_commit_descending(self, _mock_sessions):
        repos = [
            _make_info(
                "old",
                Path("/tmp/old"),
                last_updated="3 months ago",
                last_commit_timestamp=1700000000,
            ),
            _make_info(
                "new",
                Path("/tmp/new"),
                last_updated="2 hours ago",
                last_commit_timestamp=1710000000,
            ),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sort_column = 4
            app._sort_reverse = True
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/new")
            table.move_cursor(row=1)
            assert app._get_selected_path() == Path("/tmp/old")

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_by_last_commit_none_timestamp(self, _mock_sessions):
        repos = [
            _make_info(
                "has-ts",
                Path("/tmp/has-ts"),
                last_commit_timestamp=1700000000,
            ),
            _make_info(
                "no-ts",
                Path("/tmp/no-ts"),
                last_commit_timestamp=None,
            ),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sort_column = 4
            app._sort_reverse = False
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/no-ts")

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_combined_with_search(self, _mock_sessions):
        repos = [
            _make_info("gamma-api", Path("/tmp/gamma-api"), branch="main"),
            _make_info("alpha-api", Path("/tmp/alpha-api"), branch="develop"),
            _make_info("beta-web", Path("/tmp/beta-web"), branch="main"),
            _make_info("delta-api", Path("/tmp/delta-api"), branch="feature"),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "api"
            app._sort_column = 0
            app._sort_reverse = False
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 3
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/alpha-api")
            table.move_cursor(row=2)
            assert app._get_selected_path() == Path("/tmp/gamma-api")

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_status_bar_indicator(self, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sort_column = 2
            app._sort_reverse = True
            app._apply_filter_and_sort()
            await pilot.pause()
            status_text = app.query_one("#status-bar", Static).content
            assert "sort:" in status_text
            assert "Branch" in status_text
            assert "\u25bc" in status_text

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_binding_opens_menu(self, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            assert isinstance(app.screen, SortMenuScreen)

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_handle_sort_selection_none(self, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            original_col = app._sort_column
            original_rev = app._sort_reverse
            app._handle_sort_selection(None)
            assert app._sort_column == original_col
            assert app._sort_reverse == original_rev

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_preserves_filter(self, _mock_sessions):
        repos = [
            _make_info("alpha-api", Path("/tmp/alpha-api")),
            _make_info("beta-web", Path("/tmp/beta-web")),
            _make_info("gamma-api", Path("/tmp/gamma-api")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "api"
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 2
            app._sort_column = 0
            app._sort_reverse = True
            app._apply_filter_and_sort()
            await pilot.pause()
            assert table.row_count == 2
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/gamma-api")
