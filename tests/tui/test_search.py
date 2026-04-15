"""Tests for TUI search and filter functionality."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from textual.widgets import DataTable, Input, Static

from gitdirector.commands.tui import GitDirectorConsole

from .conftest import SAMPLE_SESSIONS, _make_info, _mock_manager


class TestSearch:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_search_shows_input(self, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            container = app.query_one("#search-container")
            assert not container.display
            await pilot.press("slash")
            assert container.display

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_search_hides_on_enter(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("slash")
            container = app.query_one("#search-container")
            search_bar = app.query_one("#search-bar", Input)
            search_bar.value = "alpha"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert not container.display
            assert app._search_query == "alpha"

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_search_escape_clears(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("slash")
            container = app.query_one("#search-container")
            search_bar = app.query_one("#search-bar", Input)
            search_bar.value = "alpha"
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert not container.display
            assert app._search_query == ""
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 2

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_search_filters_by_name(self, _mock_sessions):
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
            app._search_query = "alpha"
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 1

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_search_filters_by_branch(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha"), branch="main"),
            _make_info("beta", Path("/tmp/beta"), branch="develop"),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "develop"
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 1

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_search_filters_by_path(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/home/user/alpha")),
            _make_info("beta", Path("/opt/projects/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "/opt/"
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 1

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_search_case_insensitive(self, _mock_sessions):
        repos = [
            _make_info("AlphaRepo", Path("/tmp/AlphaRepo")),
            _make_info("beta", Path("/tmp/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "alpharepo"
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 1

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_search_no_match(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "zzz_no_match"
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 0

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_search_status_bar_shows_filter(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "alpha"
            app._apply_filter_and_sort()
            await pilot.pause()
            status_text = app.query_one("#status-bar", Static).content
            assert "1 of 2" in status_text
            assert "filter:" in status_text


class TestEscapeClearsFilter:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_escape_clears_active_filter_repos(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "alpha"
            app._apply_filter_and_sort()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 1
            await pilot.press("escape")
            await pilot.pause()
            assert app._search_query == ""
            assert table.row_count == 2

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=SAMPLE_SESSIONS)
    async def test_escape_clears_active_filter_sessions(self, _mock):
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
            await pilot.press("escape")
            await pilot.pause()
            assert app._search_query == ""
            assert table.row_count == 3

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_escape_noop_when_no_filter(self, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 1
            await pilot.press("escape")
            await pilot.pause()
            assert table.row_count == 1
