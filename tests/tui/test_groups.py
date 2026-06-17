"""Tests for repository groups in the TUI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from textual.widgets import DataTable, OptionList, Static, TabbedContent

from gitdirector.commands.tui import GitDirectorConsole, GroupActionMenuScreen
from gitdirector.commands.tui.app_groups import detect_repo_groups

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


class TestGroupsTab:
    async def test_groups_table_exists(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)):
            table = app.query_one("#groups-table", DataTable)
            assert len(table.columns) == 3

    async def test_key_4_switches_to_groups(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("4")
            await pilot.pause()
            tabs = app.query_one("#tabs", TabbedContent)
            assert tabs.active == "groups"

    async def test_groups_table_populates_from_linked_repo_parents(self):
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
            app.action_tab_groups()
            await pilot.pause()

            table = app.query_one("#groups-table", DataTable)
            assert table.row_count == 1
            row_key = str(Path("/tmp/work"))
            assert table.get_cell(row_key, app._groups_col_keys[0]) == "work"
            assert table.get_cell(row_key, app._groups_col_keys[1]) == "2: alpha, beta"
            assert table.get_cell(row_key, app._groups_col_keys[2]) == row_key
            assert app._get_selected_path() == Path("/tmp/work")

    async def test_repositories_column_wraps_long_group_members(self):
        repos = [
            _make_info(
                "astro-blog-starter-template", Path("/tmp/templates/astro-blog-starter-template")
            ),
            _make_info(
                "browser-extension-template", Path("/tmp/templates/browser-extension-template")
            ),
            _make_info("caddy-template", Path("/tmp/templates/caddy-template")),
            _make_info("grafana-template", Path("/tmp/templates/grafana-template")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)

        async with app.run_test(size=(80, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.action_tab_groups()
            await pilot.pause()

            table = app.query_one("#groups-table", DataTable)
            row_key = str(Path("/tmp/templates"))
            cell = table.get_cell(row_key, app._groups_col_keys[1])
            width = table.columns[app._groups_col_keys[1]].width

            assert "\n" in cell
            assert table.get_row_height(row_key) > 1
            assert all(len(line) <= width for line in cell.splitlines())

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
            app.action_tab_groups()
            await pilot.pause()
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

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.action_tab_groups()
            await pilot.pause()
            app._suspend_and_attach = MagicMock()

            app.action_open_tmux()

            mock_create.assert_called_once_with(
                "work",
                Path("/tmp/work"),
                purpose="shell",
                description=None,
                repo_label="group_work",
            )
            app._suspend_and_attach.assert_called_once_with(
                "gd/work/shell/1",
                Path("/tmp/work"),
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
