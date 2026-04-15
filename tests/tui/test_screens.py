"""Tests for TUI modal screens (ConfirmScreen, ActionMenuScreen, SortMenuScreen,
RemoveSessionScreen)."""

from __future__ import annotations

import termios
from pathlib import Path
from unittest.mock import MagicMock, patch

from textual.widgets import LoadingIndicator, OptionList, Static

from gitdirector.commands.tui import (
    _SESSIONS_SORT_COLUMN_NAMES,
    ActionMenuScreen,
    AgentLoadingScreen,
    ConfirmScreen,
    GitDirectorConsole,
    RepoInfoScreen,
    RemoveSessionScreen,
    SortMenuScreen,
)
from gitdirector.info import FileTypeInfo, RepoInfoResult

from .conftest import _make_info, _mock_manager


class TestConfirmScreen:
    async def test_compose_renders_message(self):
        screen = ConfirmScreen("Delete everything?")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            title = app.screen.query_one("#menu-title", Static)
            assert "Delete everything?" in title.content

    async def test_yes_option_returns_true(self):
        results: list[bool] = []
        screen = ConfirmScreen("Proceed?")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            menu.focus()
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert results == [True]

    async def test_no_option_returns_false(self):
        results: list[bool] = []
        screen = ConfirmScreen("Proceed?")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            menu.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert results == [False]

    async def test_escape_returns_false(self):
        results: list[bool] = []
        screen = ConfirmScreen("Proceed?")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert results == [False]

    async def test_j_k_navigation(self):
        screen = ConfirmScreen("Test?")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            initial = menu.highlighted
            await pilot.press("j")
            assert menu.highlighted != initial
            await pilot.press("k")
            assert menu.highlighted == initial

    async def test_navigation_boundaries(self):
        screen = ConfirmScreen("Boundary?")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            await pilot.press("up")
            await pilot.press("down")
            await pilot.press("down")
            assert menu


class TestActionMenuScreen:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_compose_no_sessions(self, mock_sessions):
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            title = app.screen.query_one("#menu-title", Static)
            assert "my-repo" in title.content
            branch_label = app.screen.query_one("#menu-branch", Static)
            assert "main" in branch_label.content

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_no_branch_shows_dash(self, mock_sessions):
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"), branch=None)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            branch_label = app.screen.query_one("#menu-branch", Static)
            assert "\u2014" in branch_label.content

    @patch(
        "gitdirector.integrations.tmux.list_repo_sessions",
        return_value=["gd/my-repo/shell/1"],
    )
    async def test_compose_with_sessions(self, mock_sessions):
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count > 1

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_escape_dismisses(self, mock_sessions):
        results: list = []
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"))
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert results == [None]

    def test_cursor_actions_delegate_to_option_list(self):
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"))
        menu = MagicMock()
        screen.query_one = MagicMock(return_value=menu)

        screen.action_cursor_down()
        screen.action_cursor_up()

        menu.action_cursor_down.assert_called_once_with()
        menu.action_cursor_up.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_select_new_session(self, mock_sessions):
        results: list = []
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"))
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert results == ["new_session"]

    @patch(
        "gitdirector.integrations.tmux.list_repo_sessions",
        return_value=["gd/my-repo/shell/1", "gd/my-repo/claude/1"],
    )
    async def test_session_count_label(self, mock_sessions):
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count == 13

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=["s1", "s2"])
    async def test_disabled_options_and_navigation(self, _mock_sessions):
        screen = ActionMenuScreen("repo", Path("/tmp/repo"), branch="main")
        results = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            for _ in range(menu.option_count):
                await pilot.press("down")
            await pilot.press("enter")
            assert results


class TestSortMenuScreen:
    async def test_compose_shows_all_columns(self):
        screen = SortMenuScreen(0, False)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count == 7

    async def test_title_shows_sort_by(self):
        screen = SortMenuScreen(0, False)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            title = app.screen.query_one("#menu-title", Static)
            assert "Sort by" in title.content

    async def test_selecting_same_column_toggles(self):
        results: list = []
        screen = SortMenuScreen(0, False)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert results == [(0, True)]

    async def test_selecting_different_column(self):
        results: list = []
        screen = SortMenuScreen(0, False)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert results == [(1, False)]

    async def test_escape_returns_none(self):
        results: list = []
        screen = SortMenuScreen(0, False)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert results == [None]

    async def test_j_k_navigation(self):
        screen = SortMenuScreen(0, False)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            initial = menu.highlighted
            await pilot.press("j")
            assert menu.highlighted != initial
            await pilot.press("k")
            assert menu.highlighted == initial


class TestSortMenuScreenCustomColumns:
    async def test_custom_column_names(self):
        screen = SortMenuScreen(0, False, _SESSIONS_SORT_COLUMN_NAMES)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count == 4

    async def test_default_column_names(self):
        screen = SortMenuScreen(0, False)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count == 7

    async def test_toggle_on_custom_column(self):
        results: list = []
        screen = SortMenuScreen(1, False, _SESSIONS_SORT_COLUMN_NAMES)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert results == [(1, True)]


class TestRemoveSessionScreen:
    @patch(
        "gitdirector.integrations.tmux.list_repo_sessions",
        return_value=["gd/my-repo/shell/1"],
    )
    async def test_compose_with_sessions(self, mock_sessions):
        screen = RemoveSessionScreen("my-repo")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count == 1

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_compose_no_sessions(self, mock_sessions):
        screen = RemoveSessionScreen("my-repo")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menus = app.screen.query("#action-menu")
            assert len(menus) == 0

    @patch(
        "gitdirector.integrations.tmux.list_repo_sessions",
        return_value=["gd/my-repo/shell/1"],
    )
    async def test_escape_dismisses(self, mock_sessions):
        results: list = []
        screen = RemoveSessionScreen("my-repo")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert results == [None]

    @patch(
        "gitdirector.integrations.tmux.list_repo_sessions",
        return_value=["gd/my-repo/shell/1"],
    )
    async def test_select_session_to_remove(self, mock_sessions):
        results: list = []
        screen = RemoveSessionScreen("my-repo")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert results == ["gd/my-repo/shell/1"]

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=["s1", "s2"])
    async def test_navigation(self, _mock_sessions):
        screen = RemoveSessionScreen("repo")
        results = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            app.screen.query_one("#action-menu", OptionList)
            await pilot.press("down")
            await pilot.press("up")
            await pilot.press("enter")
            assert results

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=["s1", "s2"])
    async def test_j_k_navigation(self, _mock_sessions):
        screen = RemoveSessionScreen("repo")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            initial = menu.highlighted
            await pilot.press("j")
            assert menu.highlighted != initial
            await pilot.press("k")
            assert menu.highlighted == initial


class TestRepoInfoScreen:
    async def test_compose_shows_loading_state(self):
        screen = RepoInfoScreen("my-repo", Path("/tmp/my-repo"))
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            title = app.screen.query_one("#info-title", Static)
            path_label = app.screen.query_one("#info-path", Static)
            loading = app.screen.query_one("#info-loading", LoadingIndicator)
            hint = app.screen.query_one("#info-hint", Static)
            assert "my-repo" in title.content
            assert "/tmp/my-repo" in path_label.content
            assert loading is not None
            assert hint.content == ""

    async def test_populate_renders_stats_and_table(self):
        screen = RepoInfoScreen("my-repo", Path("/tmp/my-repo"))
        result = RepoInfoResult(
            total_files=3,
            file_types=[
                FileTypeInfo(".py", 2, 10, 20),
                FileTypeInfo(".txt", 1, None, None),
            ],
            total_lines=10,
            total_tokens=20,
            max_depth=2,
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            screen.populate(result)
            await pilot.pause()
            assert len(app.screen.query("#info-loading")) == 0
            stats = app.screen.query_one("#info-stats", Static)
            table = app.screen.query_one("#info-table", Static)
            hint = app.screen.query_one("#info-hint", Static)
            assert "Files" in stats.content
            assert "EXTENSION" in table.content
            assert ".py" in table.content
            assert "close" in hint.content

    async def test_populate_without_file_types_skips_table(self):
        screen = RepoInfoScreen("my-repo", Path("/tmp/my-repo"))
        result = RepoInfoResult(
            total_files=0,
            file_types=[],
            total_lines=0,
            total_tokens=0,
            max_depth=0,
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            screen.populate(result)
            await pilot.pause()
            assert len(app.screen.query("#info-table")) == 0
            assert len(app.screen.query("#info-stats")) == 1

    async def test_escape_dismisses(self):
        results: list[None] = []
        screen = RepoInfoScreen("my-repo", Path("/tmp/my-repo"))
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert results == [None]


class TestAgentLoadingScreen:
    async def test_compose_shows_loading_text(self, tmp_path):
        screen = AgentLoadingScreen(
            "copilot",
            "gd/my-repo/copilot/1",
            tmp_path / "agent.ready",
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            loading_text = app.screen.query_one("#loading-text", Static)
            loading_hint = app.screen.query_one("#loading-hint", Static)
            assert "Launching" in loading_text.content
            assert "copilot" in loading_text.content
            assert "waiting for agent to initialize" in loading_hint.content

    @patch("gitdirector.commands.tui.screens.time.monotonic", return_value=42.0)
    def test_on_mount_starts_poll_and_timeout_timers(self, mock_monotonic):
        screen = AgentLoadingScreen("copilot", "gd/my-repo/copilot/1", Path("/tmp/agent.ready"))
        poll_timer = MagicMock()
        timeout_timer = MagicMock()
        screen.set_interval = MagicMock(return_value=poll_timer)
        screen.set_timer = MagicMock(return_value=timeout_timer)
        screen.call_after_refresh = MagicMock()

        screen.on_mount()

        assert screen._start_time == 42.0
        screen.set_interval.assert_called_once_with(screen._POLL_INTERVAL, screen._check_ready)
        screen.set_timer.assert_called_once_with(screen._MAX_WAIT, screen._force_dismiss)
        screen.call_after_refresh.assert_called_once_with(screen._check_ready)
        assert screen._poll_timer is poll_timer
        assert screen._timeout_timer is timeout_timer
        mock_monotonic.assert_called_once_with()

    @patch("gitdirector.commands.tui.screens.time.monotonic")
    def test_check_ready_waits_for_minimum_time_and_marker(self, mock_monotonic):
        screen = AgentLoadingScreen("copilot", "gd/my-repo/copilot/1", Path("/tmp/agent.ready"))
        screen._poll_timer = MagicMock()
        screen._timeout_timer = MagicMock()
        screen._do_dismiss = MagicMock()
        screen._ready_marker = MagicMock()
        screen._start_time = 100.0

        screen._dismissed = True
        screen._check_ready()
        screen._do_dismiss.assert_not_called()

        screen._dismissed = False
        mock_monotonic.return_value = 100.5
        screen._check_ready()
        screen._ready_marker.exists.assert_not_called()

        mock_monotonic.return_value = 101.5
        screen._ready_marker.exists.return_value = False
        screen._check_ready()

        screen._ready_marker.exists.assert_called_once_with()
        screen._poll_timer.stop.assert_not_called()
        screen._timeout_timer.stop.assert_not_called()
        screen._do_dismiss.assert_not_called()

    @patch("gitdirector.commands.tui.screens.time.monotonic", return_value=101.5)
    def test_check_ready_dismisses_when_marker_exists(self, _mock_monotonic):
        screen = AgentLoadingScreen("copilot", "gd/my-repo/copilot/1", Path("/tmp/agent.ready"))
        screen._poll_timer = MagicMock()
        screen._timeout_timer = MagicMock()
        screen._ready_marker = MagicMock()
        screen._ready_marker.exists.return_value = True
        screen._do_dismiss = MagicMock()
        screen._start_time = 100.0

        screen._check_ready()

        assert screen._dismissed is True
        screen._poll_timer.stop.assert_called_once_with()
        screen._timeout_timer.stop.assert_called_once_with()
        screen._do_dismiss.assert_called_once_with()

    def test_force_dismiss_stops_poll_timer_once(self):
        screen = AgentLoadingScreen("copilot", "gd/my-repo/copilot/1", Path("/tmp/agent.ready"))
        screen._poll_timer = MagicMock()
        screen._do_dismiss = MagicMock()

        screen._force_dismiss()
        screen._force_dismiss()

        assert screen._dismissed is True
        screen._poll_timer.stop.assert_called_once_with()
        screen._do_dismiss.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.attach_tmux_session")
    @patch("subprocess.run")
    @patch("termios.tcflush")
    def test_do_dismiss_attaches_and_clears_terminal(self, mock_tcflush, mock_run, mock_attach):
        screen = AgentLoadingScreen("copilot", "gd/my-repo/copilot/1", Path("/tmp/agent.ready"))
        screen._ready_marker = MagicMock()
        screen.dismiss = MagicMock()
        app = GitDirectorConsole()
        suspend_context = MagicMock()
        suspend_context.__enter__.return_value = None
        suspend_context.__exit__.return_value = False
        app.suspend = MagicMock(return_value=suspend_context)
        screen._parent = app
        stdout = MagicMock()
        stdin = MagicMock()
        stdin.fileno.return_value = 7

        with patch("sys.stdout", new=stdout), patch("sys.stdin", new=stdin):
            screen._do_dismiss()

        screen._ready_marker.unlink.assert_called_once_with()
        app.suspend.assert_called_once_with()
        assert stdout.write.call_args_list[0].args[0] == "\033[?1049h\033[H\033[2J\033[?25l"
        assert stdout.write.call_args_list[1].args[0] == "\033[?25h"
        assert stdout.flush.call_count == 2
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0].args[0] == [
            "tmux",
            "send-keys",
            "-t",
            "gd/my-repo/copilot/1",
            "C-l",
            "",
        ]
        assert mock_run.call_args_list[0].kwargs == {"check": False}
        assert mock_run.call_args_list[1].args[0] == [
            "tmux",
            "clear-history",
            "-t",
            "gd/my-repo/copilot/1",
        ]
        assert mock_run.call_args_list[1].kwargs == {"check": False}
        mock_attach.assert_called_once_with("gd/my-repo/copilot/1")
        mock_tcflush.assert_called_once_with(7, termios.TCIFLUSH)
        screen.dismiss.assert_called_once_with(None)

    @patch("gitdirector.integrations.tmux.attach_tmux_session")
    @patch("subprocess.run")
    @patch("termios.tcflush", side_effect=OSError)
    def test_do_dismiss_ignores_missing_marker_and_tcflush_errors(
        self, _mock_tcflush, mock_run, mock_attach
    ):
        screen = AgentLoadingScreen("copilot", "gd/my-repo/copilot/1", Path("/tmp/agent.ready"))
        screen._ready_marker = MagicMock()
        screen._ready_marker.unlink.side_effect = FileNotFoundError
        screen.dismiss = MagicMock()
        app = GitDirectorConsole()
        suspend_context = MagicMock()
        suspend_context.__enter__.return_value = None
        suspend_context.__exit__.return_value = False
        app.suspend = MagicMock(return_value=suspend_context)
        screen._parent = app
        stdout = MagicMock()
        stdin = MagicMock()
        stdin.fileno.return_value = 11

        with patch("sys.stdout", new=stdout), patch("sys.stdin", new=stdin):
            screen._do_dismiss()

        assert mock_run.call_count == 2
        mock_attach.assert_called_once_with("gd/my-repo/copilot/1")
        screen.dismiss.assert_called_once_with(None)


class TestRemoveFlow:
    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    async def test_do_remove_confirmed(self, mock_kill):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as _:
            app._do_remove(True, "gd/my-repo/shell/1")
            mock_kill.assert_called_once_with("gd/my-repo/shell/1")

    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    async def test_do_remove_not_confirmed(self, mock_kill):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as _:
            app._do_remove(False, "gd/my-repo/shell/1")
            mock_kill.assert_not_called()

    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    @patch("gitdirector.integrations.tmux._sanitize_repo_name", side_effect=lambda x: x)
    async def test_do_remove_updates_repo_row(self, _mock_sanitize, mock_kill):
        repos = [_make_info("my-repo", Path("/tmp/my-repo"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sessions_cache[str(Path("/tmp/my-repo"))] = 2
            app._sessions_entries = [
                {"session_name": "gd/my-repo/shell/1", "repo": "my-repo", "purpose": "shell"},
                {"session_name": "gd/my-repo/shell/2", "repo": "my-repo", "purpose": "shell"},
            ]
            app._do_remove(True, "gd/my-repo/shell/1")
            mock_kill.assert_called_once_with("gd/my-repo/shell/1")
            assert app._sessions_cache[str(Path("/tmp/my-repo"))] == 1
            assert len(app._sessions_entries) == 1

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_handle_menu_action_remove_session(self, _mock_sessions):
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        app.push_screen = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._handle_menu_action("remove_session")
            app.push_screen.assert_called_once()

    async def test_get_selected_path_empty_table(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app._get_selected_path() is None

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_get_selected_path_with_repos(self, _mock_sessions):
        repos = [
            _make_info("alpha", Path("/tmp/alpha")),
            _make_info("beta", Path("/tmp/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            selected = app._get_selected_path()
            assert selected == Path("/tmp/alpha")


class TestListAllGdSessions:
    @patch("subprocess.run")
    def test_returns_all_gd_sessions(self, mock_run):
        from gitdirector.integrations.tmux import list_all_gd_sessions

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="gd/myrepo/shell/1\ngd/myrepo/claude/1\ngd/other/shell/1\nrandom-session\n",
        )
        result = list_all_gd_sessions()
        assert len(result) == 3
        assert result[0]["session_name"] == "gd/myrepo/claude/1"
        assert result[0]["repo"] == "myrepo"
        assert result[0]["purpose"] == "claude"
        assert result[1]["session_name"] == "gd/myrepo/shell/1"
        assert result[2]["session_name"] == "gd/other/shell/1"
        assert result[2]["repo"] == "other"

    @patch("subprocess.run")
    def test_returns_empty_on_no_tmux(self, mock_run):
        from gitdirector.integrations.tmux import list_all_gd_sessions

        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = list_all_gd_sessions()
        assert result == []

    @patch("subprocess.run")
    def test_returns_empty_when_no_gd_sessions(self, mock_run):
        from gitdirector.integrations.tmux import list_all_gd_sessions

        mock_run.return_value = MagicMock(returncode=0, stdout="my-session\nanother\n")
        result = list_all_gd_sessions()
        assert result == []
