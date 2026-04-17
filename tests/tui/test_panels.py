"""Tests for panel-focused TUI behavior."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from textual.app import App
from textual.widgets import DataTable, Static
from textual.widgets._footer import FooterKey

from gitdirector.commands.tui import (
    _PANELS_SORT_COLUMN_NAMES,
    ConfirmScreen,
    GitDirectorConsole,
    Panel,
    PanelStore,
    PanelViewScreen,
    PaneWidget,
)
from gitdirector.commands.tui.app import _panel_row_height, _render_panel_preview
from gitdirector.commands.tui.screens import RenamePanelScreen
from gitdirector.ui_theme import resolve_panel_theme

from .conftest import _mock_manager


class TestPaneWidget:
    def test_build_header_text_shows_session_slug(self):
        theme = resolve_panel_theme("rose-pine")
        pane = PaneWidget(2, "gd/my-repo/copilot/3", theme_name="rose-pine")

        header = pane._build_header_text()

        assert " 2 " in header
        assert "copilot my-repo/3" in header
        assert f"on {theme.badge_active_bg.lower()}" in header.lower()
        assert f"on {theme.label_active_bg.lower()}" in header.lower()

    def test_session_command_uses_embedded_attach_wrapper(self):
        pane = PaneWidget(2, "gd/my-repo/copilot/3", theme_name="rose-pine")

        command = pane._session_command("gd/my-repo/copilot/3")

        assert command.startswith("sh -c ")
        assert "tmux set-option -q -t gd/my-repo/copilot/3 status off" in command
        assert "tmux attach-session -t gd/my-repo/copilot/3" in command

    def test_session_command_uses_panel_proxy_when_panel_name_is_set(self):
        pane = PaneWidget(
            2,
            "gd/my-repo/copilot/3",
            theme_name="rose-pine",
            panel_name="Main",
        )

        command = pane._session_command("gd/my-repo/copilot/3")

        assert command.startswith("sh -c ")
        assert "tmux new-session -d -t gd/my-repo/copilot/3 -s gd-proxy/panel/main/2" in command
        assert "tmux set-option -q -t gd-proxy/panel/main/2 status off" in command
        assert "tmux attach-session -t gd-proxy/panel/main/2" in command
        assert "tmux set-option -q -t gd/my-repo/copilot/3 status off" not in command

    def test_closed_body_text_shows_session_closed_hint(self):
        pane = PaneWidget(2, "gd/my-repo/copilot/3", theme_name="rose-pine")

        body = pane._closed_body_text()

        assert body.startswith("\n[dim]SESSION CLOSED[/dim]")
        assert "autodelete" in body
        assert "assign session" not in body

    def test_compose_uses_closed_body_text_when_initialized_closed(self):
        pane = PaneWidget(2, None, theme_name="rose-pine", closed=True)

        assert pane._body_text().startswith("\n[dim]SESSION CLOSED[/dim]")


class TestPanelViewScreen:
    def test_build_status_text_shows_session_slug(self):
        theme = resolve_panel_theme("rose-pine")
        panel = Panel(
            name="Main",
            rows=1,
            cols=2,
            panes={1: "gd/my-repo/copilot/3", 2: None},
        )
        screen = PanelViewScreen(panel, MagicMock(), theme_name="rose-pine")
        screen._focused_pane = 1
        screen._pane_widgets = {
            1: PaneWidget(1, "gd/my-repo/copilot/3", theme_name="rose-pine"),
            2: PaneWidget(2, None, theme_name="rose-pine"),
        }

        status = screen._build_status_text()

        assert " 1/2 " in status
        assert "copilot my-repo/3" in status
        assert f"on {theme.badge_active_bg.lower()}" in status.lower()
        assert f"on {theme.label_active_bg.lower()}" in status.lower()

    async def test_on_mount_applies_spans_for_tall_left_layout(self):
        panel = Panel(
            name="Focus",
            rows=2,
            cols=2,
            panes={1: None, 2: None, 3: None},
            layout_key="tall_left",
        )
        screen = PanelViewScreen(panel, MagicMock(), theme_name="rose-pine")
        app = GitDirectorConsole()
        app.manager = _mock_manager([])

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()

            pane1 = app.screen.query_one("#pane-1")
            pane2 = app.screen.query_one("#pane-2")
            pane3 = app.screen.query_one("#pane-3")

            assert pane1.styles.row_span == 2
            assert pane1.styles.column_span == 1
            assert pane2.styles.row_span == 1
            assert pane2.styles.column_span == 1
            assert pane3.styles.row_span == 1
            assert pane3.styles.column_span == 1

    def test_handle_pane_session_closed_updates_pane_state(self):
        panel = Panel(
            name="Main",
            rows=1,
            cols=2,
            panes={1: "gd/my-repo/copilot/3", 2: None},
        )
        store = MagicMock()
        store.update_pane.return_value = False
        screen = PanelViewScreen(panel, store, theme_name="rose-pine")
        screen._pane_widgets = {1: MagicMock(spec=PaneWidget)}
        screen._update_status = MagicMock()
        screen.action_detach = MagicMock()

        screen._handle_pane_session_closed(1)

        store.update_pane.assert_called_once_with("Main", 1, None, closed=True)
        assert panel.panes[1] is None
        assert panel.closed_panes == {1}
        screen._pane_widgets[1].show_session_closed.assert_called_once_with()
        screen._update_status.assert_called_once_with()
        screen.action_detach.assert_not_called()

    def test_handle_pane_session_closed_detaches_when_panel_becomes_empty(self):
        panel = Panel(
            name="Main",
            rows=1,
            cols=1,
            panes={1: "gd/my-repo/copilot/3"},
        )
        store = MagicMock()
        store.update_pane.return_value = True
        screen = PanelViewScreen(panel, store, theme_name="rose-pine")
        screen._pane_widgets = {1: MagicMock(spec=PaneWidget)}
        screen._update_status = MagicMock()
        screen.action_detach = MagicMock()

        screen._handle_pane_session_closed(1)

        store.update_pane.assert_called_once_with("Main", 1, None, closed=True)
        assert panel.closed_panes == {1}
        screen._pane_widgets[1].show_session_closed.assert_called_once_with()
        screen.action_detach.assert_called_once_with()
        screen._update_status.assert_not_called()

    async def test_on_mount_shows_closed_message_for_persisted_closed_pane(self):
        panel = Panel(
            name="Focus",
            rows=1,
            cols=2,
            panes={1: None, 2: None},
            closed_panes={1},
        )
        screen = PanelViewScreen(panel, MagicMock(), theme_name="rose-pine")
        app = GitDirectorConsole()
        app.manager = _mock_manager([])

        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()

            empty = screen.query_one("#pane-empty-1", Static)

            assert "SESSION CLOSED" in str(empty.content)
            assert "No session assigned" not in str(empty.content)


class TestPanelStore:
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_create_skips_empty_panel(self, mock_kill_panel_tmux_session, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()

            panel = store.create("Empty", layout_key="grid_1x2", panes={1: None, 2: None})

        assert panel is None
        assert store.panels == []
        assert not (tmp_path / ".gitdirector" / "panels.yaml").exists()
        mock_kill_panel_tmux_session.assert_not_called()

    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_update_pane_removes_panel_when_last_assignment_is_cleared(
        self, mock_kill_panel_tmux_session, tmp_path
    ):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x2", panes={1: "gd/my-repo/shell/1"})

            panel_removed = store.update_pane("Main", 1, None)

        assert panel_removed is True
        assert store.get("Main") is None
        assert store.panels == []
        mock_kill_panel_tmux_session.assert_called_once_with("Main")

    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_delete_kills_panel_tmux_session(self, mock_kill_panel_tmux_session, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x2", panes={1: "gd/my-repo/shell/1"})

            deleted = store.delete("Main")

        assert deleted is True
        assert store.get("Main") is None
        assert store.panels == []
        mock_kill_panel_tmux_session.assert_called_once_with("Main")

    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_delete_missing_panel_skips_tmux_cleanup(self, mock_kill_panel_tmux_session, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()

            deleted = store.delete("Missing")

        assert deleted is False
        mock_kill_panel_tmux_session.assert_not_called()

    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_update_pane_persists_closed_state(self, mock_kill_panel_tmux_session, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create(
                "Main",
                layout_key="grid_1x2",
                panes={1: "gd/my-repo/shell/1", 2: "gd/my-repo/shell/2"},
            )

            panel_removed = store.update_pane("Main", 1, None, closed=True)

            reloaded_store = PanelStore()

        assert panel_removed is False
        panel = store.get("Main")
        assert panel is not None
        assert panel.panes[1] is None
        assert panel.closed_panes == {1}
        reloaded_panel = reloaded_store.get("Main")
        assert reloaded_panel is not None
        assert reloaded_panel.panes[1] is None
        assert reloaded_panel.closed_panes == {1}
        mock_kill_panel_tmux_session.assert_not_called()

    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    @patch(
        "gitdirector.integrations.tmux._session_exists",
        side_effect=lambda session_name: session_name != "gd/my-repo/shell/1",
    )
    def test_cleanup_orphans_marks_closed_panes_when_panel_survives(
        self,
        _mock_session_exists,
        mock_kill_panel_tmux_session,
        tmp_path,
    ):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create(
                "Main",
                layout_key="grid_1x2",
                panes={1: "gd/my-repo/shell/1", 2: "gd/my-repo/shell/2"},
            )

            removed_names = store.cleanup_orphans()

        assert removed_names == []
        panel = store.get("Main")
        assert panel is not None
        assert panel.panes[1] is None
        assert panel.panes[2] == "gd/my-repo/shell/2"
        assert panel.closed_panes == {1}
        mock_kill_panel_tmux_session.assert_not_called()

    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    @patch("gitdirector.integrations.tmux._session_exists", return_value=False)
    def test_cleanup_orphans_removes_panels_that_end_up_empty(
        self,
        _mock_session_exists,
        mock_kill_panel_tmux_session,
        tmp_path,
    ):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x2", panes={1: "gd/my-repo/shell/1"})

            removed_names = store.cleanup_orphans()

        assert removed_names == ["Main"]
        assert store.panels == []
        mock_kill_panel_tmux_session.assert_called_once_with("Main")


class TestTabStyling:
    def test_active_tab_uses_filled_style(self):
        assert "#tabs Tab.-active" in GitDirectorConsole.CSS
        assert "background: $accent;" in GitDirectorConsole.CSS


class TestGitDirectorConsolePanels:
    def test_render_panel_preview_matches_panel_layout(self):
        panel = Panel(
            name="Main",
            rows=2,
            cols=2,
            panes={1: "gd/alpha/shell/1", 2: None, 3: None, 4: "gd/beta/copilot/1"},
        )

        preview = _render_panel_preview(panel)

        assert preview == "\n".join(
            [
                "┌■ □┐",
                "└□ ■┘",
            ]
        )

    def test_handle_create_panel_skips_opening_empty_panel(self):
        app = GitDirectorConsole()
        app._panel_store = MagicMock()
        app._panel_store.get.return_value = None
        app._panel_store.panels = []
        app._panel_store.create.return_value = None
        app._load_panels = MagicMock()
        app._open_panel = MagicMock()
        app._update_status = MagicMock()

        app._handle_create_panel(("Empty", "grid_1x2", {1: None, 2: None}))

        app._panel_store.create.assert_called_once_with(
            "Empty",
            panes={1: None, 2: None},
            layout_key="grid_1x2",
        )
        app._load_panels.assert_called_once_with()
        app._open_panel.assert_not_called()
        app._update_status.assert_called_once_with(
            "Panel 'Empty' was not created because all panes are empty"
        )

    def test_render_panel_preview_matches_tall_left_layout(self):
        panel = Panel(
            name="Focus",
            rows=2,
            cols=2,
            panes={1: "gd/alpha/shell/1", 2: None, 3: "gd/beta/copilot/1"},
            layout_key="tall_left",
        )

        preview = _render_panel_preview(panel)

        assert preview == "\n".join(
            [
                "┌─┬□┐",
                "│■├─┤",
                "└─┴■┘",
            ]
        )

    def test_render_panel_preview_matches_two_by_three_top_left_duo_layout(self):
        panel = Panel(
            name="Wall",
            rows=2,
            cols=3,
            panes={
                1: "gd/alpha/shell/1",
                2: None,
                3: "gd/beta/copilot/1",
                4: None,
                5: "gd/gamma/shell/1",
            },
            layout_key="duo_top_left_2x3",
        )

        preview = _render_panel_preview(panel)

        assert preview == "\n".join(
            [
                "┌───┬─┐",
                "│ ■ │□│",
                "├─┬─┼─┤",
                "│■│□│■│",
                "└─┴─┴─┘",
            ]
        )

    def test_render_panel_preview_matches_three_by_three_bottom_right_duo_layout(self):
        panel = Panel(
            name="Grid",
            rows=3,
            cols=3,
            panes={
                1: "gd/alpha/shell/1",
                2: None,
                3: "gd/beta/copilot/1",
                4: None,
                5: "gd/gamma/shell/1",
                6: None,
                7: "gd/delta/shell/1",
                8: None,
            },
            layout_key="duo_bottom_right_3x3",
        )

        preview = _render_panel_preview(panel)

        assert preview == "\n".join(
            [
                "┌─┬─┬─┐",
                "│■│□│■│",
                "├─┼─┼─┤",
                "│□│■│□│",
                "├─┼─┴─┤",
                "│■│ □ │",
                "└─┴───┘",
            ]
        )

    def test_render_panel_preview_matches_three_by_three_top_left_quad_layout(self):
        panel = Panel(
            name="Studio",
            rows=3,
            cols=3,
            panes={
                1: "gd/alpha/shell/1",
                2: None,
                3: "gd/beta/copilot/1",
                4: None,
                5: "gd/gamma/shell/1",
                6: None,
            },
            layout_key="quad_top_left_3x3",
        )

        preview = _render_panel_preview(panel)

        assert preview == "\n".join(
            [
                "┌───┬─┐",
                "│   │□│",
                "│ ■ ├─┤",
                "│   │■│",
                "├─┬─┼─┤",
                "│□│■│□│",
                "└─┴─┴─┘",
            ]
        )

    def test_panel_row_height_tracks_preview_rows(self):
        assert (
            _panel_row_height(Panel(name="Solo", rows=1, cols=3, panes={1: None, 2: None, 3: None}))
            == 3
        )
        assert (
            _panel_row_height(
                Panel(name="Main", rows=2, cols=2, panes={1: None, 2: None, 3: None, 4: None})
            )
            == 4
        )
        assert (
            _panel_row_height(Panel(name="Grid", rows=3, cols=1, panes={1: None, 2: None, 3: None}))
            == 5
        )
        assert (
            _panel_row_height(
                Panel(
                    name="Focus",
                    rows=2,
                    cols=2,
                    panes={1: None, 2: None, 3: None},
                    layout_key="tall_left",
                )
            )
            == 5
        )
        assert (
            _panel_row_height(
                Panel(
                    name="Wall",
                    rows=2,
                    cols=3,
                    panes={1: None, 2: None, 3: None, 4: None, 5: None},
                    layout_key="duo_top_left_2x3",
                )
            )
            == 7
        )
        assert (
            _panel_row_height(
                Panel(
                    name="Grid",
                    rows=3,
                    cols=3,
                    panes={1: None, 2: None, 3: None, 4: None, 5: None, 6: None, 7: None, 8: None},
                    layout_key="duo_bottom_right_3x3",
                )
            )
            == 9
        )
        assert (
            _panel_row_height(
                Panel(
                    name="Studio",
                    rows=3,
                    cols=3,
                    panes={1: None, 2: None, 3: None, 4: None, 5: None, 6: None},
                    layout_key="quad_top_left_3x3",
                )
            )
            == 9
        )

    def test_panel_delete_hotkey_removed(self):
        assert all(binding.key != "d" for binding in GitDirectorConsole.BINDINGS)

    def test_tab_switch_hotkeys_hidden_from_footer(self):
        hidden_keys = {binding.key for binding in GitDirectorConsole.BINDINGS if not binding.show}

        assert {"1", "2", "3"}.issubset(hidden_keys)

    async def test_new_panel_footer_binding_only_shows_on_panels_tab(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()

            assert not any(
                binding.key == "n" and binding.description == "New Panel"
                for binding in app.query(FooterKey)
            )

            await pilot.press("3")
            await pilot.pause()

            assert any(
                binding.key == "n" and binding.description == "New Panel"
                for binding in app.query(FooterKey)
            )

            await pilot.press("1")
            await pilot.pause()

            assert not any(
                binding.key == "n" and binding.description == "New Panel"
                for binding in app.query(FooterKey)
            )

    def test_action_select_row_on_panels_opens_action_menu(self):
        app = GitDirectorConsole()
        app._active_tab = "panels"
        app._open_selected_panel_menu = MagicMock()

        app.action_select_row()

        app._open_selected_panel_menu.assert_called_once_with()

    def test_panel_row_selected_opens_action_menu(self):
        app = GitDirectorConsole()
        app._open_selected_panel_menu = MagicMock()
        event = MagicMock()
        event.data_table.id = "panels-table"

        app.on_data_table_row_selected(event)

        app._open_selected_panel_menu.assert_called_once_with()

    def test_handle_panel_action_rename_pushes_rename_screen(self):
        app = GitDirectorConsole()
        app.push_screen = MagicMock()

        app._handle_panel_action("rename", "Main")

        assert isinstance(app.push_screen.call_args.args[0], RenamePanelScreen)

    def test_handle_panel_action_delete_pushes_confirmation(self):
        app = GitDirectorConsole()
        app.push_screen = MagicMock()

        app._handle_panel_action("delete", "Main")

        assert isinstance(app.push_screen.call_args.args[0], ConfirmScreen)

    async def test_load_panels_renders_consistent_spacing_on_each_row(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        first_panel = Panel(
            name="Main",
            rows=2,
            cols=2,
            panes={1: "gd/alpha/shell/1", 2: None, 3: None, 4: "gd/beta/copilot/1"},
        )
        second_panel = Panel(
            name="Ops",
            rows=1,
            cols=3,
            panes={1: None, 2: "gd/ops/shell/1", 3: None},
        )
        third_panel = Panel(
            name="Studio",
            rows=3,
            cols=3,
            panes={
                1: "gd/alpha/shell/1",
                2: None,
                3: "gd/beta/copilot/1",
                4: None,
                5: "gd/gamma/shell/1",
                6: None,
            },
            layout_key="quad_top_left_3x3",
        )
        app._panel_store = MagicMock()
        app._panel_store.panels = [first_panel, second_panel, third_panel]

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._load_panels()
            table = app.query_one("#panels-table", DataTable)

            assert len(table.columns) == 5
            assert table.row_count == 3
            assert table.get_cell("Main", app._panels_col_keys[0]) == "\n".join(
                [
                    "",
                    "┌■ □┐",
                    "└□ ■┘",
                ]
            )
            assert table.get_cell("Main", app._panels_col_keys[1]) == "\nMain"
            assert table.get_cell("Main", app._panels_col_keys[2]) == "\ngd/panel/main"
            assert table.get_cell("Main", app._panels_col_keys[3]) == "\n2×2"
            assert table.get_cell("Main", app._panels_col_keys[4]) == "\n2/4"
            assert table.get_row_height("Main") == 4
            assert table.get_cell("Ops", app._panels_col_keys[0]) == "\n".join(
                [
                    "",
                    "┌□ ■ □┐",
                ]
            )
            assert table.get_cell("Ops", app._panels_col_keys[1]) == "\nOps"
            assert table.get_cell("Ops", app._panels_col_keys[2]) == "\ngd/panel/ops"
            assert table.get_cell("Ops", app._panels_col_keys[3]) == "\n1×3"
            assert table.get_cell("Ops", app._panels_col_keys[4]) == "\n1/3"
            assert table.get_row_height("Ops") == 3
            assert table.get_cell("Studio", app._panels_col_keys[0]) == "\n".join(
                [
                    "",
                    "┌───┬─┐",
                    "│   │□│",
                    "│ ■ ├─┤",
                    "│   │■│",
                    "├─┬─┼─┤",
                    "│□│■│□│",
                    "└─┴─┴─┘",
                ]
            )
            assert table.get_cell("Studio", app._panels_col_keys[1]) == "\nStudio"
            assert table.get_cell("Studio", app._panels_col_keys[2]) == "\ngd/panel/studio"
            assert table.get_cell("Studio", app._panels_col_keys[3]) == "\n3×3 Top-left quad"
            assert table.get_cell("Studio", app._panels_col_keys[4]) == "\n3/6"
            assert table.get_row_height("Studio") == 9

    async def test_panels_table_uses_panel_tmux_column_label(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()

            table = app.query_one("#panels-table", DataTable)

            assert str(table.columns[app._panels_col_keys[2]].label) == "TMUX"
            assert _PANELS_SORT_COLUMN_NAMES[1] == "TMUX"

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    async def test_on_mount_syncs_tmux_theme_config(self, mock_sync):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        app.manager.config.theme = "nord"
        app.theme = "nord"
        mock_sync.reset_mock()

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()

        assert mock_sync.call_args_list
        assert mock_sync.call_args_list[-1].args == ("nord",)

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    def test_theme_change_persists_and_syncs_tmux_config(self, mock_sync):
        app = GitDirectorConsole()
        app.manager = MagicMock()
        app.manager.config.theme = "rose-pine"
        app.manager.config.save = MagicMock()
        mock_sync.reset_mock()

        with patch.object(App, "_watch_theme", return_value=None):
            app._watch_theme("nord")

        assert app.manager.config.theme == "nord"
        app.manager.config.save.assert_called_once_with()
        mock_sync.assert_called_once_with("nord")
