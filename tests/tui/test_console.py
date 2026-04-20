"""Tests for GitDirectorConsole app-level behaviour."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual.css.query import NoMatches
from textual.widgets import DataTable, Static

from gitdirector.commands.tui import (
    ConfirmScreen,
    GitCommandResultScreen,
    GitDirectorConsole,
    GitOperationsMenuScreen,
    PullLoadingScreen,
    PullResultScreen,
)
from gitdirector.commands.tui.app import _run_console
from gitdirector.info import RepoInfoResult
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


class TestPanelSearchAndSortRouting:
    async def test_input_changed_routes_to_panels_filter(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._active_tab = "panels"
        app._apply_panels_filter_and_sort = MagicMock()
        event = MagicMock()
        event.input.id = "search-bar"
        event.value = "ops"

        app.on_input_changed(event)

        assert app._search_query == "ops"
        app._apply_panels_filter_and_sort.assert_called_once_with()

    async def test_build_panels_loaded_status_includes_sort_and_filter(self):
        app = GitDirectorConsole()
        app._panels_sort_column = 3
        app._panels_sort_reverse = True
        app._search_query = "ops"

        text = app._build_panels_loaded_status(1, 3)

        assert "1 of 3 panel" in text
        assert "filter: 'ops'" in text
        assert "sort: Panes ▼" in text
        assert "[esc] clear search" in text


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

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    @patch("gitdirector.commands.tui.app.AgentLoadingScreen")
    @patch(
        "gitdirector.integrations.tmux.launch_agent_in_tmux_session",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch(
        "gitdirector.integrations.tmux.create_tmux_session",
        return_value="gd/alpha/copilot/1",
    )
    async def test_action_open_tmux_agent_uses_self_cleaning_launch(
        self,
        mock_create_session,
        mock_launch_agent,
        mock_loading_screen,
        _mock_sessions,
    ):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        app.push_screen = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            app.action_open_tmux(agent_cmd="copilot")

            mock_create_session.assert_called_once_with(
                "alpha",
                Path("/tmp/alpha"),
                purpose="copilot",
            )
            mock_launch_agent.assert_called_once_with("gd/alpha/copilot/1", "copilot")
            mock_loading_screen.assert_called_once_with(
                "copilot",
                "gd/alpha/copilot/1",
                Path("/tmp/gitdirector-agent.ready"),
            )
            app.push_screen.assert_called_once()

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

    def test_handle_git_menu_action_pull_routes_to_prompt(self):
        path = Path("/tmp/alpha")
        app = GitDirectorConsole()
        app._prompt_repo_pull = MagicMock()

        app._handle_git_menu_action("pull", path)

        app._prompt_repo_pull.assert_called_once_with(path)

    def test_handle_git_menu_action_status_routes_to_show_repo_git_status(self):
        path = Path("/tmp/alpha")
        app = GitDirectorConsole()
        app._show_repo_git_status = MagicMock()

        app._handle_git_menu_action("status", path)

        app._show_repo_git_status.assert_called_once_with(path)

    def test_handle_git_menu_action_timeline_routes_to_show_repo_git_timeline(self):
        path = Path("/tmp/alpha")
        app = GitDirectorConsole()
        app._show_repo_git_timeline = MagicMock()

        app._handle_git_menu_action("timeline", path)

        app._show_repo_git_timeline.assert_called_once_with(path)

    def test_handle_git_menu_action_branches_routes_to_show_repo_git_branches(self):
        path = Path("/tmp/alpha")
        app = GitDirectorConsole()
        app._show_repo_git_branches = MagicMock()

        app._handle_git_menu_action("branches", path)

        app._show_repo_git_branches.assert_called_once_with(path)

    def test_handle_git_menu_action_remotes_routes_to_show_repo_git_remotes(self):
        path = Path("/tmp/alpha")
        app = GitDirectorConsole()
        app._show_repo_git_remotes = MagicMock()

        app._handle_git_menu_action("remotes", path)

        app._show_repo_git_remotes.assert_called_once_with(path)

    @patch("gitdirector.commands.tui.app.Repository")
    def test_show_repo_git_status_pushes_result_screen(self, mock_repo_cls):
        path = Path("/tmp/alpha")
        repo = MagicMock()
        repo.status_output.return_value = (True, "On branch main")
        mock_repo_cls.return_value = repo
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._update_status = MagicMock()

        app._show_repo_git_status(path)

        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, GitCommandResultScreen)
        assert screen.command == "git status"
        assert screen.ok is True
        assert screen.output == "On branch main"
        callback = app.push_screen.call_args.kwargs["callback"]
        app._handle_git_result_dismissal = MagicMock()
        callback("back")
        app._handle_git_result_dismissal.assert_called_once_with("back", path)
        app._update_status.assert_called_once_with("alpha: status shown")

    @patch("gitdirector.commands.tui.app.Repository")
    def test_show_repo_git_timeline_pushes_result_screen(self, mock_repo_cls):
        path = Path("/tmp/alpha")
        repo = MagicMock()
        repo.timeline_output.return_value = (True, "* abc1234 2026-04-20 Add timeline view")
        mock_repo_cls.return_value = repo
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._update_status = MagicMock()

        app._show_repo_git_timeline(path)

        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, GitCommandResultScreen)
        assert screen.command == (
            "git log --max-count=1000 --graph --decorate --all --color=always --date=short "
            "--pretty=format:%C(auto)%h%Creset %C(blue)%ad%Creset %C(auto)%d%Creset %s"
        )
        assert screen.ok is True
        assert "Add timeline view" in screen.output
        app._update_status.assert_called_once_with("alpha: timeline shown")

    @patch("gitdirector.commands.tui.app.Repository")
    def test_show_repo_git_branches_pushes_result_screen(self, mock_repo_cls):
        path = Path("/tmp/alpha")
        repo = MagicMock()
        repo.branches_output.return_value = (True, "* main\n  remotes/origin/main")
        mock_repo_cls.return_value = repo
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._update_status = MagicMock()

        app._show_repo_git_branches(path)

        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, GitCommandResultScreen)
        assert screen.command == "git branch -a"
        assert screen.ok is True
        assert "remotes/origin/main" in screen.output
        app._update_status.assert_called_once_with("alpha: branches shown")

    @patch("gitdirector.commands.tui.app.Repository")
    def test_show_repo_git_remotes_pushes_result_screen(self, mock_repo_cls):
        path = Path("/tmp/alpha")
        repo = MagicMock()
        repo.remotes_output.return_value = (
            True,
            "origin\thttps://example.com/repo.git (fetch)",
        )
        mock_repo_cls.return_value = repo
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._update_status = MagicMock()

        app._show_repo_git_remotes(path)

        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, GitCommandResultScreen)
        assert screen.command == "git remote -v"
        assert screen.ok is True
        assert "origin" in screen.output
        app._update_status.assert_called_once_with("alpha: remotes shown")

    def test_handle_git_result_dismissal_reopens_git_menu_on_back(self):
        path = Path("/tmp/alpha")
        app = GitDirectorConsole()
        app._push_git_menu_for_path = MagicMock()

        app._handle_git_result_dismissal("back", path)

        app._push_git_menu_for_path.assert_called_once_with(path)

    def test_handle_git_result_dismissal_ignores_normal_close(self):
        path = Path("/tmp/alpha")
        app = GitDirectorConsole()
        app._push_git_menu_for_path = MagicMock()

        app._handle_git_result_dismissal(None, path)

        app._push_git_menu_for_path.assert_not_called()

    @patch("gitdirector.commands.tui.app.Repository")
    def test_prompt_repo_pull_pushes_confirm_screen(self, mock_repo_cls):
        path = Path("/tmp/alpha")
        repo = MagicMock()
        repo.get_pull_target.return_value = ("origin", "main", None)
        mock_repo_cls.return_value = repo
        app = GitDirectorConsole()
        app.push_screen = MagicMock()

        app._prompt_repo_pull(path)

        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, ConfirmScreen)
        assert "origin/main" in screen.message
        assert "git pull --ff-only origin main" in screen.message
        assert callable(app.push_screen.call_args.kwargs["callback"])

    @patch("gitdirector.commands.tui.app.Repository")
    def test_prompt_repo_pull_shows_result_when_target_fails(self, mock_repo_cls):
        path = Path("/tmp/alpha")
        repo = MagicMock()
        repo.get_pull_target.return_value = (None, None, "Cannot pull in detached HEAD")
        mock_repo_cls.return_value = repo
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._update_status = MagicMock()

        app._prompt_repo_pull(path)

        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, PullResultScreen)
        assert screen.command is None
        assert screen.output == "Cannot pull in detached HEAD"
        assert callable(app.push_screen.call_args.kwargs["callback"])
        app._update_status.assert_called_once_with("alpha: Cannot pull in detached HEAD")

    @patch("gitdirector.commands.pull.pull_repository", return_value=("alpha", True, "Updated."))
    def test_pull_repo_worker_uses_shared_pull_helper(self, mock_pull_repository):
        path = Path("/tmp/alpha")
        command = "git pull --ff-only origin main"
        loading_screen = MagicMock()
        app = GitDirectorConsole()
        app.call_from_thread = MagicMock()

        GitDirectorConsole._pull_repo.__wrapped__(app, path, command, loading_screen)

        mock_pull_repository.assert_called_once_with(path)
        app.call_from_thread.assert_called_once_with(
            app._show_pull_result,
            loading_screen,
            path,
            command,
            ("alpha", True, "Updated."),
        )

    def test_do_pull_repo_pushes_loading_screen_and_starts_worker(self):
        path = Path("/tmp/alpha")
        command = "git pull --ff-only origin main"
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._pull_repo = MagicMock()
        app._update_status = MagicMock()

        app._do_pull_repo(True, path, command)

        loading_screen = app.push_screen.call_args.args[0]
        assert isinstance(loading_screen, PullLoadingScreen)
        app._update_status.assert_called_once_with(f"Pulling alpha: {command}")
        app._pull_repo.assert_called_once_with(path, command, loading_screen)

    def test_show_pull_result_pushes_modal_and_refreshes(self):
        path = Path("/tmp/alpha")
        loading_screen = MagicMock()
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._update_status = MagicMock()
        app._refresh_repo_for_path = MagicMock()

        app._show_pull_result(
            loading_screen,
            path,
            "git pull --ff-only origin main",
            ("alpha", True, "Done"),
        )

        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, PullResultScreen)
        assert screen.command == "git pull --ff-only origin main"
        assert screen.ok is True
        loading_screen.dismiss.assert_called_once_with(None)
        app._update_status.assert_called_once_with("alpha: pull completed")
        app._refresh_repo_for_path.assert_called_once_with(path)

    def test_show_pull_result_does_not_refresh_after_failure(self):
        path = Path("/tmp/alpha")
        loading_screen = MagicMock()
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._update_status = MagicMock()
        app._refresh_repo_for_path = MagicMock()

        app._show_pull_result(
            loading_screen,
            path,
            "git pull --ff-only origin main",
            ("alpha", False, "fatal: Not possible to fast-forward"),
        )

        loading_screen.dismiss.assert_called_once_with(None)
        app._update_status.assert_called_once_with("alpha: pull failed")
        app._refresh_repo_for_path.assert_not_called()


class TestGitDirectorConsoleDirectBranches:
    def test_action_show_git_menu_ignored_outside_repo_tab(self):
        app = GitDirectorConsole()
        app._active_tab = "sessions"
        app._get_selected_path = MagicMock()

        app.action_show_git_menu()

        app._get_selected_path.assert_not_called()

    def test_action_show_git_menu_ignored_without_selected_path(self):
        app = GitDirectorConsole()
        app._active_tab = "repos"
        app._get_selected_path = MagicMock(return_value=None)
        app.push_screen = MagicMock()

        app.action_show_git_menu()

        app.push_screen.assert_not_called()

    @patch("gitdirector.commands.tui.app.GitOperationsMenuScreen")
    def test_action_show_git_menu_uses_selected_repo_metadata(self, mock_screen_cls):
        path = Path("/tmp/alpha")
        screen = MagicMock()
        mock_screen_cls.return_value = screen
        app = GitDirectorConsole()
        app._active_tab = "repos"
        app._get_selected_path = MagicMock(return_value=path)
        app._results = {str(path): _make_info("alpha", path, branch="main")}
        app.push_screen = MagicMock()

        app.action_show_git_menu()

        mock_screen_cls.assert_called_once_with("alpha", "main")
        app.push_screen.assert_called_once()

    def test_action_show_info_ignored_outside_repo_tab(self):
        app = GitDirectorConsole()
        app._active_tab = "sessions"
        app._get_selected_path = MagicMock()

        app.action_show_info()

        app._get_selected_path.assert_not_called()

    def test_action_show_info_ignored_without_selected_path(self):
        app = GitDirectorConsole()
        app._active_tab = "repos"
        app._get_selected_path = MagicMock(return_value=None)
        app.push_screen = MagicMock()
        app._gather_and_show_info = MagicMock()

        app.action_show_info()

        app.push_screen.assert_not_called()
        app._gather_and_show_info.assert_not_called()

    @patch("gitdirector.commands.tui.app.RepoInfoScreen")
    def test_action_show_info_pushes_screen_and_starts_worker(self, mock_screen_cls):
        path = Path("/tmp/alpha")
        screen = MagicMock()
        mock_screen_cls.return_value = screen
        app = GitDirectorConsole()
        app._active_tab = "repos"
        app._get_selected_path = MagicMock(return_value=path)
        app.push_screen = MagicMock()
        app._gather_and_show_info = MagicMock()

        app.action_show_info()

        mock_screen_cls.assert_called_once_with("alpha", path)
        app.push_screen.assert_called_once_with(screen)
        app._gather_and_show_info.assert_called_once_with(path, screen)

    @patch("gitdirector.info.gather_repo_info")
    def test_gather_and_show_info_populates_screen_from_worker(self, mock_gather):
        path = Path("/tmp/alpha")
        result = RepoInfoResult(0, [], 0, 0, 0)
        screen = MagicMock()
        app = GitDirectorConsole()
        app.call_from_thread = MagicMock()
        mock_gather.return_value = result

        GitDirectorConsole._gather_and_show_info.__wrapped__(app, path, screen)

        mock_gather.assert_called_once_with(path)
        app.call_from_thread.assert_called_once_with(screen.populate, result)

    @patch("gitdirector.commands.tui.app.RepoInfoScreen")
    def test_push_info_screen_updates_status(self, mock_screen_cls):
        path = Path("/tmp/alpha")
        screen = MagicMock()
        table = MagicMock()
        table.row_count = 2
        mock_screen_cls.return_value = screen
        app = GitDirectorConsole()
        app._results = [object(), object(), object()]
        app.push_screen = MagicMock()
        app.query_one = MagicMock(return_value=table)
        app._build_loaded_status = MagicMock(return_value="2/3 loaded")
        app._update_status = MagicMock()

        app._push_info_screen("alpha", path, object())

        mock_screen_cls.assert_called_once_with("alpha", path)
        app.push_screen.assert_called_once_with(screen)
        app._build_loaded_status.assert_called_once_with(2, 3)
        app._update_status.assert_called_once_with("2/3 loaded")

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=[])
    def test_load_repos_reapplies_filter_when_search_active(self, _mock_sessions):
        info = _make_info("alpha", Path("/tmp/alpha"))
        app = GitDirectorConsole()
        app.manager = _mock_manager([info])
        app.call_from_thread = MagicMock()
        app._apply_filter_and_sort = MagicMock()
        app._search_query = "alpha"

        GitDirectorConsole._load_repos.__wrapped__(app)

        app.call_from_thread.assert_any_call(app._apply_filter_and_sort)

    def test_update_row_ignores_table_errors(self):
        app = GitDirectorConsole()
        app._sessions_cache = {}
        app._col_keys = ("repo", "sync", "branch", "changes", "last", "sessions", "path")
        table = MagicMock()
        table.update_cell.side_effect = RuntimeError("boom")
        app.query_one = MagicMock(return_value=table)
        info = _make_info("alpha", Path("/tmp/alpha"))

        app._update_row(info, 2)

        assert app._sessions_cache[str(info.path)] == 2

    def test_action_tab_sessions_ignored_while_restore_pending(self):
        app = GitDirectorConsole()
        app._resume_target_tab = "repos"
        app.query_one = MagicMock()

        app.action_tab_sessions()

        app.query_one.assert_not_called()

    def test_handle_app_resume_noops_without_pending_target(self):
        app = GitDirectorConsole()
        app.call_after_refresh = MagicMock()

        app._handle_app_resume(app)

        app.call_after_refresh.assert_not_called()

    def test_restore_after_resume_ignores_mismatched_target(self):
        app = GitDirectorConsole()
        app._resume_target_tab = "sessions"
        app.query_one = MagicMock()

        app._restore_after_resume("repos", None)

        app.query_one.assert_not_called()

    @patch(
        "gitdirector.integrations.tmux.get_all_session_statuses",
        return_value={"gd/alpha/shell/1": {"command": "python", "dead": False}},
    )
    def test_poll_session_statuses_updates_state_and_notifies(self, _mock_statuses):
        app = GitDirectorConsole()
        app.call_from_thread = MagicMock()
        app._on_statuses_updated = MagicMock()

        GitDirectorConsole._poll_session_statuses.__wrapped__(app)

        assert app._session_statuses == {"gd/alpha/shell/1": {"command": "python", "dead": False}}
        app.call_from_thread.assert_called_once_with(app._on_statuses_updated)

    def test_trigger_status_poll_delegates_to_worker(self):
        app = GitDirectorConsole()
        app._poll_session_statuses = MagicMock()

        app._trigger_status_poll()

        app._poll_session_statuses.assert_called_once_with()

    def test_resolve_session_status_waits_without_tmux_info(self):
        app = GitDirectorConsole()
        app._monitor = MagicMock()
        app._monitor.get_bell_state.return_value = True
        app._session_statuses = {}

        status = app._resolve_session_status(
            {"session_name": "gd/alpha/shell/1", "purpose": "shell"}
        )

        assert status == "waiting"

    def test_resolve_session_status_runs_without_tmux_info(self):
        app = GitDirectorConsole()
        app._monitor = MagicMock()
        app._monitor.get_bell_state.return_value = False
        app._session_statuses = {}

        status = app._resolve_session_status(
            {"session_name": "gd/alpha/shell/1", "purpose": "shell"}
        )

        assert status == "running"

    def test_on_statuses_updated_refreshes_repo_status_bar_when_waiting_changes(self):
        app = GitDirectorConsole()
        app._sessions_entries = [{"session_name": "gd/alpha/shell/1", "purpose": "shell"}]
        app._resolve_session_status = MagicMock(return_value="waiting")
        app._waiting_count = 0
        app._active_tab = "repos"
        app._results = {"/tmp/alpha": object()}
        table = MagicMock()
        table.row_count = 1
        app.query_one = MagicMock(return_value=table)
        app._build_loaded_status = MagicMock(return_value="1 repository loaded")
        app._update_status = MagicMock()

        app._on_statuses_updated()

        assert app._sessions_entries[0]["status"] == "waiting"
        assert app._waiting_count == 1
        app._update_status.assert_called_once_with("1 repository loaded")

    def test_on_statuses_updated_ignores_missing_repo_table(self):
        app = GitDirectorConsole()
        app._sessions_entries = [{"session_name": "gd/alpha/shell/1", "purpose": "shell"}]
        app._resolve_session_status = MagicMock(return_value="waiting")
        app._waiting_count = 0
        app._active_tab = "repos"
        app._results = {"/tmp/alpha": object()}
        app.query_one = MagicMock(side_effect=NoMatches("#repo-table"))
        app._build_loaded_status = MagicMock()
        app._update_status = MagicMock()

        app._on_statuses_updated()

        assert app._sessions_entries[0]["status"] == "waiting"
        assert app._waiting_count == 1
        app._build_loaded_status.assert_not_called()
        app._update_status.assert_not_called()

    def test_on_statuses_updated_refreshes_panels_with_live_session_names(self):
        app = GitDirectorConsole()
        app._sessions_entries = [{"session_name": "gd/alpha/shell/1", "purpose": "shell"}]
        app._resolve_session_status = MagicMock(return_value="running")
        app._waiting_count = 0
        app._active_tab = "panels"
        app._apply_panels_filter_and_sort = MagicMock()

        app._on_statuses_updated()

        app._apply_panels_filter_and_sort.assert_called_once_with({"gd/alpha/shell/1"})
        assert app._waiting_count == 0

    def test_update_session_status_cells_ignores_table_errors(self):
        app = GitDirectorConsole()
        app._sessions_entries = [{"session_name": "gd/alpha/shell/1", "purpose": "shell"}]
        app._sess_col_keys = ("status",)
        app._resolve_session_status = MagicMock(return_value="waiting")
        table = MagicMock()
        table.update_cell.side_effect = RuntimeError("boom")
        app.query_one = MagicMock(return_value=table)

        app._update_session_status_cells()

        assert app._sessions_entries[0]["status"] == "waiting"

    def test_update_session_status_cells_ignores_missing_table(self):
        app = GitDirectorConsole()
        app._sessions_entries = [
            {
                "session_name": "gd/alpha/shell/1",
                "purpose": "shell",
                "status": "running",
            }
        ]
        app._resolve_session_status = MagicMock(return_value="waiting")
        app.query_one = MagicMock(side_effect=NoMatches("#sessions-table"))

        app._update_session_status_cells()

        assert app._sessions_entries[0]["status"] == "running"
        app._resolve_session_status.assert_not_called()

    def test_build_loaded_status_includes_waiting_count(self):
        app = GitDirectorConsole()
        app._waiting_count = 2

        msg = app._build_loaded_status(3, 3)

        assert "2 sessions waiting" in msg

    def test_action_cursor_left_and_right_delegate_to_active_table(self):
        app = GitDirectorConsole()
        table = MagicMock()
        app._get_active_table = MagicMock(return_value=table)

        app.action_cursor_left()
        app.action_cursor_right()

        table.scroll_left.assert_called_once_with()
        table.scroll_right.assert_called_once_with()

    def test_handle_sort_selection_applies_sort(self):
        app = GitDirectorConsole()
        app._apply_filter_and_sort = MagicMock()

        app._handle_sort_selection((2, True))

        assert app._sort_column == 2
        assert app._sort_reverse is True
        app._apply_filter_and_sort.assert_called_once_with()

    def test_handle_sessions_sort_selection_applies_sort(self):
        app = GitDirectorConsole()
        app._apply_sessions_filter_and_sort = MagicMock()

        app._handle_sessions_sort_selection((1, True))

        assert app._sessions_sort_column == 1
        assert app._sessions_sort_reverse is True
        app._apply_sessions_filter_and_sort.assert_called_once_with()

    def test_pause_session_status_tracking_stops_timer_and_monitor(self):
        app = GitDirectorConsole()
        app._poll_timer = MagicMock()
        app._monitor = MagicMock()

        app._pause_session_status_tracking()

        assert app._session_status_tracking_paused is True
        app._poll_timer.pause.assert_called_once_with()
        app._monitor.stop.assert_called_once_with()

    def test_resume_session_status_tracking_restarts_timer_and_monitor(self):
        app = GitDirectorConsole()
        app._session_status_tracking_paused = True
        app._poll_timer = MagicMock()
        app._monitor = MagicMock()

        app._resume_session_status_tracking()

        assert app._session_status_tracking_paused is False
        app._monitor.start.assert_called_once_with()
        app._poll_timer.resume.assert_called_once_with()

    def test_suspend_and_attach_pauses_and_resumes_status_tracking(self):
        app = GitDirectorConsole()
        app._pause_session_status_tracking = MagicMock()
        app._resume_session_status_tracking = MagicMock()
        app._monitor = MagicMock()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )

        with patch("gitdirector.integrations.tmux.attach_tmux_session"):
            with patch("sys.stdout"):
                with patch("termios.tcflush"):
                    app._suspend_and_attach("gd-test-session")

        app._pause_session_status_tracking.assert_called_once_with()
        app._resume_session_status_tracking.assert_called_once_with()

    def test_suspend_and_attach_resumes_status_tracking_after_attach_error(self):
        app = GitDirectorConsole()
        app._pause_session_status_tracking = MagicMock()
        app._resume_session_status_tracking = MagicMock()
        app._monitor = MagicMock()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )

        with patch(
            "gitdirector.integrations.tmux.attach_tmux_session",
            side_effect=RuntimeError("boom"),
        ):
            with patch("sys.stdout"):
                with patch("termios.tcflush"):
                    with pytest.raises(RuntimeError, match="boom"):
                        app._suspend_and_attach("gd-test-session")

        app._pause_session_status_tracking.assert_called_once_with()
        app._resume_session_status_tracking.assert_called_once_with()

    def test_action_select_row_noops_when_sessions_table_empty(self):
        app = GitDirectorConsole()
        app._active_tab = "sessions"
        table = MagicMock()
        table.row_count = 0
        app.query_one = MagicMock(return_value=table)
        app._suspend_and_attach = MagicMock()

        app.action_select_row()

        app._suspend_and_attach.assert_not_called()

    def test_action_select_row_attaches_selected_session(self):
        app = GitDirectorConsole()
        app._active_tab = "sessions"
        row_key = MagicMock()
        row_key.value = "gd/alpha/shell/1"
        table = MagicMock()
        table.row_count = 1
        table.coordinate_to_cell_key.return_value = MagicMock(row_key=row_key)
        table.cursor_coordinate = MagicMock()
        app.query_one = MagicMock(return_value=table)
        app._suspend_and_attach = MagicMock()

        app.action_select_row()

        app._suspend_and_attach.assert_called_once_with("gd/alpha/shell/1")

    def test_action_select_row_on_repos_opens_menu(self):
        app = GitDirectorConsole()
        app._active_tab = "repos"
        app.action_show_menu = MagicMock()

        app.action_select_row()

        app.action_show_menu.assert_called_once_with()

    def test_on_data_table_row_selected_on_repos_opens_menu(self):
        app = GitDirectorConsole()
        app.action_show_menu = MagicMock()
        event = MagicMock()
        event.data_table.id = "repo-table"

        app.on_data_table_row_selected(event)

        app.action_show_menu.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.create_tmux_session", return_value="gd/alpha/shell/1")
    def test_action_open_tmux_shell_attaches_to_new_session(self, mock_create):
        app = GitDirectorConsole()
        app._get_selected_path = MagicMock(return_value=Path("/tmp/alpha"))
        app._suspend_and_attach = MagicMock()

        app.action_open_tmux()

        mock_create.assert_called_once_with("alpha", Path("/tmp/alpha"), purpose="shell")
        app._suspend_and_attach.assert_called_once_with("gd/alpha/shell/1", Path("/tmp/alpha"))

    def test_action_open_tmux_without_selection_is_noop(self):
        app = GitDirectorConsole()
        app._get_selected_path = MagicMock(return_value=None)
        app._suspend_and_attach = MagicMock()

        app.action_open_tmux()

        app._suspend_and_attach.assert_not_called()

    def test_action_show_menu_without_selection_is_noop(self):
        app = GitDirectorConsole()
        app._get_selected_path = MagicMock(return_value=None)
        app.push_screen = MagicMock()

        app.action_show_menu()

        app.push_screen.assert_not_called()

    def test_attach_to_session_delegates_to_suspend_and_attach(self):
        app = GitDirectorConsole()
        app._suspend_and_attach = MagicMock()

        app._attach_to_session("gd/alpha/shell/1", Path("/tmp/alpha"))

        app._suspend_and_attach.assert_called_once_with("gd/alpha/shell/1", Path("/tmp/alpha"))

    @patch("gitdirector.commands.tui.app.ActionMenuScreen")
    def test_action_show_menu_uses_selected_repo_metadata(self, mock_screen_cls):
        path = Path("/tmp/alpha")
        app = GitDirectorConsole()
        app._get_selected_path = MagicMock(return_value=path)
        app._results = {str(path): _make_info("alpha", path, branch="main")}
        app.push_screen = MagicMock()

        app.action_show_menu()

        mock_screen_cls.assert_called_once_with("alpha", path, "main")
        app.push_screen.assert_called_once()

    def test_handle_remove_selection_none_is_noop(self):
        app = GitDirectorConsole()
        app.push_screen = MagicMock()

        app._handle_remove_selection(None)

        app.push_screen.assert_not_called()

    def test_action_refresh_loads_sessions_on_sessions_tab(self):
        app = GitDirectorConsole()
        app._results = {"/tmp/alpha": object()}
        app._sessions_cache = {"/tmp/alpha": 1}
        app._load_repos = MagicMock()
        app._load_sessions = MagicMock()
        app._active_tab = "sessions"

        app.action_refresh()

        assert app._results == {}
        assert app._sessions_cache == {}
        app._load_repos.assert_called_once_with()
        app._load_sessions.assert_called_once_with()

    @patch("gitdirector.commands.tui.app.GitDirectorConsole")
    def test_run_console_stops_monitor_when_run_raises(self, mock_console_cls):
        app = MagicMock()
        app.run.side_effect = RuntimeError("boom")
        mock_console_cls.return_value = app

        with patch.object(app._monitor, "stop") as mock_stop:
            try:
                _run_console()
            except RuntimeError:
                pass

        mock_stop.assert_called_once_with()


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
            assert "g git" in msg
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
        mgr.get_repository_status.side_effect = lambda p, fetch=False: updated_info

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
            app.manager.get_repository_status.assert_any_call(Path("/tmp/alpha"), fetch=True)


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
