"""Monitoring and pane-status tests for tmux integration."""

import shlex
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest

from gitdirector.integrations.tmux import (
    TmuxMonitor,
    launch_command_in_tmux_session,
    resolve_pane_status,
)
from gitdirector.integrations.tmux.core import TmuxError, _tmux_child_environment_command
from gitdirector.integrations.tmux.monitor import (
    _AGENT_OUTPUT_MIN_SECS,
    _AGENT_REPORT_STALE_SECS,
    _BELL_GRACE_SECS,
    _CONTROL_MODE_STOP_WAIT_SECS,
    _OUTPUT_GAP_SECS,
    _SHELL_ACTIVITY_GRACE_SECS,
    _SHELL_COMMANDS,
    _SILENCE_THRESHOLD_SECS,
    PaneSample,
    ProcessSnapshot,
    _capture_pane_text,
    _ControlModeReader,
    _get_process_snapshot,
    _is_cursor_blink,
    _list_gd_panes,
    _make_agent_ready_marker,
    _normalize_process_command,
    _parse_agent_report,
    _parse_cpu_seconds,
    _resolve_pane_command,
    _tree_cpu_seconds,
    _tty_is_raw,
    reconcile_agent_report,
)

from ._shared import REAL_TMUX_MONITOR_START, REAL_TMUX_MONITOR_STOP


class TestLaunchCommandInTmuxSession:
    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
    def test_queues_cleanup_script(self, mock_run, _mock_marker):
        ready_marker = launch_command_in_tmux_session("gd/my-repo/copilot/1", "copilot")
        cleanup_script = (
            "clear; "
            "touch /tmp/gitdirector-agent.ready >/dev/null 2>&1 || true; "
            "sh -lc copilot; status=$?; "
            f"tmux detach-client -s {shlex.quote('=gd/my-repo/copilot/1')} >/dev/null 2>&1 || true; "
            f"tmux kill-session -t {shlex.quote('=gd/my-repo/copilot/1')} >/dev/null 2>&1 || true; "
            "rm -f /tmp/gitdirector-agent.ready >/dev/null 2>&1 || true; "
            "exit $status"
        )
        expected_command = _tmux_child_environment_command(f"sh -lc {shlex.quote(cleanup_script)}")
        assert ready_marker == Path("/tmp/gitdirector-agent.ready")
        mock_run.assert_called_once_with(
            [
                "tmux",
                "respawn-pane",
                "-k",
                "-t",
                "=gd/my-repo/copilot/1:",
                expected_command,
            ],
            capture_output=True,
            env=ANY,
            timeout=ANY,
        )
        # The agent command carries its own last-resort scrub, so a leak
        # survives neither the session environment nor the command itself.
        assert "-u CLAUDE_CODE_SESSION_ID " in expected_command
        assert "-u NO_COLOR " in expected_command
        assert "TERM=tmux-256color" in expected_command

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
    def test_preserves_command_with_quotes_verbatim(self, mock_run, _mock_marker):
        """The user-supplied command is embedded as-is, not shell-normalized.

        Round-tripping through ``shlex.split``/``shlex.join`` would collapse
        quoted arguments like ``echo "hello world"`` into ``echo hello world``
        and break the command.
        """
        launch_command_in_tmux_session("gd/my-repo/echo hello world/1", 'echo "hello world"')
        respawn_argv = mock_run.call_args[0][0]
        wrapped_script = respawn_argv[-1]
        assert 'echo "hello world"' in wrapped_script
        # The outer wrapping still uses shlex.quote so single-quote–bearing
        # commands survive the tmux command boundary intact.
        assert wrapped_script.startswith(_tmux_child_environment_command("sh -lc "))


def _inner_shell_script(mock_run) -> str:
    """Return the script that ``sh -lc`` actually executes.

    The wrapper passed to ``tmux respawn-pane`` is
    ``env ... sh -lc <shlex.quote(script)>``; parsing the wrapper as a
    shell line recovers the original script.
    """
    wrapped = mock_run.call_args[0][0][-1]
    parts = shlex.split(wrapped)
    shell_index = parts.index("sh")
    assert parts[shell_index + 1] == "-lc"
    return parts[shell_index + 2]


def _assert_user_command_wrapped(mock_run, command: str) -> None:
    script = _inner_shell_script(mock_run)
    assert f"sh -lc {shlex.quote(command)}; status=$?;" in script


class TestCommandQuotingInCleanupScript:
    """Lock down how user-supplied commands survive the inner ``sh -lc`` shell.

    The user types something like::

        gitdirector gd-tmux myrepo "echo \"hello world\""

    The outer shell collapses the escapes and hands Python the string
    ``echo "hello world"``. That string is embedded verbatim into a cleanup
    script wrapped in ``sh -lc ...; tmux kill-session ...``. These tests
    parse the wrapper back to the script and assert the user's command
    appears exactly as intended, so the inner shell will execute it
    correctly.
    """

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
    def test_command_with_double_quoted_argument(self, mock_run, _mock_marker):
        cmd = 'echo "hello world"'
        launch_command_in_tmux_session("gd/my-repo/echo/1", cmd)
        _assert_user_command_wrapped(mock_run, cmd)

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
    def test_command_with_single_quoted_argument(self, mock_run, _mock_marker):
        """Single quotes inside the command must survive shlex.quote round-trip.

        The outer wrapping uses ``shlex.quote`` which encodes embedded single
        quotes as the ``'\"'\"'`` pattern. When the receiving shell parses
        the wrapper it must recover the literal single-quote-bearing command.
        """
        cmd = "echo 'hello world'"
        launch_command_in_tmux_session("gd/my-repo/echo/1", cmd)
        _assert_user_command_wrapped(mock_run, cmd)

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
    def test_command_with_backslashes(self, mock_run, _mock_marker):
        """Backslashes are preserved verbatim in the inner script.

        The user's outer shell already collapsed any ``\\\\`` escapes, so
        the Python command string is the literal sequence the inner shell
        should see. The inner shell applies its own quote rules from there.
        """
        cmd = 'echo "C:\\\\Users"'
        launch_command_in_tmux_session("gd/my-repo/echo/1", cmd)
        _assert_user_command_wrapped(mock_run, cmd)

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
    def test_command_with_mixed_quotes_and_backslashes(self, mock_run, _mock_marker):
        cmd = '''python -c "print('a\\\\\\\\b')"'''
        launch_command_in_tmux_session("gd/my-repo/echo/1", cmd)
        _assert_user_command_wrapped(mock_run, cmd)

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
    def test_command_with_shell_metacharacters(self, mock_run, _mock_marker):
        """``;``, ``&&``, ``|``, ``>`` are part of the user command and must
        be embedded verbatim — the inner shell interprets them.
        """
        for cmd in [
            "echo a; echo b",
            "true && echo yes",
            "echo hi | wc -l",
            "echo out > /tmp/gd_tmux_test_out",
            "echo a; # comment with ; semicolons",
        ]:
            mock_run.reset_mock()
            launch_command_in_tmux_session("gd/my-repo/cmd/1", cmd)
            _assert_user_command_wrapped(mock_run, cmd)

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
    def test_command_with_command_substitution(self, mock_run, _mock_marker):
        """``$(...)`` and backticks are preserved — the inner shell expands them."""
        for cmd in [
            "echo $(date +%Y)",
            "echo `date +%Y`",
            "echo $HOME",
        ]:
            mock_run.reset_mock()
            launch_command_in_tmux_session("gd/my-repo/cmd/1", cmd)
            _assert_user_command_wrapped(mock_run, cmd)

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
    def test_command_with_newlines(self, mock_run, _mock_marker):
        """Newlines in the command are preserved as-is and will be treated by
        the inner shell as command separators (since the script is parsed
        as a single -c argument, embedded newlines are not honored by ``sh -c``
        on every platform; we just assert the bytes are passed through).
        """
        cmd = "echo first\necho second"
        launch_command_in_tmux_session("gd/my-repo/cmd/1", cmd)
        _assert_user_command_wrapped(mock_run, cmd)

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
    def test_exit_command_cannot_skip_outer_cleanup(self, mock_run, _mock_marker):
        cmd = "exit 7"
        launch_command_in_tmux_session("gd/my-repo/cmd/1", cmd)
        script = _inner_shell_script(mock_run)

        _assert_user_command_wrapped(mock_run, cmd)
        assert script.index(f"sh -lc {shlex.quote(cmd)}") < script.index("tmux kill-session")

    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("subprocess.run")
    def test_session_name_does_not_break_quoted_purpose(self, mock_run, _mock_marker):
        """The session name's ``purpose`` segment can contain spaces and
        quotes — the cleanup script must still quote the *session name*,
        not the purpose, so target lookups (e.g. ``tmux kill-session -t
        =<session>``) use the exact full name.
        """
        launch_command_in_tmux_session('gd/my-repo/echo "hi"/1', 'echo "hi"')
        script = _inner_shell_script(mock_run)
        # The session name (containing a literal quote) appears as a
        # shlex.quote–escaped argument to the kill-session / detach-client
        # calls, never unquoted.
        assert "tmux kill-session -t " in script
        assert "tmux detach-client -s " in script
        # The escaped form must be present; the unescaped literal would
        # corrupt the shell parsing of the script.
        assert shlex.quote('=gd/my-repo/echo "hi"/1') in script


class TestMakeAgentReadyMarker:
    def test_returns_missing_marker_path(self):
        marker = _make_agent_ready_marker()

        assert marker.name.startswith("gitdirector-agent-")
        assert marker.suffix == ".ready"
        assert marker.exists() is False

    def test_ignores_missing_temp_file(self):
        with patch(
            "gitdirector.integrations.tmux.monitor.tempfile.mkstemp",
            return_value=(123, "/tmp/gitdirector-agent-test.ready"),
        ):
            with patch("gitdirector.integrations.tmux.monitor.os.close") as mock_close:
                with patch(
                    "gitdirector.integrations.tmux.monitor.Path.unlink",
                    side_effect=FileNotFoundError,
                ):
                    marker = _make_agent_ready_marker()

        assert marker == Path("/tmp/gitdirector-agent-test.ready")
        mock_close.assert_called_once_with(123)


class TestNormalizeProcessCommand:
    def test_empty_args_return_empty_string(self):
        assert _normalize_process_command("   ") == ""

    def test_returns_executable_basename(self):
        assert _normalize_process_command("/usr/local/bin/claude --model sonnet") == "claude"


class TestParseCpuSeconds:
    def test_macos_centiseconds(self):
        assert _parse_cpu_seconds("1:02.50") == 62.5

    def test_linux_hours(self):
        assert _parse_cpu_seconds("01:02:03") == 3723.0

    def test_days_prefix(self):
        assert _parse_cpu_seconds("1-00:00:01") == 86401.0

    def test_garbage_is_zero(self):
        assert _parse_cpu_seconds("n/a") == 0.0
        assert _parse_cpu_seconds("x-00:01") == 0.0


def _snapshot(children=None, commands=None, pgid=None, tpgid=None, cpu=None) -> ProcessSnapshot:
    return ProcessSnapshot(children or {}, commands or {}, pgid or {}, tpgid or {}, cpu or {})


class TestGetProcessSnapshot:
    @patch("subprocess.run")
    def test_failure_returns_empty_snapshot(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        assert _get_process_snapshot() == ProcessSnapshot.empty()

    @patch("subprocess.run", side_effect=OSError("no ps"))
    def test_missing_ps_returns_empty_snapshot(self, _mock_run):
        assert _get_process_snapshot() == ProcessSnapshot.empty()

    @patch("subprocess.run")
    def test_parses_rows_and_skips_malformed_ones(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="malformed row\n101 1 101 101 0:01.50 -zsh\n102 101 102 101 00:00:03 node app.js\n",
        )

        snapshot = _get_process_snapshot()

        assert snapshot.children_by_parent == {1: [101], 101: [102]}
        assert snapshot.commands_by_pid == {101: "-zsh", 102: "node"}
        assert snapshot.pgid_by_pid == {101: 101, 102: 102}
        assert snapshot.tpgid_by_pid == {101: 101, 102: 101}
        assert snapshot.cpu_seconds_by_pid == {101: 1.5, 102: 3.0}


class TestResolvePaneCommand:
    def test_no_descendants_uses_fallback(self):
        assert _resolve_pane_command(1, "bash", _snapshot()) == "bash"

    def test_cycle_skips_seen_pids(self):
        snapshot = _snapshot(children={1: [2], 2: [1]}, commands={2: "python"})
        assert _resolve_pane_command(1, "bash", snapshot) == "python"

    def test_only_shell_descendants_pick_deepest_shell(self):
        snapshot = _snapshot(children={1: [2], 2: [3]}, commands={2: "-zsh", 3: "sh"})
        assert _resolve_pane_command(1, "bash", snapshot) == "sh"

    def test_prefers_foreground_process_group(self):
        snapshot = _snapshot(
            children={1: [2, 3]},
            commands={2: "git", 3: "python"},
            pgid={2: 200, 3: 300},
            tpgid={1: 300},
        )
        assert _resolve_pane_command(1, "bash", snapshot) == "python"

    def test_picks_shallowest_foreground_process_not_its_helpers(self):
        """The launched program wins over the children it spawns.

        No agent names are involved: whatever sits at the top of the
        foreground job is the program the user started.
        """
        snapshot = _snapshot(
            children={70539: [70619], 70619: [70624], 70624: [70625]},
            commands={70619: "sh", 70624: "some-agent", 70625: "git"},
            pgid={70619: 70619, 70624: 70619, 70625: 70619},
            tpgid={70539: 70619},
        )
        assert _resolve_pane_command(70539, "bash", snapshot) == "some-agent"

    def test_falls_back_to_deepest_non_shell_without_foreground_match(self):
        snapshot = _snapshot(
            children={1: [2, 3], 2: [4]},
            commands={2: "git", 3: "python", 4: "rg"},
            pgid={2: 200, 3: 300, 4: 400},
            tpgid={1: 999},
        )
        assert _resolve_pane_command(1, "bash", snapshot) == "rg"


class TestTreeCpuSeconds:
    def test_sums_pane_and_descendants(self):
        snapshot = _snapshot(
            children={1: [2], 2: [3]},
            commands={2: "node", 3: "git"},
            cpu={1: 0.5, 2: 2.0, 3: 0.25, 99: 100.0},
        )
        assert _tree_cpu_seconds(1, snapshot) == 2.75


class TestTtyIsRaw:
    def test_empty_tty_is_unknown(self):
        assert _tty_is_raw("") is None

    def test_missing_tty_is_unknown(self):
        assert _tty_is_raw("/dev/gitdirector-no-such-tty") is None

    @patch("gitdirector.integrations.tmux.monitor.termios.tcgetattr")
    @patch("gitdirector.integrations.tmux.monitor.os.close")
    @patch("gitdirector.integrations.tmux.monitor.os.open", return_value=7)
    def test_canonical_mode_is_not_raw(self, _mock_open, mock_close, mock_tcgetattr):
        import termios

        mock_tcgetattr.return_value = [0, 0, 0, termios.ICANON | termios.ECHO, 0, 0, []]
        assert _tty_is_raw("/dev/ttys001") is False
        mock_close.assert_called_once_with(7)

    @patch("gitdirector.integrations.tmux.monitor.termios.tcgetattr")
    @patch("gitdirector.integrations.tmux.monitor.os.close")
    @patch("gitdirector.integrations.tmux.monitor.os.open", return_value=7)
    def test_raw_mode_is_raw(self, _mock_open, mock_close, mock_tcgetattr):
        mock_tcgetattr.return_value = [0, 0, 0, 0, 0, 0, []]
        assert _tty_is_raw("/dev/ttys001") is True
        mock_close.assert_called_once_with(7)


def _pane_line(
    session,
    command="bash",
    dead="0",
    pid="101",
    bell="0",
    active="1",
    tty="/dev/ttys001",
    activity="1700000000",
    mouse="0",
    alt="0",
    agent="",
    interrupts="",
    label="",
    description="",
    input_activity="0",
) -> str:
    return "\t".join(
        [
            session,
            command,
            dead,
            pid,
            bell,
            active,
            tty,
            activity,
            mouse,
            alt,
            agent,
            interrupts,
            label,
            description,
            input_activity,
        ]
    )


class TestListGdPanes:
    @patch("subprocess.run")
    def test_failure_returns_none(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="oops")
        assert _list_gd_panes() is None

    @patch("subprocess.run")
    def test_timeout_returns_none(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(["tmux"], 1)
        assert _list_gd_panes() is None

    @pytest.mark.parametrize(
        "stderr",
        [
            "no server running on /tmp/tmux-501/default\n",
            "error connecting to /tmp/tmux-501/default (No such file or directory)\n",
            "error connecting to /tmp/tmux-501/default (Connection refused)\n",
            "lost server\n",
        ],
    )
    @patch("subprocess.run")
    def test_no_server_means_no_sessions(self, mock_run, stderr):
        """tmux exits with its last session; that is an answer, not a failure."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr=stderr)
        assert _list_gd_panes() == {}

    @patch("subprocess.run")
    def test_parses_active_gd_panes_only(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="\n".join(
                [
                    _pane_line("gd/alpha/shell/1", command="zsh"),
                    _pane_line("gd/beta/claude/1", command="node", pid="201", bell="1", mouse="1"),
                    _pane_line("gd/beta/claude/1", command="cat", pid="202", active="0"),
                    _pane_line("gd/panel/main", command="cat"),
                    _pane_line("gd/temp/panel/alpha/shell/1", command="cat"),
                    _pane_line("other-session", command="bash"),
                    "malformed",
                ]
            )
            + "\n",
        )

        panes = _list_gd_panes()

        assert set(panes) == {"gd/alpha/shell/1", "gd/beta/claude/1"}
        assert panes["gd/alpha/shell/1"] == PaneSample(
            "gd/alpha/shell/1",
            "zsh",
            False,
            101,
            False,
            "/dev/ttys001",
            1700000000,
            False,
            "",
            False,
            "",
            "",
        )
        beta = panes["gd/beta/claude/1"]
        assert beta.command == "node"
        assert beta.pane_pid == 201
        assert beta.bell is True
        assert beta.interactive_hint is True

    @patch("subprocess.run")
    def test_dead_pane_and_bad_pid(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=_pane_line("gd/alpha/shell/1", dead="1", pid="?", activity="x") + "\n",
        )

        pane = _list_gd_panes()["gd/alpha/shell/1"]

        assert pane.dead is True
        assert pane.pane_pid == 0
        assert pane.activity == 0

    @patch("subprocess.run")
    def test_parses_agent_reported_state(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=_pane_line("gd/alpha/claude/1", agent="waiting") + "\n"
        )

        assert _list_gd_panes()["gd/alpha/claude/1"].agent_state == "waiting"

    @patch("subprocess.run")
    def test_keeps_the_raw_stamped_report(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=_pane_line("gd/alpha/claude/1", agent="running 1700000005") + "\n"
        )

        assert _list_gd_panes()["gd/alpha/claude/1"].agent_state == "running 1700000005"

    @patch("subprocess.run")
    def test_parses_input_activity(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=_pane_line("gd/alpha/claude/1", input_activity="1700000042") + "\n",
        )

        assert _list_gd_panes()["gd/alpha/claude/1"].input_activity == 1700000042

    @patch("subprocess.run")
    def test_parses_agent_interrupt_flag(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=_pane_line("gd/a/claude/1", agent="running", interrupts="unreported")
            + "\n"
            + _pane_line("gd/b/opencode/1", agent="running")
            + "\n",
        )
        panes = _list_gd_panes()
        assert panes["gd/a/claude/1"].agent_interrupts_unreported is True
        assert panes["gd/b/opencode/1"].agent_interrupts_unreported is False

    @patch("subprocess.run")
    def test_tolerates_older_tmux_without_trailing_fields(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="gd/alpha/shell/1\tzsh\t0\t101\t0\t1\n"
        )

        pane = _list_gd_panes()["gd/alpha/shell/1"]

        assert pane.tty == ""
        assert pane.activity == 0
        assert pane.interactive_hint is False
        assert pane.input_activity == 0


class TestParseAgentReport:
    def test_plain_state_has_no_stamp(self):
        assert _parse_agent_report("running") == ("running", None)

    def test_stamped_state(self):
        assert _parse_agent_report("waiting 1700000005") == ("waiting", 1700000005.0)

    def test_unknown_state_is_no_report(self):
        assert _parse_agent_report("bogus 1700000005") == ("", None)
        assert _parse_agent_report("") == ("", None)

    def test_garbage_stamp_keeps_the_state(self):
        assert _parse_agent_report("idle soon") == ("idle", None)


class TestCursorBlinkFilter:
    def test_single_cell_flip_back_is_noise(self):
        assert _is_cursor_blink("> hello\u258c", "> hello", "> hello") is True

    def test_flip_back_with_many_cells_is_real(self):
        assert _is_cursor_blink("line one", "completely new", "completely new") is False

    def test_change_that_does_not_restore_previous_frame_is_real(self):
        # A spinner touches one cell too, but cycles through many frames.
        assert _is_cursor_blink("spin \u280b", "spin \u2819", "spin \u2839") is False

    def test_needs_history(self):
        assert _is_cursor_blink("a", "b", None) is False


class TestResolvePaneStatus:
    def _status(self, **overrides):
        kwargs = {
            "dead": False,
            "bell": False,
            "command": "some-program",
            "interactive": True,
            "change_age": 100.0,
            "cpu_age": 100.0,
        }
        kwargs.update(overrides)
        return resolve_pane_status(**kwargs)

    def test_dead_returns_idle_even_with_bell(self):
        assert self._status(dead=True, bell=True) == "idle"

    def test_bell_returns_waiting_regardless_of_activity(self):
        assert self._status(bell=True, change_age=0.0) == "waiting"
        assert self._status(bell=True, command="zsh") == "waiting"

    def test_shell_prompt_is_idle(self):
        for shell in _SHELL_COMMANDS:
            assert self._status(command=shell) == "idle"
            assert self._status(command=f"-{shell}") == "idle"

    def test_shell_with_fresh_output_is_running(self):
        assert self._status(command="zsh", change_age=_SHELL_ACTIVITY_GRACE_SECS - 0.5) == "running"
        assert self._status(command="zsh", change_age=_SHELL_ACTIVITY_GRACE_SECS) == "idle"

    def test_recent_content_change_is_running(self):
        assert self._status(change_age=_SILENCE_THRESHOLD_SECS - 0.5) == "running"

    def test_recent_cpu_is_running_even_without_output(self):
        assert self._status(cpu_age=_SILENCE_THRESHOLD_SECS - 0.5) == "running"

    def test_quiet_interactive_program_is_waiting(self):
        assert self._status(interactive=True) == "waiting"

    def test_quiet_non_interactive_program_is_idle(self):
        assert self._status(interactive=False) == "idle"

    def test_exactly_at_threshold_is_quiet(self):
        assert (
            self._status(change_age=_SILENCE_THRESHOLD_SECS, cpu_age=_SILENCE_THRESHOLD_SECS)
            == "waiting"
        )

    def test_does_not_depend_on_program_name(self):
        for command in ("claude", "opencode", "codex", "vim", "python", "node", "my-own-tool"):
            assert self._status(command=command, change_age=1.0) == "running"
            assert self._status(command=command) == "waiting"
            assert self._status(command=command, interactive=False) == "idle"


class TestControlModeReader:
    @patch("threading.Thread")
    def test_start_spawns_thread(self, mock_thread_cls):
        thread = MagicMock()
        mock_thread_cls.return_value = thread
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)

        reader.start()

        assert reader._running is True
        mock_thread_cls.assert_called_once_with(target=reader._run, daemon=True)
        thread.start.assert_called_once_with()

    def test_stop_kills_process_if_terminate_fails(self):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._process = MagicMock()
        reader._process.terminate.side_effect = RuntimeError("boom")

        reader.stop()

        reader._process.kill.assert_called_once_with()

    def test_stop_waits_for_process_when_terminate_succeeds(self):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._process = MagicMock()

        reader.stop()

        reader._process.terminate.assert_called_once_with()
        reader._process.wait.assert_called_once_with(timeout=_CONTROL_MODE_STOP_WAIT_SECS)

    def test_stop_without_wait_skips_waiting_for_process(self):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._process = MagicMock()

        reader.stop(wait=False)

        reader._process.terminate.assert_called_once_with()
        reader._process.wait.assert_not_called()

    def test_stop_ignores_kill_failure(self):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._process = MagicMock()
        reader._process.terminate.side_effect = RuntimeError("boom")
        reader._process.kill.side_effect = RuntimeError("still broken")

        reader.stop()

        reader._process.kill.assert_called_once_with()

    def test_is_alive_reflects_thread_state(self):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._running = True
        reader._thread = MagicMock()
        reader._thread.is_alive.return_value = True

        assert reader.is_alive() is True

    def test_parse_bell(self):
        events = []
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._parse_line("%bell @0 0")
        assert events == [("gd/repo/shell/1", "bell")]

    def test_parse_output_is_ignored(self):
        events = []
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._parse_line("%output %0 some data here")
        assert events == []

    def test_parse_exit(self):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._running = True
        reader._parse_line("%exit")
        assert reader._running is False

    def test_ignores_other_lines(self):
        events = []
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._parse_line("%begin 1234")
        reader._parse_line("%end 1234")
        reader._parse_line("%session-changed $0 mysession")
        reader._parse_line("some random text")
        assert events == []

    @patch("subprocess.Popen")
    def test_run_parses_output_and_cleans_up(self, mock_popen):
        events = []
        process = MagicMock()
        process.stdout = iter(["%bell @0 0\n", "%output %0 hello\n"])
        mock_popen.return_value = process
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._running = True

        reader._run()

        assert events == [("gd/repo/shell/1", "bell")]
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=_CONTROL_MODE_STOP_WAIT_SECS)
        assert reader._running is False
        assert reader._process is None

    @patch("subprocess.Popen")
    def test_run_stops_before_parsing_when_not_running(self, mock_popen):
        events = []
        process = MagicMock()
        process.stdout = iter(["%bell @0 0\n"])
        mock_popen.return_value = process
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._running = False

        reader._run()

        assert events == []
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=_CONTROL_MODE_STOP_WAIT_SECS)

    @patch("subprocess.Popen")
    def test_run_ignores_kill_failure_during_cleanup(self, mock_popen):
        process = MagicMock()
        process.stdout = iter(())
        process.terminate.side_effect = RuntimeError("boom")
        process.kill.side_effect = RuntimeError("still broken")
        mock_popen.return_value = process
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._running = True

        reader._run()

        process.kill.assert_called_once_with()
        assert reader._running is False
        assert reader._process is None

    @patch("subprocess.Popen", side_effect=RuntimeError("boom"))
    def test_run_ignores_popen_errors(self, _mock_popen):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._running = True

        reader._run()

        assert reader._running is False
        assert reader._process is None


class TestTmuxMonitor:
    @patch("threading.Thread")
    def test_start_spawns_sync_thread_once(self, mock_thread_cls):
        monitor = TmuxMonitor()
        thread = MagicMock()
        mock_thread_cls.return_value = thread

        REAL_TMUX_MONITOR_START(monitor)
        REAL_TMUX_MONITOR_START(monitor)

        assert monitor._running is True
        mock_thread_cls.assert_called_once_with(target=monitor._sync_sessions, daemon=True)
        thread.start.assert_called_once_with()

    def test_stop_stops_all_readers_and_clears_registry(self):
        monitor = TmuxMonitor()
        reader_one = MagicMock()
        reader_two = MagicMock()
        monitor._readers = {
            "gd/alpha/shell/1": reader_one,
            "gd/beta/claude/1": reader_two,
        }
        monitor._running = True

        REAL_TMUX_MONITOR_STOP(monitor)

        assert monitor._running is False
        assert monitor._readers == {}
        reader_one.request_stop.assert_called_once_with()
        reader_two.request_stop.assert_called_once_with()
        reader_one.wait_for_stop.assert_called_once_with(timeout=_CONTROL_MODE_STOP_WAIT_SECS)
        reader_two.wait_for_stop.assert_called_once_with(timeout=_CONTROL_MODE_STOP_WAIT_SECS)

    def test_stop_without_wait_requests_reader_shutdown_without_blocking(self):
        monitor = TmuxMonitor()
        reader = MagicMock()
        sync_thread = MagicMock()
        sync_thread.is_alive.return_value = True
        monitor._readers = {"gd/alpha/shell/1": reader}
        monitor._sync_thread = sync_thread
        monitor._running = True

        REAL_TMUX_MONITOR_STOP(monitor, wait=False)

        assert monitor._running is False
        assert monitor._readers == {}
        assert monitor._sync_thread is None
        reader.request_stop.assert_called_once_with()
        reader.wait_for_stop.assert_not_called()
        sync_thread.join.assert_not_called()

    def test_stop_waits_for_sync_thread_to_exit(self):
        monitor = TmuxMonitor()
        sync_thread = MagicMock()
        sync_thread.is_alive.return_value = True
        monitor._sync_thread = sync_thread

        REAL_TMUX_MONITOR_STOP(monitor)

        assert monitor._sync_thread is None
        sync_thread.join.assert_called_once_with(timeout=3)

    def test_stop_stops_readers_attached_while_stopping(self):
        monitor = TmuxMonitor()
        late_reader = MagicMock()
        sync_thread = MagicMock()
        sync_thread.is_alive.return_value = True
        sync_thread.join.side_effect = lambda timeout: monitor._readers.__setitem__(
            "gd/late/shell/1", late_reader
        )
        monitor._sync_thread = sync_thread

        REAL_TMUX_MONITOR_STOP(monitor)

        late_reader.stop.assert_called_once_with(wait=False)
        assert monitor._readers == {}

    @patch("gitdirector.integrations.tmux.monitor._ControlModeReader")
    def test_add_reader_starts_control_reader(self, mock_reader_cls):
        monitor = TmuxMonitor()
        reader = MagicMock()
        mock_reader_cls.return_value = reader

        monitor._add_reader("gd/repo/shell/1")

        assert monitor._readers["gd/repo/shell/1"] is reader
        reader.start.assert_called_once_with()

    def test_bell_event_sets_state_and_status(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        assert monitor.get_bell_state("gd/repo/shell/1") is True
        assert monitor.status_for("gd/repo/shell/1") == "waiting"

    def test_other_events_are_ignored(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "output")
        assert monitor.statuses() == {}

    def test_clear_bell(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        monitor.clear_bell("gd/repo/shell/1")
        assert monitor.get_bell_state("gd/repo/shell/1") is False

    def test_default_states(self):
        monitor = TmuxMonitor()
        assert monitor.get_bell_state("nonexistent") is False
        assert monitor.status_for("nonexistent") is None
        assert monitor.statuses() == {}


class _FakeTmux:
    """Scripted tmux/ps/tty world for exercising ``TmuxMonitor.refresh``."""

    def __init__(self):
        self.panes: dict[str, PaneSample] = {}
        self.snapshot = ProcessSnapshot.empty()
        self.content: dict[str, str | None] = {}
        self.raw: dict[str, bool | None] = {}
        self.captures: list[str] = []
        self.now = 1_700_000_000.0

    def install(self, stack):
        stack.enter_context(
            patch("gitdirector.integrations.tmux.monitor._list_gd_panes", lambda: dict(self.panes))
        )
        stack.enter_context(
            patch(
                "gitdirector.integrations.tmux.monitor._get_process_snapshot", lambda: self.snapshot
            )
        )
        stack.enter_context(
            patch("gitdirector.integrations.tmux.monitor._capture_pane_text", self._capture)
        )
        stack.enter_context(
            patch(
                "gitdirector.integrations.tmux.monitor._tty_is_raw", lambda tty: self.raw.get(tty)
            )
        )
        stack.enter_context(
            patch("gitdirector.integrations.tmux.monitor.time.time", lambda: self.now)
        )

    def _capture(self, session_name):
        self.captures.append(session_name)
        return self.content.get(session_name)

    def pane(self, session_name, **overrides):
        base = {
            "session_name": session_name,
            "command": "bash",
            "dead": False,
            "pane_pid": 100,
            "bell": False,
            "tty": "/dev/ttys001",
            "activity": int(self.now),
            "interactive_hint": False,
            "agent_state": "",
            "agent_interrupts_unreported": False,
            "repo_label": "",
            "description": "",
            "input_activity": 0,
        }
        base.update(overrides)
        self.panes[session_name] = PaneSample(**base)
        return self.panes[session_name]

    def type_keys(self, session_name):
        """A client pressed a key (or attached) just now."""
        self.panes[session_name] = replace(self.panes[session_name], input_activity=int(self.now))

    def run_program(self, session_name, command, *, cpu=0.0, raw=True):
        """Put *command* in the foreground under the pane's shell."""
        pane = self.panes[session_name]
        self.snapshot = ProcessSnapshot(
            {pane.pane_pid: [pane.pane_pid + 1]},
            {pane.pane_pid + 1: command},
            {pane.pane_pid + 1: pane.pane_pid + 1},
            {pane.pane_pid: pane.pane_pid + 1},
            {pane.pane_pid: 0.0, pane.pane_pid + 1: cpu},
        )
        self.raw[pane.tty] = raw

    def advance(self, seconds, session_name=None, content=None, output=True):
        """Move the clock; optionally show *content* in *session_name*.

        With *output* tmux's activity stamp advances too, as it would for
        real output.
        """
        self.now += seconds
        if session_name is not None and content is not None:
            self.content[session_name] = content
            if output:
                self.panes[session_name] = replace(self.panes[session_name], activity=int(self.now))


class TestTmuxMonitorRefresh:
    def _world(self):
        from contextlib import ExitStack

        stack = ExitStack()
        world = _FakeTmux()
        world.install(stack)
        return stack, world

    def test_entries_come_from_the_same_sample(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            assert monitor.entries() is None
            world.pane("gd/alpha_abc23/shell/1", command="zsh", repo_label="", description="")
            world.pane("gd/beta/claude/2", command="zsh", repo_label="Beta", description="wip")
            monitor.refresh()
            assert monitor.entries() == [
                {
                    "session_name": "gd/alpha_abc23/shell/1",
                    "repo": "alpha",
                    "repo_slug": "alpha_abc23",
                    "purpose": "shell",
                    "description": "-",
                },
                {
                    "session_name": "gd/beta/claude/2",
                    "repo": "Beta",
                    "repo_slug": "beta",
                    "purpose": "claude",
                    "description": "wip",
                },
            ]

    def test_failed_listing_keeps_previous_statuses(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", command="zsh")
            world.content["gd/repo/shell/1"] = "$ "
            assert monitor.refresh() == {"gd/repo/shell/1": "running"}

            with patch("gitdirector.integrations.tmux.monitor._list_gd_panes", lambda: None):
                assert monitor.refresh() == {"gd/repo/shell/1": "running"}

    def test_tmux_error_keeps_previous_statuses(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", command="zsh")
            monitor.refresh()

            def boom():
                raise TmuxError("gone")

            with patch("gitdirector.integrations.tmux.monitor._list_gd_panes", boom):
                assert monitor.refresh() == {"gd/repo/shell/1": "running"}

    def test_server_exit_forgets_every_session(self):
        """The last gd session closing takes the tmux server with it.

        Regression: the Sessions tab kept showing an exited session because
        the monitor read the resulting listing failure as "tmux unavailable"
        and held on to its previous entries and statuses.
        """
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", command="zsh")
            world.content["gd/repo/shell/1"] = "$ "
            monitor.refresh()
            assert monitor.entries() != []

            with patch("gitdirector.integrations.tmux.monitor._list_gd_panes", lambda: {}):
                assert monitor.refresh() == {}
            assert monitor.entries() == []
            assert monitor.status_for("gd/repo/shell/1") is None

    def test_vanished_sessions_are_forgotten(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", command="zsh")
            monitor.refresh()
            world.panes.clear()
            assert monitor.refresh() == {}
            assert monitor.status_for("gd/repo/shell/1") is None

    def test_first_sample_seeds_quiet_time_from_tmux_activity(self):
        """A session that has been silent for a minute is idle immediately."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", command="zsh", activity=int(world.now) - 60)
            world.content["gd/repo/shell/1"] = "$ "
            assert monitor.refresh() == {"gd/repo/shell/1": "idle"}

    def test_shell_prompt_goes_idle_after_grace(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", command="zsh")
            world.content["gd/repo/shell/1"] = "$ "
            assert monitor.refresh()["gd/repo/shell/1"] == "running"
            world.advance(_SHELL_ACTIVITY_GRACE_SECS + 0.5)
            assert monitor.refresh()["gd/repo/shell/1"] == "idle"

    def test_interactive_program_running_then_waiting(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/agent/1")
            world.content["gd/repo/agent/1"] = "working \u280b"
            world.run_program("gd/repo/agent/1", "some-agent", raw=True)
            monitor.refresh()
            for frame in ("working \u2819", "working \u2839", "done.\n> "):
                world.advance(1.0, "gd/repo/agent/1", frame)
                assert monitor.refresh()["gd/repo/agent/1"] == "running"

            world.advance(_SILENCE_THRESHOLD_SECS)
            assert monitor.refresh()["gd/repo/agent/1"] == "waiting"

    def test_non_interactive_program_quiet_is_idle(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1")
            world.content["gd/repo/shell/1"] = "listening on :5173"
            world.run_program("gd/repo/shell/1", "node", raw=False)
            monitor.refresh()
            world.advance(_SILENCE_THRESHOLD_SECS + 1)
            assert monitor.refresh()["gd/repo/shell/1"] == "idle"

    def test_tmux_hint_used_when_tty_cannot_be_read(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", interactive_hint=True)
            world.content["gd/repo/shell/1"] = "editor"
            world.run_program("gd/repo/shell/1", "vim", raw=None)
            monitor.refresh()
            world.advance(_SILENCE_THRESHOLD_SECS + 1)
            assert monitor.refresh()["gd/repo/shell/1"] == "waiting"

    def test_sustained_cpu_counts_as_running_without_output(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1")
            world.content["gd/repo/shell/1"] = "compiling..."
            cpu = 1.0
            world.run_program("gd/repo/shell/1", "cc", cpu=cpu, raw=False)
            monitor.refresh()
            world.advance(_SILENCE_THRESHOLD_SECS + 1)
            assert monitor.refresh()["gd/repo/shell/1"] == "idle"
            # A compiler pegs the CPU: a full second of work every second.
            for _ in range(3):
                world.advance(1.0)
                cpu += 1.0
                world.run_program("gd/repo/shell/1", "cc", cpu=cpu, raw=False)
                monitor.refresh()
            assert monitor.status_for("gd/repo/shell/1") == "running"
            world.advance(_SILENCE_THRESHOLD_SECS + 1)
            assert monitor.refresh()["gd/repo/shell/1"] == "idle"

    def test_idle_housekeeping_cpu_bursts_do_not_flap_to_running(self):
        """An agent at its prompt burns a little CPU now and then; that is not work."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/agent/1")
            world.content["gd/repo/agent/1"] = "> "
            cpu = 5.0
            world.run_program("gd/repo/agent/1", "some-agent", cpu=cpu, raw=True)
            monitor.refresh()
            world.advance(_SILENCE_THRESHOLD_SECS + 1)
            assert monitor.refresh()["gd/repo/agent/1"] == "waiting"
            for burst in (0.0, 0.07, 0.01, 0.0, 0.02, 0.0, 0.09, 0.01):
                world.advance(1.0)
                cpu += burst
                world.run_program("gd/repo/agent/1", "some-agent", cpu=cpu, raw=True)
                assert monitor.refresh()["gd/repo/agent/1"] == "waiting", burst

    def test_self_drawn_cursor_blink_does_not_count_as_activity(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/agent/1")
            world.content["gd/repo/agent/1"] = "> \u258c"
            world.run_program("gd/repo/agent/1", "some-agent", raw=True)
            monitor.refresh()
            frames = ["> ", "> \u258c"] * 4
            for frame in frames:
                world.advance(1.0, "gd/repo/agent/1", frame)
                monitor.refresh()
            assert monitor.status_for("gd/repo/agent/1") == "waiting"

    def test_capture_only_when_tmux_reports_new_output(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", command="zsh")
            world.content["gd/repo/shell/1"] = "$ "
            monitor.refresh()
            monitor.refresh()
            monitor.refresh()
            assert world.captures == ["gd/repo/shell/1"]
            world.advance(1.0, "gd/repo/shell/1", "$ ls")
            monitor.refresh()
            assert world.captures == ["gd/repo/shell/1", "gd/repo/shell/1"]

    def test_bell_flag_makes_waiting_until_real_output(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/agent/1")
            world.content["gd/repo/agent/1"] = "spinning \u280b"
            world.run_program("gd/repo/agent/1", "some-agent")
            monitor.refresh()
            world.pane("gd/repo/agent/1", bell=True)
            assert monitor.refresh()["gd/repo/agent/1"] == "waiting"

            # Output inside the grace period is the bell's own render.
            world.advance(_BELL_GRACE_SECS / 2, "gd/repo/agent/1", "result shown")
            assert monitor.refresh()["gd/repo/agent/1"] == "waiting"

            # Later output means the program moved on.
            world.advance(_BELL_GRACE_SECS, "gd/repo/agent/1", "working again \u2819")
            assert monitor.refresh()["gd/repo/agent/1"] == "running"

    def test_control_mode_bell_is_cleared_by_later_output(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/agent/1")
            world.content["gd/repo/agent/1"] = "frame 1"
            world.run_program("gd/repo/agent/1", "some-agent")
            monitor.refresh()
            monitor._on_event("gd/repo/agent/1", "bell")
            assert monitor.refresh()["gd/repo/agent/1"] == "waiting"
            world.advance(_BELL_GRACE_SECS + 1, "gd/repo/agent/1", "frame 2 with more text")
            assert monitor.refresh()["gd/repo/agent/1"] == "running"

    def test_agent_reported_state_wins_over_heuristics(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="idle")
            world.content["gd/repo/claude/1"] = "spinning \u280b"
            world.run_program("gd/repo/claude/1", "claude", cpu=50.0, raw=True)
            monitor.refresh()
            monitor._on_event("gd/repo/claude/1", "bell")
            # Fresh output, CPU, raw tty, even a bell: the hook report is the truth.
            world.advance(1.0, "gd/repo/claude/1", "spinning \u2819")
            assert monitor.refresh()["gd/repo/claude/1"] == "idle"
            assert monitor.get_bell_state("gd/repo/claude/1") is False

            world.pane("gd/repo/claude/1", agent_state="waiting")
            assert monitor.refresh()["gd/repo/claude/1"] == "waiting"
            world.pane("gd/repo/claude/1", agent_state="running")
            assert monitor.refresh()["gd/repo/claude/1"] == "running"

    def test_reported_running_interrupted_by_a_keypress_becomes_idle(self):
        """Escape during a turn fires no hook; the keypress and the redraw give it away."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="running", agent_interrupts_unreported=True)
            world.content["gd/repo/claude/1"] = "working \u280b"
            world.run_program("gd/repo/claude/1", "claude", raw=True)
            monitor.refresh()
            for frame in ("working \u2819", "working \u2839", "working \u2838"):
                world.advance(1.0, "gd/repo/claude/1", frame)
                assert monitor.refresh()["gd/repo/claude/1"] == "running"
            world.type_keys("gd/repo/claude/1")
            world.advance(1.0, "gd/repo/claude/1", "Interrupted. > ")
            assert monitor.refresh()["gd/repo/claude/1"] == "running"
            world.advance(_AGENT_REPORT_STALE_SECS)
            assert monitor.refresh()["gd/repo/claude/1"] == "idle"

    def test_reported_running_with_a_frozen_screen_stays_running(self):
        """Claude Code stops redrawing for stretches of a turn; that is not an interrupt."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="running", agent_interrupts_unreported=True)
            world.content["gd/repo/claude/1"] = "Baking… (10m 15s)"
            world.run_program("gd/repo/claude/1", "claude", raw=True)
            monitor.refresh()
            world.advance(_AGENT_REPORT_STALE_SECS * 20)
            assert monitor.refresh()["gd/repo/claude/1"] == "running"

    def test_attaching_to_a_running_turn_does_not_end_it(self):
        """An attach stamps input like a key, but nothing is redrawn for it."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="running", agent_interrupts_unreported=True)
            world.content["gd/repo/claude/1"] = "Baking… (10m 15s)"
            world.run_program("gd/repo/claude/1", "claude", raw=True)
            monitor.refresh()
            world.advance(3.0)
            world.type_keys("gd/repo/claude/1")
            world.advance(_AGENT_REPORT_STALE_SECS * 20)
            assert monitor.refresh()["gd/repo/claude/1"] == "running"

    def test_a_fresh_stamped_report_outranks_an_earlier_keypress(self):
        """Typing a follow-up mid-turn, then a tool call, then a stall: still running."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane(
                "gd/repo/claude/1",
                agent_state=f"running {int(world.now)}",
                agent_interrupts_unreported=True,
            )
            world.content["gd/repo/claude/1"] = "working \u280b"
            world.run_program("gd/repo/claude/1", "claude", raw=True)
            monitor.refresh()
            world.advance(1.0, "gd/repo/claude/1", "working \u2819 > fix th")
            world.type_keys("gd/repo/claude/1")
            monitor.refresh()
            world.advance(2.0, "gd/repo/claude/1", "Read(file.py) \u2839")
            world.pane(
                "gd/repo/claude/1",
                agent_state=f"running {int(world.now)}",
                agent_interrupts_unreported=True,
            )
            monitor.refresh()
            world.advance(_AGENT_REPORT_STALE_SECS * 20)
            assert monitor.refresh()["gd/repo/claude/1"] == "running"

    def test_reported_idle_with_sustained_output_is_running(self):
        """A turn resumed by a background task fires no hook until its first tool call."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="idle", agent_interrupts_unreported=True)
            world.content["gd/repo/claude/1"] = "> "
            world.run_program("gd/repo/claude/1", "claude", raw=True)
            monitor.refresh()
            world.advance(30.0)
            assert monitor.refresh()["gd/repo/claude/1"] == "idle"

            statuses = []
            for step in range(int(_AGENT_OUTPUT_MIN_SECS) + 2):
                world.advance(1.0, "gd/repo/claude/1", f"thinking {step}")
                statuses.append(monitor.refresh()["gd/repo/claude/1"])
            # A short burst is idle-screen animation; a sustained one is a turn.
            assert statuses[:3] == ["idle"] * 3
            assert statuses[-1] == "running"

            world.advance(1.0, "gd/repo/claude/1", "done. > ")
            assert monitor.refresh()["gd/repo/claude/1"] == "running"
            world.advance(_OUTPUT_GAP_SECS + 0.1)
            assert monitor.refresh()["gd/repo/claude/1"] == "idle"

    def test_reported_idle_stays_idle_while_the_user_types(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="idle", agent_interrupts_unreported=True)
            world.content["gd/repo/claude/1"] = "> "
            world.run_program("gd/repo/claude/1", "claude", raw=True)
            monitor.refresh()
            for text in ("> f", "> fi", "> fix", "> fix ", "> fix t", "> fix th"):
                world.advance(1.0, "gd/repo/claude/1", text)
                world.type_keys("gd/repo/claude/1")
                assert monitor.refresh()["gd/repo/claude/1"] == "idle"

    def test_reported_idle_promotes_once_the_watcher_stops_typing(self):
        """Attaching stamps input too; a spinner that outlives it is the agent's."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="idle", agent_interrupts_unreported=True)
            world.content["gd/repo/claude/1"] = "> "
            world.run_program("gd/repo/claude/1", "claude", raw=True)
            monitor.refresh()
            world.type_keys("gd/repo/claude/1")
            statuses = []
            for step in range(int(_AGENT_OUTPUT_MIN_SECS) + 4):
                world.advance(1.0, "gd/repo/claude/1", f"working {step}")
                statuses.append(monitor.refresh()["gd/repo/claude/1"])
            assert statuses[0] == "idle"
            assert statuses[-1] == "running"

    def test_reported_idle_without_the_gap_flag_is_trusted(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/opencode/1", agent_state="idle")
            world.content["gd/repo/opencode/1"] = "> "
            world.run_program("gd/repo/opencode/1", "opencode", raw=True)
            monitor.refresh()
            for step in range(int(_AGENT_OUTPUT_MIN_SECS) + 4):
                world.advance(1.0, "gd/repo/opencode/1", f"redraw {step}")
                assert monitor.refresh()["gd/repo/opencode/1"] == "idle"

    def test_reported_waiting_stays_while_the_prompt_is_on_screen(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="running", agent_interrupts_unreported=True)
            world.content["gd/repo/claude/1"] = "working \u280b"
            world.run_program("gd/repo/claude/1", "claude", raw=True)
            monitor.refresh()
            world.pane("gd/repo/claude/1", agent_state="waiting", agent_interrupts_unreported=True)
            world.advance(0.5, "gd/repo/claude/1", "Do you want to create probe.txt? 1. Yes 2. No")
            monitor.refresh()
            world.advance(_AGENT_REPORT_STALE_SECS * 20)
            assert monitor.refresh()["gd/repo/claude/1"] == "waiting"

    def test_reported_waiting_dismissed_with_a_keypress_becomes_idle(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="waiting", agent_interrupts_unreported=True)
            world.content["gd/repo/claude/1"] = "Do you want to create probe.txt? 1. Yes 2. No"
            world.run_program("gd/repo/claude/1", "claude", raw=True)
            monitor.refresh()
            world.advance(3.0)
            world.type_keys("gd/repo/claude/1")
            world.advance(1.0, "gd/repo/claude/1", "Interrupted. > ")
            assert monitor.refresh()["gd/repo/claude/1"] == "running"
            world.advance(_AGENT_REPORT_STALE_SECS)
            assert monitor.refresh()["gd/repo/claude/1"] == "idle"

    def test_reported_waiting_that_was_approved_is_running_while_the_tool_works(self):
        """Nothing fires between approving a tool and its PostToolUse."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="waiting", agent_interrupts_unreported=True)
            world.content["gd/repo/claude/1"] = "Run pytest? 1. Yes 2. No"
            world.run_program("gd/repo/claude/1", "claude", raw=True)
            monitor.refresh()
            world.advance(3.0)
            world.type_keys("gd/repo/claude/1")
            for step in range(8):
                world.advance(1.0, "gd/repo/claude/1", f"Bash(pytest) running {step}s")
                assert monitor.refresh()["gd/repo/claude/1"] == "running"

    def test_reported_waiting_ignores_keys_that_change_nothing(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="waiting", agent_interrupts_unreported=True)
            world.content["gd/repo/claude/1"] = "Run pytest? 1. Yes 2. No"
            world.run_program("gd/repo/claude/1", "claude", raw=True)
            monitor.refresh()
            world.advance(3.0)
            world.type_keys("gd/repo/claude/1")
            world.advance(_AGENT_REPORT_STALE_SECS * 20)
            assert monitor.refresh()["gd/repo/claude/1"] == "waiting"

    def test_unknown_agent_state_falls_back_to_heuristics(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", command="zsh", agent_state="bogus")
            world.content["gd/repo/shell/1"] = "$ "
            monitor.refresh()
            world.advance(_SHELL_ACTIVITY_GRACE_SECS + 1)
            assert monitor.refresh()["gd/repo/shell/1"] == "idle"

    def test_reported_running_is_trusted_when_the_agent_reports_interrupts(self):
        """OpenCode turns idle itself on Escape, so a static screen is not suspect."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/opencode/1", agent_state="running")
            world.content["gd/repo/opencode/1"] = "thinking"
            world.run_program("gd/repo/opencode/1", "opencode", raw=True)
            monitor.refresh()
            world.advance(_AGENT_REPORT_STALE_SECS * 10)
            assert monitor.refresh()["gd/repo/opencode/1"] == "running"

    def test_dead_pane_is_idle(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", dead=True, pane_pid=0, bell=True)
            assert monitor.refresh() == {"gd/repo/shell/1": "idle"}


class TestReconcileAgentReport:
    def _reconcile(self, reported, **overrides):
        kwargs = {
            "reported": reported,
            "quiet": False,
            "answered": False,
            "input_age": 60.0,
            "unattended_output_secs": 0.0,
        }
        kwargs.update(overrides)
        return reconcile_agent_report(**kwargs)

    def test_running_is_trusted_however_quiet_without_a_keypress(self):
        assert self._reconcile("running", quiet=True) == "running"

    def test_running_answered_and_quiet_was_interrupted(self):
        assert self._reconcile("running", answered=True, quiet=True) == "idle"

    def test_running_answered_but_still_drawing_stays_running(self):
        assert self._reconcile("running", answered=True, quiet=False) == "running"

    def test_a_keypress_is_debounced_for_the_stale_window(self):
        assert (
            self._reconcile(
                "running", answered=True, quiet=True, input_age=_AGENT_REPORT_STALE_SECS - 1
            )
            == "running"
        )

    def test_waiting_prompt_still_on_screen_stays_waiting(self):
        assert self._reconcile("waiting", quiet=True) == "waiting"

    def test_waiting_answered_is_running_until_quiet(self):
        assert self._reconcile("waiting", answered=True) == "running"
        assert self._reconcile("waiting", answered=True, quiet=True) == "idle"

    def test_idle_with_sustained_unattended_output_is_running(self):
        assert self._reconcile("idle", unattended_output_secs=_AGENT_OUTPUT_MIN_SECS) == "running"

    def test_idle_with_a_short_burst_stays_idle(self):
        assert (
            self._reconcile("idle", unattended_output_secs=_AGENT_OUTPUT_MIN_SECS - 0.5) == "idle"
        )


class TestSyncReaders:
    def test_removes_dead_readers_and_adds_new_ones(self):
        monitor = TmuxMonitor()
        monitor._running = True
        stale_reader = MagicMock()
        existing_reader = MagicMock()
        existing_reader.is_alive.return_value = False
        monitor._readers = {
            "gd/stale/shell/1": stale_reader,
            "gd/existing/shell/1": existing_reader,
        }
        live = [
            "gd/new/shell/1",
            "gd/existing/shell/1",
            "other-session",
            "gd/panel/main",
            "gd/temp/panel/repo/shell/1",
        ]
        added: list[str] = []
        removed: list[str] = []

        def add_reader(session_name: str):
            added.append(session_name)
            replacement = MagicMock()
            replacement.is_alive.return_value = True
            monitor._readers[session_name] = replacement

        def remove_reader(session_name: str):
            removed.append(session_name)
            monitor._readers.pop(session_name, None)

        monitor._add_reader = MagicMock(side_effect=add_reader)
        monitor._remove_reader = MagicMock(side_effect=remove_reader)

        monitor._sync_readers(live)

        assert set(added) == {"gd/new/shell/1"}
        assert set(removed) == {"gd/stale/shell/1", "gd/existing/shell/1"}
        assert monitor._reader_failure_backoff["gd/existing/shell/1"] > time.time()

    def test_skips_reader_retry_during_backoff(self):
        monitor = TmuxMonitor()
        monitor._running = True
        monitor._reader_failure_backoff["gd/repo/shell/1"] = time.time() + 60
        monitor._add_reader = MagicMock()

        monitor._sync_readers(["gd/repo/shell/1"])

        monitor._add_reader.assert_not_called()

    def test_clears_backoff_after_successful_reader_start(self):
        monitor = TmuxMonitor()
        monitor._running = True
        monitor._reader_failure_backoff["gd/repo/shell/1"] = time.time() - 1

        def add_reader(session_name: str):
            monitor._readers[session_name] = MagicMock(is_alive=MagicMock(return_value=True))

        monitor._add_reader = MagicMock(side_effect=add_reader)

        monitor._sync_readers(["gd/repo/shell/1"])

        monitor._add_reader.assert_called_once_with("gd/repo/shell/1")
        assert "gd/repo/shell/1" not in monitor._reader_failure_backoff

    def test_does_not_add_readers_once_stopped(self):
        monitor = TmuxMonitor()
        monitor._running = False
        monitor._add_reader = MagicMock()

        monitor._sync_readers(["gd/repo/shell/1"])

        monitor._add_reader.assert_not_called()

    @patch("gitdirector.integrations.tmux.monitor.time.sleep")
    def test_sync_loop_survives_errors(self, mock_sleep):
        monitor = TmuxMonitor()
        monitor._running = True
        monitor.refresh = MagicMock(side_effect=RuntimeError("boom"))

        def stop_after_first_sleep(_seconds: float):
            monitor._running = False

        mock_sleep.side_effect = stop_after_first_sleep

        monitor._sync_sessions()

        mock_sleep.assert_called()


class TestCapturePaneText:
    @patch("subprocess.run")
    def test_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="pane content\nhere\n")
        assert _capture_pane_text("gd/repo/shell/1") == "pane content\nhere\n"

    @patch("subprocess.run")
    def test_returns_none_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _capture_pane_text("gd/repo/shell/1") is None


# ---------------------------------------------------------------------------
# Edge-case regression tests: tmux exact-match ``=`` prefix
# ---------------------------------------------------------------------------
# tmux uses *prefix matching* when ``-t`` targets don't match exactly.
# Without the ``=`` prefix every ``-t`` argument is vulnerable to accidentally
# matching a session whose name starts with the supplied string – the cascade
# kill bug.  The tests below guarantee the ``=`` prefix is always present.
# ---------------------------------------------------------------------------
