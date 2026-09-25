"""Theme and config tests for tmux panel behavior."""

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from gitdirector.integrations.tmux import sync_panel_tmux_config
from gitdirector.integrations.tmux.core import (
    _default_terminal,
    _live_panel_sessions,
    _live_session_windows,
    _panel_pane_title,
    _panel_tmux_config,
    _panel_window_status_format,
    _session_header_format,
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

    def test_session_header_shows_label_and_a_slot_badge_only_in_panels(self):
        theme = resolve_panel_theme("rose-pine")
        header = _session_header_format("gd/my-repo/copilot/3", "rose-pine")

        assert " copilot my-repo/3 " in header
        assert "#{?#{@gd_slot}," in header
        assert f"bg={theme.badge_active_bg}] #{{@gd_slot}} " in header
        assert f"bg={theme.label_active_bg}" in header

    @patch("gitdirector.integrations.tmux.core.Config")
    def test_session_header_defaults_to_config_theme(self, mock_config):
        mock_config.return_value.theme = "nord"
        theme = resolve_panel_theme("nord")

        assert f"bg={theme.label_active_bg}" in _session_header_format("gd/r/shell/1")

    def test_panel_window_status_format_uses_active_pane(self):
        assert _panel_window_status_format() == " #{pane_index}:#{pane_title} "

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
        # Every gitdirector session carries its own header.
        assert "set-window-option -t =gd/my-repo/shell/1:2 pane-border-status top" in config
        assert "set-window-option -t =gd/my-repo/shell/1:2 pane-border-lines heavy" in config
        assert "pane-border-format" in config and " shell my-repo/1 " in config
        assert "set-option -t =gd/my-repo/shell/1: status on" in config
        assert "set-option -t =gd/my-repo/shell/1: detach-on-destroy on" in config
        assert "set-hook -t =gd/my-repo/shell/1: after-new-window" in config

    @patch(
        "gitdirector.integrations.tmux.core._current_window_target",
        return_value="gd/my-repo/copilot/1:0",
    )
    def test_session_tmux_config_themes_agent_sessions(self, _mock_target):
        config = _session_tmux_config("gd/my-repo/copilot/1", "rose-pine")

        assert "COPILOT" in config
        assert "my-repo/copilot/1" in config
        assert "pane-border-status top" in config
        assert "set-clipboard on" in config

    def test_status_line_truncates_badge_and_label_with_an_ellipsis(self):
        config = _panel_tmux_config("Main", "gd/panel/main", "rose-pine")
        assert "set-option -t =gd/panel/main: @gd_badge PANEL" in config
        assert "set-option -t =gd/panel/main: @gd_label Main" in config
        assert "set-option -t =gd/panel/main: status-left-length 1000" in config
        assert "#{=/24/…:@gd_badge}" in config
        assert "…:@gd_label}" in config and "#{window_width}" in config

    def test_panel_window_draws_no_title_row_of_its_own(self):
        config = _panel_tmux_config("Main", "gd/panel/main", "rose-pine")
        assert "pane-border-status off" in config
        assert "pane-border-format" not in config

    def test_panel_tmux_config_emits_set_clipboard_on(self):
        with patch(
            "gitdirector.integrations.tmux.core._current_window_target",
            return_value="gd/panel/main:0",
        ):
            config = _panel_tmux_config("Main", "gd/panel/main", "rose-pine")
        assert "set-option -t =gd/panel/main: set-clipboard on" in config

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
            ["tmux", "source-file", str(config_path)],
            capture_output=True,
            env=ANY,
            cwd=ANY,
            timeout=ANY,
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
            ["tmux", "source-file", str(config_path)],
            capture_output=True,
            env=ANY,
            cwd=ANY,
            timeout=ANY,
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
            ["tmux", "source-file", str(config_path)],
            capture_output=True,
            env=ANY,
            cwd=ANY,
            timeout=ANY,
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
        assert ["tmux", "set-option", "-p", "-t", "%2", "@gd_slot", "2"] in commands
        assert [
            "tmux",
            "set-window-option",
            "-t",
            "=gd/panel/main:^",
            "pane-border-status",
            "off",
        ] in commands


# ---------------------------------------------------------------------------
# Subprocess-based functions
# ---------------------------------------------------------------------------


class TestDeckDivider:
    def test_the_divider_is_hidden_where_tmux_can_draw_space_borders(self):
        import shlex

        from gitdirector.integrations.tmux.core import _deck_tmux_config

        config = _deck_tmux_config("gd/deck/1-a").splitlines()
        (line,) = [line for line in config if line.startswith("if-shell")]
        argv = shlex.split(line)
        assert argv[:3] == ["if-shell", "-F", "#{>=:#{version},3.6}"]
        assert "pane-border-lines spaces" in argv[3]
        assert 'pane-border-style "fg=default,bg=default"' in argv[3]
        assert 'pane-active-border-style "fg=default,bg=default"' in argv[3]

    def test_older_tmux_keeps_the_heavy_divider(self):
        from gitdirector.integrations.tmux.core import _deck_tmux_config

        assert "pane-border-lines heavy" in _deck_tmux_config("gd/deck/1-a")


class TestDeckStatusHints:
    @staticmethod
    def _status(config: str, option: str) -> str:
        import shlex

        (line,) = [line for line in config.splitlines() if f" {option} " in line]
        return shlex.split(line)[-1]

    def test_keys_replace_the_badge_on_the_left_with_the_live_prefix(self):
        from gitdirector.integrations.tmux.core import _deck_tmux_config

        config = _deck_tmux_config("gd/deck/1-a")
        status_left = self._status(config, "status-left")
        assert "#{prefix} b" in status_left and "toggle" in status_left
        assert "#{prefix} d" in status_left and "console" in status_left
        assert "⇥ / #{prefix} ⇥" in status_left and "session ↔ sidebar" in status_left
        assert "@gd_badge" not in config and "@gd_label" not in config

    def test_the_clock_stays_alone_on_the_right(self):
        from gitdirector.integrations.tmux.core import _deck_tmux_config

        config = _deck_tmux_config("gd/deck/1-a")
        status_right = self._status(config, "status-right")
        assert "%H:%M" in status_right
        assert "prefix" not in status_right and "console" not in status_right

    @patch(
        "gitdirector.integrations.tmux.core._current_window_target",
        return_value="gd/my-repo/claude/1:0",
    )
    def test_other_sessions_keep_the_badge_and_label(self, _mock_target):
        for config in (
            _session_tmux_config("gd/my-repo/claude/1", "rose-pine"),
            _panel_tmux_config("Main", "gd/panel/main", "rose-pine"),
        ):
            status_left = self._status(config, "status-left")
            assert "@gd_badge" in status_left and "@gd_label" in status_left
            assert "prefix" not in status_left
            assert "prefix" not in self._status(config, "status-right")

    def test_narrow_clients_get_a_shorter_form_then_none(self):
        from gitdirector.integrations.tmux.core import (
            _DECK_HINTS_COMPACT_MIN_WIDTH,
            _DECK_HINTS_MIN_WIDTH,
            _deck_key_hints,
        )

        hints = _deck_key_hints(resolve_panel_theme(None))
        wide = f"#{{?#{{e|>=:#{{client_width}},{_DECK_HINTS_MIN_WIDTH}}},"
        medium = f",#{{?#{{e|>=:#{{client_width}},{_DECK_HINTS_COMPACT_MIN_WIDTH}}},"
        assert hints.startswith(wide) and hints.endswith(",}}")
        full, compact = hints[len(wide) : -len(",}}")].split(medium)
        # A comma inside a branch would end it early.
        assert "," not in full and "," not in compact
        assert "session ↔ sidebar" in full
        assert "sidebar" in compact and "session ↔" not in compact
        assert "#{prefix} b" in compact and "#{prefix} d" in compact
