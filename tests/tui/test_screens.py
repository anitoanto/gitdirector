"""Tests for TUI modal screens (ConfirmScreen, ActionMenuScreen, SortMenuScreen,
RemoveSessionScreen)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from textual.widgets import OptionList, Static

from gitdirector.commands.tui import (
    _SESSIONS_SORT_COLUMN_NAMES,
    ActionMenuScreen,
    ConfirmScreen,
    GitDirectorConsole,
    RemoveSessionScreen,
    SortMenuScreen,
)

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
            assert menu.option_count == 3

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
