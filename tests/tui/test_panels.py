"""Tests for panel-focused TUI behavior."""

from __future__ import annotations

from time import monotonic
from unittest.mock import MagicMock, patch

import pytest
import yaml
from textual.app import App
from textual.color import Color
from textual.widgets import DataTable, Static
from textual.widgets._footer import FooterKey

from gitdirector.commands.tui import (
    _PANELS_SORT_COLUMN_NAMES,
    ConfirmScreen,
    CreatePanelScreen,
    GitDirectorConsole,
    Panel,
    PanelStore,
    PaneWidget,
)
from gitdirector.commands.tui.app import _panel_row_height, _render_panel_preview
from gitdirector.commands.tui.panels import resolve_panel_layout
from gitdirector.commands.tui.screens import RenamePanelScreen
from gitdirector.ui_theme import resolve_panel_theme

from .conftest import _mock_manager


def _valid_panel_config(**overrides):
    return {
        "name": "Main",
        "rows": 1,
        "cols": 2,
        "layout": "grid_1x2",
        "panes": {},
        "closed_panes": [],
        **overrides,
    }


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
        assert "tmux set-option -t =gd/my-repo/copilot/3: status-position bottom" in command
        assert "tmux set-option -t =gd/my-repo/copilot/3: status-left" in command
        assert "tmux set-option -q -t =gd/my-repo/copilot/3: status off" not in command
        assert "tmux attach-session -t =gd/my-repo/copilot/3" in command

    def test_session_command_uses_direct_attach_when_panel_name_is_set(self):
        pane = PaneWidget(
            2,
            "gd/my-repo/copilot/3",
            theme_name="rose-pine",
            panel_name="Main",
        )

        command = pane._session_command("gd/my-repo/copilot/3")

        assert command.startswith("sh -c ")
        assert "tmux new-session -d -t =gd/my-repo/copilot/3 -s" not in command
        assert "tmux set-option -q -t =gd/my-repo/copilot/3: status off" in command
        assert "tmux attach-session -t =gd/my-repo/copilot/3" in command

    def test_closed_body_text_shows_session_closed_hint(self):
        pane = PaneWidget(2, "gd/my-repo/copilot/3", theme_name="rose-pine")

        body = pane._closed_body_text()

        assert body.startswith("\n[dim]SESSION CLOSED[/dim]")
        assert "assign session" in body

    def test_compose_uses_closed_body_text_when_initialized_closed(self):
        pane = PaneWidget(2, None, theme_name="rose-pine", closed=True)

        assert pane._body_text().startswith("\n[dim]SESSION CLOSED[/dim]")

    def test_watch_pane_focused_uses_heavier_border_style(self):
        theme = resolve_panel_theme("rose-pine")
        pane = PaneWidget(2, None, theme_name="rose-pine")

        assert pane.styles.border.top[0] == "round"
        assert pane.styles.border.top[1] == Color.parse(theme.border_inactive)

        pane.watch_pane_focused(True)

        assert pane.styles.border.top[0] == "thick"
        assert pane.styles.border.top[1] == Color.parse(theme.accent)

        pane.watch_pane_focused(False)

        assert pane.styles.border.top[0] == "round"
        assert pane.styles.border.top[1] == Color.parse(theme.border_inactive)

    def test_stop_terminal_stops_embedded_terminal(self):
        pane = PaneWidget(
            2,
            "gd/my-repo/copilot/3",
            theme_name="rose-pine",
            panel_name="Main",
        )
        pane._terminal = MagicMock()

        pane.stop_terminal()

        pane._terminal.stop.assert_called_once_with()


class TestPanelStore:
    @pytest.mark.parametrize(
        ("data", "message"),
        [
            ({"panels": {}}, "'panels' must be a list"),
            ({"panels": ["invalid"]}, "panel 1 must be a mapping"),
            ({"panels": [_valid_panel_config(name=" ")]}, "name must be a non-empty string"),
            ({"panels": [_valid_panel_config(layout=None)]}, "layout must be a known layout key"),
            (
                {"panels": [_valid_panel_config(layout="unknown")]},
                "layout must be a known layout key",
            ),
            (
                {"panels": [_valid_panel_config(rows=True)]},
                "rows must be an integer between 1 and 3",
            ),
            ({"panels": [_valid_panel_config(cols=4)]}, "cols must be an integer between 1 and 3"),
            ({"panels": [_valid_panel_config(panes=[])]}, "panes must be a mapping"),
            (
                {"panels": [_valid_panel_config(panes={3: "gd/repo/shell/1"})]},
                "panes pane index 3 is outside 1..2",
            ),
            (
                {"panels": [_valid_panel_config(panes={1: 1})]},
                "panes\\[1\\] must be a string or null",
            ),
            ({"panels": [_valid_panel_config(closed_panes={})]}, "closed_panes must be a list"),
            (
                {"panels": [_valid_panel_config(closed_panes=[3])]},
                "closed_panes pane index 3 is outside 1..2",
            ),
            (
                {
                    "panels": [
                        _valid_panel_config(),
                        _valid_panel_config(name="Main"),
                    ]
                },
                "duplicates panel name 'Main'",
            ),
            (
                {
                    "panels": [
                        _valid_panel_config(),
                        _valid_panel_config(name="MAIN!"),
                    ]
                },
                "normalizes to duplicate tmux session name 'gd/panel/main'",
            ),
        ],
    )
    def test_load_rejects_malformed_persisted_panels(self, tmp_path, data, message):
        config_dir = tmp_path / ".gitdirector"
        config_dir.mkdir()
        (config_dir / "panels.yaml").write_text(yaml.safe_dump(data))

        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            with pytest.raises(ValueError, match=message):
                PanelStore()

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
    def test_update_pane_keeps_panel_when_last_assignment_is_cleared(
        self, mock_kill_panel_tmux_session, tmp_path
    ):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x2", panes={1: "gd/my-repo/shell/1"})

            panel_updated = store.update_pane("Main", 1, None)

        assert panel_updated is True
        assert store.get("Main") is not None
        assert store.get("Main").panes[1] is None
        mock_kill_panel_tmux_session.assert_not_called()

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_delete_kills_panel_tmux_session(
        self, mock_kill_panel_tmux_session, mock_sync_panel_tmux_config, tmp_path
    ):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x2", panes={1: "gd/my-repo/shell/1"})

            deleted = store.delete("Main")

        assert deleted is True
        assert store.get("Main") is None
        assert store.panels == []
        mock_kill_panel_tmux_session.assert_called_once_with("Main")
        mock_sync_panel_tmux_config.assert_called_once_with()

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.panel_tmux_session_exists", return_value=True)
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session", return_value=False)
    def test_delete_preserves_panel_when_its_tmux_session_cannot_be_killed(
        self,
        mock_kill_panel_tmux_session,
        mock_panel_tmux_session_exists,
        mock_sync_panel_tmux_config,
        tmp_path,
    ):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x2", panes={1: "gd/my-repo/shell/1"})

            deleted = store.delete("Main")

        assert deleted is False
        assert store.get("Main") is not None
        mock_kill_panel_tmux_session.assert_called_once_with("Main")
        mock_panel_tmux_session_exists.assert_called_once_with("Main")
        mock_sync_panel_tmux_config.assert_not_called()

    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_delete_missing_panel_skips_tmux_cleanup(self, mock_kill_panel_tmux_session, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()

            deleted = store.delete("Missing")

        assert deleted is False
        mock_kill_panel_tmux_session.assert_not_called()

    @patch("gitdirector.integrations.tmux.cleanup_panel_attached_session")
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_rename_cleans_old_normalized_panel_session_and_inner_sessions(
        self,
        mock_kill_panel_tmux_session,
        mock_cleanup_panel_attached_session,
        tmp_path,
    ):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x2", panes={1: "gd/repo/shell/1"})

            renamed = store.rename("Main", "Operations")

        assert renamed is True
        assert store.get("Operations") is not None
        mock_kill_panel_tmux_session.assert_called_once_with("Main")
        mock_cleanup_panel_attached_session.assert_called_once_with("gd/repo/shell/1")

    @patch("gitdirector.integrations.tmux.cleanup_panel_attached_session")
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_rename_preserves_shared_normalized_panel_session(
        self,
        mock_kill_panel_tmux_session,
        mock_cleanup_panel_attached_session,
        tmp_path,
    ):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main!", layout_key="grid_1x2", panes={1: "gd/repo/shell/1"})

            renamed = store.rename("Main!", "main")

        assert renamed is True
        mock_kill_panel_tmux_session.assert_not_called()
        mock_cleanup_panel_attached_session.assert_not_called()

    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_update_pane_persists_closed_state(self, mock_kill_panel_tmux_session, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create(
                "Main",
                layout_key="grid_1x2",
                panes={1: "gd/my-repo/shell/1", 2: "gd/my-repo/shell/2"},
            )

            panel_updated = store.update_pane("Main", 1, None, closed=True)

            reloaded_store = PanelStore()

        assert panel_updated is True
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
    def test_update_pane_keeps_panel_when_last_live_assignment_closes_but_other_slots_remain(
        self,
        mock_kill_panel_tmux_session,
        tmp_path,
    ):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create(
                "Main",
                layout_key="grid_1x2",
                panes={1: "gd/my-repo/shell/1", 2: None},
            )

            panel_updated = store.update_pane("Main", 1, None, closed=True)
            store.reload()

        assert panel_updated is True
        panel = store.get("Main")
        assert panel is not None
        assert panel.panes[1] is None
        assert panel.panes[2] is None
        assert panel.closed_panes == {1}
        mock_kill_panel_tmux_session.assert_not_called()

    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_reconfigure_updates_layout_assignments_and_kills_panel_tmux_session(
        self,
        mock_kill_panel_tmux_session,
        tmp_path,
    ):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create(
                "Main",
                layout_key="grid_1x2",
                panes={1: "gd/my-repo/shell/1", 2: "gd/my-repo/copilot/1"},
            )

            reconfigured = store.reconfigure(
                "Main",
                layout_key="wide_bottom",
                panes={1: "gd/my-repo/shell/1", 2: None, 3: "gd/my-repo/copilot/1"},
            )

            reloaded_store = PanelStore()

        assert reconfigured is True
        panel = store.get("Main")
        assert panel is not None
        assert panel.layout_key == "wide_bottom"
        assert panel.rows == 2
        assert panel.cols == 2
        assert panel.panes == {
            1: "gd/my-repo/shell/1",
            2: None,
            3: "gd/my-repo/copilot/1",
        }
        assert panel.closed_panes == set()
        reloaded_panel = reloaded_store.get("Main")
        assert reloaded_panel is not None
        assert reloaded_panel.layout_key == "wide_bottom"
        assert reloaded_panel.panes == panel.panes
        mock_kill_panel_tmux_session.assert_called_once_with("Main")

    @patch("gitdirector.integrations.tmux.cleanup_panel_attached_session")
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_reconfigure_cleans_sessions_removed_from_new_layout(
        self,
        mock_kill_panel_tmux_session,
        mock_cleanup_panel_attached_session,
        tmp_path,
    ):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create(
                "Main",
                layout_key="grid_1x2",
                panes={1: "gd/repo/shell/1", 2: "gd/repo/copilot/1"},
            )

            reconfigured = store.reconfigure(
                "Main",
                layout_key="grid_1x1",
                panes={1: "gd/repo/shell/1"},
            )

        assert reconfigured is True
        assert mock_cleanup_panel_attached_session.call_args_list == [
            (("gd/repo/shell/1",),),
            (("gd/repo/copilot/1",),),
        ]


class TestPanelStoreCreateCaseCollision:
    """Bug regression: ``PanelStore.create`` rejected case-collisions on
    a panel's tmux session name (matches ``_sanitize_repo_name``-style
    sanitization that ``rename`` and ``load`` both check).
    """

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    def test_create_rejects_collision_with_existing_panel(self, _mock_sync, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            first = store.create(
                "Foo",
                layout_key="grid_1x1",
                panes={1: "gd/repo/shell/1"},
            )
            assert first is not None

            second = store.create(
                "foo",
                layout_key="grid_1x1",
                panes={1: "gd/repo/shell/2"},
            )

        assert second is None, "expected second create to be rejected due to session-name collision"
        names = [p.name for p in store.panels]
        assert names == ["Foo"], f"only the first panel should exist, got {names}"

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    def test_rename_rejects_case_insensitive_collision_with_other_panel(self, _mock_sync, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create(
                "Main",
                layout_key="grid_1x1",
                panes={1: "gd/repo/shell/1"},
            )
            store.create(
                "Other",
                layout_key="grid_1x1",
                panes={1: "gd/repo/shell/2"},
            )

            renamed = store.rename("Main", "OTHER")

        assert renamed is False, "expected rename to be rejected due to case-insensitive collision"
        names = [p.name for p in store.panels]
        assert sorted(names) == ["Main", "Other"], names


class TestReconfigureEdgeCases:
    """Success-path edge cases for ``PanelStore.reconfigure``."""

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_reconfigure_persists_new_panes_on_success(self, _mock_kill, _mock_sync, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x1", panes={1: "gd/repo/shell/1"})

            ok = store.reconfigure("Main", layout_key="grid_1x1", panes={1: "gd/repo/shell/9"})

        assert ok is True
        panel = store.get("Main")
        assert panel is not None
        assert panel.panes[1] == "gd/repo/shell/9"

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_unknown_panel_returns_false(self, _mock_kill, _mock_sync, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()

            ok = store.reconfigure("Ghost", layout_key="grid_1x1", panes={1: None})

        assert ok is False
        assert store.panels == []


class TestPanelStoreKillFailurePath:
    """Bug regressions: ``_kill_panel_sessions`` returning False (the
    panel tmux session still exists) must abort ``reconfigure`` and
    ``rename`` so the YAML doesn't silently drift away from the live
    session name.
    """

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    def test_reconfigure_aborts_when_outer_session_kill_fails(self, _mock_sync, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x1", panes={1: "gd/repo/shell/1"})
            panel = store.get("Main")
            assert panel is not None
            original_panes = panel.panes.copy()
            original_config = store.panels_file.read_text()

            cleanup_calls: list[list[str]] = []

            def tracker(self, session_names):
                cleanup_calls.append(list(session_names))

            with (
                patch.object(PanelStore, "_cleanup_inner_panel_sessions", tracker),
                patch.object(
                    PanelStore,
                    "_kill_panel_sessions",
                    lambda self, _names: False,
                ),
            ):
                ok = store.reconfigure("Main", layout_key="grid_1x1", panes={1: "gd/repo/other/1"})

        assert ok is False
        assert cleanup_calls == [], (
            "cleanup_inner_panel_sessions must not be called when kill failed"
        )
        panel = store.get("Main")
        assert panel is not None
        assert panel.panes == original_panes
        assert store.panels_file.read_text() == original_config

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    def test_rename_reports_failure_when_outer_session_kill_fails(self, _mock_sync, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x1", panes={1: "gd/repo/shell/1"})

            with patch.object(
                PanelStore,
                "_kill_panel_sessions",
                lambda self, _names: False,
            ):
                ok = store.rename("Main", "Renamed")

        assert ok is False, "rename must return False when the old panel session cannot be killed"
        # YAML must not have been rewritten — the panel still owns the old name.
        assert store.get("Main") is not None
        assert store.get("Renamed") is None

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    def test_rename_aborts_cleanup_when_kill_fails(self, _mock_sync, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x1", panes={1: "gd/repo/shell/1"})

            cleanup_calls: list[list[str]] = []

            def tracker(self, session_names):
                cleanup_calls.append(list(session_names))

            with (
                patch.object(PanelStore, "_cleanup_inner_panel_sessions", tracker),
                patch.object(
                    PanelStore,
                    "_kill_panel_sessions",
                    lambda self, _names: False,
                ),
            ):
                store.rename("Main", "Renamed")

        assert cleanup_calls == [], (
            "cleanup must not run when the outer panel session can't be killed"
        )


class TestUpdatePaneSemantics:
    """Bug regression: ``update_pane`` previously returned ``False`` on
    success; the boolean is now ``True`` after a real update.
    """

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_returns_true_on_successful_update(self, _mock_kill, _mock_sync, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x1", panes={1: "gd/repo/old/1"})

            updated = store.update_pane("Main", 1, "gd/repo/new/1")

        assert updated is True

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_returns_false_for_missing_panel(self, _mock_kill, _mock_sync, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()

            updated = store.update_pane("Ghost", 1, "gd/repo/x/1")

        assert updated is False

    @patch("gitdirector.integrations.tmux.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.kill_panel_tmux_session")
    def test_returns_false_when_pane_index_out_of_range(self, _mock_kill, _mock_sync, tmp_path):
        with patch("gitdirector.commands.tui.panels.Path.home", return_value=tmp_path):
            store = PanelStore()
            store.create("Main", layout_key="grid_1x2", panes={1: "gd/repo/a/1"})

            updated = store.update_pane("Main", 5, "gd/repo/x/1")

        assert updated is False


class TestTabStyling:
    def test_tab_headers_use_three_row_height(self):
        assert "#tabs Tabs" in GitDirectorConsole.CSS
        assert "height: 3;" in GitDirectorConsole.CSS

    def test_active_tab_uses_filled_style(self):
        assert "#tabs Tab.-active" in GitDirectorConsole.CSS
        assert "background: $accent;" in GitDirectorConsole.CSS


class TestGitDirectorConsolePanels:
    async def test_empty_panels_show_message_without_table(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        app._panel_store = MagicMock()
        app._panel_store.panels = []

        async with app.run_test(size=(120, 30)) as pilot:
            app._load_panels()
            await pilot.pause()

            table = app.query_one("#panels-table", DataTable)
            empty_message = app.query_one("#no-panels-message", Static)
            assert table.row_count == 0
            assert table.display is False
            assert empty_message.display is True

    def test_three_pane_presets_use_equalized_layout_ratios(self):
        tall_left = resolve_panel_layout("tall_left")
        tall_right = resolve_panel_layout("tall_right")
        wide_top = resolve_panel_layout("wide_top")
        wide_bottom = resolve_panel_layout("wide_bottom")

        assert tall_left.col_ratios is None
        assert tall_right.col_ratios is None
        assert wide_top.row_ratios is None
        assert wide_bottom.row_ratios is None

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
                "┌─┬─┐",
                "│■│□│",
                "├─┼─┤",
                "│□│■│",
                "└─┴─┘",
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
                "┌─┬─┐",
                "│ │□│",
                "│■├─┤",
                "│ │■│",
                "└─┴─┘",
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
            == 5
        )
        assert (
            _panel_row_height(
                Panel(name="Main", rows=2, cols=2, panes={1: None, 2: None, 3: None, 4: None})
            )
            == 7
        )
        assert (
            _panel_row_height(Panel(name="Grid", rows=3, cols=1, panes={1: None, 2: None, 3: None}))
            == 9
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
            == 7
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

    def test_description_edit_binding_uses_d_key(self):
        assert any(
            binding.key == "d" and binding.action == "edit_session_description"
            for binding in GitDirectorConsole.BINDINGS
        )

    def test_tab_switch_hotkeys_hidden_from_footer(self):
        hidden_keys = {binding.key for binding in GitDirectorConsole.BINDINGS if not binding.show}

        assert {"1", "2", "3", "_"}.issubset(hidden_keys)

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

    async def test_resume_to_panels_suppresses_first_stray_new_panel_key(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()

            app._resume_new_panel_guard_until = monotonic() + 60

            await pilot.press("n")
            await pilot.pause()

            assert not isinstance(app.screen, CreatePanelScreen)

            app._resume_new_panel_guard_until = 0.0

            await pilot.press("n")
            await pilot.pause()

            assert isinstance(app.screen, CreatePanelScreen)

    def test_suspend_and_attach_arms_new_panel_guard(self):
        app = GitDirectorConsole()
        app._active_tab = "panels"
        table = MagicMock()
        app.query_one = MagicMock(return_value=table)
        app._pause_session_status_tracking = MagicMock()
        app._resume_session_status_tracking = MagicMock()
        app._monitor = MagicMock()
        app.suspend = MagicMock(
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock(return_value=False))
        )

        before = monotonic()
        with patch("gitdirector.integrations.tmux.attach_tmux_session"):
            with patch("sys.stdout"):
                with patch("termios.tcflush"):
                    app._suspend_and_attach("test")

        assert app._resume_new_panel_guard_until > before

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

    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=False)
    def test_rename_rejects_normalized_persisted_panel_session_collision(
        self, _mock_session_exists
    ):
        app = GitDirectorConsole()
        app._panel_store = MagicMock()
        app._panel_store.get.return_value = None
        app._panel_store.panels = [
            Panel(name="Main", rows=1, cols=1),
            Panel(name="Old", rows=1, cols=1),
        ]
        app._update_status = MagicMock()

        app._do_rename_panel("Old", "MAIN!")

        app._panel_store.rename.assert_not_called()
        app._update_status.assert_called_once_with(
            "Panel 'MAIN!' conflicts with tmux session name 'gd/panel/main'"
        )

    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=True)
    def test_rename_rejects_live_panel_session_collision(self, _mock_session_exists):
        app = GitDirectorConsole()
        app._panel_store = MagicMock()
        app._panel_store.get.return_value = None
        app._panel_store.panels = [Panel(name="Old", rows=1, cols=1)]
        app._update_status = MagicMock()

        app._do_rename_panel("Old", "Main")

        app._panel_store.rename.assert_not_called()
        app._update_status.assert_called_once_with("TMUX session 'gd/panel/main' already exists")

    def test_handle_panel_action_delete_pushes_confirmation(self):
        app = GitDirectorConsole()
        app.push_screen = MagicMock()

        app._handle_panel_action("delete", "Main")

        assert isinstance(app.push_screen.call_args.args[0], ConfirmScreen)

    @patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        return_value=[
            {
                "session_name": "gd/my-repo/shell/1",
                "repo": "my-repo",
                "purpose": "shell",
            },
            {
                "session_name": "gd/my-repo/copilot/1",
                "repo": "my-repo",
                "purpose": "copilot",
            },
        ],
    )
    def test_handle_panel_action_reconfigure_pushes_create_panel_screen(self, _mock_sessions):
        app = GitDirectorConsole()
        app.push_screen = MagicMock()
        app._panel_store = MagicMock()
        app._panel_store.get.return_value = Panel(
            name="Main",
            rows=2,
            cols=2,
            panes={1: "gd/my-repo/shell/1", 2: None, 3: "gd/my-repo/copilot/1"},
            layout_key="wide_bottom",
        )

        app._handle_panel_action("reconfigure", "Main")

        screen = app.push_screen.call_args.args[0]
        assert isinstance(screen, CreatePanelScreen)
        assert screen._editing is True
        assert screen._selected_layout_key == "wide_bottom"
        assert screen._pane_assignments[1] == "gd/my-repo/shell/1"
        assert screen._pane_assignments[3] == "gd/my-repo/copilot/1"

    def test_handle_reconfigure_panel_updates_store_and_opens_panel(self):
        app = GitDirectorConsole()
        app._panel_store = MagicMock()
        app._panel_store.reconfigure.return_value = True
        app._load_panels = MagicMock()
        app._open_panel = MagicMock()
        app._update_status = MagicMock()

        app._handle_reconfigure_panel(
            "Main",
            ("Main", "wide_bottom", {1: "gd/my-repo/shell/1", 2: None, 3: None}),
        )

        app._panel_store.reconfigure.assert_called_once_with(
            "Main",
            panes={1: "gd/my-repo/shell/1", 2: None, 3: None},
            layout_key="wide_bottom",
        )
        app._load_panels.assert_called_once_with()
        app._open_panel.assert_called_once_with("Main")
        app._update_status.assert_not_called()

    @patch("gitdirector.integrations.tmux.core._protect_session")
    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=True)
    def test_open_panel_attaches_immediately(self, _mock_exists, mock_protect):
        app = GitDirectorConsole()
        app._panel_store = MagicMock()
        app._panel_store.get.return_value = Panel(name="Main", rows=1, cols=1, panes={1: None})
        app._suspend_and_attach = MagicMock()

        app._open_panel("Main")

        mock_protect.assert_called_once_with("gd/panel/main")
        app._suspend_and_attach.assert_called_once_with("gd/panel/main", row_key="Main")

    @patch(
        "gitdirector.integrations.tmux.rebuild_panel_tmux_session",
        return_value="gd/panel/main",
    )
    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=False)
    def test_open_panel_rebuilds_without_inner_delay(self, _mock_exists, mock_rebuild):
        app = GitDirectorConsole()
        app._panel_store = MagicMock()
        panel = Panel(name="Main", rows=1, cols=1, panes={1: "gd/alpha/shell/1"})
        app._panel_store.get.return_value = panel
        app._suspend_and_attach = MagicMock()

        app._open_panel("Main")

        mock_rebuild.assert_called_once_with(
            "Main",
            1,
            1,
            {1: "gd/alpha/shell/1"},
            closed_panes=set(),
            layout_key="grid_1x1",
        )
        app._suspend_and_attach.assert_called_once_with("gd/panel/main", row_key="Main")

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=[
            "gd/alpha/shell/1",
            "gd/beta/copilot/1",
            "gd/ops/shell/1",
            "gd/gamma/shell/1",
        ],
    )
    async def test_load_panels_renders_consistent_spacing_on_each_row(self, _mock_list):
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

            assert len(table.columns) == 6
            assert table.row_count == 3
            assert table.get_cell("Main", app._panels_col_keys[0]) == "\n".join(
                [
                    "",
                    "┌─┬─┐",
                    "│■│□│",
                    "├─┼─┤",
                    "│□│■│",
                    "└─┴─┘",
                ]
            )
            assert table.get_cell("Main", app._panels_col_keys[1]) == "\nMain"
            assert table.get_cell("Main", app._panels_col_keys[2]) == "\ngd/panel/main"
            assert table.get_cell("Main", app._panels_col_keys[3]) == "\n2×2"
            assert table.get_cell("Main", app._panels_col_keys[4]) == "\n2/4"
            assert table.get_cell("Main", app._panels_col_keys[5]) == "\n[green]● active[/green]"
            assert table.get_row_height("Main") == 7
            assert table.get_cell("Ops", app._panels_col_keys[0]) == "\n".join(
                [
                    "",
                    "┌─┬─┬─┐",
                    "│□│■│□│",
                    "└─┴─┴─┘",
                ]
            )
            assert table.get_cell("Ops", app._panels_col_keys[1]) == "\nOps"
            assert table.get_cell("Ops", app._panels_col_keys[2]) == "\ngd/panel/ops"
            assert table.get_cell("Ops", app._panels_col_keys[3]) == "\n1×3"
            assert table.get_cell("Ops", app._panels_col_keys[4]) == "\n1/3"
            assert table.get_cell("Ops", app._panels_col_keys[5]) == "\n[green]● active[/green]"
            assert table.get_row_height("Ops") == 5
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
            assert table.get_cell("Studio", app._panels_col_keys[5]) == "\n[green]● active[/green]"
            assert table.get_row_height("Studio") == 9

    @patch("gitdirector.integrations.tmux.core._list_sessions", return_value=[])
    async def test_load_panels_counts_only_live_sessions_in_panes_column(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        panel = Panel(
            name="Main",
            rows=1,
            cols=3,
            panes={1: "gd/alpha/shell/1", 2: "gd/stale/shell/1", 3: None},
        )
        app._panel_store = MagicMock()
        app._panel_store.panels = [panel]

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._load_panels()
            table = app.query_one("#panels-table", DataTable)

            assert table.get_cell("Main", app._panels_col_keys[4]) == "\n0/3"
            assert table.get_cell("Main", app._panels_col_keys[5]) == "\n[dim]○ empty[/dim]"

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=["gd/alpha/shell/1"],
    )
    async def test_load_panels_renders_stale_sessions_as_open_squares(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        panel = Panel(
            name="Main",
            rows=1,
            cols=3,
            panes={1: "gd/alpha/shell/1", 2: "gd/stale/shell/1", 3: None},
        )
        app._panel_store = MagicMock()
        app._panel_store.panels = [panel]

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._load_panels()
            table = app.query_one("#panels-table", DataTable)

            assert table.get_cell("Main", app._panels_col_keys[0]) == "\n".join(
                [
                    "",
                    "┌─┬─┬─┐",
                    "│■│□│□│",
                    "└─┴─┴─┘",
                ]
            )
            assert table.get_cell("Main", app._panels_col_keys[4]) == "\n1/3"
            assert table.get_cell("Main", app._panels_col_keys[5]) == "\n[green]● active[/green]"

    @patch(
        "gitdirector.integrations.tmux.core._list_sessions",
        return_value=["gd/alpha/shell/1", "gd/ops/shell/1"],
    )
    async def test_panel_refresh_preserves_selected_row(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])
        app._panel_store = MagicMock()
        app._panel_store.panels = [
            Panel(
                name="Main",
                rows=2,
                cols=2,
                panes={1: "gd/alpha/shell/1", 2: None, 3: None, 4: None},
            ),
            Panel(
                name="Ops",
                rows=1,
                cols=3,
                panes={1: None, 2: "gd/ops/shell/1", 3: None},
            ),
            Panel(
                name="Studio",
                rows=1,
                cols=2,
                panes={1: None, 2: None},
            ),
        ]

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._active_tab = "panels"
            app._load_panels()
            await pilot.pause()

            table = app.query_one("#panels-table", DataTable)
            table.move_cursor(row=1)
            selected_before = table.coordinate_to_cell_key(table.cursor_coordinate).row_key

            app._apply_panels_filter_and_sort({"gd/alpha/shell/1", "gd/ops/shell/1"})
            await pilot.pause()

            selected_after = table.coordinate_to_cell_key(table.cursor_coordinate).row_key

            assert str(selected_before.value) == "Ops"
            assert str(selected_after.value) == "Ops"
            assert table.cursor_coordinate.row == 1

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


class TestPanelOpenFailureIsContained:
    """A panel that cannot be built must not take the TUI down with it.

    Building a panel drives a long sequence of tmux commands, and the tmux
    server can die partway through. That used to propagate straight out of
    the event loop.
    """

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_build_failure_reports_and_keeps_running(self, _mock_sessions):
        app = GitDirectorConsole()
        app.manager = _mock_manager([])

        panel = Panel(name="work", rows=1, cols=1, panes={1: None})
        app._panel_store = MagicMock()
        app._panel_store.get.return_value = panel

        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            with (
                patch(
                    "gitdirector.integrations.tmux._session_exists",
                    return_value=False,
                ),
                patch(
                    "gitdirector.integrations.tmux.rebuild_panel_tmux_session",
                    side_effect=RuntimeError("the tmux server exited"),
                ),
                patch.object(app, "_suspend_and_attach") as mock_attach,
            ):
                app._open_panel("work")
                await pilot.pause()

            mock_attach.assert_not_called()
            assert app.is_running
            assert "failed to open" in app._status_message
            assert "the tmux server exited" in app._status_message
