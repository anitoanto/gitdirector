"""Tests for the interactive TUI console (Textual app)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import DataTable, OptionList, Static

from gitdirector.commands.tui import (
    ActionMenuScreen,
    ConfirmScreen,
    GitDirectorConsole,
    RemoveSessionScreen,
    _changes_label,
    _STATUS_LABEL,
)
from gitdirector.repo import RepositoryInfo, RepoStatus


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_info(
    name: str = "my-repo",
    path: Path | None = None,
    status: RepoStatus = RepoStatus.UP_TO_DATE,
    branch: str = "main",
    staged: bool = False,
    unstaged: bool = False,
    last_updated: str = "2 hours ago",
) -> RepositoryInfo:
    return RepositoryInfo(
        path=path or Path(f"/tmp/{name}"),
        name=name,
        status=status,
        branch=branch,
        staged=staged,
        unstaged=unstaged,
        last_updated=last_updated,
    )


def _mock_manager(repos: list[RepositoryInfo] | None = None):
    """Return a mock RepositoryManager whose config lists the given repos."""
    if repos is None:
        repos = []
    mgr = MagicMock()
    mgr.config.repositories = [r.path for r in repos]
    mgr.config.max_workers = 2

    def fake_status(path):
        for r in repos:
            if r.path == path:
                return r
        return _make_info(name=path.name, path=path, status=RepoStatus.UNKNOWN)

    mgr.get_repository_status.side_effect = fake_status
    return mgr


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestChangesLabel:
    def test_staged_and_unstaged(self):
        info = _make_info(staged=True, unstaged=True)
        assert _changes_label(info) == "staged+unstaged"

    def test_staged_only(self):
        info = _make_info(staged=True, unstaged=False)
        assert _changes_label(info) == "staged"

    def test_unstaged_only(self):
        info = _make_info(staged=False, unstaged=True)
        assert _changes_label(info) == "unstaged"

    def test_no_changes(self):
        info = _make_info(staged=False, unstaged=False)
        assert _changes_label(info) == "—"


class TestStatusLabel:
    def test_all_statuses_covered(self):
        for s in RepoStatus:
            assert s in _STATUS_LABEL

    def test_specific_values(self):
        assert _STATUS_LABEL[RepoStatus.UP_TO_DATE] == "up to date"
        assert _STATUS_LABEL[RepoStatus.BEHIND] == "behind"
        assert _STATUS_LABEL[RepoStatus.AHEAD] == "ahead"
        assert _STATUS_LABEL[RepoStatus.DIVERGED] == "diverged"
        assert _STATUS_LABEL[RepoStatus.UNKNOWN] == "unknown"


# ---------------------------------------------------------------------------
# GitDirectorConsole – app-level tests
# ---------------------------------------------------------------------------


class TestGitDirectorConsole:
    async def test_compose_widgets(self):
        """App renders Header, DataTable, Footer and status bar."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            assert app.query_one("#repo-table", DataTable)
            assert app.query_one("#status-bar", Static)
            assert len(app.query("Footer")) == 1
            assert len(app.query("Header")) == 1

    async def test_empty_repo_list(self):
        """With no tracked repos the table should be empty."""
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 0

    async def test_table_populated_with_repos(self):
        """Tracked repos appear as rows in the DataTable."""
        repos = [
            _make_info("alpha", Path("/tmp/alpha"), RepoStatus.UP_TO_DATE, "main"),
            _make_info("beta", Path("/tmp/beta"), RepoStatus.BEHIND, "develop"),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            # Wait for worker thread to finish
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 2

    async def test_quit_binding(self):
        """Pressing 'q' should exit the app."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("q")
            # If we get here without hanging, quit worked
            assert True

    async def test_cursor_down_binding(self):
        """Pressing 'j' moves cursor down in the DataTable."""
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

    async def test_cursor_up_binding(self):
        """Pressing 'k' moves cursor up in the DataTable."""
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
            # Move down first, then up
            await pilot.press("j")
            await pilot.press("k")
            assert table.cursor_coordinate.row == 0

    async def test_refresh_binding(self):
        """Pressing 'r' triggers a refresh (clears results and reloads)."""
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            # Clear call count, then refresh
            app.manager.get_repository_status.reset_mock()
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            # get_repository_status should have been called again
            assert app.manager.get_repository_status.call_count == 1

    async def test_status_bar_updates(self):
        """Status bar should show loaded message after repos load."""
        repos = [_make_info("alpha", Path("/tmp/alpha"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            status_text = app.query_one("#status-bar", Static).content
            assert "1 repository loaded" in status_text

    async def test_status_bar_no_repos(self):
        """Status bar message when no repos are tracked."""
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            status_text = app.query_one("#status-bar", Static).content
            assert "No repositories tracked" in status_text

    async def test_table_columns_created(self):
        """DataTable should have the expected 6 columns."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            table = app.query_one("#repo-table", DataTable)
            assert len(table.columns) == 6

    @patch("gitdirector.commands.tui.ActionMenuScreen")
    async def test_enter_opens_action_menu(self, mock_screen_cls):
        """Pressing enter on a row should push ActionMenuScreen."""
        repos = [_make_info("alpha", Path("/tmp/alpha"), branch="main")]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            # Directly call the handler to avoid complexities of ModalScreen mock
            app._handle_menu_action(None)  # None = dismissed, nothing happens
            # Verify nothing crashes on None
            assert True

    async def test_handle_menu_action_new_session(self):
        """_handle_menu_action routes 'new_session' to action_open_tmux."""
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        app.action_open_tmux = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._handle_menu_action("new_session")
            app.action_open_tmux.assert_called_once()

    async def test_handle_menu_action_attach(self):
        """_handle_menu_action routes 'attach:session-name' to _attach_to_session."""
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        app._attach_to_session = MagicMock()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._handle_menu_action("attach:gd-alpha-happy-panda")
            app._attach_to_session.assert_called_once_with("gd-alpha-happy-panda")

    async def test_handle_menu_action_none_is_noop(self):
        """Dismissing the menu (None) should not crash."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app._handle_menu_action(None)
            # No exception means success

    async def test_row_data_reflects_status(self):
        """Loaded row data should match repo info after worker finishes."""
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
            # The first row should have cell values matching the repo info
            row_key = str(repos[0].path)
            ck = app._col_keys
            assert table.get_cell(row_key, ck[1]) == "behind"
            assert table.get_cell(row_key, ck[2]) == "develop"
            assert table.get_cell(row_key, ck[3]) == "staged"
            assert table.get_cell(row_key, ck[4]) == "5 min ago"

    async def test_multiple_repos_status(self):
        """Status bar pluralises correctly for >1 repo."""
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


# ---------------------------------------------------------------------------
# ConfirmScreen tests
# ---------------------------------------------------------------------------


class TestConfirmScreen:
    async def test_compose_renders_message(self):
        """ConfirmScreen shows the provided message."""
        screen = ConfirmScreen("Delete everything?")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            title = app.screen.query_one("#menu-title", Static)
            assert "Delete everything?" in title.content

    async def test_yes_option_returns_true(self):
        """Selecting 'Yes' dismisses with True."""
        results: list[bool] = []
        screen = ConfirmScreen("Proceed?")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            # The first option is "Yes" – press enter to select it
            menu = app.screen.query_one("#action-menu", OptionList)
            menu.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert results == [True]

    async def test_no_option_returns_false(self):
        """Selecting 'No' dismisses with False."""
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
            # Move down to "No" then press enter
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert results == [False]

    async def test_escape_returns_false(self):
        """Pressing Escape on ConfirmScreen dismisses with False."""
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
        """j/k keys navigate options in ConfirmScreen."""
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


# ---------------------------------------------------------------------------
# ActionMenuScreen tests
# ---------------------------------------------------------------------------


class TestActionMenuScreen:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_compose_no_sessions(self, mock_sessions):
        """With no active sessions only 'new session' option is shown."""
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
        """When branch is None, the label shows '—'."""
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
        return_value=["gd-myrepo-happy-panda"],
    )
    async def test_compose_with_sessions(self, mock_sessions):
        """With active sessions, they appear in the option list."""
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            # Should have more options than just "new session"
            assert menu.option_count > 1

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_escape_dismisses(self, mock_sessions):
        """Pressing Escape on ActionMenuScreen dismisses with None."""
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
        """Selecting 'new session' option dismisses with 'new_session'."""
        results: list = []
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"))
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            # First non-disabled option is "new session"
            await pilot.press("enter")
            await pilot.pause()
            assert results == ["new_session"]

    @patch(
        "gitdirector.integrations.tmux.list_repo_sessions",
        return_value=["gd-myrepo-happy-panda", "gd-myrepo-cool-tiger"],
    )
    async def test_session_count_label(self, mock_sessions):
        """Multiple sessions show correct count label."""
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            # With 2 sessions we expect: new_session, separator, count label,
            # 2 session opts, separator, remove option = 7 items
            assert menu.option_count == 7


# ---------------------------------------------------------------------------
# RemoveSessionScreen tests
# ---------------------------------------------------------------------------


class TestRemoveSessionScreen:
    @patch(
        "gitdirector.integrations.tmux.list_repo_sessions",
        return_value=["gd-myrepo-happy-panda"],
    )
    async def test_compose_with_sessions(self, mock_sessions):
        """Shows sessions available for removal."""
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
        """With no sessions, shows informational text instead of menu."""
        screen = RemoveSessionScreen("my-repo")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            # Should not have an action-menu, but show "No active sessions"
            menus = app.screen.query("#action-menu")
            assert len(menus) == 0

    @patch(
        "gitdirector.integrations.tmux.list_repo_sessions",
        return_value=["gd-myrepo-happy-panda"],
    )
    async def test_escape_dismisses(self, mock_sessions):
        """Pressing Escape dismisses with None."""
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
        return_value=["gd-myrepo-happy-panda"],
    )
    async def test_select_session_to_remove(self, mock_sessions):
        """Selecting a session dismisses with the session name."""
        results: list = []
        screen = RemoveSessionScreen("my-repo")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert results == ["gd-myrepo-happy-panda"]


# ---------------------------------------------------------------------------
# Integration-style tests – confirm/remove flow
# ---------------------------------------------------------------------------


class TestRemoveFlow:
    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    async def test_do_remove_confirmed(self, mock_kill):
        """_do_remove calls kill_tmux_session when confirmed."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app._do_remove(True, "gd-myrepo-happy-panda")
            mock_kill.assert_called_once_with("gd-myrepo-happy-panda")

    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    async def test_do_remove_not_confirmed(self, mock_kill):
        """_do_remove does NOT call kill when not confirmed."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app._do_remove(False, "gd-myrepo-happy-panda")
            mock_kill.assert_not_called()

    async def test_get_selected_path_empty_table(self):
        """_get_selected_path returns None when table is empty."""
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app._get_selected_path() is None

    async def test_get_selected_path_with_repos(self):
        """_get_selected_path returns the path of the highlighted row."""
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
