"""Theme and config tests for tmux panel behavior."""

from types import SimpleNamespace
from unittest.mock import patch

from gitdirector.integrations.tmux import (
    _configure_panel_window,
    _embedded_tmux_attach_command,
    _live_panel_sessions,
    _live_repo_tmux_sessions,
    _load_panel_tmux_config,
    _panel_border_format,
    _panel_pane_command,
    _panel_pane_title,
    _panel_tmux_config,
    _panel_window_status_format,
    _resolved_panel_theme_name,
    _session_tmux_config,
    sync_panel_tmux_config,
)
from gitdirector.ui_theme import resolve_panel_theme


class TestPanelPaneTitles:
    def test_panel_pane_title_uses_session_slug(self):
        assert _panel_pane_title(1, "gd/my-repo/copilot/3") == "copilot my-repo/3"

    def test_panel_pane_title_marks_empty_slots(self):
        assert _panel_pane_title(2, None) == "empty"

    def test_panel_border_format_styles_badge_separately(self):
        theme = resolve_panel_theme("rose-pine")
        border_format = _panel_border_format("rose-pine")

        assert "#{pane_index}" in border_format
        assert " #{pane_title} " in border_format
        assert f"bg={theme.badge_active_bg}" in border_format
        assert f"bg={theme.label_active_bg}" in border_format

    def test_panel_border_format_can_hide_pane_number(self):
        border_format = _panel_border_format("rose-pine", show_pane_number=False)

        assert "#{pane_index}" not in border_format
        assert "#{pane_title}" in border_format

    def test_panel_window_status_format_uses_active_pane(self):
        assert _panel_window_status_format() == " #{pane_index}:#{pane_title} "

    @patch("gitdirector.integrations.tmux.core.Config")
    def test_panel_border_format_defaults_to_config_theme(self, mock_config):
        mock_config.return_value.theme = "nord"
        theme = resolve_panel_theme("nord")

        border_format = _panel_border_format()

        assert f"bg={theme.badge_active_bg}" in border_format
        assert f"bg={theme.label_active_bg}" in border_format

    @patch("gitdirector.integrations.tmux.core.Config")
    def test_resolved_panel_theme_name_uses_config(self, mock_config):
        mock_config.return_value.theme = "gruvbox"

        assert _resolved_panel_theme_name() == "gruvbox"

    def test_panel_tmux_config_themes_bottom_status_line(self):
        theme = resolve_panel_theme("rose-pine")
        config = _panel_tmux_config("Main", "gd/panel/main", "rose-pine")

        assert "set-option -t =gd/panel/main: status-position bottom" in config
        assert "set-option -t =gd/panel/main: status-left" in config
        assert "set-option -t =gd/panel/main: status-right" in config
        assert "window-status-current-format ' #{pane_index}:#{pane_title} '" in config
        assert f'message-style "fg={theme.badge_active_fg},bg={theme.badge_active_bg}"' in config
        assert (
            f'window-status-current-style "fg={theme.badge_active_fg},bg={theme.badge_active_bg},bold"'
            in config
        )

    def test_panel_pane_command_hides_session_status_while_attached(self):
        command = _panel_pane_command("Main", 1, "gd/my-repo/copilot/3")

        assert "tmux new-session -d -t =gd/my-repo/copilot/3 -s" not in command
        assert "tmux set-option -q -t =gd/my-repo/copilot/3: status off" in command
        assert "tmux attach-session -t =gd/my-repo/copilot/3" in command
        assert "SESSION CLOSED" in command
        assert "Once all panes are closed, this panel will autodelete" not in command
        assert "Reopen the panel from GitDirector to attach again." not in command

    def test_panel_pane_command_shows_closed_message_for_closed_empty_pane(self):
        command = _panel_pane_command("Main", 1, None, closed=True)

        assert "SESSION CLOSED" in command
        assert "Once all panes are closed, this panel will autodelete" not in command
        assert "Pane 1: unassigned" not in command

    def test_embedded_tmux_attach_command_reapplies_session_chrome_when_not_in_panel(self):
        command = _embedded_tmux_attach_command("gd/my-repo/copilot/3")

        assert command.startswith("sh -c ")
        assert "tmux set-option -t =gd/my-repo/copilot/3: status-position bottom" in command
        assert "tmux set-option -t =gd/my-repo/copilot/3: status-left" in command
        assert "tmux set-option -q -t =gd/my-repo/copilot/3: status off" not in command
        assert 'tmux set-window-option -q -t "$panel_window" pane-border-status off' not in command
        assert "tmux attach-session -t =gd/my-repo/copilot/3" in command

    @patch(
        "gitdirector.integrations.tmux.core._current_window_target",
        return_value="gd/my-repo/shell/1:2",
    )
    def test_session_tmux_config_themes_regular_sessions(self, _mock_target):
        theme = resolve_panel_theme("rose-pine")
        config = _session_tmux_config("gd/my-repo/shell/1", "rose-pine")

        assert "set-option -t =gd/my-repo/shell/1: status-left" in config
        assert "SHELL" in config
        assert "my-repo/shell/1" in config
        assert "window-status-current-format ' #I:#W '" in config
        assert "set-window-option -t =gd/my-repo/shell/1:2 pane-border-style" in config
        assert f'pane-active-border-style "fg={theme.border_active}"' in config
        assert "pane-border-lines" not in config
        assert "pane-border-status top" not in config
        assert "pane-border-format" not in config

    @patch(
        "gitdirector.integrations.tmux.core._current_window_target",
        return_value="gd/my-repo/copilot/1:0",
    )
    def test_session_tmux_config_themes_agent_sessions(self, _mock_target):
        config = _session_tmux_config("gd/my-repo/copilot/1", "rose-pine")

        assert "COPILOT" in config
        assert "my-repo/copilot/1" in config
        assert "pane-border-status top" not in config

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_load_panel_tmux_config_writes_and_sources_file(self, mock_run, tmp_path):
        config_path = tmp_path / "gd-tmux.conf"

        with patch(
            "gitdirector.integrations.tmux.core._gd_tmux_config_path", return_value=config_path
        ):
            written_path = _load_panel_tmux_config("Main", "gd/panel/main", "nord")

        assert written_path == config_path
        assert config_path.exists()
        content = config_path.read_text()
        assert "set-option -t =gd/panel/main: status-position bottom" in content
        assert "set-window-option -t =gd/panel/main:0 pane-border-lines heavy" in content
        mock_run.assert_called_once_with(["tmux", "source-file", str(config_path)], check=True)

    @patch("gitdirector.integrations.tmux.core._session_exists", side_effect=[True, False])
    @patch("gitdirector.commands.tui.panels.PanelStore")
    def test_live_panel_sessions_filters_running_sessions(self, mock_store, _mock_exists):
        mock_store.return_value.panels = [
            SimpleNamespace(name="Main"),
            SimpleNamespace(name="Other"),
        ]

        assert _live_panel_sessions() == [("Main", "gd/panel/main")]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_sync_panel_tmux_config_writes_all_live_sessions(self, mock_run, tmp_path):
        config_path = tmp_path / "gd-tmux.conf"
        with patch(
            "gitdirector.integrations.tmux.core._gd_tmux_config_path", return_value=config_path
        ):
            with patch(
                "gitdirector.integrations.tmux.core._live_panel_sessions",
                return_value=[("Main", "gd/panel/main"), ("Me2", "gd/panel/me2")],
            ):
                with patch(
                    "gitdirector.integrations.tmux.core._live_repo_tmux_sessions", return_value=[]
                ):
                    written_path = sync_panel_tmux_config("nord")

        assert written_path == config_path
        content = config_path.read_text()
        assert "# theme: nord" in content
        assert "set-option -t =gd/panel/main: status-position bottom" in content
        assert "set-option -t =gd/panel/me2: status-position bottom" in content
        mock_run.assert_called_once_with(["tmux", "source-file", str(config_path)], check=True)

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_sync_panel_tmux_config_writes_regular_sessions(self, mock_run, tmp_path):
        config_path = tmp_path / "gd-tmux.conf"
        with patch(
            "gitdirector.integrations.tmux.core._gd_tmux_config_path", return_value=config_path
        ):
            with patch("gitdirector.integrations.tmux.core._live_panel_sessions", return_value=[]):
                with patch(
                    "gitdirector.integrations.tmux.core._live_repo_tmux_sessions",
                    return_value=["gd/my-repo/shell/1"],
                ):
                    with patch(
                        "gitdirector.integrations.tmux.core._current_window_target",
                        return_value="gd/my-repo/shell/1:2",
                    ):
                        written_path = sync_panel_tmux_config("nord")

        assert written_path == config_path
        content = config_path.read_text()
        assert "# theme: nord" in content
        assert "set-option -t =gd/my-repo/shell/1: status-left" in content
        assert "SHELL" in content
        assert "set-window-option -t =gd/my-repo/shell/1:2 pane-border-style" in content
        mock_run.assert_called_once_with(["tmux", "source-file", str(config_path)], check=True)

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_sync_panel_tmux_config_skips_source_when_no_live_sessions(self, mock_run, tmp_path):
        config_path = tmp_path / "gd-tmux.conf"
        with patch(
            "gitdirector.integrations.tmux.core._gd_tmux_config_path", return_value=config_path
        ):
            with patch("gitdirector.integrations.tmux.core._live_panel_sessions", return_value=[]):
                with patch(
                    "gitdirector.integrations.tmux.core._live_repo_tmux_sessions", return_value=[]
                ):
                    written_path = sync_panel_tmux_config("rose-pine")

        assert written_path == config_path
        content = config_path.read_text()
        assert "# theme: rose-pine" in content
        mock_run.assert_not_called()

    @patch(
        "gitdirector.integrations.tmux.core.list_all_gd_sessions",
        side_effect=Exception("tmux error"),
    )
    def test_live_repo_tmux_sessions_handles_listing_error(self, _mock_list):
        assert _live_repo_tmux_sessions() == []

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_sync_panel_tmux_config_ignores_source_file_failure(self, mock_run, tmp_path):
        config_path = tmp_path / "gd-tmux.conf"
        mock_run.side_effect = __import__("subprocess").CalledProcessError(
            1, ["tmux", "source-file", str(config_path)]
        )

        with patch(
            "gitdirector.integrations.tmux.core._gd_tmux_config_path", return_value=config_path
        ):
            with patch("gitdirector.integrations.tmux.core._live_panel_sessions", return_value=[]):
                with patch(
                    "gitdirector.integrations.tmux.core._live_repo_tmux_sessions",
                    return_value=["gd/my-repo/shell/1"],
                ):
                    with patch(
                        "gitdirector.integrations.tmux.core._current_window_target",
                        return_value="gd/my-repo/shell/1:0",
                    ):
                        written_path = sync_panel_tmux_config("rose-pine")

        assert written_path == config_path
        assert config_path.exists()
        mock_run.assert_called_once_with(["tmux", "source-file", str(config_path)], check=True)

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_configure_panel_window_sets_titles_with_slugs(self, mock_run):
        theme = resolve_panel_theme("nord")
        _configure_panel_window(
            "gd/panel/main",
            ["%1", "%2"],
            {1: "gd/my-repo/copilot/3", 2: None},
            "nord",
        )

        commands = [call.args[0] for call in mock_run.call_args_list]

        assert ["tmux", "select-pane", "-t", "%1", "-T", "copilot my-repo/3"] in commands
        assert ["tmux", "select-pane", "-t", "%2", "-T", "empty"] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:0",
            "pane-border-lines",
            "heavy",
        ] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:0",
            "pane-border-style",
            f"fg={theme.border_inactive}",
        ] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:0",
            "pane-active-border-style",
            f"fg={theme.border_active}",
        ] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:0",
            "pane-border-format",
            _panel_border_format("nord"),
        ] in commands


# ---------------------------------------------------------------------------
# Subprocess-based functions
# ---------------------------------------------------------------------------
