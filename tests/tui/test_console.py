"""Tests for GitDirectorConsole app-level behaviour."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

from textual.css.query import NoMatches
from textual.widgets import DataTable, Static

from gitdirector.commands.tui import (
    AgentLoadingScreen,
    ConfirmScreen,
    GitCommandResultScreen,
    GitDirectorConsole,
    PullLoadingScreen,
    PullResultScreen,
)
from gitdirector.commands.tui.app import RefreshFooter, _run_console
from gitdirector.commands.tui.app_groups import RepoGroup
from gitdirector.commands.tui.app_repos import _REPO_LOADING_CELL_VALUE
from gitdirector.commands.tui.constants import _REPO_CACHE_TTL_SECS
from gitdirector.info import FileTypeInfo, RepoInfoResult
from gitdirector.integrations.tmux.core import _repo_session_name_segment
from gitdirector.repo import Repository, RepoStatus
from gitdirector.storage import load_yaml_mapping

from .conftest import _make_info, _mock_manager


class TestGitDirectorConsole:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_startup_uses_fresh_repository_cache(self, _mock_sessions):
        info = _make_info("alpha", Path("/tmp/alpha"), RepoStatus.UP_TO_DATE, "main")
        refreshed = _make_info("alpha", Path("/tmp/alpha"), RepoStatus.BEHIND, "main")
        cached_app = GitDirectorConsole()
        cached_app.manager = _mock_manager([info])
        cached_app._results = {str(info.path): info}
        cached_app._save_repos_cache()

        app = GitDirectorConsole()
        app.manager = _mock_manager([info])
        fetch_started = Event()
        release_fetch = Event()

        def delayed_status(*_args, **_kwargs):
            fetch_started.set()
            assert release_fetch.wait(timeout=1)
            return refreshed

        app.manager.get_repository_status.side_effect = delayed_status
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)

            assert table.row_count == 1
            assert fetch_started.is_set()
            assert table.get_cell(str(info.path), app._col_keys[1]) == "up to date"

            release_fetch.set()
            await app.workers.wait_for_complete()
            await pilot.pause()

        assert "behind" in str(table.get_cell(str(info.path), app._col_keys[1]))

    @patch("gitdirector.commands.tui.app_repos.time")
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_startup_refreshes_stale_repository_cache(self, _mock_sessions, mock_time):
        info = _make_info("alpha", Path("/tmp/alpha"), RepoStatus.UP_TO_DATE, "main")
        cached_app = GitDirectorConsole()
        cached_app.manager = _mock_manager([info])
        cached_app._results = {str(info.path): info}
        mock_time.return_value = 1_000
        cached_app._save_repos_cache()

        mock_time.return_value = 1_000 + _REPO_CACHE_TTL_SECS + 1
        app = GitDirectorConsole()
        app.manager = _mock_manager([info])
        fetch_started = Event()
        release_fetch = Event()

        def delayed_status(*_args, **_kwargs):
            fetch_started.set()
            assert release_fetch.wait(timeout=1)
            return info

        app.manager.get_repository_status.side_effect = delayed_status
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            footer = app.query_one(RefreshFooter)

            try:
                assert fetch_started.is_set()
                assert footer.refreshing is True
                assert len(footer.query(".-refresh-indicator")) == 1
                assert table.get_cell(str(info.path), app._col_keys[1]) == _REPO_LOADING_CELL_VALUE
                status = str(app.query_one("#status-bar", Static).content)
                assert not status.startswith(("Loading ", "Checking "))
            finally:
                release_fetch.set()

            await app.workers.wait_for_complete()
            await pilot.pause()

        app.manager.get_repository_status.assert_any_call(info.path, fetch=True)

    @patch("gitdirector.commands.tui.app_repos.time", return_value=1_000)
    def test_row_refresh_updates_cache_without_extending_ttl(self, _mock_time):
        original = _make_info("alpha", Path("/tmp/alpha"), RepoStatus.UP_TO_DATE, "main")
        updated = _make_info("alpha", Path("/tmp/alpha"), RepoStatus.BEHIND, "main")
        app = GitDirectorConsole()
        app.manager = _mock_manager([updated])
        app._repo_paths = [updated.path]
        app._results = {str(original.path): original}
        app._save_repos_cache()
        app.call_from_thread = MagicMock()

        GitDirectorConsole._refresh_repo_for_path.__wrapped__(app, updated.path)

        cached = load_yaml_mapping(app._repos_cache_file, description="repository cache")
        assert cached["updated_at"] == 1_000
        assert cached["repositories"][0]["status"] == RepoStatus.BEHIND.value

    def test_load_repos_from_cache_rejects_changed_config_token(self):
        info = _make_info("alpha", Path("/tmp/alpha"), RepoStatus.UP_TO_DATE, "main")
        app = GitDirectorConsole()
        app.manager = _mock_manager([info])
        app._results = {str(info.path): info}

        app._save_repos_cache()

        app._results = {}
        app.manager.config.repository_cache_token.return_value = {
            "config": [True, 999, 1],
            "secrets": [False, None, None],
        }

        assert app._load_repos_from_cache() is False

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
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            empty_message = app.query_one("#no-repos-message", Static)
            assert table.row_count == 0
            assert table.display is False
            assert empty_message.display is True

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
            assert table.row_count == 3
            assert app._visible_repo_count == 2

    async def test_quit_binding(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        app._monitor = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("q")
        app._monitor.stop.assert_called_once_with(wait=True)

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
    async def test_arrow_keys_navigate_repos_tab(self, _mock_sessions):
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
            assert app.focused != table
            await pilot.press("down")
            assert table.cursor_coordinate.row == 1
            await pilot.press("up")
            assert table.cursor_coordinate.row == 0

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {
                "session_name": "gd/alpha/shell/1",
                "repo": "alpha",
                "repo_slug": "alpha",
                "purpose": "shell",
                "description": "-",
            },
            {
                "session_name": "gd/beta/claude/1",
                "repo": "beta",
                "repo_slug": "beta",
                "purpose": "claude",
                "description": "-",
            },
            {
                "session_name": "gd/gamma/copilot/1",
                "repo": "gamma",
                "repo_slug": "gamma",
                "purpose": "copilot",
                "description": "-",
            },
        ],
    )
    async def test_arrow_keys_navigate_sessions_tab(self, _mock_sessions):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#sessions-table", DataTable)
            assert table.row_count == 3
            assert app.focused != table
            await pilot.press("down")
            assert table.cursor_coordinate.row == 1
            await pilot.press("up")
            assert table.cursor_coordinate.row == 0

    async def test_arrow_keys_navigate_panels_tab(self):
        from gitdirector.commands.tui import Panel

        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        app._panels_entries = [
            Panel(name="Alpha", rows=1, cols=1, panes={1: None}),
            Panel(name="Beta", rows=1, cols=1, panes={1: None}),
            Panel(name="Gamma", rows=1, cols=1, panes={1: None}),
        ]
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._active_tab = "panels"
            app._apply_panels_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#panels-table", DataTable)
            assert table.row_count == 3
            assert app.focused != table
            await pilot.press("down")
            assert table.cursor_coordinate.row == 1
            await pilot.press("up")
            assert table.cursor_coordinate.row == 0

    async def test_underscore_resets_horizontal_scroll_in_each_tab(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])

        async with app.run_test(size=(120, 30)) as pilot:
            for tab_id, selector in (
                ("repos", "#repo-table"),
                ("sessions", "#sessions-table"),
                ("panels", "#panels-table"),
            ):
                app._active_tab = tab_id
                table = app.query_one(selector, DataTable)
                with patch.object(table, "scroll_to") as scroll_to:
                    await pilot.press("_")
                    scroll_to.assert_called_once_with(x=0, animate=False)

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

    async def test_status_bar_appends_update_notice(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._set_update_notice("Update available: v1.5.0 (current v1.4.2)")
            app._update_status("No repositories linked")
            status_text = app.query_one("#status-bar", Static).content
            assert "No repositories linked" in status_text
            assert "Update available: v1.5.0 (current v1.4.2)" in status_text

    async def test_status_bar_treats_raw_errors_as_plain_text(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            message = "tmux attach failed: [/]"
            app._update_status(message)
            status_text = app.query_one("#status-bar", Static).content
            assert status_text.startswith(message)

    async def test_table_columns_created(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as _:
            table = app.query_one("#repo-table", DataTable)
            assert len(table.columns) == 6

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

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[{"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"}],
    )
    async def test_repo_load_does_not_query_session_counts(self, mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            row_key = str(repos[0].path)
            assert table.get_cell(row_key, app._col_keys[5]) == str(repos[0].path)
            mock_sessions.assert_not_called()

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
            assert table.get_cell(row_key, ck[1]) == "[bold yellow]behind[/bold yellow]"
            assert table.get_cell(row_key, ck[2]) == "develop"
            assert table.get_cell(row_key, ck[3]) == "[bold yellow]staged[/bold yellow]"
            assert table.get_cell(row_key, ck[4]) == "5 min ago"
            assert table.get_cell(row_key, ck[5]) == str(repos[0].path)

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
            assert table.row_count == 2
            assert app._visible_repo_count == 1
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
    async def test_handle_menu_action_claude_skip_permissions(self, _):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        app.action_open_tmux = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._handle_menu_action("agent:claude-skip-permissions")
            app.action_open_tmux.assert_called_once_with(
                agent_cmd="claude --dangerously-skip-permissions",
                purpose="claude-dangerously-skip-permissions",
            )

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    @patch(
        "gitdirector.integrations.tmux.launch_command_in_tmux_session",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch(
        "gitdirector.integrations.tmux.create_tmux_session",
        return_value="gd/alpha/copilot/1",
    )
    async def test_action_open_tmux_agent_uses_self_cleaning_launch(
        self,
        mock_create_session,
        mock_launch_command,
        _mock_sessions,
    ):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        app._suspend_and_attach = MagicMock()
        app.push_screen = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            app.action_open_tmux(agent_cmd="copilot")

            mock_create_session.assert_called_once_with(
                "alpha",
                Path("/tmp/alpha"),
                purpose="copilot",
                description=None,
            )
            mock_launch_command.assert_called_once_with("gd/alpha/copilot/1", "copilot")
            app.push_screen.assert_called_once()
            screen = app.push_screen.call_args.args[0]
            assert isinstance(screen, AgentLoadingScreen)
            assert screen._agent_cmd == "copilot"
            assert screen._ready_marker == Path("/tmp/gitdirector-agent.ready")

            screen._on_attach()
            app._suspend_and_attach.assert_called_once_with(
                "gd/alpha/copilot/1",
                Path("/tmp/alpha"),
                row_key=None,
                skip_config_sync=True,
            )

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

    def test_handle_git_menu_action_push_routes_to_prompt(self):
        path = Path("/tmp/alpha")
        app = GitDirectorConsole()
        app._prompt_repo_push = MagicMock()

        app._handle_git_menu_action("push", path)

        app._prompt_repo_push.assert_called_once_with(path)

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

    @patch("gitdirector.commands.tui.app.Repository")
    def test_prompt_repo_push_pushes_confirm_screen(self, mock_repo_cls):
        path = Path("/tmp/alpha")
        mock_repo_cls.return_value = MagicMock()
        app = GitDirectorConsole()
        app.push_screen = MagicMock()

        app._prompt_repo_push(path)

        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, ConfirmScreen)
        assert "Push 'alpha' to remote" in screen.message
        assert "git push" in screen.message
        assert callable(app.push_screen.call_args.kwargs["callback"])

    @patch("gitdirector.commands.tui.app.Repository")
    def test_push_repository_falls_back_to_set_upstream(self, mock_repo_cls):
        path = Path("/tmp/alpha")
        repo = MagicMock()
        repo.push.side_effect = [
            (False, "fatal: The current branch main has no upstream branch."),
            (True, "To origin"),
        ]
        repo.get_current_branch.return_value = "main"
        mock_repo_cls.return_value = repo
        app = GitDirectorConsole()

        result = app._push_repository(path, "git push")

        assert result == ("alpha", True, "To origin", "git push -u origin main")
        repo.push.assert_any_call()
        repo.push.assert_any_call(set_upstream=True)

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

    def test_do_push_repo_pushes_loading_screen_and_starts_worker(self):
        path = Path("/tmp/alpha")
        command = "git push"
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._push_repo = MagicMock()
        app._update_status = MagicMock()

        app._do_push_repo(True, path, command)

        loading_screen = app.push_screen.call_args.args[0]
        assert isinstance(loading_screen, PullLoadingScreen)
        assert loading_screen.verb == "Pushing"
        app._update_status.assert_called_once_with(f"Pushing alpha: {command}")
        app._push_repo.assert_called_once_with(path, command, loading_screen)

    def test_show_push_result_pushes_modal_and_refreshes(self):
        path = Path("/tmp/alpha")
        loading_screen = MagicMock()
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._update_status = MagicMock()
        app._refresh_repo_for_path = MagicMock()

        app._show_push_result(
            loading_screen,
            path,
            ("alpha", True, "To origin", "git push"),
        )

        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, PullResultScreen)
        assert screen.command == "git push"
        assert screen.ok is True
        assert screen.operation == "Push"
        loading_screen.dismiss.assert_called_once_with(None)
        app._update_status.assert_called_once_with("alpha: push completed")
        app._refresh_repo_for_path.assert_called_once_with(path)

    def test_show_push_result_does_not_refresh_after_failure(self):
        path = Path("/tmp/alpha")
        loading_screen = MagicMock()
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._update_status = MagicMock()
        app._refresh_repo_for_path = MagicMock()

        app._show_push_result(
            loading_screen,
            path,
            ("alpha", False, "fatal: rejected", "git push"),
        )

        loading_screen.dismiss.assert_called_once_with(None)
        app._update_status.assert_called_once_with("alpha: push failed")
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

    @patch("gitdirector.commands.tui.app.RepoInfoScreen")
    def test_action_show_info_for_group_starts_aggregate_worker(self, mock_screen_cls):
        group = RepoGroup(Path("/tmp/work"), (Path("/tmp/work/alpha"), Path("/tmp/work/beta")))
        screen = MagicMock()
        mock_screen_cls.return_value = screen
        app = GitDirectorConsole()
        app._active_tab = "repos"
        app._get_selected_group = MagicMock(return_value=group)
        app._get_selected_path = MagicMock()
        app.push_screen = MagicMock()
        app._gather_and_show_group_info = MagicMock()

        app.action_show_info()

        mock_screen_cls.assert_called_once_with("work (2 repos)", Path("/tmp/work"))
        app.push_screen.assert_called_once_with(screen)
        app._gather_and_show_group_info.assert_called_once_with(group, screen)
        app._get_selected_path.assert_not_called()

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

    @patch("gitdirector.info.gather_repo_info")
    def test_gather_and_show_group_info_aggregates_repository_results(self, mock_gather):
        group = RepoGroup(Path("/tmp/work"), (Path("/tmp/work/alpha"), Path("/tmp/work/beta")))
        alpha = RepoInfoResult(
            2,
            [FileTypeInfo(".py", 1, 10, 20), FileTypeInfo(".png", 1, None, None)],
            10,
            20,
            2,
        )
        beta = RepoInfoResult(
            3,
            [FileTypeInfo(".py", 2, 30, 60), FileTypeInfo(".md", 1, 5, 10)],
            35,
            70,
            4,
        )
        screen = MagicMock()
        app = GitDirectorConsole()
        app.call_from_thread = MagicMock()
        mock_gather.side_effect = [alpha, beta]

        GitDirectorConsole._gather_and_show_group_info.__wrapped__(app, group, screen)

        assert mock_gather.call_args_list[0].args == (Path("/tmp/work/alpha"),)
        assert mock_gather.call_args_list[1].args == (Path("/tmp/work/beta"),)
        result = app.call_from_thread.call_args.args[1]
        assert result.total_files == 5
        assert result.total_lines == 45
        assert result.total_tokens == 90
        assert result.max_depth == 4
        assert result.file_types == [
            FileTypeInfo(".py", 3, 40, 80),
            FileTypeInfo(".md", 1, 5, 10),
            FileTypeInfo(".png", 1, None, None),
        ]
        assert app.call_from_thread.call_args.args[0] is screen.populate

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

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=[])
    def test_load_repos_uses_loading_placeholders_for_missing_rows(self, _mock_sessions):
        info = _make_info("alpha", Path("/tmp/alpha"))
        app = GitDirectorConsole()
        app.manager = _mock_manager([info])
        app.call_from_thread = MagicMock()

        GitDirectorConsole._load_repos.__wrapped__(app)

        call_targets = [call.args[0] for call in app.call_from_thread.call_args_list]
        populate_call = next(
            call
            for call in app.call_from_thread.call_args_list
            if call.args[0] == app._populate_initial_rows
        )
        assert populate_call.kwargs == {}
        assert app._update_row in call_targets

    def test_load_repos_skips_table_updates_after_app_stops(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        worker = MagicMock(is_cancelled=False)
        app._current_worker_or_none = MagicMock(return_value=worker)
        app.call_from_thread = MagicMock()
        app._save_repos_cache = MagicMock()

        assert app.is_running is False
        GitDirectorConsole._load_repos.__wrapped__(app)

        app.call_from_thread.assert_called_once_with(app._hide_refresh_indicator)
        app._save_repos_cache.assert_not_called()

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_cached_repo_rows_keep_loading_column_widths(self, _mock_sessions):
        info = _make_info("alpha", Path("/tmp/alpha"), branch="m", last_updated="now")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            app._repo_paths = [info.path]
            app._groups_entries = []
            app._results = {str(info.path): info}
            app._populate_initial_rows()
            await pilot.pause()

            table = app.query_one("#repo-table", DataTable)
            width = len(_REPO_LOADING_CELL_VALUE)
            for index in (1, 2, 3, 4):
                assert table.columns[app._col_keys[index]].content_width >= width

    def test_update_row_ignores_table_errors(self):
        app = GitDirectorConsole()
        app._col_keys = ("repo", "sync", "branch", "changes", "last", "path")
        table = MagicMock()
        table.update_cell.side_effect = RuntimeError("boom")
        app.query_one = MagicMock(return_value=table)
        info = _make_info("alpha", Path("/tmp/alpha"))

        app._update_row(info)

        table.update_cell.assert_called_once()

    def test_update_row_ignores_missing_table_during_shutdown(self):
        app = GitDirectorConsole()
        app.query_one = MagicMock(side_effect=NoMatches("#repo-table"))

        app._update_row(_make_info("alpha", Path("/tmp/alpha")))

        app.query_one.assert_called_once_with("#repo-table", DataTable)

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
    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[{"session_name": "gd/alpha/shell/1"}],
    )
    def test_poll_session_statuses_updates_state_and_notifies(self, _mock_sessions, _mock_statuses):
        app = GitDirectorConsole()
        app._active_tab = "sessions"
        app._sessions_entries = [{"session_name": "gd/alpha/shell/1"}]
        app._sessions_snapshot_generation = 1
        app.call_from_thread = lambda callback, *args: callback(*args)
        app._on_statuses_updated = MagicMock()

        GitDirectorConsole._poll_session_statuses_worker.__wrapped__(app, 1)

        assert app._session_statuses == {"gd/alpha/shell/1": {"command": "python", "dead": False}}
        app._on_statuses_updated.assert_called_once_with()

    def test_trigger_status_poll_delegates_to_worker(self):
        app = GitDirectorConsole()
        app._active_tab = "sessions"
        app._poll_session_statuses = MagicMock()

        app._trigger_status_poll()

        app._poll_session_statuses.assert_called_once_with()

    def test_trigger_status_poll_skips_inactive_tabs(self):
        app = GitDirectorConsole()
        app._active_tab = "repos"
        app._poll_session_statuses = MagicMock()

        app._trigger_status_poll()

        app._poll_session_statuses.assert_not_called()

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

    def test_on_statuses_updated_skips_panels_refresh_when_live_sessions_unchanged(self):
        app = GitDirectorConsole()
        app._sessions_entries = [{"session_name": "gd/alpha/shell/1", "purpose": "shell"}]
        app._resolve_session_status = MagicMock(return_value="running")
        app._waiting_count = 0
        app._active_tab = "panels"
        app._panels_live_sessions = {"gd/alpha/shell/1"}
        app._apply_panels_filter_and_sort = MagicMock()

        app._on_statuses_updated()

        app._apply_panels_filter_and_sort.assert_not_called()
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
        app._session_status_tracking_running = True
        app._poll_timer = MagicMock()
        app._monitor = MagicMock()

        app._pause_session_status_tracking()

        assert app._session_status_tracking_paused is True
        app._poll_timer.pause.assert_called_once_with()
        app._monitor.stop.assert_called_once_with(wait=True)

    def test_resume_session_status_tracking_restarts_timer_and_monitor(self):
        app = GitDirectorConsole()
        app._active_tab = "sessions"
        app._session_status_tracking_paused = True
        app._poll_timer = MagicMock()
        app._monitor = MagicMock()

        app._resume_session_status_tracking()

        assert app._session_status_tracking_paused is False
        app._monitor.start.assert_called_once_with()
        app._poll_timer.resume.assert_called_once_with()

    def test_resume_session_status_tracking_stays_stopped_off_sessions_tab(self):
        app = GitDirectorConsole()
        app._active_tab = "repos"
        app._session_status_tracking_paused = True
        app._poll_timer = MagicMock()
        app._monitor = MagicMock()

        app._resume_session_status_tracking()

        assert app._session_status_tracking_paused is False
        app._monitor.start.assert_not_called()
        app._poll_timer.resume.assert_not_called()
        app._poll_timer.pause.assert_called_once_with()

    def test_action_quit_uses_non_blocking_session_monitor_shutdown(self):
        app = GitDirectorConsole()
        app._pause_session_status_tracking = MagicMock()
        app._monitor = MagicMock()
        executor = MagicMock()
        app._repo_status_executor = executor
        app.exit = MagicMock()

        with patch.object(app.workers, "cancel_all") as mock_cancel_all:
            with patch.object(Repository, "kill_running_git_commands") as mock_kill_git:
                app.action_quit()

        app._pause_session_status_tracking.assert_called_once_with(wait=False)
        app._monitor.stop.assert_called_once_with(wait=True)
        executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        mock_cancel_all.assert_called_once_with()
        mock_kill_git.assert_called_once_with()
        app.exit.assert_called_once_with()

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

        app._pause_session_status_tracking.assert_called_once_with(wait=False)
        app._resume_session_status_tracking.assert_called_once_with()

    def test_suspend_and_attach_resumes_status_tracking_after_attach_error(self):
        app = GitDirectorConsole()
        app._pause_session_status_tracking = MagicMock()
        app._resume_session_status_tracking = MagicMock()
        app._monitor = MagicMock()
        app._update_status = MagicMock()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )

        with patch(
            "gitdirector.integrations.tmux.attach_tmux_session",
            side_effect=RuntimeError("boom"),
        ):
            with patch("sys.stdout"):
                with patch("termios.tcflush"):
                    app._suspend_and_attach("gd-test-session")

        app._pause_session_status_tracking.assert_called_once_with(wait=False)
        app._resume_session_status_tracking.assert_called_once_with()
        app._update_status.assert_called_once()

    def test_resolve_repo_refresh_path_matches_current_session_names(self):
        path = Path("/tmp/beta")
        session_name = f"gd/{_repo_session_name_segment(path)}/shell/1"
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("beta", path)])

        assert app._resolve_repo_refresh_path(session_name) == path

    def test_resolve_repo_refresh_path_uses_legacy_single_match_fallback(self):
        path = Path("/tmp/beta")
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("beta", path)])

        assert app._resolve_repo_refresh_path("gd/beta/shell/1") == path

    def test_resolve_repo_refresh_path_skips_ambiguous_legacy_matches(self):
        repos = [
            _make_info("beta", Path("/tmp/team-a/beta")),
            _make_info("beta", Path("/tmp/team-b/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)

        assert app._resolve_repo_refresh_path("gd/beta/shell/1") is None

    def test_action_select_row_noops_when_sessions_table_empty(self):
        app = GitDirectorConsole()
        app._active_tab = "sessions"
        table = MagicMock()
        table.row_count = 0
        app.query_one = MagicMock(return_value=table)
        app.push_screen = MagicMock()

        app.action_select_row()

        app.push_screen.assert_not_called()

    def test_action_select_row_reattaches_selected_session_with_inner_delay(self):
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

        app._suspend_and_attach.assert_called_once_with(
            "gd/alpha/shell/1",
            attach_delay_seconds=AgentLoadingScreen._MIN_WAIT,
        )

    def test_on_data_table_row_selected_reattaches_agent_session_with_inner_delay(self):
        app = GitDirectorConsole()
        app._suspend_and_attach = MagicMock()
        event = MagicMock()
        event.data_table.id = "sessions-table"
        event.row_key.value = "gd/alpha/copilot/1"

        app.on_data_table_row_selected(event)

        app._suspend_and_attach.assert_called_once_with(
            "gd/alpha/copilot/1",
            attach_delay_seconds=AgentLoadingScreen._MIN_WAIT,
        )

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
    def test_action_open_tmux_shell_uses_loading_screen(self, mock_create):
        app = GitDirectorConsole()
        app._get_selected_path = MagicMock(return_value=Path("/tmp/alpha"))
        app._suspend_and_attach = MagicMock()
        app.push_screen = MagicMock()

        app.action_open_tmux()

        mock_create.assert_called_once_with(
            "alpha", Path("/tmp/alpha"), purpose="shell", description=None
        )

        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, AgentLoadingScreen)
        assert screen._agent_cmd == "shell"
        assert screen._ready_marker is None
        assert screen._loading_hint == "waiting for session to initialize…"

        screen._on_attach()
        app._suspend_and_attach.assert_called_once_with(
            "gd/alpha/shell/1",
            Path("/tmp/alpha"),
            row_key=None,
            skip_config_sync=True,
        )

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

    def test_attach_to_session_reuses_temp_attach_with_inner_delay(self):
        app = GitDirectorConsole()
        app._suspend_and_attach = MagicMock()

        app._attach_to_session("gd/alpha/copilot/1", Path("/tmp/alpha"))

        app._suspend_and_attach.assert_called_once_with(
            "gd/alpha/copilot/1",
            Path("/tmp/alpha"),
            attach_delay_seconds=AgentLoadingScreen._MIN_WAIT,
        )

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

    def test_action_refresh_loads_only_sessions_on_sessions_tab(self):
        app = GitDirectorConsole()
        result = object()
        app._results = {"/tmp/alpha": result}
        app._load_repos = MagicMock()
        app._load_sessions = MagicMock()
        app._active_tab = "sessions"

        app.action_refresh()

        assert app._results == {"/tmp/alpha": result}
        app._load_repos.assert_not_called()
        app._load_sessions.assert_called_once_with()

    def test_action_refresh_loads_only_panels_on_panels_tab(self):
        app = GitDirectorConsole()
        app._load_repos = MagicMock()
        app._load_panels = MagicMock()
        app._active_tab = "panels"

        app.action_refresh()

        app._load_repos.assert_not_called()
        app._load_panels.assert_called_once_with()

    def test_action_refresh_reloads_repos_with_loading_rows(self):
        app = GitDirectorConsole()
        app._active_tab = "repos"
        result = object()
        app._results = {"/tmp/alpha": result}
        app._repos_cache_updated_at = 123.0
        app._load_repos = MagicMock()
        app._show_refresh_indicator = MagicMock()

        app.action_refresh()

        assert app._results == {}
        assert app._repos_cache_updated_at == 123.0
        app._show_refresh_indicator.assert_called_once_with()
        app._load_repos.assert_called_once_with()

    def test_in_place_repos_refresh_preserves_cached_results(self):
        app = GitDirectorConsole()
        result = object()
        app._results = {"/tmp/alpha": result}
        app._load_repos = MagicMock()
        app._show_refresh_indicator = MagicMock()

        app._refresh_repos()

        assert app._results == {"/tmp/alpha": result}
        app._show_refresh_indicator.assert_called_once_with()
        app._load_repos.assert_called_once_with()

    def test_refresh_repos_forces_loading_after_config_change(self):
        app = GitDirectorConsole()
        result = object()
        app._results = {"/tmp/alpha": result}
        app._reload_config_if_changed = MagicMock(return_value=True)
        app._show_refresh_indicator = MagicMock()
        app._load_repos = MagicMock()

        app._refresh_repos()

        assert app._results == {}
        app._show_refresh_indicator.assert_called_once_with()
        app._load_repos.assert_called_once_with()

    def test_expired_repos_cache_refreshes_in_place_on_tab_activation(self):
        app = GitDirectorConsole()
        app.refresh_bindings = MagicMock()
        app._sync_session_status_tracking = MagicMock()
        app._reload_config_if_changed = MagicMock(return_value=False)
        app._repo_cache_expired = MagicMock(return_value=True)
        app._refresh_repos = MagicMock()
        event = MagicMock()
        event.pane.id = "repos"

        app.on_tabbed_content_tab_activated(event)

        app._refresh_repos.assert_called_once_with(show_loading=True)

    def test_repo_tab_activation_refreshes_when_config_changed(self):
        app = GitDirectorConsole()
        app.refresh_bindings = MagicMock()
        app._sync_session_status_tracking = MagicMock()
        app._reload_config_if_changed = MagicMock(return_value=True)
        app._repo_cache_expired = MagicMock(return_value=False)
        app._refresh_repos = MagicMock()
        event = MagicMock()
        event.pane.id = "repos"

        app.on_tabbed_content_tab_activated(event)

        app._refresh_repos.assert_called_once_with(show_loading=True)

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

        mock_stop.assert_called_once_with(wait=True)


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
    async def test_load_repos_does_not_query_session_counts(self, mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 1
            mock_sessions.assert_not_called()

    def test_sort_key_func_all_columns(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        infos = [
            _make_info("a", Path("/tmp/a"), branch="main", last_commit_timestamp=2),
            _make_info("b", Path("/tmp/b"), branch="dev", last_commit_timestamp=1),
        ]
        for col in range(6):
            app._sort_column = col
            app._sort_reverse = False
            sorted_infos = sorted(infos, key=app._sort_key_func())
            assert isinstance(sorted_infos, list)

    @patch("gitdirector.integrations.tmux.list_repo_sessions", side_effect=Exception("fail"))
    async def test_refresh_repo_for_path_does_not_query_sessions(self, mock_sessions):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._refresh_repo_for_path(Path("/tmp/alpha"))
            await app.workers.wait_for_complete()
            mock_sessions.assert_not_called()


class TestRefreshRepoForPath:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=["gd/alpha/shell/1"])
    async def test_refresh_updates_results_and_row(self, mock_list):
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
        mgr.get_repository_status.side_effect = lambda p, fetch=False, include_size=False: (
            updated_info
        )

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
            assert table.get_cell(row_key, ck[1]) == "[bold yellow]behind[/bold yellow]"
            assert table.get_cell(row_key, ck[2]) == "develop"
            assert table.get_cell(row_key, ck[3]) == "[bold yellow]staged[/bold yellow]"
            assert table.get_cell(row_key, ck[5]) == row_key
            assert app._results[row_key].status == RepoStatus.BEHIND
            app.manager.get_repository_status.assert_any_call(Path("/tmp/alpha"), fetch=True)
            mock_list.assert_not_called()


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
