"""Regression guards for exact-match tmux targets and cleanup behavior."""

import inspect
import shlex
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import gitdirector.integrations.tmux.panels as tmux_panels
from gitdirector.integrations.tmux import (
    attach_tmux_session,
    kill_tmux_session,
    launch_command_in_tmux_session,
)
from gitdirector.integrations.tmux.core import (
    _current_window_target,
    _session_exists,
    _tmux_theme_config,
)
from gitdirector.integrations.tmux.monitor import _capture_pane_text
from gitdirector.integrations.tmux.panels import (
    _panel_pane_command,
    _respawn_pane,
    _tmux_output,
)


class TestExactMatchSessionExists:
    """_session_exists must use ``=`` so ``gd/panel/dev`` doesn't match ``gd/panel/dev-tools``."""

    @patch("subprocess.run")
    def test_has_session_uses_exact_prefix(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        _session_exists("gd/panel/dev")
        args = mock_run.call_args[0][0]
        assert args == ["tmux", "has-session", "-t", "=gd/panel/dev"]

    @patch("subprocess.run")
    def test_similar_name_not_matched(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        result = _session_exists("gd/panel/dev")
        assert result is False
        target_arg = mock_run.call_args[0][0][3]
        assert target_arg.startswith("=")


class TestExactMatchKillTmuxSession:
    """kill_tmux_session must use ``=`` so killing one session can't cascade."""

    @patch("subprocess.run")
    def test_kill_uses_exact_prefix(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        kill_tmux_session("gd/panel/dev")
        args = mock_run.call_args[0][0]
        assert args == ["tmux", "kill-session", "-t", "=gd/panel/dev"]

    @patch("subprocess.run")
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

    @patch("subprocess.run")
    def test_valid_full_name_still_works(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        kill_tmux_session("gd/repo/shell/1")
        assert mock_run.call_args[0][0] == [
            "tmux",
            "kill-session",
            "-t",
            "=gd/repo/shell/1",
        ]

    @patch("subprocess.run")
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

    @patch("subprocess.run")
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

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("subprocess.run")
    def test_regular_session_switch_client_exact_target(self, mock_run, _mock_sync):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            attach_tmux_session("gd/repo/shell/1")
        target = mock_run.call_args[0][0][3]
        assert target == "=gd/repo/shell/1"

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core.reflow_panel_tmux_session")
    @patch("gitdirector.integrations.tmux.panels._ensure_panel_prefix_bindings")
    @patch("subprocess.run")
    def test_switch_client_exact(
        self,
        mock_run,
        mock_prefix_bindings,
        mock_reflow,
        _mock_sync,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            attach_tmux_session("gd/panel/dev")
        target = mock_run.call_args[0][0][3]
        assert target == "=gd/panel/dev"
        mock_prefix_bindings.assert_called_once_with()
        mock_reflow.assert_called_once_with("gd/panel/dev")

    @patch("gitdirector.integrations.tmux.core.sync_panel_tmux_config")
    @patch("gitdirector.integrations.tmux.core.reflow_panel_tmux_session")
    @patch("gitdirector.integrations.tmux.panels._ensure_panel_prefix_bindings")
    @patch("subprocess.run")
    def test_attach_session_exact(
        self,
        mock_run,
        mock_prefix_bindings,
        mock_reflow,
        _mock_sync,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd/panel/dev")
        target = mock_run.call_args[0][0][3]
        assert target == "=gd/panel/dev"
        mock_prefix_bindings.assert_called_once_with()
        mock_reflow.assert_called_once_with("gd/panel/dev")


class TestExactMatchPanelPaneCommand:
    """_panel_pane_command must use ``=`` in has-session check."""

    def test_assigned_pane_uses_exact_has_session(self):
        cmd = _panel_pane_command("Dev", 1, "gd/repo/shell/1")
        assert "has-session -t" in cmd
        has_session_part = cmd.split("has-session -t ")[1].split()[0]
        unquoted = has_session_part.strip("'\"")
        assert unquoted.startswith("=")

    def test_assigned_pane_views_the_session_through_an_exact_target(self):
        script = shlex.split(_panel_pane_command("Dev", 2, "gd/repo/shell/1"))[2]
        assert "new-session -t =gd/repo/shell/1 -s gd/view/dev-2-$$" in script
        assert "set-option status off" in script
        assert "set-option @gd_slot 2" in script
        assert "set-option destroy-unattached on" in script

    def test_unassigned_pane_has_no_tmux_target(self):
        cmd = _panel_pane_command("Dev", 1, None)
        script = shlex.split(cmd)[2]
        assert "has-session" not in cmd
        assert "UNASSIGNED" not in cmd
        assert script.endswith("exit 0")
        assert "Panel: Dev" not in cmd
        assert "1: empty" in script

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


class TestExactMatchLaunchCommand:
    """launch_command_in_tmux_session must use exact session and pane targets."""

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
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
    @patch("subprocess.run")
    def test_cleanup_script_kill_session_uses_equals(self, mock_run, _mock_marker):
        launch_command_in_tmux_session("gd/my-repo/copilot/1", "copilot")
        cleanup_cmd = mock_run.call_args[0][0][-1]
        assert f"kill-session -t {shlex.quote('=gd/my-repo/copilot/1')}" in cleanup_cmd


class TestExactMatchCapturePaneText:
    """_capture_pane_text must use ``=`` prefix."""

    @patch("subprocess.run")
    def test_capture_target_uses_equals(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="text")
        _capture_pane_text("gd/repo/shell/1")
        args = mock_run.call_args[0][0]
        assert args == ["tmux", "capture-pane", "-p", "-t", "=gd/repo/shell/1:"]


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

    @patch("subprocess.run")
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

        from gitdirector.integrations.tmux import core, monitor, panels

        violations = []
        for module in (core, monitor, panels):
            tree = ast.parse(inspect.getsource(module))
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
                            violations.append(
                                f"{module.__name__}:{node.lineno}: literal '-t' followed by {val!r}"
                            )
                    elif isinstance(next_elt, ast.JoinedStr):
                        first_val = next_elt.values[0] if next_elt.values else None
                        # Pane ids ("%3") are exact by construction.
                        if isinstance(first_val, ast.Constant) and not str(
                            first_val.value
                        ).startswith(("=", "%")):
                            violations.append(
                                f"{module.__name__}:{node.lineno}: f-string '-t' target doesn't start with '='"
                            )
                        elif isinstance(first_val, ast.FormattedValue):
                            violations.append(
                                f"{module.__name__}:{node.lineno}: f-string '-t' target starts with a variable"
                            )
        assert violations == [], (
            "tmux subprocess -t targets missing '=' exact-match prefix:\n" + "\n".join(violations)
        )


class TestTmuxServerDeathIsReportedClearly:
    """A dead tmux server must not read as a generic command failure.

    The server has been observed to die mid-command (tmux itself crashing
    inside ``respawn-pane``). Every session on it is gone at that point, so
    reporting only "command exited 1" sends the reader looking for a bug in
    the command instead of at the missing server.
    """

    @patch("gitdirector.integrations.tmux.panels.time.sleep")
    @patch("gitdirector.integrations.tmux.panels.subprocess.run")
    def test_respawn_pane_reports_a_dead_server(self, mock_run, _mock_sleep):
        from gitdirector.integrations.tmux import TmuxError

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="server exited unexpectedly\n"
        )

        with pytest.raises(TmuxError, match="tmux server exited"):
            _respawn_pane("%1", "cat")

    @patch("gitdirector.integrations.tmux.panels.time.sleep")
    @patch("gitdirector.integrations.tmux.panels.subprocess.run")
    def test_no_server_running_is_reported_the_same_way(self, mock_run, _mock_sleep):
        from gitdirector.integrations.tmux import TmuxError

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr=b"no server running on /tmp/tmux-501/default\n"
        )

        with pytest.raises(TmuxError, match="tmux server exited"):
            _tmux_output("list-panes")

    @patch("gitdirector.integrations.tmux.panels.time.sleep")
    @patch("gitdirector.integrations.tmux.panels.subprocess.run")
    def test_ordinary_failures_are_left_alone(self, mock_run, _mock_sleep):
        """Only server death is reclassified; other failures keep their type."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no such pane")

        with pytest.raises(subprocess.CalledProcessError):
            _respawn_pane("%1", "cat")
