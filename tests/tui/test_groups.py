"""Tests for repository groups in the TUI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual.css.query import NoMatches
from textual.widgets import DataTable, OptionList, Static, TabbedContent
from textual.widgets._footer import FooterKey

from gitdirector.commands.tui import AgentLoadingScreen, GitDirectorConsole, GroupActionMenuScreen
from gitdirector.commands.tui.app_groups import detect_repo_groups, group_row_key

from .conftest import _make_info, _mock_manager


class TestRepoGroupDetection:
    def test_detects_one_level_parent_groups(self):
        groups = detect_repo_groups(
            [
                Path("/tmp/work/api"),
                Path("/tmp/work/web"),
                Path("/tmp/work/nested/worker"),
            ]
        )

        assert len(groups) == 1
        assert groups[0].path == Path("/tmp/work")
        assert [repo.name for repo in groups[0].repositories] == ["api", "web"]

    def test_ignores_single_repo_parents(self):
        assert detect_repo_groups([Path("/tmp/work/api")]) == []


class TestRepositoryGroups:
    async def test_groups_table_removed_from_top_level_tabs(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("4")
            await pilot.pause()

            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "repos"
            with pytest.raises(NoMatches):
                app.query_one("#groups-table", DataTable)

    async def test_toggle_group_footer_binding_shows_on_repos_tab(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()

            assert any(
                binding.key == "space" and binding.description == "Toggle"
                for binding in app.query(FooterKey)
            )

            await pilot.press("3")
            await pilot.pause()

            assert not any(
                binding.key == "space" and binding.description == "Toggle"
                for binding in app.query(FooterKey)
            )

    async def test_repo_table_populates_group_headers_from_linked_repo_parents(self):
        repos = [
            _make_info("alpha", Path("/tmp/work/alpha")),
            _make_info("beta", Path("/tmp/work/beta")),
            _make_info("solo", Path("/tmp/other/solo")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#repo-table", DataTable)
            group_key = group_row_key(Path("/tmp/work"))
            assert table.row_count == 4
            assert "work" in str(table.get_cell(group_key, app._col_keys[0]))
            assert "2 repos" in str(table.get_cell(group_key, app._col_keys[1]))
            assert table.get_cell(group_key, app._col_keys[5]) == "/tmp/work"
            assert table.get_cell("/tmp/work/alpha", app._col_keys[0]) == "  alpha"
            assert table.get_cell("/tmp/work/beta", app._col_keys[0]) == "  beta"
            assert table.get_cell("/tmp/other/solo", app._col_keys[0]) == "solo"
            assert "[space] toggle" in app.query_one("#status-bar", Static).content

            table.move_cursor(row=0)
            assert app._get_selected_path() == Path("/tmp/work")
            assert app._get_selected_group().name == "work"

    async def test_group_rows_can_collapse_and_expand(self):
        repos = [
            _make_info("alpha", Path("/tmp/work/alpha")),
            _make_info("beta", Path("/tmp/work/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 3
            table.move_cursor(row=0)

            app.action_toggle_group()
            await pilot.pause()

            assert table.row_count == 1
            group_key = group_row_key(Path("/tmp/work"))
            assert "▸" in str(table.get_cell(group_key, app._col_keys[0]))

            app.action_toggle_group()
            await pilot.pause()

            assert table.row_count == 3
            assert "▾" in str(table.get_cell(group_key, app._col_keys[0]))

    async def test_group_toggle_during_loading_preserves_loaded_status_cells(self):
        repos = [
            _make_info("alpha", Path("/tmp/work/alpha")),
            _make_info("beta", Path("/tmp/work/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager([])

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            app._repo_paths = [repo.path for repo in repos]
            app._groups_entries = detect_repo_groups(app._repo_paths)
            app._results = {str(repos[0].path): repos[0]}
            app._populate_initial_rows()

            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=0)
            app.action_toggle_group()
            app.action_toggle_group()
            await pilot.pause()

            assert table.get_cell("/tmp/work/alpha", app._col_keys[1]) != "... ... ... ..."
            assert table.get_cell("/tmp/work/beta", app._col_keys[1]) == "... ... ... ..."

    async def test_search_by_group_name_shows_group_repositories(self):
        repos = [
            _make_info("alpha", Path("/tmp/work/alpha")),
            _make_info("beta", Path("/tmp/work/beta")),
            _make_info("solo", Path("/tmp/other/solo")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            app._search_query = "work"
            app._apply_filter_and_sort()
            await pilot.pause()

            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 3
            assert table.get_cell("/tmp/work/alpha", app._col_keys[0]) == "  alpha"
            assert table.get_cell("/tmp/work/beta", app._col_keys[0]) == "  beta"

    async def test_action_show_menu_uses_group_action_screen(self):
        repos = [
            _make_info("alpha", Path("/tmp/work/alpha")),
            _make_info("beta", Path("/tmp/work/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=0)
            app.push_screen = MagicMock()

            with patch("gitdirector.commands.tui.app.GroupActionMenuScreen") as mock_screen:
                app.action_show_menu()

            mock_screen.assert_called_once_with("work", Path("/tmp/work"), 2, "alpha, beta")
            app.push_screen.assert_called_once()

    @patch("gitdirector.integrations.tmux.create_tmux_session", return_value="gd/work/shell/1")
    async def test_open_tmux_from_group_uses_parent_path(self, mock_create):
        repos = [
            _make_info("alpha", Path("/tmp/work/alpha")),
            _make_info("beta", Path("/tmp/work/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        app._suspend_and_attach = MagicMock()
        app.push_screen = MagicMock()

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=0)

            app.action_open_tmux()

        mock_create.assert_called_once_with(
            "work",
            Path("/tmp/work"),
            purpose="shell",
            description=None,
            repo_label="group_work",
        )
        app.push_screen.assert_called_once()
        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, AgentLoadingScreen)
        assert screen._agent_cmd == "shell"
        screen._on_attach()
        app._suspend_and_attach.assert_called_once_with(
            "gd/work/shell/1",
            Path("/tmp/work"),
            row_key=None,
            skip_config_sync=True,
        )

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {
                "session_name": "gd/work/shell/1",
                "repo": "group_work",
                "purpose": "shell",
                "description": "-",
            }
        ],
    )
    async def test_group_sessions_appear_on_sessions_tab(self, _mock_sessions):
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(120, 30)) as pilot:
            app.action_tab_sessions()
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#sessions-table", DataTable)
            row_key = "gd/work/shell/1"
            assert table.row_count == 1
            assert table.get_cell(row_key, app._sess_col_keys[2]) == "group_work"
            assert table.get_cell(row_key, app._sess_col_keys[3]) == row_key


class TestGroupActionMenuScreen:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_compose_no_sessions(self, _mock_sessions):
        screen = GroupActionMenuScreen("work", Path("/tmp/work"), 2, "alpha, beta")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            title = app.screen.query_one("#menu-title", Static)
            branch_label = app.screen.query_one("#menu-branch", Static)
            menu = app.screen.query_one("#action-menu", OptionList)

            assert "work" in title.content
            assert "alpha, beta" in branch_label.content
            assert menu.option_count == 8

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_select_new_session(self, _mock_sessions):
        results: list[str | None] = []
        screen = GroupActionMenuScreen("work", Path("/tmp/work"), 2, "alpha, beta")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda value: results.append(value))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert results == ["new_session"]
