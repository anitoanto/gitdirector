"""Tests for repository groups in the TUI."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual.css.query import NoMatches
from textual.widgets import DataTable, OptionList, Static, TabbedContent
from textual.widgets._footer import FooterKey

from gitdirector.commands.tui import AgentLoadingScreen, GitDirectorConsole, GroupActionMenuScreen
from gitdirector.commands.tui.app_groups import detect_repo_groups, group_row_key
from gitdirector.integrations.tmux.core import _repo_session_name_segment
from gitdirector.repo import RepoStatus

from .conftest import _make_info, _mock_manager, repo_row_text


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
            # Standalone repositories first, then each group under its heading.
            assert [str(key.value) for key in table.rows] == [
                "/tmp/other/solo",
                group_key,
                "/tmp/work/alpha",
                "/tmp/work/beta",
            ]
            heading = repo_row_text(app, group_key)
            assert heading.splitlines()[-1].strip() == "▾ work"
            # Names line up: standalone repos with group names, members one level in.
            assert repo_row_text(app, "/tmp/other/solo").startswith("   solo")
            assert repo_row_text(app, "/tmp/work/alpha").startswith("     alpha")
            assert repo_row_text(app, "/tmp/work/beta").startswith("     beta")
            assert "[space] toggle" in app.query_one("#status-bar", Static).content

            table.move_cursor(row=1)
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

    async def test_toggle_all_groups_collapses_then_expands_every_group(self):
        repos = [
            _make_info("alpha", Path("/tmp/work/alpha")),
            _make_info("beta", Path("/tmp/work/beta")),
            _make_info("gamma", Path("/tmp/play/gamma")),
            _make_info("delta", Path("/tmp/play/delta")),
            _make_info("solo", Path("/tmp/other/solo")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#repo-table", DataTable)
            assert table.row_count == 7
            assert "[shift+space] toggle all" in app.query_one("#status-bar", Static).content

            # One group already collapsed: the others still collapse.
            table.move_cursor(row=0)
            app.action_toggle_group()
            await pilot.press("shift+space")
            await pilot.pause()
            assert table.row_count == 3
            for parent in (Path("/tmp/work"), Path("/tmp/play")):
                assert "▸" in str(table.get_cell(group_row_key(parent), app._col_keys[0]))

            # Every group collapsed: the next press expands them all.
            await pilot.press("shift+space")
            await pilot.pause()
            assert table.row_count == 7
            for parent in (Path("/tmp/work"), Path("/tmp/play")):
                assert "▾" in str(table.get_cell(group_row_key(parent), app._col_keys[0]))

    async def test_toggle_all_groups_is_inert_off_the_repos_tab(self):
        repos = [
            _make_info("alpha", Path("/tmp/work/alpha")),
            _make_info("beta", Path("/tmp/work/beta")),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("3")
            await pilot.press("shift+space")
            await pilot.pause()

            assert app._collapsed_groups == set()

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

            assert "checking…" not in repo_row_text(app, "/tmp/work/alpha")
            assert "checking…" in repo_row_text(app, "/tmp/work/beta")

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
            assert repo_row_text(app, "/tmp/work/alpha").strip().startswith("alpha")
            assert repo_row_text(app, "/tmp/work/beta").strip().startswith("beta")

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
            shell=True,
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
            cell = table.get_cell(row_key, app._sess_col_keys[0]).plain
            assert "group_work" in cell
            assert row_key in cell


def _work_repos():
    return [
        _make_info("alpha", Path("/tmp/work/alpha")),
        _make_info("beta", Path("/tmp/work/beta")),
    ]


async def _folded_work_group(app, pilot) -> DataTable:
    await app.workers.wait_for_complete()
    await pilot.pause()
    table = app.query_one("#repo-table", DataTable)
    table.move_cursor(row=0)
    app.action_toggle_group()
    await pilot.pause()
    assert table.row_count == 1
    return table


class TestFoldedGroupHeading:
    async def test_repaints_when_a_hidden_repo_gains_a_session(self):
        repos = _work_repos()
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        group_key = group_row_key(Path("/tmp/work"))

        async with app.run_test(size=(120, 30)) as pilot:
            await _folded_work_group(app, pilot)
            assert "waiting" not in repo_row_text(app, group_key)

            slug = _repo_session_name_segment(repos[0].path)
            app._sessions_entries = [
                {
                    "session_name": f"gd/{slug}/claude/1",
                    "repo_slug": slug,
                    "purpose": "claude",
                    "status": "waiting",
                }
            ]
            app._refresh_repo_session_cells()
            await pilot.pause()

            assert "● 1 waiting" in repo_row_text(app, group_key)

    async def test_repaints_when_a_hidden_repo_result_lands(self):
        repos = _work_repos()
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        group_key = group_row_key(Path("/tmp/work"))

        async with app.run_test(size=(120, 30)) as pilot:
            await _folded_work_group(app, pilot)
            assert "attention" not in repo_row_text(app, group_key)
            app._apply_filter_and_sort = MagicMock(wraps=app._apply_filter_and_sort)

            dirty = replace(repos[0], unstaged=True, unstaged_files=["x"])
            app._results[str(dirty.path)] = dirty
            app._update_row(dirty)
            await pilot.pause()

            app._apply_filter_and_sort.assert_not_called()
            assert "1 needs attention" in repo_row_text(app, group_key)


class TestGroupToggleEdges:
    async def test_collapse_all_moves_cursor_from_child_to_its_group(self):
        repos = [
            _make_info("solo", Path("/tmp/other/solo")),
            _make_info("gamma", Path("/tmp/play/gamma")),
            _make_info("delta", Path("/tmp/play/delta")),
            *_work_repos(),
        ]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            keys = [str(key.value) for key in table.rows]
            table.move_cursor(row=keys.index("/tmp/play/gamma"))

            await pilot.press("shift+space")
            await pilot.pause()

            assert table.row_count == 3
            assert app._get_selected_group().path == Path("/tmp/play")

    async def test_toggles_are_inert_while_searching(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager(_work_repos())

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._search_query = "work"
            app._apply_filter_and_sort()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=0)

            app.action_toggle_group()
            app.action_toggle_all_groups()
            await pilot.pause()

            assert app._collapsed_groups == set()
            assert table.row_count == 3
            assert "[space] toggle" not in app.query_one("#status-bar", Static).content


class TestRefreshLayout:
    async def test_status_column_narrows_once_a_refresh_completes(self):
        dirty = replace(
            _make_info("alpha", Path("/tmp/alpha"), status=RepoStatus.DIVERGED),
            ahead=3,
            behind=4,
            staged=True,
            staged_files=["a", "b"],
            unstaged=True,
            unstaged_files=["c"],
        )
        repos = [dirty]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)

        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            wide = app._repo_layout.status

            repos[0] = _make_info("alpha", Path("/tmp/alpha"))
            app._refresh_repos()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app._repos_refreshing is False
            assert app._repo_layout.status < wide


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

            assert "work" in str(title.render())
            assert "alpha, beta" in str(branch_label.render())
            assert menu.highlighted_option.id == "agent:claude"

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_select_new_session(self, _mock_sessions):
        results: list[str | None] = []
        screen = GroupActionMenuScreen("work", Path("/tmp/work"), 2, "alpha, beta")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda value: results.append(value))
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()

            assert results == ["new_session"]
