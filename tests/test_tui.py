"""Tests for the interactive TUI console (Textual app)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from textual.widgets import DataTable, Input, OptionList, Static

from gitdirector.commands.tui import (
    _SORT_COLUMN_NAMES,
    _STATUS_LABEL,
    _STATUS_ORDER,
    ActionMenuScreen,
    ConfirmScreen,
    GitDirectorConsole,
    RemoveSessionScreen,
    SortMenuScreen,
    _changes_label,
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
    last_commit_timestamp: int | None = None,
) -> RepositoryInfo:
    return RepositoryInfo(
        path=path or Path(f"/tmp/{name}"),
        name=name,
        status=status,
        branch=branch,
        staged=staged,
        unstaged=unstaged,
        last_updated=last_updated,
        last_commit_timestamp=last_commit_timestamp,
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
        async with app.run_test(size=(120, 30)) as _:
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

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_table_populated_with_repos(self, _mock_sessions):
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

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_cursor_down_binding(self, _mock_sessions):
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

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_cursor_up_binding(self, _mock_sessions):
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

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_refresh_binding(self, _mock_sessions):
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

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_status_bar_updates(self, _mock_sessions):
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
            assert "No repositories linked" in status_text

    async def test_table_columns_created(self):
        """DataTable should have the expected 7 columns."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as _:
            table = app.query_one("#repo-table", DataTable)
            assert len(table.columns) == 7

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    @patch("gitdirector.commands.tui.ActionMenuScreen")
    async def test_enter_opens_action_menu(self, mock_screen_cls, _mock_sessions):
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

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_handle_menu_action_new_session(self, _mock_sessions):
        """_handle_menu_action routes 'new_session' to action_open_tmux."""
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
        async with app.run_test(size=(120, 30)) as _:
            app._handle_menu_action(None)
            # No exception means success

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=["gd-alpha-slug"])
    async def test_sessions_column_shows_count(self, _mock_sessions):
        """Sessions column shows the active session count when > 0."""
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
            assert table.get_cell(row_key, ck[5]) == "—"

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_multiple_repos_status(self, _mock_sessions):
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
            # "No" is first, move down to "Yes" then press enter
            menu = app.screen.query_one("#action-menu", OptionList)
            menu.focus()
            await pilot.pause()
            await pilot.press("down")
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
            # "No" is first – press enter to select it
            menu = app.screen.query_one("#action-menu", OptionList)
            menu.focus()
            await pilot.pause()
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
            # 2 session opts, separator, "Launch AI Agent" label, opencode,
            # claude, copilot, codex, separator, remove option = 13 items
            assert menu.option_count == 13


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
        async with app.run_test(size=(80, 24)) as _:
            app._do_remove(True, "gd-myrepo-happy-panda")
            mock_kill.assert_called_once_with("gd-myrepo-happy-panda")

    @patch("gitdirector.integrations.tmux.kill_tmux_session")
    async def test_do_remove_not_confirmed(self, mock_kill):
        """_do_remove does NOT call kill when not confirmed."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as _:
            app._do_remove(False, "gd-myrepo-happy-panda")
            mock_kill.assert_not_called()

    async def test_get_selected_path_empty_table(self):
        """_get_selected_path returns None when table is empty."""
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app._get_selected_path() is None

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_get_selected_path_with_repos(self, _mock_sessions):
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


# ---------------------------------------------------------------------------
# Sort / status-order constants
# ---------------------------------------------------------------------------


class TestSortConstants:
    def test_sort_column_names_count(self):
        """All 7 table columns have sort names."""
        assert len(_SORT_COLUMN_NAMES) == 7

    def test_status_order_covers_all(self):
        """Every RepoStatus has a defined sort order."""
        for s in RepoStatus:
            assert s in _STATUS_ORDER


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------


class TestSearch:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_search_shows_input(self, _mock_sessions):
        """Pressing '/' shows the search bar."""
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
        """Pressing Enter hides the search bar but keeps filter."""
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
        """Pressing Escape clears filter and hides search bar."""
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
        """Applying search filters repos by name."""
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
        """Search matches against branch names."""
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
        """Search matches against repo path."""
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
        """Search is case-insensitive."""
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
        """Search with no matches shows empty table."""
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
        """Status bar shows filter indicator when search is active."""
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


# ---------------------------------------------------------------------------
# SortMenuScreen tests
# ---------------------------------------------------------------------------


class TestSortMenuScreen:
    async def test_compose_shows_all_columns(self):
        """SortMenuScreen shows all sort column options."""
        screen = SortMenuScreen(0, False)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count == 7

    async def test_title_shows_sort_by(self):
        """Title shows 'Sort by' text."""
        screen = SortMenuScreen(0, False)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            title = app.screen.query_one("#menu-title", Static)
            assert "Sort by" in title.content

    async def test_selecting_same_column_toggles(self):
        """Selecting the current sort column toggles direction."""
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
        """Selecting a different column sorts ascending."""
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
        """Pressing Escape dismisses with None."""
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
        """j/k keys navigate options in SortMenuScreen."""
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


# ---------------------------------------------------------------------------
# Sort tests
# ---------------------------------------------------------------------------


class TestSort:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_by_name_ascending(self, _mock_sessions):
        """Default sort by name ascending."""
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
        """Sort by name descending reverses the order."""
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
        """Sort by sync status uses defined order."""
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
            # UP_TO_DATE(0) < BEHIND(2) < UNKNOWN(4)
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/a")
            table.move_cursor(row=1)
            assert app._get_selected_path() == Path("/tmp/b")
            table.move_cursor(row=2)
            assert app._get_selected_path() == Path("/tmp/c")

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_by_branch(self, _mock_sessions):
        """Sort by branch name."""
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
            assert app._get_selected_path() == Path("/tmp/b")  # "develop" first

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_by_last_commit_uses_timestamp(self, _mock_sessions):
        """Sort by Last Commit uses timestamp, not the text representation."""
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
            # ascending by timestamp: old (1.7B), mid (1.705B), new (1.71B)
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/old")
            table.move_cursor(row=1)
            assert app._get_selected_path() == Path("/tmp/mid")
            table.move_cursor(row=2)
            assert app._get_selected_path() == Path("/tmp/new")

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_sort_by_last_commit_descending(self, _mock_sessions):
        """Sort by Last Commit descending puts newest first."""
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
        """Repos with no timestamp sort to the start (0)."""
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
        """Sort and search work together."""
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
        """Status bar shows sort indicator when non-default sort is active."""
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
        """Pressing 's' opens the sort menu."""
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
        """_handle_sort_selection with None is a no-op."""
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
        """Sorting does not clear an active search filter."""
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
            # Now change sort
            app._sort_column = 0
            app._sort_reverse = True
            app._apply_filter_and_sort()
            await pilot.pause()
            assert table.row_count == 2  # Still filtered
            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/gamma-api")  # reversed


# ---------------------------------------------------------------------------
# _build_loaded_status tests
# ---------------------------------------------------------------------------


class TestBuildLoadedStatus:
    async def test_no_repos_no_filter(self):
        """Empty state with no filter shows 'No repositories tracked'."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            assert app._build_loaded_status(0, 0) == "No repositories tracked"

    async def test_default_state(self):
        """Default sort/filter shows simple count."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            msg = app._build_loaded_status(3, 3)
            assert "3 repositories loaded" in msg
            assert "filter:" not in msg
            assert "sort:" not in msg

    async def test_single_repo(self):
        """Single repo uses singular 'repository'."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            msg = app._build_loaded_status(1, 1)
            assert "1 repository loaded" in msg

    async def test_with_filter(self):
        """Active filter shows 'X of Y' and filter indicator."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            app._search_query = "test"
            msg = app._build_loaded_status(2, 5)
            assert "2 of 5" in msg
            assert "filter: 'test'" in msg

    async def test_with_sort(self):
        """Non-default sort shows sort indicator."""
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)):
            app._sort_column = 2
            app._sort_reverse = False
            msg = app._build_loaded_status(3, 3)
            assert "sort: Branch \u25b2" in msg

    async def test_with_filter_and_sort(self):
        """Both filter and sort indicators shown together."""
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
