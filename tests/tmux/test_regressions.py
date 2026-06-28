"""Regression guards for exact-match tmux targets and cleanup behavior."""

import inspect
import shlex
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import gitdirector.integrations.tmux.panels as tmux_panels
from gitdirector.integrations.tmux import (
    _capture_pane_text,
    _ControlModeReader,
    _current_window_target,
    _embedded_tmux_attach_command,
    _ensure_panel_resize_tracking,
    _kill_tmux_session_by_name,
    _panel_attach_fragment,
    _panel_pane_command,
    _respawn_pane,
    _session_exists,
    _tmux_output,
    _tmux_session_actual_name,
    _tmux_theme_config,
    attach_tmux_session,
    cleanup_panel_attached_session,
    kill_tmux_session,
    launch_command_in_tmux_session,
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


class TestKillTmuxSessionInputValidation:
    """Defensive validation that prevents accidental bulk kills."""

    @pytest.mark.parametrize(
        "bad_name",
        [
            "",
            "=",
            "=gd/repo/shell/1",
            "gd/",
            "repo/shell/1",
            "non-gd-session",
            "gd/repo/shell/1:",
            "gd/repo/shell/1:0",
            "gd/repo/shell/1:0.0",
            "gd/*",
            "gd/?",
            "gd/[abc]",
            "gd/]x[",
            "*",
            "?",
        ],
    )
    def test_rejects_dangerous_or_partial_session_names(self, bad_name):
        """Empty / non-gd / already-prefixed / glob-bearing / colon-bearing
        names must NOT reach ``tmux kill-session`` — they could kill many
        sessions or none, and silently wipe user state."""
        with pytest.raises(ValueError):
            kill_tmux_session(bad_name)

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_valid_full_name_still_works(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        kill_tmux_session("gd/repo/shell/1")
        assert mock_run.call_args[0][0] == [
            "tmux",
            "kill-session",
            "-t",
            "=gd/repo/shell/1",
        ]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_panel_session_name_with_three_segments_is_valid(self, mock_run):
        """``gd/panel/<name>`` has 3 segments — also valid (panel sessions)."""
        mock_run.return_value = MagicMock(returncode=0)
        kill_tmux_session("gd/panel/main")
        assert mock_run.call_args[0][0] == [
            "tmux",
            "kill-session",
            "-t",
            "=gd/panel/main",
        ]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_does_not_invoke_tmux_when_input_invalid(self, mock_run):
        """Even when validation raises, no subprocess should fire."""
        with pytest.raises(ValueError):
            kill_tmux_session("")
        with pytest.raises(ValueError):
            kill_tmux_session("gd/")
        with pytest.raises(ValueError):
            kill_tmux_session("=gd/whatever")
        mock_run.assert_not_called()


class TestExactMatchAttachTmuxSession:
    """attach_tmux_session must use ``=`` for both switch-client and attach-session."""

    @patch(
        "gitdirector.integrations.tmux.panels.ensure_temp_panel_tmux_session",
        return_value="gd/temp/panel/repo/shell/1",
    )
    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_regular_session_switch_client_exact_temp_panel_target(
        self, mock_run, _mock_sync, _mock_ensure
    ):
        mock_run.return_value = MagicMock(returncode=0)
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
        mock_run.return_value = MagicMock(returncode=0)
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
        mock_run.return_value = MagicMock(returncode=0)
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
        mock_run.side_effect = [
            result,
            MagicMock(),
        ]

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

    def test_temp_panel_kill_session_uses_equals(self):
        """Shell-embedded kill-session in temp panel script must use ``=``.

        Without ``=``, tmux's prefix matching would kill every session
        whose name starts with the wrapper name — including all gd/
        sessions. Belt-and-braces assertion.
        """
        from gitdirector.integrations.tmux.panels import _temp_panel_pane_command

        cmd = _temp_panel_pane_command("gd/temp/panel/repo/shell/1", "gd/repo/shell/1")
        assert f"kill-session -t {shlex.quote('=gd/temp/panel/repo/shell/1')}" in cmd
        # All kill-session invocations in the embedded script must be exact.
        for line in cmd.splitlines() + [cmd]:
            if "kill-session -t" in line:
                after_t = line.split("kill-session -t ", 1)[1].split()[0]
                unquoted = after_t.strip("'\"")
                assert unquoted.startswith("="), (
                    f"shell kill-session target not exact-match: {after_t!r}"
                )

    def test_unassigned_pane_has_no_tmux_target(self):
        cmd = _panel_pane_command("Dev", 1, None)
        script = shlex.split(cmd)[2]
        assert "has-session" not in cmd
        assert "UNASSIGNED" in cmd
        assert "printf '%s\\n' '' UNASSIGNED" in script
        assert "Panel: Dev" not in cmd
        assert "Pane 1: unassigned" not in cmd

    def test_unassigned_pane_exits_without_placeholder_process(self):
        cmd = _panel_pane_command("Dev", 1, None)
        script = shlex.split(cmd)[2]

        assert "tail -f /dev/null" not in script
        assert "read -r" not in script
        assert "while :" not in script
        assert script.endswith("exit 0")

    def test_assigned_pane_exits_without_placeholder_process(self):
        cmd = _panel_pane_command("Dev", 1, "gd/repo/shell/1")
        script = shlex.split(cmd)[2]

        assert "tail -f /dev/null" not in script
        assert "read -r" not in script
        assert "while :" not in script
        assert script.endswith("exit 0")

    def test_temp_panel_exits_without_placeholder_process(self):
        cmd = tmux_panels._temp_panel_pane_command("gd/temp/panel/repo/shell/1", "gd/repo/shell/1")
        script = shlex.split(cmd)[2]

        assert "tail -f /dev/null" not in script
        assert "read -r" not in script
        assert "while :" not in script
        assert script.endswith("exit 0")

    def test_panels_module_does_not_reintroduce_tail_placeholders(self):
        assert "tail -f /dev/null" not in inspect.getsource(tmux_panels)


class TestRespawnPane:
    @patch("gitdirector.integrations.tmux.panels.time.sleep")
    @patch("gitdirector.integrations.tmux.panels.subprocess.run")
    def test_retries_transient_fork_failure(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            MagicMock(
                returncode=1, stderr="respawn pane failed: fork failed: Device not configured"
            ),
            MagicMock(returncode=0, stderr=""),
        ]

        _respawn_pane("%1", "cat")

        assert mock_run.call_count == 2
        mock_sleep.assert_called_once_with(0.05)

    @patch("gitdirector.integrations.tmux.panels.time.sleep")
    @patch("gitdirector.integrations.tmux.panels.subprocess.run")
    def test_does_not_retry_non_fork_failure(self, mock_run, mock_sleep):
        mock_run.return_value = MagicMock(returncode=1, stderr="no such pane")

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            _respawn_pane("%1", "cat")

        assert exc_info.value.returncode == 1
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()


class TestTmuxOutput:
    @patch("gitdirector.integrations.tmux.panels.time.sleep")
    @patch("gitdirector.integrations.tmux.panels.subprocess.run")
    def test_retries_transient_split_window_fork_failure(self, mock_run, mock_sleep):
        mock_run.side_effect = [
            MagicMock(
                returncode=1,
                stdout="",
                stderr="create pane failed: fork failed: Device not configured",
            ),
            MagicMock(returncode=0, stdout="%1\n", stderr=""),
        ]

        output = _tmux_output("split-window", "-P", "-F", "#{pane_id}", "cat")

        assert output == "%1"
        assert mock_run.call_count == 2
        mock_sleep.assert_called_once_with(0.05)

    @patch("gitdirector.integrations.tmux.panels.time.sleep")
    @patch("gitdirector.integrations.tmux.panels.subprocess.run")
    def test_does_not_retry_non_fork_failure(self, mock_run, mock_sleep):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no such target")

        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            _tmux_output("split-window", "cat")

        assert exc_info.value.returncode == 1
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()


class TestOrphanSessionNameTmuxSafe:
    """Orphan session names must avoid ``.`` so tmux does not munge them.

    tmux silently replaces ``.`` with ``_`` in session names, so any session
    we create with a dot is stored under a different name. The old code used
    ``{name}.orphaned-{pid}-{ts}`` which caused the orphan to leak because
    the Python kill call couldn't find it.
    """

    def test_actual_name_replaces_dots_with_underscores(self):
        assert _tmux_session_actual_name("gd/panel/main.orphaned-1-2") == (
            "gd/panel/main_orphaned-1-2"
        )

    def test_actual_name_passthrough_when_no_dots(self):
        assert _tmux_session_actual_name("gd/panel/main") == "gd/panel/main"

    def test_actual_name_handles_multiple_dots(self):
        assert _tmux_session_actual_name("a.b.c.d") == "a_b_c_d"

    def test_rebuild_panel_uses_underscore_in_orphan_name(self):
        """Source-level guarantee that we don't regress to ``.orphaned-``."""
        source_path = (
            Path(__file__).resolve().parents[2] / "src/gitdirector/integrations/tmux/panels.py"
        )
        assert source_path.is_file(), f"panels.py not found at {source_path}"
        source = source_path.read_text()
        assert ".orphaned-" not in source, (
            "rebuild_panel_tmux_session must use '_orphaned-' so tmux does not munge it"
        )

    @patch("gitdirector.integrations.tmux.panels.kill_tmux_session")
    def test_kill_by_name_falls_back_to_munged_form(self, mock_kill):
        """When the intended name has a dot, try the munged form too."""
        mock_kill.side_effect = [False, True]
        assert _kill_tmux_session_by_name("gd/panel/main.orphaned-1-2") is True
        assert mock_kill.call_count == 2
        first_call_args = mock_kill.call_args_list[0][0]
        second_call_args = mock_kill.call_args_list[1][0]
        assert first_call_args[0] == "gd/panel/main.orphaned-1-2"
        assert second_call_args[0] == "gd/panel/main_orphaned-1-2"

    @patch("gitdirector.integrations.tmux.panels.kill_tmux_session")
    def test_kill_by_name_returns_true_on_first_success(self, mock_kill):
        """If the intended name works directly, don't try the munged form."""
        mock_kill.return_value = True
        assert _kill_tmux_session_by_name("gd/panel/main") is True
        assert mock_kill.call_count == 1

    @patch("gitdirector.integrations.tmux.panels.kill_tmux_session")
    def test_kill_by_name_returns_false_when_both_forms_fail(self, mock_kill):
        mock_kill.return_value = False
        assert _kill_tmux_session_by_name("gd/panel/main.orphaned-1-2") is False


class TestExactMatchLaunchCommand:
    """launch_command_in_tmux_session must use exact session and pane targets."""

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_respawn_pane_target_uses_equals(self, mock_run, _mock_marker):
        launch_command_in_tmux_session("gd/my-repo/copilot/1", "copilot")
        respawn_args = mock_run.call_args[0][0]
        assert respawn_args[0:5] == [
            "tmux",
            "respawn-pane",
            "-k",
            "-t",
            "=gd/my-repo/copilot/1:",
        ]

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_cleanup_script_kill_session_uses_equals(self, mock_run, _mock_marker):
        launch_command_in_tmux_session("gd/my-repo/copilot/1", "copilot")
        cleanup_cmd = mock_run.call_args[0][0][-1]
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
