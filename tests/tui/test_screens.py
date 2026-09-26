"""Tests for TUI modal screens (ConfirmScreen, ActionMenuScreen, GitOperationsMenuScreen,
GitCommandResultScreen, PullLoadingScreen, PullResultScreen, SortMenuScreen,
RemoveSessionScreen)."""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Input, LoadingIndicator, OptionList, Static

from gitdirector.commands.tui import (
    ActionMenuScreen,
    AgentLoadingScreen,
    ConfirmScreen,
    CreatePanelScreen,
    GitCommandResultScreen,
    GitDirectorConsole,
    GitOperationsMenuScreen,
    Panel,
    PullLoadingScreen,
    PullResultScreen,
    RemoveSessionScreen,
    RepoInfoScreen,
    SortMenuScreen,
)
from gitdirector.commands.tui.panels import get_create_panel_layouts
from gitdirector.commands.tui.screens import PanelActionMenuScreen
from gitdirector.info import FileTypeInfo, RepoInfoResult
from gitdirector.repo import RepoStatus

from .conftest import _make_info, _mock_manager, _wait_for_animated_scroll


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
            assert menu.option_count == 2
            assert menu.highlighted == 0
            await pilot.press("up")
            assert menu.highlighted == 1
            await pilot.press("down")
            assert menu.highlighted == 0
            await pilot.press("down")
            assert menu.highlighted == 1


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
            assert "my-repo" in str(title.render())
            assert str(app.screen.query_one("#menu-meta", Static).render()) == "main"
            assert str(app.screen.query_one("#menu-branch", Static).render()) == "/tmp/my-repo"
            menu = app.screen.query_one("#action-menu", OptionList)
            ids = [opt.id for opt in menu.options if opt.id is not None]
            # Agents lead: they are what the launcher is for.
            assert ids[:5] == [
                f"agent:{key}" for key in ("claude", "opencode", "codex", "copilot", "pi")
            ]
            assert ids[5:] == ["new_session", "vscode"]
            assert menu.highlighted_option.id == "agent:claude"

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_no_branch_shows_dash(self, mock_sessions):
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"), branch=None)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            assert str(app.screen.query_one("#menu-meta", Static).render()) == "detached"

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
            await pilot.press("s")
            await pilot.pause()
            assert results == ["new_session"]

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_letter_launches_agent_in_its_mode(self, mock_sessions):
        results: list = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(
                ActionMenuScreen("my-repo", Path("/tmp/my-repo")),
                callback=lambda v: results.append(v),
            )
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            assert results == ["agent:claude:auto"]

    @pytest.mark.parametrize(("key", "agent"), [("g", "copilot"), ("p", "pi")])
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_g_is_copilot_and_p_is_pi(self, mock_sessions, key, agent):
        results: list = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(
                ActionMenuScreen("my-repo", Path("/tmp/my-repo")),
                callback=lambda v: results.append(v),
            )
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
            assert results == [f"agent:{agent}"]

    @patch(
        "gitdirector.integrations.tmux.list_repo_sessions",
        return_value=["gd/my-repo/shell/1", "gd/my-repo/claude/1"],
    )
    async def test_digit_rejoins_session_instead_of_switching_tab(self, mock_sessions):
        results: list = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(
                ActionMenuScreen("my-repo", Path("/tmp/my-repo")),
                callback=lambda v: results.append(v),
            )
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            assert results == ["attach:gd/my-repo/claude/1"]
            assert app._active_tab == "repos"

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_status_sits_under_the_path(self, mock_sessions):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(
                ActionMenuScreen("my-repo", Path("/tmp/my-repo"), "main", Text("↑1 to push"))
            )
            await pilot.pause()
            subtitle = str(app.screen.query_one("#menu-branch", Static).render())
            assert subtitle.splitlines() == ["/tmp/my-repo", "↑1 to push"]

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
            assert menu.option_count == 15


class TestActionMenuAgentModes:
    async def _open(self, pilot, app, results):
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app.push_screen(screen, callback=lambda v: results.append(v))
        await pilot.pause()
        menu = app.screen.query_one("#action-menu", OptionList)
        menu.highlighted = menu.get_option_index("agent:claude")
        await pilot.pause()
        return menu

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_claude_launches_in_auto_mode_by_default(self, _):
        results: list = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(100, 32)) as pilot:
            await self._open(pilot, app, results)
            await pilot.press("enter")
            await pilot.pause()
            assert results == ["agent:claude:auto"]

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_arrows_cycle_the_mode_and_wrap(self, _):
        results: list = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(100, 32)) as pilot:
            await self._open(pilot, app, results)
            await pilot.press("right")
            assert app.screen._modes["claude"] == "bypass"
            await pilot.press("right")
            await pilot.press("enter")
            await pilot.pause()
            assert results == ["agent:claude:default"]

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_h_and_l_cycle_too(self, _):
        results: list = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(100, 32)) as pilot:
            await self._open(pilot, app, results)
            await pilot.press("h")
            await pilot.press("h")
            await pilot.press("l")
            await pilot.press("enter")
            await pilot.pause()
            assert results == ["agent:claude:default"]

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_tab_cycles_forward_and_shift_tab_back(self, _):
        results: list = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(100, 32)) as pilot:
            await self._open(pilot, app, results)
            await pilot.press("tab")
            assert app.screen._modes["claude"] == "bypass"
            await pilot.press("tab")
            assert app.screen._modes["claude"] == "default"
            await pilot.press("shift+tab")
            await pilot.press("enter")
            await pilot.pause()
            assert results == ["agent:claude:bypass"]

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_arrows_do_nothing_on_rows_without_modes(self, _):
        results: list = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(100, 32)) as pilot:
            menu = await self._open(pilot, app, results)
            menu.highlighted = menu.get_option_index("agent:codex")
            await pilot.pause()
            await pilot.press("right")
            await pilot.press("enter")
            await pilot.pause()
            assert results == ["agent:codex"]

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_mode_hint_shows_only_on_rows_with_modes(self, _):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(100, 32)) as pilot:
            menu = await self._open(pilot, app, [])
            hint = app.screen.query_one("#menu-hint", Static)
            assert "mode" in str(hint.content)
            menu.highlighted = menu.get_option_index("new_session")
            await pilot.pause()
            assert "mode" not in str(hint.content)

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_vscode_is_offered(self, _):
        results: list = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(100, 32)) as pilot:
            app.push_screen(
                ActionMenuScreen("my-repo", Path("/tmp/my-repo")),
                callback=lambda v: results.append(v),
            )
            await pilot.pause()
            await pilot.press("v")
            await pilot.pause()
            assert results == ["vscode"]


class TestGitOperationsMenuScreen:
    async def test_compose_shows_repo_and_branch(self):
        screen = GitOperationsMenuScreen("my-repo", branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()

            title = app.screen.query_one("#menu-title", Static)
            menu = app.screen.query_one("#action-menu", OptionList)

            assert "my-repo" in str(title.render())
            assert str(app.screen.query_one("#menu-meta", Static).render()) == "main"
            ids = [opt.id for opt in menu.options if opt.id is not None]
            assert ids == [
                "status",
                "timeline",
                "branches",
                "remotes",
                "pull",
                "push",
                "review_diff",
            ]

    async def test_shows_what_pull_push_and_review_would_do(self):
        info = replace(
            _make_info("my-repo", Path("/tmp/my-repo"), RepoStatus.DIVERGED, staged=True),
            ahead=1,
            behind=3,
            staged_files=["a", "b"],
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(GitOperationsMenuScreen("my-repo", "main", info, info.path))
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)

            def line(action: str) -> str:
                console = Console(width=60, record=True, file=io.StringIO())
                console.print(menu.get_option(action).prompt)
                return console.export_text()

            assert "↓3 commits to pull" in line("pull")
            assert "↑1 commit to push" in line("push")
            # Push needs Shift, and its key says so.
            assert line("push").rstrip().endswith("⇧P")
            assert "2 staged" in line("review_diff")
            assert "git status" in line("status")

    async def test_shift_p_pushes(self):
        results: list = []
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(
                GitOperationsMenuScreen("my-repo", "main"), callback=lambda v: results.append(v)
            )
            await pilot.pause()
            await pilot.press("P")
            await pilot.pause()
            assert results == ["push"]

    async def test_select_status(self):
        results: list[str | None] = []
        screen = GitOperationsMenuScreen("my-repo", branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda value: results.append(value))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert results == ["status"]

    async def test_select_timeline(self):
        results: list[str | None] = []
        screen = GitOperationsMenuScreen("my-repo", branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda value: results.append(value))
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

            assert results == ["timeline"]

    async def test_select_branches(self):
        results: list[str | None] = []
        screen = GitOperationsMenuScreen("my-repo", branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda value: results.append(value))
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

            assert results == ["branches"]

    async def test_select_remotes(self):
        results: list[str | None] = []
        screen = GitOperationsMenuScreen("my-repo", branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda value: results.append(value))
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

            assert results == ["remotes"]

    async def test_select_pull(self):
        results: list[str | None] = []
        screen = GitOperationsMenuScreen("my-repo", branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda value: results.append(value))
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

            assert results == ["pull"]

    async def test_select_push(self):
        results: list[str | None] = []
        screen = GitOperationsMenuScreen("my-repo", branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda value: results.append(value))
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()

            assert results == ["push"]


class TestPanelActionMenuScreen:
    async def test_letters_pick_actions(self):
        from gitdirector.commands.tui.panels import Panel

        results: list = []
        panel = Panel(name="Main", rows=1, cols=2, panes={1: None, 2: None})
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        with patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=[]):
            async with app.run_test(size=(100, 30)) as pilot:
                app.push_screen(PanelActionMenuScreen(panel), callback=results.append)
                await pilot.pause()
                await pilot.press("e")
                await pilot.pause()
        assert results == ["reconfigure"]

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {
                "session_name": "gd/alpha_a/shell/1",
                "repo": "alpha",
                "repo_slug": "alpha_a",
                "purpose": "shell",
                "description": "-",
            }
        ],
    )
    async def test_shows_the_panel_its_map_and_what_each_pane_holds(self, _mock_sessions):
        panel = Panel(
            name="Main",
            rows=2,
            cols=2,
            panes={1: "gd/alpha_a/shell/1", 2: None, 3: "gd/gone_b/codex/1", 4: None},
        )
        screen = PanelActionMenuScreen(panel)
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            title = app.screen.query_one("#menu-title", Static)
            subtitle = app.screen.query_one("#menu-branch", Static)
            preview = app.screen.query_one("#panel-layout-preview", Static)
            sessions = app.screen.query_one("#panel-sessions", Static)
            menu = app.screen.query_one("#action-menu", OptionList)

            assert "Main" in title.content
            assert str(app.screen.query_one("#menu-meta", Static).render()) == "2×2"
            assert str(subtitle.render()) == "gd/panel/main"
            for pane in "1234":
                assert pane in preview.content.plain
            lines = sessions.content.plain.splitlines()
            # Repos line up: the closed session's repo is the widest.
            assert lines[0].split() == ["1", "alpha", "shell/1"]
            assert lines[1] == "2 empty"
            assert lines[2].endswith("closed")
            assert [option.id for option in menu.options if option.id] == [
                "open",
                "reconfigure",
                "rename",
                "delete",
            ]
            assert app.screen.query_one("#panel-preview-pane").region.x > menu.region.x

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=[])
    async def test_asymmetric_layouts_draw_their_own_shape(self, _mock_sessions):
        from gitdirector.commands.tui.panels import render_panel_layout_text

        panel = Panel(
            name="Focus",
            rows=2,
            cols=2,
            panes={1: None, 2: None, 3: None},
            layout_key="wide_bottom",
        )
        screen = PanelActionMenuScreen(panel)
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            preview = app.screen.query_one("#panel-layout-preview", Static)
            expected = render_panel_layout_text(panel.layout, cell_width=5).plain
            assert preview.content.plain == expected


class TestPullResultScreen:
    async def test_compose_shows_command_and_output(self):
        screen = PullResultScreen(
            "my-repo",
            "git pull --ff-only origin main",
            True,
            "Already up to date.",
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()

            title = app.screen.query_one("#menu-title", Static)
            command = app.screen.query_one("#menu-branch", Static)
            status = app.screen.query_one("#result-status", Static)
            scroll = app.screen.query_one("#result-output-scroll", VerticalScroll)
            output = app.screen.query_one("#result-output", Static)

            assert "my-repo" in str(title.render())
            # The badge sits beside the title without squeezing it.
            assert title.region.width >= len("my-repo")
            assert status.region.width == len("✓ Pull completed")
            assert str(command.render()) == "$ git pull --ff-only origin main"
            assert str(status.render()) == "✓ Pull completed"
            assert scroll is not None
            assert "Already up to date." in output.content

    async def test_enter_closes_screen(self):
        results: list[None] = []
        screen = PullResultScreen("my-repo", None, False, "fatal: some error")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen, callback=lambda value: results.append(value))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert results == [None]

    async def test_escape_returns_back(self):
        results: list[str | None] = []
        screen = PullResultScreen("my-repo", None, False, "fatal: some error")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen, callback=lambda value: results.append(value))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            assert results == ["back"]

    async def test_output_renders_ansi_text(self):
        screen = PullResultScreen("my-repo", None, True, "\x1b[32mAlready up to date.\x1b[0m")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()

            output = app.screen.query_one("#result-output", Static)

            assert isinstance(output.content, Text)
            assert output.content.plain == "Already up to date."
            assert output.content.spans

    async def test_long_output_scrolls_with_arrow_and_jk(self):
        output = "\n".join(f"line {index}" for index in range(80))
        screen = PullResultScreen("my-repo", None, True, output)
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(100, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()

            scroll = app.screen.query_one("#result-output-scroll", VerticalScroll)
            assert scroll.max_scroll_y > 0
            assert scroll.scroll_y == 0

            await pilot.press("down")
            await _wait_for_animated_scroll(pilot, scroll)
            after_down = scroll.scroll_y
            assert after_down > 0

            await pilot.press("j")
            await _wait_for_animated_scroll(pilot, scroll)
            after_j = scroll.scroll_y
            assert after_j > after_down

            await pilot.press("up")
            await _wait_for_animated_scroll(pilot, scroll)
            after_up = scroll.scroll_y
            assert after_up < after_j

            await pilot.press("k")
            await _wait_for_animated_scroll(pilot, scroll)
            assert scroll.scroll_y <= after_up


class TestCardsSitCentred:
    @pytest.mark.parametrize(
        "make",
        [
            lambda: GitCommandResultScreen("my-repo", "git status", True, "x"),
            lambda: PullResultScreen("my-repo", "git pull", True, "x"),
            lambda: PullLoadingScreen("my-repo", "git pull"),
        ],
        ids=["git-output", "pull-result", "pull-loading"],
    )
    async def test_subclassed_cards_are_centred(self, make):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(make())
            await pilot.pause()
            card = app.screen.query_one("#menu-container").region
            assert abs(card.x - (120 - card.right)) <= 1
            assert abs(card.y - (40 - card.bottom)) <= 1

    async def test_output_card_takes_most_of_the_screen(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 40)) as pilot:
            app.push_screen(GitCommandResultScreen("my-repo", "git status", True, "x"))
            await pilot.pause()
            card = app.screen.query_one("#menu-container").region
            assert card.width == 108 and card.height == 34


class TestPrettyGraph:
    def test_graph_prefix_is_drawn_with_box_characters(self):
        from gitdirector.commands.tui.screens.repos import pretty_graph

        raw = Text.from_ansi("* \x1b[33mabc1234\x1b[0m main work\n|\\  \n| * def5678 a/b fix")
        pretty = pretty_graph(raw)
        assert pretty.plain.splitlines() == ["● abc1234 main work", "│╲  ", "│ ● def5678 a/b fix"]
        # The colour of the hash stays on the hash.
        assert pretty.spans == raw.spans


class TestGitCommandResultScreen:
    async def test_compose_shows_status_output(self):
        screen = GitCommandResultScreen(
            "my-repo",
            "git status",
            True,
            "On branch main\nnothing to commit, working tree clean",
            success_text="Status output",
            failure_text="Status failed",
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()

            command = app.screen.query_one("#menu-branch", Static)
            status = app.screen.query_one("#result-status", Static)
            scroll = app.screen.query_one("#result-output-scroll", VerticalScroll)
            output = app.screen.query_one("#result-output", Static)

            assert str(command.render()) == "$ git status"
            assert str(status.render()) == "✓ Status output"
            assert scroll is not None
            assert "working tree clean" in output.content

    async def test_escape_returns_back(self):
        results: list[str | None] = []
        screen = GitCommandResultScreen(
            "my-repo",
            "git status",
            True,
            "On branch main",
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen, callback=lambda value: results.append(value))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            assert results == ["back"]

    async def test_output_renders_ansi_text(self):
        screen = GitCommandResultScreen(
            "my-repo",
            "git log",
            True,
            "\x1b[33m* abc1234\x1b[0m add timeline",
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()

            output = app.screen.query_one("#result-output", Static)

            assert isinstance(output.content, Text)
            assert output.content.plain == "* abc1234 add timeline"
            assert output.content.spans


class TestPullLoadingScreen:
    async def test_compose_shows_loading_text_and_command(self):
        screen = PullLoadingScreen("my-repo", "git pull --ff-only origin main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()

            title = str(app.screen.query_one("#menu-title", Static).render())
            command = str(app.screen.query_one("#menu-branch", Static).render())
            loading = app.screen.query_one("LoadingIndicator", LoadingIndicator)

            assert title == "Pulling my-repo"
            assert command == "$ git pull --ff-only origin main"
            assert loading is not None


def _gd(name: str, repo: str) -> dict[str, str]:
    return {
        "session_name": name,
        "repo": repo,
        "repo_slug": name.split("/")[1],
        "purpose": name.split("/")[2],
        "description": "-",
    }


_FOUR_SESSIONS = [
    _gd("gd/a_x/shell/1", "alpha"),
    _gd("gd/b_x/claude-auto/1", "beta"),
    _gd("gd/c_x/codex/1", "gamma"),
    _gd("gd/d_x/shell/1", "delta"),
]


class TestCreatePanelScreen:
    def test_layout_registry_skips_single_pane_and_includes_asymmetric_presets(self):
        layout_keys = [layout.key for layout in get_create_panel_layouts()]

        assert "grid_1x1" not in layout_keys
        assert {
            "tall_left",
            "tall_right",
            "wide_top",
            "wide_bottom",
            "duo_top_left_2x3",
            "duo_top_right_2x3",
            "duo_bottom_left_2x3",
            "duo_bottom_right_2x3",
            "duo_top_left_3x3",
            "duo_top_right_3x3",
            "duo_bottom_left_3x3",
            "duo_bottom_right_3x3",
            "quad_top_left_3x3",
            "quad_top_right_3x3",
            "quad_bottom_left_3x3",
            "quad_bottom_right_3x3",
        }.issubset(layout_keys)

    async def _open(self, pilot, app, screen, results):
        app.manager = _mock_manager()
        app._panel_store = MagicMock()
        app._panel_store.get.return_value = None
        app._panel_store.panels = []
        app.push_screen(screen, callback=lambda result: results.append(result))
        await pilot.pause()

    async def _to_sessions(self, pilot, name: str = "Ops", layout_steps: int = 0):
        await pilot.press(*name, "enter")
        for _ in range(layout_steps):
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=False)
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_three_steps_lead_to_a_panel(self, _mock_sessions, _mock_exists):
        results: list = []
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, results)
            # Naming: a compact card, just the input; no preview yet.
            assert screen._step == 1
            container = app.screen.query_one("#create-panel-container")
            assert container.has_class("-naming")
            assert container.region.width == 56
            assert not app.screen.query_one("#create-panel-right").display
            assert not app.screen.query_one("#left-label").display
            await pilot.press(*"Ops")
            await pilot.press("enter")
            # Layout: the wide view opens and the preview follows the highlight.
            assert screen._step == 2
            await pilot.pause()
            assert not container.has_class("-naming")
            assert container.region.width > 56
            assert app.screen.query_one("#create-panel-right").display
            await pilot.press("down", "down", "down")
            assert screen._layout_key == "grid_2x2"
            await pilot.press("enter")
            # Sessions: choose one for pane 1, the cursor moves on to pane 2.
            assert screen._step == 3
            await pilot.press("enter")
            assert screen._picking == 1
            await pilot.press("down", "enter")
            assert screen._assignments[1] == "gd/b_x/claude-auto/1"
            assert screen._focused_pane() == 2
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert results == [
                ("Ops", "grid_2x2", {1: "gd/b_x/claude-auto/1", 2: None, 3: None, 4: None})
            ]

    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=False)
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_the_create_row_finishes_too(self, _mock_sessions, _mock_exists):
        results: list = []
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, results)
            await self._to_sessions(pilot)
            await pilot.press("a", "end", "enter")
            await pilot.pause()
            assert results == [
                ("Ops", "grid_1x2", {1: "gd/a_x/shell/1", 2: "gd/b_x/claude-auto/1"})
            ]

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_fill_takes_free_sessions_and_keeps_what_is_set(self, _mock_sessions):
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, [])
            await self._to_sessions(pilot, layout_steps=3)
            # The picker opens on the first session; two down is the third.
            await pilot.press("down", "enter", "down", "down", "enter")
            assert screen._assignments[2] == "gd/c_x/codex/1"
            await pilot.press("a")
            assert [screen._assignments[pane] for pane in (1, 2, 3, 4)] == [
                "gd/a_x/shell/1",
                "gd/c_x/codex/1",
                "gd/b_x/claude-auto/1",
                "gd/d_x/shell/1",
            ]

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=[])
    async def test_fill_without_sessions_leaves_panes_empty(self, _mock_sessions):
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, [])
            await self._to_sessions(pilot)
            await pilot.press("a")
            assert not any(screen._assignments.values())

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_clearing_a_pane_clears_only_that_pane(self, _mock_sessions):
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, [])
            await self._to_sessions(pilot, layout_steps=3)
            await pilot.press("a", "home", "down", "x")
            assert [screen._assignments[pane] for pane in (1, 2, 3, 4)] == [
                "gd/a_x/shell/1",
                None,
                "gd/c_x/codex/1",
                "gd/d_x/shell/1",
            ]
            # "leave empty" in the picker empties just its pane as well.
            await pilot.press("down", "enter", "home", "enter")
            assert [screen._assignments[pane] for pane in (1, 2, 3, 4)] == [
                "gd/a_x/shell/1",
                None,
                None,
                "gd/d_x/shell/1",
            ]

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_browsing_layouts_keeps_every_assignment(self, _mock_sessions):
        # Regression: passing a smaller layout emptied the panes it lacked.
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, [])
            await self._to_sessions(pilot, layout_steps=3)
            await pilot.press("a", "escape")
            assert screen._step == 2
            await pilot.press("home", "end", "home")
            await pilot.press("down", "down", "down", "enter")
            assert screen._layout_key == "grid_2x2"
            assert all(screen._assignments[pane] for pane in (1, 2, 3, 4))

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_choosing_a_session_again_moves_it(self, _mock_sessions):
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, [])
            await self._to_sessions(pilot)
            await pilot.press("a")
            assert screen._assignments == {1: "gd/a_x/shell/1", 2: "gd/b_x/claude-auto/1"}
            await pilot.press("home", "down", "enter")
            menu = app.screen.query_one("#session-menu", OptionList)
            assert "pane 1" in str(menu.get_option("gd/a_x/shell/1").prompt)
            menu.highlighted = [o.id for o in menu.options].index("gd/a_x/shell/1")
            await pilot.press("enter")
            assert screen._assignments == {1: None, 2: "gd/a_x/shell/1"}

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_escape_steps_back_then_cancels(self, _mock_sessions):
        results: list = []
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, results)
            await self._to_sessions(pilot)
            await pilot.press("enter")
            assert screen._picking == 1
            await pilot.press("escape")
            assert screen._picking is None and screen._step == 3
            await pilot.press("escape")
            assert screen._step == 2
            await pilot.press("escape")
            assert screen._step == 1
            await pilot.press("escape")
            await pilot.pause()
            assert results == [None]

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_single_letter_keys_type_into_the_name(self, _mock_sessions):
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, [])
            await pilot.press(*"jaxk")
            assert app.screen.query_one("#panel-name-input", Input).value == "jaxk"
            assert screen._step == 1

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_a_name_is_required(self, _mock_sessions):
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, [])
            await pilot.press("enter")
            error = app.screen.query_one("#create-panel-error", Static)
            assert screen._step == 1
            assert "name" in str(error.content)
            assert error.has_class("-shown")

    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=False)
    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=[])
    async def test_an_empty_panel_is_not_created(self, _mock_sessions, _mock_exists):
        results: list = []
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, results)
            await self._to_sessions(pilot)
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert results == []
            assert "at least one pane" in str(
                app.screen.query_one("#create-panel-error", Static).content
            )

    @pytest.mark.parametrize(
        ("existing", "panels", "session_exists", "message"),
        [
            (True, [], False, "already exists"),
            (False, [Panel(name="OPS", rows=1, cols=2, panes={})], False, "conflicts with tmux"),
            (False, [], True, "TMUX session 'gd/panel/ops' already exists"),
        ],
    )
    async def test_name_conflicts_stay_on_the_name_step(
        self, existing, panels, session_exists, message
    ):
        results: list = []
        screen = None
        with (
            patch(
                "gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS
            ),
            patch(
                "gitdirector.integrations.tmux.core._session_exists", return_value=session_exists
            ),
        ):
            screen = CreatePanelScreen()
            app = GitDirectorConsole()
            async with app.run_test(size=(130, 36)) as pilot:
                await self._open(pilot, app, screen, results)
                app._panel_store.get.return_value = MagicMock() if existing else None
                app._panel_store.panels = panels
                await pilot.press(*"ops", "enter")
                assert screen._step == 1
                assert message in str(app.screen.query_one("#create-panel-error", Static).content)
                assert results == []

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_edit_starts_on_sessions_with_the_panel_as_it_is(self, _mock_sessions):
        results: list = []
        screen = CreatePanelScreen(
            panel_name="Main",
            initial_layout_key="grid_2x2",
            initial_panes={
                1: "gd/a_x/shell/1",
                2: "gd/closed_z/shell/1",
                3: None,
                4: "gd/d_x/shell/1",
            },
            editing=True,
        )
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, results)
            assert screen._step == 3
            assert "Main" in str(app.screen.query_one("#create-panel-title", Static).content)
            # A session that has since closed starts empty.
            assert screen._assignments[2] is None
            assert "✓ Name" in app.screen.query_one("#create-panel-steps", Static).content.plain
            await pilot.press("escape")
            assert screen._step == 2
            await pilot.press("enter", "ctrl+o")
            await pilot.pause()
            assert results == [
                ("Main", "grid_2x2", {1: "gd/a_x/shell/1", 2: None, 3: None, 4: "gd/d_x/shell/1"})
            ]

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_edit_escape_from_layout_cancels(self, _mock_sessions):
        results: list = []
        screen = CreatePanelScreen(
            panel_name="Main", initial_layout_key="grid_1x2", initial_panes={}, editing=True
        )
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, results)
            await pilot.press("escape", "escape")
            await pilot.pause()
            assert results == [None]

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_edit_keeps_a_layout_the_create_menu_does_not_offer(self, _mock_sessions):
        assert "grid_1x1" not in [layout.key for layout in get_create_panel_layouts()]
        results: list = []
        screen = CreatePanelScreen(
            panel_name="Solo",
            initial_layout_key="grid_1x1",
            initial_panes={1: "gd/a_x/shell/1"},
            editing=True,
        )
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, results)
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert results == [("Solo", "grid_1x1", {1: "gd/a_x/shell/1"})]

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_edit_layout_step_offers_the_current_layout(self, _mock_sessions):
        results: list = []
        screen = CreatePanelScreen(
            panel_name="Solo",
            initial_layout_key="grid_1x1",
            initial_panes={1: "gd/a_x/shell/1"},
            editing=True,
        )
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, results)
            await pilot.press("escape")
            menu = app.screen.query_one("#layout-menu", OptionList)
            assert menu.highlighted_option.id == "layout:grid_1x1"
            await pilot.press("enter", "ctrl+o")
            await pilot.pause()
            assert results == [("Solo", "grid_1x1", {1: "gd/a_x/shell/1"})]

    @patch("gitdirector.integrations.tmux.list_all_gd_sessions", return_value=_FOUR_SESSIONS)
    async def test_preview_names_each_panes_repo(self, _mock_sessions):
        screen = CreatePanelScreen()
        app = GitDirectorConsole()
        async with app.run_test(size=(130, 36)) as pilot:
            await self._open(pilot, app, screen, [])
            await self._to_sessions(pilot)
            await pilot.press("a")
            preview = app.screen.query_one("#grid-preview", Static).content.plain
            assert "1 alpha" in preview and "2 beta" in preview


class TestSortMenuScreen:
    async def test_compose_shows_all_columns(self):
        screen = SortMenuScreen(0, False)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count == 5

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


_CUSTOM_COLUMNS = {0: "Status", 1: "Name", 2: "Size", 3: "Owner", 4: "Notes"}


class TestSortMenuScreenCustomColumns:
    async def test_custom_column_names(self):
        screen = SortMenuScreen(0, False, _CUSTOM_COLUMNS)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count == 5

    async def test_default_column_names(self):
        screen = SortMenuScreen(0, False)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count == 5

    async def test_toggle_on_custom_column(self):
        results: list = []
        screen = SortMenuScreen(1, False, _CUSTOM_COLUMNS)
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
        screen = RemoveSessionScreen("my-repo", Path("/tmp/my-repo"))
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.option_count == 1

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_compose_no_sessions(self, mock_sessions):
        screen = RemoveSessionScreen("my-repo", Path("/tmp/my-repo"))
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
        screen = RemoveSessionScreen("my-repo", Path("/tmp/my-repo"))
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
        screen = RemoveSessionScreen("my-repo", Path("/tmp/my-repo"))
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen, callback=lambda v: results.append(v))
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert results == ["gd/my-repo/shell/1"]

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=["s1", "s2"])
    async def test_j_k_navigation(self, _mock_sessions):
        screen = RemoveSessionScreen("repo", Path("/tmp/repo"))
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
            title = app.screen.query_one("#menu-title", Static)
            path_label = app.screen.query_one("#menu-branch", Static)
            loading = app.screen.query_one("#info-loading", LoadingIndicator)
            assert "my-repo" in str(title.render())
            assert "/tmp/my-repo" in str(path_label.render())
            assert loading is not None
            # Without git facts there is no summary to show.
            assert not app.screen.query("#info-facts")

    async def test_shows_git_facts_above_the_code(self):
        facts = [("Branch", Text("main")), ("Worktree", Text("2 changed"))]
        screen = RepoInfoScreen("my-repo", Path("/tmp/my-repo"), facts, Text("main"))
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            console = Console(width=80, record=True, file=io.StringIO())
            console.print(app.screen.query_one("#info-facts", Static).content)
            lines = [line.split() for line in console.export_text().splitlines()]
            assert lines == [["Branch", "main"], ["Worktree", "2", "changed"]]

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
            stats = str(app.screen.query_one("#info-stats", Static).render())
            assert stats == "3 files   10 lines   20 tokens   2 levels deep"
            console = Console(width=100, record=True, file=io.StringIO())
            console.print(app.screen.query_one("#info-table", Static).content)
            rows = console.export_text().splitlines()
            # .py holds every line, so its bar is full; .txt has no lines to count.
            assert rows[0].startswith(".py   " + "█" * 18)
            assert rows[1].startswith(".txt  " + "░" * 18)
            assert "2 files" in rows[0] and "10 lines" in rows[0]

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
            title = str(app.screen.query_one("#menu-title", Static).render())
            subtitle = str(app.screen.query_one("#menu-branch", Static).render())
            hint = str(app.screen.query_one("#menu-hint", Static).render())
            assert title == "Launching copilot"
            assert subtitle == "gd/my-repo/copilot/1"
            assert hint == "waiting for agent to initialize\u2026"

    @patch("gitdirector.commands.tui.screens.panels.time.monotonic", return_value=42.0)
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

    @patch("gitdirector.commands.tui.screens.panels.time.monotonic", return_value=42.0)
    def test_on_mount_without_ready_marker_uses_min_wait_timer(self, mock_monotonic):
        screen = AgentLoadingScreen("shell", "gd/my-repo/shell/1")
        timeout_timer = MagicMock()
        screen.set_interval = MagicMock()
        screen.set_timer = MagicMock(return_value=timeout_timer)
        screen.call_after_refresh = MagicMock()

        screen.on_mount()

        assert screen._start_time == 42.0
        screen.set_interval.assert_not_called()
        screen.set_timer.assert_called_once_with(screen._MIN_WAIT, screen._force_dismiss)
        screen.call_after_refresh.assert_not_called()
        assert screen._poll_timer is None
        assert screen._timeout_timer is timeout_timer
        mock_monotonic.assert_called_once_with()

    @patch("gitdirector.commands.tui.screens.panels.time.monotonic")
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
        mock_monotonic.return_value = 100.1
        screen._check_ready()
        screen._ready_marker.exists.assert_not_called()

        mock_monotonic.return_value = 100.5
        screen._ready_marker.exists.return_value = False
        screen._check_ready()

        screen._ready_marker.exists.assert_called_once_with()
        screen._poll_timer.stop.assert_not_called()
        screen._timeout_timer.stop.assert_not_called()
        screen._do_dismiss.assert_not_called()

    @patch("gitdirector.commands.tui.screens.panels.time.monotonic", return_value=101.5)
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

    def test_force_dismiss_without_ready_marker_skips_poll_timer(self):
        screen = AgentLoadingScreen("shell", "gd/my-repo/shell/1")
        screen._do_dismiss = MagicMock()

        screen._force_dismiss()

        assert screen._dismissed is True
        screen._do_dismiss.assert_called_once_with()

    def test_do_dismiss_removes_marker_and_delegates_attach_to_app(self):
        screen = AgentLoadingScreen("copilot", "gd/my-repo/copilot/1", Path("/tmp/agent.ready"))
        screen._ready_marker = MagicMock()
        screen.dismiss = MagicMock()
        app = GitDirectorConsole()
        app._suspend_and_attach = MagicMock()
        screen._parent = app

        screen._do_dismiss()

        screen._ready_marker.unlink.assert_called_once_with(missing_ok=True)
        app._suspend_and_attach.assert_called_once_with(
            "gd/my-repo/copilot/1", skip_config_sync=True
        )
        screen.dismiss.assert_called_once_with(None)

    def test_do_dismiss_prefers_on_attach_callback(self):
        on_attach = MagicMock()
        screen = AgentLoadingScreen("shell", "gd/my-repo/shell/1", on_attach=on_attach)
        screen.dismiss = MagicMock()
        app = GitDirectorConsole()
        app._suspend_and_attach = MagicMock()
        screen._parent = app

        screen._do_dismiss()

        on_attach.assert_called_once_with()
        app._suspend_and_attach.assert_not_called()
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
    async def test_do_remove_updates_session_entries(self, mock_kill):
        repos = [_make_info("my-repo", Path("/tmp/my-repo"))]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._sessions_entries = [
                {"session_name": "gd/my-repo/shell/1", "repo": "my-repo", "purpose": "shell"},
                {"session_name": "gd/my-repo/shell/2", "repo": "my-repo", "purpose": "shell"},
            ]
            app._do_remove(True, "gd/my-repo/shell/1")
            mock_kill.assert_called_once_with("gd/my-repo/shell/1")
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
            assert selected == Path("/tmp")

            table = app.query_one("#repo-table", DataTable)
            table.move_cursor(row=1)
            assert app._get_selected_path() == Path("/tmp/alpha")


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
