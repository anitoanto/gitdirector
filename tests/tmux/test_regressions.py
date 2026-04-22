"""Regression guards for exact-match tmux targets and cleanup behavior."""

import shlex
from pathlib import Path
from unittest.mock import MagicMock, patch

from gitdirector.integrations.tmux import (
    _capture_pane_text,
    _ControlModeReader,
    _current_window_target,
    _embedded_tmux_attach_command,
    _ensure_panel_resize_tracking,
    _panel_attach_fragment,
    _panel_pane_command,
    _session_exists,
    _tmux_theme_config,
    attach_tmux_session,
    cleanup_panel_attached_session,
    kill_tmux_session,
    launch_agent_in_tmux_session,
)

class TestExactMatchSessionExists:
    """_session_exists must use ``=`` so ``gd/panel/dev`` doesn't match ``gd/panel/dev-tools``."""

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_has_session_uses_exact_prefix(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        _session_exists("gd/panel/dev")
        args = mock_run.call_args[0][0]
        assert args == ["tmux", "has-session", "-t", "=gd/panel/dev"]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_similar_name_not_matched(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        result = _session_exists("gd/panel/dev")
        assert result is False
        target_arg = mock_run.call_args[0][0][3]
        assert target_arg.startswith("=")


class TestExactMatchKillTmuxSession:
    """kill_tmux_session must use ``=`` so killing one session can't cascade."""

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_kill_uses_exact_prefix(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        kill_tmux_session("gd/panel/dev")
        args = mock_run.call_args[0][0]
        assert args == ["tmux", "kill-session", "-t", "=gd/panel/dev"]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_kill_cannot_prefix_match_similar_session(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        kill_tmux_session("gd/panel/dev")
        target = mock_run.call_args[0][0][3]
        assert target == "=gd/panel/dev"
        assert target != "gd/panel/dev"


class TestExactMatchAttachTmuxSession:
    """attach_tmux_session must use ``=`` for both switch-client and attach-session."""

    @patch(
        "gitdirector.integrations.tmux.panels.rebuild_temp_panel_tmux_session",
        return_value="gd/temp/panel/repo/shell/1",
    )
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_regular_session_switch_client_exact_temp_panel_target(
        self, mock_run, _mock_sync, _mock_rebuild
    ):
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            attach_tmux_session("gd/repo/shell/1")
        target = mock_run.call_args[0][0][3]
        assert target == "=gd/temp/panel/repo/shell/1"

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core.reflow_panel_tmux_session")
    @patch("gitdirector.integrations.tmux.core._ensure_panel_resize_tracking")
    @patch("gitdirector.integrations.tmux.panels._ensure_panel_prefix_bindings")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_switch_client_exact(
        self,
        mock_run,
        mock_prefix_bindings,
        mock_track_resize,
        mock_reflow,
        _mock_sync,
    ):
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            attach_tmux_session("gd/panel/dev")
        target = mock_run.call_args[0][0][3]
        assert target == "=gd/panel/dev"
        mock_prefix_bindings.assert_called_once_with()
        mock_track_resize.assert_called_once_with("gd/panel/dev")
        mock_reflow.assert_called_once_with("gd/panel/dev")

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core.reflow_panel_tmux_session")
    @patch("gitdirector.integrations.tmux.core._ensure_panel_resize_tracking")
    @patch("gitdirector.integrations.tmux.panels._ensure_panel_prefix_bindings")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_attach_session_exact(
        self,
        mock_run,
        mock_prefix_bindings,
        mock_track_resize,
        mock_reflow,
        _mock_sync,
    ):
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd/panel/dev")
        target = mock_run.call_args[0][0][3]
        assert target == "=gd/panel/dev"
        mock_prefix_bindings.assert_called_once_with()
        mock_track_resize.assert_called_once_with("gd/panel/dev")
        mock_reflow.assert_called_once_with("gd/panel/dev")


class TestPanelResizeTracking:
    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=True)
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_sets_resize_hooks_on_panel_session_and_window(self, mock_run, _mock_exists):
        _ensure_panel_resize_tracking("gd/panel/dev")

        assert mock_run.call_args_list[0].args[0] == [
            "tmux",
            "set-window-option",
            "-q",
            "-t",
            "=gd/panel/dev:0",
            "aggressive-resize",
            "on",
        ]
        assert mock_run.call_args_list[1].args[0][:5] == [
            "tmux",
            "set-hook",
            "-t",
            "=gd/panel/dev:",
            "client-resized",
        ]
        assert mock_run.call_args_list[2].args[0][:6] == [
            "tmux",
            "set-hook",
            "-w",
            "-t",
            "=gd/panel/dev:0",
            "window-resized",
        ]

    @patch("gitdirector.integrations.tmux.core._session_exists", return_value=False)
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_skips_missing_panel_session(self, mock_run, _mock_exists):
        _ensure_panel_resize_tracking("gd/panel/dev")

        mock_run.assert_not_called()


class TestExactMatchPanelAttachFragment:
    """_panel_attach_fragment shell script must use ``=`` for all -t args."""

    def test_all_tmux_targets_use_equals(self):
        fragment = _panel_attach_fragment("gd/panel/dev")
        for part in fragment.split("tmux ")[1:]:
            if " -t " in part:
                target = part.split(" -t ")[1].split()[0]
                unquoted = target.strip("'\"")
                assert unquoted.startswith("=") or unquoted.startswith("$"), (
                    f"tmux -t target missing '=' prefix in fragment: ...tmux {part[:60]}..."
                )


class TestCleanupPanelAttachedSession:
    @patch("gitdirector.integrations.tmux.panels.sync_panel_tmux_config")
    @patch(
        "gitdirector.integrations.tmux.panels._current_window_target",
        return_value="gd/repo/shell/1:0",
    )
    @patch("gitdirector.integrations.tmux.panels._session_exists", return_value=True)
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_restores_session_chrome_when_last_panel_client_stops(
        self,
        mock_run,
        _mock_exists,
        _mock_window_target,
        mock_sync,
    ):
        def completed(stdout: str = "", returncode: int = 0):
            result = MagicMock()
            result.stdout = stdout
            result.returncode = returncode
            return result

        mock_run.side_effect = [
            completed("1\n"),
            completed("on\n"),
            completed("off\n"),
            completed("gd/repo/shell/1:2\n"),
            completed(),
            completed(),
            completed(),
            completed(),
            completed(),
            completed(),
        ]

        cleanup_panel_attached_session("gd/repo/shell/1", theme_name="rose-pine")

        assert mock_run.call_args_list[4].args[0] == [
            "tmux",
            "set-option",
            "-q",
            "-t",
            "=gd/repo/shell/1:",
            "status",
            "on",
        ]
        assert mock_run.call_args_list[5].args[0] == [
            "tmux",
            "set-window-option",
            "-q",
            "-t",
            "=gd/repo/shell/1:2",
            "pane-border-status",
            "off",
        ]
        mock_sync.assert_called_once_with("rose-pine")

    @patch("gitdirector.integrations.tmux.panels.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.panels._session_exists", return_value=True)
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_decrements_client_count_when_other_panel_clients_remain(
        self,
        mock_run,
        _mock_exists,
        mock_sync,
    ):
        result = MagicMock()
        result.stdout = "3\n"
        result.returncode = 0
        mock_run.side_effect = [result, MagicMock()]

        cleanup_panel_attached_session("gd/repo/shell/1")

        assert mock_run.call_args_list[1].args[0] == [
            "tmux",
            "set-option",
            "-q",
            "-t",
            "=gd/repo/shell/1:",
            "@gitdirector_panel_clients",
            "2",
        ]
        mock_sync.assert_not_called()

class TestExactMatchEmbeddedTmuxAttachCommand:
    """_embedded_tmux_attach_command must use ``=`` in has-session check."""

    def test_has_session_uses_equals(self):
        cmd = _embedded_tmux_attach_command("gd/repo/shell/1")
        assert "has-session -t" in cmd
        has_session_part = cmd.split("has-session -t ")[1].split()[0]
        unquoted = has_session_part.strip("'\"")
        assert unquoted.startswith("="), f"has-session -t missing '=' prefix: {has_session_part}"

    def test_with_panel_proxy_uses_equals(self):
        cmd = _embedded_tmux_attach_command("gd/repo/shell/1", panel_name="Dev", pane_index=1)
        assert "has-session -t" in cmd
        has_session_part = cmd.split("has-session -t ")[1].split()[0]
        unquoted = has_session_part.strip("'\"")
        assert unquoted.startswith("=")


class TestExactMatchPanelPaneCommand:
    """_panel_pane_command must use ``=`` in has-session check."""

    def test_assigned_pane_uses_exact_has_session(self):
        cmd = _panel_pane_command("Dev", 1, "gd/repo/shell/1")
        assert "has-session -t" in cmd
        has_session_part = cmd.split("has-session -t ")[1].split()[0]
        unquoted = has_session_part.strip("'\"")
        assert unquoted.startswith("=")

    def test_unassigned_pane_has_no_tmux_target(self):
        cmd = _panel_pane_command("Dev", 1, None)
        script = shlex.split(cmd)[2]
        assert "has-session" not in cmd
        assert "UNASSIGNED" in cmd
        assert "printf '%s\\n' '' UNASSIGNED" in script
        assert "Panel: Dev" not in cmd
        assert "Pane 1: unassigned" not in cmd


class TestExactMatchLaunchAgent:
    """launch_agent_in_tmux_session must use exact session and pane targets."""

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_send_keys_target_uses_equals(self, mock_run, _mock_marker):
        launch_agent_in_tmux_session("gd/my-repo/copilot/1", "copilot")
        send_keys_args = mock_run.call_args[0][0]
        assert send_keys_args[0:3] == ["tmux", "send-keys", "-t"]
        assert send_keys_args[3] == "=gd/my-repo/copilot/1:"

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_cleanup_script_kill_session_uses_equals(self, mock_run, _mock_marker):
        launch_agent_in_tmux_session("gd/my-repo/copilot/1", "copilot")
        cleanup_cmd = mock_run.call_args[0][0][4]
        assert f"kill-session -t {shlex.quote('=gd/my-repo/copilot/1')}" in cleanup_cmd


class TestExactMatchCapturePaneText:
    """_capture_pane_text must use ``=`` prefix."""

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_capture_target_uses_equals(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="text")
        _capture_pane_text("gd/repo/shell/1")
        args = mock_run.call_args[0][0]
        assert args == ["tmux", "capture-pane", "-p", "-t", "=gd/repo/shell/1:"]


class TestExactMatchControlModeReader:
    """_ControlModeReader must use ``=`` prefix in attach-session."""

    def test_attach_command_uses_equals(self):
        reader = _ControlModeReader("gd/repo/shell/1", callback=lambda *a: None)
        with patch("gitdirector.integrations.tmux.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout = iter([])
            mock_popen.return_value = mock_proc
            reader._run()
            popen_args = mock_popen.call_args[0][0]
            assert popen_args == ["tmux", "-C", "attach-session", "-t", "=gd/repo/shell/1", "-r"]


class TestExactMatchTmuxThemeConfig:
    """_tmux_theme_config must use ``=`` prefix in all set-option/set-window-option targets."""

    def test_all_config_lines_use_equals_prefix(self):
        config = _tmux_theme_config(
            badge_text="SHELL",
            label_text="my-repo",
            session_name="gd/my-repo/shell/1",
            pane_border_status="top",
            pane_border_format="test-format",
        )
        for line in config.strip().splitlines():
            if " -t " not in line:
                continue
            target = line.split(" -t ")[1].split()[0]
            unquoted = target.strip("'\"")
            assert unquoted.startswith("="), f"config line missing '=' prefix in -t target: {line}"

    def test_custom_window_target_gets_equals(self):
        config = _tmux_theme_config(
            badge_text="PANEL",
            label_text="dev",
            session_name="gd/panel/dev",
            window_target="gd/panel/dev:0",
        )
        for line in config.strip().splitlines():
            if "set-window-option" in line and " -t " in line:
                target = line.split(" -t ")[1].split()[0]
                unquoted = target.strip("'\"")
                assert unquoted.startswith("="), f"window target missing '=' prefix: {line}"


class TestExactMatchCurrentWindowTarget:
    """_current_window_target must use ``=`` prefix."""

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_display_message_uses_equals(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="gd/repo/shell/1:0\n")
        _current_window_target("gd/repo/shell/1")
        args = mock_run.call_args[0][0]
        assert "-t" in args
        t_index = args.index("-t")
        assert args[t_index + 1] == "=gd/repo/shell/1:"


class TestExactMatchSourceCodeAudit:
    """Scan tmux.py source for any subprocess ``-t`` arg missing the ``=`` prefix.

    This is a structural guard: any new code that passes ``-t`` to a subprocess
    call list without ``=`` will be caught here.
    """

    def test_all_subprocess_list_targets_use_equals(self):
        import ast
        import inspect

        import gitdirector.integrations.tmux as tmux_mod

        source = inspect.getsource(tmux_mod)
        tree = ast.parse(source)

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.List):
                continue
            elts = node.elts
            for i, elt in enumerate(elts):
                if not (isinstance(elt, ast.Constant) and elt.value == "-t"):
                    continue
                if i + 1 >= len(elts):
                    continue
                next_elt = elts[i + 1]
                if isinstance(next_elt, ast.Constant):
                    val = str(next_elt.value)
                    if not val.startswith("="):
                        violations.append(f"Line {node.lineno}: literal '-t' followed by {val!r}")
                elif isinstance(next_elt, ast.JoinedStr):
                    first_val = next_elt.values[0] if next_elt.values else None
                    if isinstance(first_val, ast.Constant) and not str(first_val.value).startswith(
                        "="
                    ):
                        violations.append(
                            f"Line {node.lineno}: f-string '-t' target doesn't start with '='"
                        )
                    elif isinstance(first_val, ast.FormattedValue):
                        violations.append(
                            f"Line {node.lineno}: f-string '-t' target starts with a variable (should prefix '=')"
                        )
        assert violations == [], (
            "tmux subprocess -t targets missing '=' exact-match prefix:\n" + "\n".join(violations)
        )
