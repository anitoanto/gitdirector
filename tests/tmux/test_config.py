"""Theme and config tests for tmux panel behavior."""

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from gitdirector.integrations.tmux import sync_panel_tmux_config
from gitdirector.integrations.tmux.core import (
    _default_terminal,
    _live_panel_sessions,
    _live_session_windows,
    _load_panel_tmux_config,
    _panel_border_format,
    _panel_pane_title,
    _panel_tmux_config,
    _panel_window_status_format,
    _resolved_panel_theme_name,
    _session_tmux_config,
)
from gitdirector.integrations.tmux.panels import _configure_panel_window, _panel_pane_command
from gitdirector.ui_theme import resolve_panel_theme

from ._shared import split_chained_tmux


class TestDefaultTerminal:
    def setup_method(self):
        _default_terminal.cache_clear()

    def teardown_method(self):
        _default_terminal.cache_clear()

    @patch("subprocess.run", return_value=MagicMock(returncode=0))
    def test_uses_tmux_terminfo_when_present(self, _mock_run):
        assert _default_terminal() == "tmux-256color"

    @patch("subprocess.run", return_value=MagicMock(returncode=1))
    def test_falls_back_without_tmux_terminfo(self, _mock_run):
        assert _default_terminal() == "screen-256color"

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_falls_back_without_infocmp(self, _mock_run):
        assert _default_terminal() == "screen-256color"


class TestPanelPaneTitles:
    def test_panel_pane_title_uses_session_slug(self):
        assert _panel_pane_title("gd/my-repo/copilot/3") == "copilot my-repo/3"

    def test_panel_pane_title_marks_empty_slots(self):
        assert _panel_pane_title(None) == "empty"

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
        assert f"default-terminal {_default_terminal()}" in config
        assert f"set-environment -t =gd/panel/main: TERM {_default_terminal()}" in config
        assert "'terminal-features[90]' '*:RGB'" in config
        assert "'terminal-overrides[90]' '*:Tc'" in config
        assert "set-environment -r -t =gd/panel/main: NO_COLOR" in config
        assert "set-environment -t =gd/panel/main: COLORTERM truecolor" in config
        assert "set-environment -t =gd/panel/main: FORCE_COLOR 3" in config
        assert "set-environment -t =gd/panel/main: CLICOLOR_FORCE 1" in config
        assert "set-environment -t =gd/panel/main: CLAUDE_CODE_TMUX_TRUECOLOR 1" in config
        assert "set-option -t =gd/panel/main: mouse on" in config
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

    @patch(
        "gitdirector.integrations.tmux.core._current_window_target",
        return_value="gd/my-repo/shell/1:2",
    )
    def test_session_tmux_config_themes_regular_sessions(self, _mock_target):
        theme = resolve_panel_theme("rose-pine")
        config = _session_tmux_config("gd/my-repo/shell/1", "rose-pine")

        assert "set-option -t =gd/my-repo/shell/1: status-left" in config
        assert f"default-terminal {_default_terminal()}" in config
        assert "'terminal-features[90]' '*:RGB'" in config
        assert "'terminal-overrides[90]' '*:Tc'" in config
        assert "set-environment -r -t =gd/my-repo/shell/1: NO_COLOR" in config
        assert "set-environment -t =gd/my-repo/shell/1: COLORTERM truecolor" in config
        assert "set-environment -t =gd/my-repo/shell/1: FORCE_COLOR 3" in config
        assert "set-environment -t =gd/my-repo/shell/1: CLICOLOR_FORCE 1" in config
        assert "set-environment -t =gd/my-repo/shell/1: CLAUDE_CODE_TMUX_TRUECOLOR 1" in config
        assert "set-option -t =gd/my-repo/shell/1: mouse on" in config
        assert "set-option -t =gd/my-repo/shell/1: set-clipboard on" in config
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
        assert "set-clipboard on" in config

    def test_panel_tmux_config_emits_set_clipboard_on(self):
        with patch(
            "gitdirector.integrations.tmux.core._current_window_target",
            return_value="gd/panel/main:0",
        ):
            config = _panel_tmux_config("Main", "gd/panel/main", "rose-pine")
        assert "set-option -t =gd/panel/main: set-clipboard on" in config

    @patch("subprocess.run")
    def test_load_panel_tmux_config_writes_and_sources_file(self, mock_run, tmp_path):
        config_path = tmp_path / "tmux_design.conf"

        with patch(
            "gitdirector.integrations.tmux.core._tmux_design_config_path", return_value=config_path
        ):
            written_path = _load_panel_tmux_config("Main", "gd/panel/main", "nord")

        assert written_path == config_path
        assert config_path.exists()
        content = config_path.read_text()
        assert "set-option -t =gd/panel/main: status-position bottom" in content
        assert "set-window-option -t '=gd/panel/main:^' pane-border-lines heavy" in content
        mock_run.assert_called_once_with(
            ["tmux", "source-file", str(config_path)], capture_output=True, env=ANY, timeout=ANY
        )

    @patch("gitdirector.commands.tui.panels.PanelStore")
    def test_live_panel_sessions_filters_running_sessions(self, mock_store):
        mock_store.return_value.panels = [
            SimpleNamespace(name="Main"),
            SimpleNamespace(name="Other"),
        ]

        assert _live_panel_sessions({"gd/panel/main": "gd/panel/main:0"}) == [
            ("Main", "gd/panel/main")
        ]

    @patch("gitdirector.integrations.tmux.core._run_tmux")
    def test_live_session_windows_maps_each_session_to_its_current_window(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="gd/my-repo/shell/1\t2\nother\t0\n")

        assert _live_session_windows() == {
            "gd/my-repo/shell/1": "gd/my-repo/shell/1:2",
            "other": "other:0",
        }

    @patch("gitdirector.integrations.tmux.core._run_tmux")
    def test_live_session_windows_is_empty_without_a_server(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        assert _live_session_windows() == {}

    @patch("subprocess.run")
    def test_sync_panel_tmux_config_writes_all_live_sessions(self, mock_run, tmp_path):
        config_path = tmp_path / "tmux_design.conf"
        with patch(
            "gitdirector.integrations.tmux.core._tmux_design_config_path", return_value=config_path
        ):
            with patch(
                "gitdirector.integrations.tmux.core._live_session_windows",
                return_value={"gd/panel/main": "gd/panel/main:0", "gd/panel/me2": "gd/panel/me2:0"},
            ):
                with patch(
                    "gitdirector.integrations.tmux.core._live_panel_sessions",
                    return_value=[("Main", "gd/panel/main"), ("Me2", "gd/panel/me2")],
                ):
                    written_path = sync_panel_tmux_config("nord")

        assert written_path == config_path
        content = config_path.read_text()
        assert "# theme: nord" in content
        assert "set-option -t =gd/panel/main: status-position bottom" in content
        assert "set-option -t =gd/panel/me2: status-position bottom" in content
        mock_run.assert_called_once_with(
            ["tmux", "source-file", str(config_path)], capture_output=True, env=ANY, timeout=ANY
        )

    @patch("subprocess.run")
    def test_sync_panel_tmux_config_writes_regular_sessions(self, mock_run, tmp_path):
        config_path = tmp_path / "tmux_design.conf"
        with patch(
            "gitdirector.integrations.tmux.core._tmux_design_config_path", return_value=config_path
        ):
            with patch(
                "gitdirector.integrations.tmux.core._live_session_windows",
                return_value={"gd/my-repo/shell/1": "gd/my-repo/shell/1:2", "misc": "misc:0"},
            ):
                with patch(
                    "gitdirector.integrations.tmux.core._live_panel_sessions", return_value=[]
                ):
                    written_path = sync_panel_tmux_config("nord")

        assert written_path == config_path
        content = config_path.read_text()
        assert "# theme: nord" in content
        assert "set-option -t =gd/my-repo/shell/1: status-left" in content
        assert "SHELL" in content
        assert "set-window-option -t =gd/my-repo/shell/1:2 pane-border-style" in content
        assert "misc" not in content
        mock_run.assert_called_once_with(
            ["tmux", "source-file", str(config_path)], capture_output=True, env=ANY, timeout=ANY
        )

    @patch("subprocess.run")
    def test_sync_panel_tmux_config_skips_source_when_no_live_sessions(self, mock_run, tmp_path):
        config_path = tmp_path / "tmux_design.conf"
        with patch(
            "gitdirector.integrations.tmux.core._tmux_design_config_path", return_value=config_path
        ):
            with patch("gitdirector.integrations.tmux.core._live_session_windows", return_value={}):
                with patch(
                    "gitdirector.integrations.tmux.core._live_panel_sessions", return_value=[]
                ):
                    written_path = sync_panel_tmux_config("rose-pine")

        assert written_path == config_path
        content = config_path.read_text()
        assert "# theme: rose-pine" in content
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_sync_panel_tmux_config_ignores_source_file_failure(self, mock_run, tmp_path):
        config_path = tmp_path / "tmux_design.conf"
        mock_run.side_effect = __import__("subprocess").CalledProcessError(
            1, ["tmux", "source-file", str(config_path)]
        )

        with patch(
            "gitdirector.integrations.tmux.core._tmux_design_config_path", return_value=config_path
        ):
            with patch(
                "gitdirector.integrations.tmux.core._live_session_windows",
                return_value={"gd/my-repo/shell/1": "gd/my-repo/shell/1:0"},
            ):
                with patch(
                    "gitdirector.integrations.tmux.core._live_panel_sessions", return_value=[]
                ):
                    written_path = sync_panel_tmux_config("rose-pine")

        assert written_path == config_path
        assert config_path.exists()
        mock_run.assert_called_once_with(
            ["tmux", "source-file", str(config_path)], capture_output=True, env=ANY, timeout=ANY
        )

    @patch("subprocess.run")
    def test_configure_panel_window_sets_titles_with_slugs(self, mock_run):
        theme = resolve_panel_theme("nord")
        _configure_panel_window(
            "gd/panel/main",
            ["%1", "%2"],
            {1: "gd/my-repo/copilot/3", 2: None},
            "nord",
        )

        mock_run.assert_called_once()
        commands = split_chained_tmux(mock_run.call_args.args[0])

        assert ["tmux", "select-pane", "-t", "%1", "-T", "copilot my-repo/3"] in commands
        assert ["tmux", "select-pane", "-t", "%2", "-T", "empty"] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:^",
            "pane-border-lines",
            "heavy",
        ] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:^",
            "remain-on-exit",
            "on",
        ] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:^",
            "pane-border-style",
            f"fg={theme.border_inactive}",
        ] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:^",
            "pane-active-border-style",
            f"fg={theme.border_active}",
        ] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:^",
            "pane-border-format",
            _panel_border_format("nord"),
        ] in commands


# ---------------------------------------------------------------------------
# Subprocess-based functions
# ---------------------------------------------------------------------------
