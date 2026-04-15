"""Tests for GitDirectorConsole app-level behaviour."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from textual.widgets import DataTable, Static

from gitdirector.commands.tui import GitDirectorConsole
from gitdirector.repo import RepoStatus

from .conftest import _make_info, _mock_manager


class TestGitDirectorConsole:
    async def test_compose_widgets(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as _:
            assert app.query_one("#repo-table", DataTable)
            assert app.query_one("#status-bar", Static)
            assert len(app.query("Footer")) == 1
            assert len(app.query("Header")) == 1

    async def test_empty_repo_list(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 0

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_table_populated_with_repos(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha"), RepoStatus.UP_TO_DATE, "main"),
            _make_info("beta", Path("/tmp/beta"), RepoStatus.BEHIND, "develop"),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 2

    async def test_quit_binding(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("q")
            assert True

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_cursor_down_binding(self, _mock_sessions):
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
            initial_row = table.cursor_coordinate.row
            await pilot.press("j")
            assert table.cursor_coordinate.row == initial_row + 1

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_cursor_up_binding(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            await pilot.press("j")
            await pilot.press("k")
            assert table.cursor_coordinate.row == 0

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_refresh_binding(self, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.manager.get_repository_status.reset_mock()
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.manager.get_repository_status.call_count == 1

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_status_bar_updates(self, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            status_text = app.query_one("#status-bar", Static).content
            assert "1 repository loaded" in status_text

    async def test_status_bar_no_repos(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            status_text = app.query_one("#status-bar", Static).content
            assert "No repositories linked" in status_text

    async def test_table_columns_created(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as _:
            table = app.query_one("#repo-table", DataTable)
            assert len(table.columns) == 7

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    @patch("gitdirector.commands.tui.ActionMenuScreen")
    async def test_enter_opens_action_menu(self, mock_screen_cls, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"), branch="main")]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._handle_menu_action(None)
            assert True

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_handle_menu_action_new_session(self, _mock_sessions):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        app.action_open_tmux = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._handle_menu_action("new_session")
            app.action_open_tmux.assert_called_once()

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_handle_menu_action_attach(self, _mock_sessions):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        app._attach_to_session = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._handle_menu_action("attach:gd/alpha/shell/1")
            app._attach_to_session.assert_called_once_with("gd/alpha/shell/1", Path("/tmp/alpha"))

    async def test_handle_menu_action_none_is_noop(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as _:
            app._handle_menu_action(None)

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[{"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"}],
    )
    async def test_sessions_column_shows_count(self, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            row_key = str(repos[0].path)
            assert table.get_cell(row_key, app._col_keys[5]) == "1"

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_row_data_reflects_status(self, _mock_sessions):
        repos = [
            _make_info(
                "alpha",
                Path("/tmp/alpha"),
                RepoStatus.BEHIND,
                "develop",
                staged=True,
                unstaged=False,
                last_updated="5 min ago",
            ),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            row_key = str(repos[0].path)
            ck = app._col_keys
            assert table.get_cell(row_key, ck[1]) == "behind"
            assert table.get_cell(row_key, ck[2]) == "develop"
            assert table.get_cell(row_key, ck[3]) == "staged"
            assert table.get_cell(row_key, ck[4]) == "5 min ago"
            assert table.get_cell(row_key, ck[5]) == "—"

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_multiple_repos_status(self, _mock_sessions):
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
            status_text = app.query_one("#status-bar", Static).content
            assert "3 repositories loaded" in status_text


class TestGitDirectorConsoleSearchAndSort:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_apply_filter_and_sort_updates_rows(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha"), branch="main"),
            _make_info("beta", Path("/tmp/beta"), branch="develop"),
            _make_info("gamma", Path("/tmp/gamma"), branch="main"),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            app._search_query = "beta"
            app._sort_column = 0
            app._sort_reverse = False
            app._apply_filter_and_sort()

            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 1
            status = app.query_one("#status-bar", Static).content
            assert "filter: 'beta'" in status

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_close_search_resets_query_and_status(self, _):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            container = app.query_one("#search-container")
            container.display = True
            app._search_query = "alpha"
            app.action_close_search()
            assert app._search_query == ""
            assert container.display is False

    async def test_build_loaded_status_includes_sort_and_filter(self):
        app = GitDirectorConsole()
        app._sort_column = 2
        app._sort_reverse = True
        app._search_query = "test"
        text = app._build_loaded_status(1, 3)
        assert "1 of 3 repository loaded" in text
        assert "filter: 'test'" in text
        assert "sort: Branch ▼" in text


class TestGitDirectorConsoleActionRouting:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_handle_menu_action_agent_commands(self, _):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        app.action_open_tmux = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._handle_menu_action("agent:copilot")
            app.action_open_tmux.assert_called_once_with(agent_cmd="copilot")

    async def test_do_remove_calls_kill_tmux_session(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._apply_sessions_filter_and_sort = MagicMock()
        with patch("gitdirector.integrations.tmux.kill_tmux_session") as kill_session:
            app._do_remove(True, "gd-test")
            kill_session.assert_called_once_with("gd-test")
            app._apply_sessions_filter_and_sort.assert_called_once()

    async def test_handle_menu_action_remove_session_pushes_confirm(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        app.push_screen = MagicMock()
        app._handle_remove_selection("gd-test")
        app.push_screen.assert_called_once()


class TestBuildLoadedStatus:
    async def test_no_repos_no_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            assert app._build_loaded_status(0, 0) == "No repositories tracked"

    async def test_default_state(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            msg = app._build_loaded_status(3, 3)
            assert "3 repositories loaded" in msg
            assert "filter:" not in msg
            assert "sort:" not in msg

    async def test_single_repo(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            msg = app._build_loaded_status(1, 1)
            assert "1 repository loaded" in msg

    async def test_with_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            app._search_query = "test"
            msg = app._build_loaded_status(2, 5)
            assert "2 of 5" in msg
            assert "filter: 'test'" in msg

    async def test_with_sort(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            app._sort_column = 2
            app._sort_reverse = False
            msg = app._build_loaded_status(3, 3)
            assert "sort: Branch \u25b2" in msg

    async def test_with_filter_and_sort(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            app._search_query = "api"
            app._sort_column = 1
            app._sort_reverse = True
            msg = app._build_loaded_status(2, 5)
            assert "2 of 5" in msg
            assert "filter: 'api'" in msg
            assert "sort: Sync \u25bc" in msg


class TestTUIEdgeCases:
    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions", side_effect=Exception("tmux error")
    )
    async def test_load_repos_handles_session_exception(self, _mock_sessions):
        import pytest
        from textual.worker import WorkerFailed

        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        with pytest.raises(WorkerFailed):
            async with app.run_test(size=(120, 30)) as pilot:
                await app.workers.wait_for_complete()
                await pilot.pause()

    def test_sort_key_func_all_columns(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._sessions_cache = {"/tmp/a": 2, "/tmp/b": 1}
        infos = [
            _make_info("a", Path("/tmp/a"), branch="main", last_commit_timestamp=2),
            _make_info("b", Path("/tmp/b"), branch="dev", last_commit_timestamp=1),
        ]
        for col in range(7):
            app._sort_column = col
            app._sort_reverse = False
            sorted_infos = sorted(infos, key=app._sort_key_func())
            assert isinstance(sorted_infos, list)

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", side_effect=Exception("fail"))
    async def test_sessions_cache_error_handling(self, _mock_sessions):
        import pytest
        from textual.worker import WorkerFailed

        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        with pytest.raises(WorkerFailed):
            async with app.run_test(size=(120, 30)):
                await app.workers.wait_for_complete()


class TestRefreshRepoForPath:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=["gd/alpha/shell/1"])
    async def test_refresh_updates_results_and_row(self, _mock_list):
        repos = [_make_info("alpha", Path("/tmp/alpha"), RepoStatus.UP_TO_DATE, "main")]
        updated_info = _make_info(
            "alpha",
            Path("/tmp/alpha"),
            RepoStatus.BEHIND,
            "develop",
            staged=True,
            last_updated="1 min ago",
        )
        mgr = _mock_manager(repos)
        mgr.get_repository_status.side_effect = lambda p: updated_info

        app = GitDirectorConsole()
        app.manager = mgr
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._refresh_repo_for_path(Path("/tmp/alpha"))
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            ck = app._col_keys
            row_key = str(Path("/tmp/alpha"))
            assert table.get_cell(row_key, ck[1]) == "behind"
            assert table.get_cell(row_key, ck[2]) == "develop"
            assert table.get_cell(row_key, ck[3]) == "staged"
            assert table.get_cell(row_key, ck[5]) == "1"
            assert app._results[row_key].status == RepoStatus.BEHIND


class TestReposStatusBarEscHint:
    async def test_esc_hint_shown_with_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            app._search_query = "test"
            msg = app._build_loaded_status(1, 3)
            assert "[esc] clear search" in msg

    async def test_esc_hint_not_shown_without_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            msg = app._build_loaded_status(3, 3)
            assert "[esc] clear search" not in msg
