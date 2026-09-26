"""Monitoring and pane-status tests for tmux integration."""

import json
import os
import shlex
import subprocess
import threading
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
    _APPROVED_TOOL_MIN_SECS,
    _BELL_GRACE_SECS,
    _CPU_WINDOW_SECS,
    _PANE_LIST_NAMES,
    _RESIZE_REDRAW_SECS,
    _SILENCE_THRESHOLD_SECS,
    PaneSample,
    ProcessSnapshot,
    _capture_pane_text,
    _consume_bell,
    _get_process_snapshot,
    _is_cursor_blink,
    _is_interactive_shell,
    _last_interrupt,
    _list_gd_panes,
    _make_agent_ready_marker,
    _normalize_process_command,
    _parse_agent_report,
    _parse_cpu_seconds,
    _prompt_shell,
    _resolve_pane_command,
    _SessionActivity,
    _started_a_tool_since,
    _tree_cpu_seconds,
    resolve_agent_status,
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
            f"tmux kill-session -g -t {shlex.quote('=gd/my-repo/copilot/1')} >/dev/null 2>&1 || true; "
            "rm -f /tmp/gitdirector-agent.ready >/dev/null 2>&1 || true; "
            "exit $status"
        )
        expected_command = _tmux_child_environment_command(f"sh -c {shlex.quote(cleanup_script)}")
        assert ready_marker == Path("/tmp/gitdirector-agent.ready")
        assert mock_run.call_count == 2
        mock_run.assert_called_with(
            [
                "tmux",
                "respawn-pane",
                "-k",
                "-t",
                "=gd/my-repo/copilot/1:",
                expected_command,
            ],
            capture_output=True,
            text=True,
            env=ANY,
            cwd=ANY,
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
        assert wrapped_script.startswith(_tmux_child_environment_command("sh -c "))


def _inner_shell_script(mock_run) -> str:
    """Return the script the outer ``sh -c`` actually executes.

    The wrapper passed to ``tmux respawn-pane`` is
    ``env ... sh -c <shlex.quote(script)>``; parsing the wrapper as a
    shell line recovers the original script.
    """
    wrapped = mock_run.call_args[0][0][-1]
    parts = shlex.split(wrapped)
    shell_index = parts.index("sh")
    assert parts[shell_index + 1] == "-c"
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
        assert "tmux kill-session -g -t " in script
        assert "tmux detach-client -s " in script
        # The escaped form must be present; the unescaped literal would
        # corrupt the shell parsing of the script.
        assert shlex.quote('=gd/my-repo/echo "hi"/1') in script


class TestMakeAgentReadyMarker:
    def test_returns_missing_marker_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITDIRECTOR_HOME", str(tmp_path))
        marker = _make_agent_ready_marker()

        # Inside GitDirector's own folder, not the system temp directory.
        assert marker.parent == tmp_path / "cache" / "temp"
        assert marker.name.startswith("agent-")
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


def _snapshot(
    children=None, commands=None, pgid=None, tpgid=None, cpu=None, elapsed=None, args=None
) -> ProcessSnapshot:
    return ProcessSnapshot(
        children or {},
        commands or {},
        pgid or {},
        tpgid or {},
        cpu or {},
        elapsed or {},
        args or {},
    )


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
            stdout=(
                "malformed row\n101 1 101 101 0:01.50 01:02:03 -zsh\n"
                "102 101 102 101 00:00:03 00:07 node app.js\n"
            ),
        )

        snapshot = _get_process_snapshot()

        assert snapshot.children_by_parent == {1: [101], 101: [102]}
        assert snapshot.commands_by_pid == {101: "-zsh", 102: "node"}
        assert snapshot.pgid_by_pid == {101: 101, 102: 102}
        assert snapshot.tpgid_by_pid == {101: 101, 102: 101}
        assert snapshot.cpu_seconds_by_pid == {101: 1.5, 102: 3.0}
        assert snapshot.elapsed_by_pid == {101: 3723.0, 102: 7.0}
        assert snapshot.args_by_pid == {101: "-zsh", 102: "node app.js"}


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


class TestPromptShell:
    @pytest.mark.parametrize(
        "args", ["-zsh", "zsh", "/bin/bash", "bash --login", "zsh -il", "-fish", "sh -"]
    )
    def test_interactive_shells(self, args):
        assert _is_interactive_shell(args) is True

    @pytest.mark.parametrize(
        "args",
        [
            "",
            "sh -c clear; codex",
            "bash -lc make",
            "zsh -ic 'npm test'",
            "bash ./build.sh",
            "/bin/sh /usr/local/bin/tool --flag",
            "node server.js",
            "python -i",
        ],
    )
    def test_commands_and_scripts_are_not_interactive(self, args):
        assert _is_interactive_shell(args) is False

    def test_shell_holding_the_terminal_owns_its_helpers(self):
        """Profile scripts and prompt helpers run in the shell's own group."""
        snapshot = _snapshot(
            children={1: [2, 3]},
            commands={1: "-zsh", 2: "oh-my-posh", 3: "gitstatusd"},
            pgid={1: 1, 2: 1, 3: 3},
            tpgid={1: 1},
            args={1: "-zsh", 2: "oh-my-posh print primary", 3: "gitstatusd -s -1"},
        )
        assert _prompt_shell(1, snapshot) == "-zsh"

    def test_a_job_holds_the_terminal(self):
        snapshot = _snapshot(
            children={1: [2]},
            commands={1: "-zsh", 2: "bash"},
            pgid={1: 1, 2: 2},
            tpgid={1: 2},
            args={1: "-zsh", 2: "bash ./build.sh"},
        )
        assert _prompt_shell(1, snapshot) is None

    def test_a_nested_interactive_shell_is_a_prompt(self):
        snapshot = _snapshot(
            children={1: [2]},
            commands={1: "-zsh", 2: "bash"},
            pgid={1: 1, 2: 2},
            tpgid={1: 2},
            args={1: "-zsh", 2: "bash"},
        )
        assert _prompt_shell(1, snapshot) == "bash"

    def test_a_command_run_by_a_non_interactive_shell_is_not_a_prompt(self):
        """An agent launched through ``sh -c`` shares its launcher's group."""
        snapshot = _snapshot(
            children={1: [2]},
            commands={1: "sh", 2: "codex"},
            pgid={1: 1, 2: 1},
            tpgid={1: 1},
            args={1: "sh -c clear; sh -lc codex", 2: "codex"},
        )
        assert _prompt_shell(1, snapshot) is None

    def test_unknown_foreground_is_not_a_prompt(self):
        assert _prompt_shell(1, _snapshot()) is None


class TestTreeCpuSeconds:
    def test_sums_pane_and_descendants(self):
        snapshot = _snapshot(
            children={1: [2], 2: [3]},
            commands={2: "node", 3: "git"},
            cpu={1: 0.5, 2: 2.0, 3: 0.25, 99: 100.0},
        )
        assert _tree_cpu_seconds(1, snapshot) == 2.75


def _pane_line(
    session,
    command="bash",
    dead="0",
    pid="101",
    bell="0",
    active="1",
    tty="/dev/ttys001",
    activity="1700000000",
    size="80x24",
    agent="",
    waiter="",
    transcript="",
    label="",
    description="",
    window_active="1",
    pane_id=None,
    bell_mark="",
    helpers="",
) -> str:
    """One ``list-panes`` row, in the monitor's field order."""
    values = {
        "session": session,
        "command": command,
        "dead": dead,
        "pid": pid,
        "bell": bell,
        "pane_active": active,
        "window_active": window_active,
        "pane_id": pane_id or f"%{pid}",
        "tty": tty,
        "activity": activity,
        "size": size,
        "agent_state": agent,
        "agent_waiter": waiter,
        "agent_transcript": transcript,
        "agent_helpers": helpers,
        "bell_mark": bell_mark,
        "repo_label": label,
        "description": description,
    }
    return "\t".join(values[name] for name in _PANE_LIST_NAMES)


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
                    _pane_line("gd/beta/claude/1", command="node", pid="201", bell="1"),
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
            "80x24",
        )
        beta = panes["gd/beta/claude/1"]
        assert beta.command == "node"
        assert beta.pane_pid == 201
        assert beta.bell is True

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
    def test_parses_the_waiter_and_the_transcript(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=_pane_line(
                "gd/a/claude/1", agent="idle 5", waiter="ab12 7.5", transcript="/t/s.jsonl"
            )
            + "\n",
        )
        pane = _list_gd_panes()["gd/a/claude/1"]
        assert pane.agent_waiter == "ab12 7.5"
        assert pane.agent_transcript == "/t/s.jsonl"

    @patch("subprocess.run")
    def test_tab_in_description_does_not_shift_fields(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=_pane_line("gd/alpha/shell/1", description="left\tright", label="Alpha") + "\n",
        )

        pane = _list_gd_panes()["gd/alpha/shell/1"]

        assert pane.description == "left\tright"
        assert pane.repo_label == "Alpha"

    @patch("subprocess.run")
    def test_incomplete_rows_are_skipped(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="gd/alpha/shell/1\tzsh\t0\t101\t0\t1\n"
        )

        assert _list_gd_panes() == {}

    @patch("subprocess.run")
    def test_active_pane_of_the_current_window_wins(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="\n".join(
                [
                    _pane_line("gd/alpha/shell/1", command="claude", pid="101"),
                    _pane_line("gd/alpha/shell/1", command="zsh", pid="202", window_active="0"),
                ]
            )
            + "\n",
        )

        assert _list_gd_panes()["gd/alpha/shell/1"].command == "claude"


class TestParseAgentReport:
    def test_plain_state_has_no_stamp(self):
        assert _parse_agent_report("running") == ("running", None)

    def test_stamped_state(self):
        assert _parse_agent_report("waiting 1700000005") == ("waiting", 1700000005.0)

    def test_an_approval_suffix_keeps_the_stamp(self):
        assert _parse_agent_report("waiting 1700000005 approval") == ("waiting", 1700000005.0)

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
            "at_prompt": False,
            "change_age": 100.0,
            "cpu_age": 100.0,
        }
        kwargs.update(overrides)
        return resolve_pane_status(**kwargs)

    def test_dead_returns_idle_even_with_bell(self):
        assert self._status(dead=True, bell=True) == "idle"

    def test_bell_returns_waiting_regardless_of_activity(self):
        assert self._status(bell=True, change_age=0.0) == "waiting"
        assert self._status(bell=True, at_prompt=True) == "waiting"

    def test_a_shell_prompt_is_idle_whatever_changes_on_screen(self):
        assert self._status(at_prompt=True) == "idle"
        assert self._status(at_prompt=True, change_age=0.0, cpu_age=0.0) == "idle"

    def test_recent_content_change_is_running(self):
        assert self._status(change_age=_SILENCE_THRESHOLD_SECS - 0.5) == "running"

    def test_recent_cpu_is_running_even_without_output(self):
        assert self._status(cpu_age=_SILENCE_THRESHOLD_SECS - 0.5) == "running"

    def test_a_quiet_program_is_idle(self):
        # Waiting means someone is needed; only a bell says so.
        assert self._status() == "idle"

    def test_exactly_at_threshold_is_quiet(self):
        assert (
            self._status(change_age=_SILENCE_THRESHOLD_SECS, cpu_age=_SILENCE_THRESHOLD_SECS)
            == "idle"
        )


class TestTmuxMonitor:
    @patch("threading.Thread")
    def test_start_spawns_sync_thread_once(self, mock_thread_cls):
        monitor = TmuxMonitor()
        thread = MagicMock()
        mock_thread_cls.return_value = thread

        REAL_TMUX_MONITOR_START(monitor)
        REAL_TMUX_MONITOR_START(monitor)

        mock_thread_cls.assert_called_once_with(
            target=monitor._sync_sessions, args=(monitor._stop_event,), daemon=True
        )
        thread.start.assert_called_once_with()

    def test_stop_without_wait_signals_without_blocking(self):
        monitor = TmuxMonitor()
        sync_thread = MagicMock()
        sync_thread.is_alive.return_value = True
        stop_event = threading.Event()
        monitor._sync_thread = sync_thread
        monitor._stop_event = stop_event

        REAL_TMUX_MONITOR_STOP(monitor, wait=False)

        assert stop_event.is_set()
        assert monitor._sync_thread is None
        sync_thread.join.assert_not_called()

    def test_stop_waits_for_sync_thread_to_exit(self):
        monitor = TmuxMonitor()
        sync_thread = MagicMock()
        sync_thread.is_alive.return_value = True
        monitor._sync_thread = sync_thread
        monitor._stop_event = threading.Event()

        REAL_TMUX_MONITOR_STOP(monitor)

        assert monitor._sync_thread is None
        sync_thread.join.assert_called_once_with(timeout=3)

    def test_restart_does_not_revive_the_previous_loop(self):
        monitor = TmuxMonitor()
        with patch.object(monitor, "refresh"), patch("threading.Thread"):
            REAL_TMUX_MONITOR_START(monitor)
            first_event = monitor._stop_event
            REAL_TMUX_MONITOR_STOP(monitor, wait=False)
            REAL_TMUX_MONITOR_START(monitor)

        assert first_event.is_set()
        assert monitor._stop_event is not first_event
        assert not monitor._stop_event.is_set()

    def test_clear_bell(self):
        monitor = TmuxMonitor()
        monitor._sessions["gd/repo/shell/1"] = _SessionActivity(bell_active=True)
        assert monitor.get_bell_state("gd/repo/shell/1") is True
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
        self.captures: list[str] = []
        self.consumed: list[str] = []
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
            patch("gitdirector.integrations.tmux.monitor.time.time", lambda: self.now)
        )
        stack.enter_context(
            patch("gitdirector.integrations.tmux.monitor._consume_bell", self._consume_bell)
        )

    def _consume_bell(self, session_name, now):
        self.consumed.append(session_name)
        mark = f"{now:.3f}"
        self.panes[session_name] = replace(self.panes[session_name], bell=False, bell_mark=mark)
        return mark

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
            "size": "80x24",
            "agent_state": "",
            "agent_waiter": "",
            "agent_transcript": "",
            "agent_helpers": "",
            "bell_mark": "",
            "repo_label": "",
            "description": "",
        }
        base.update(overrides)
        self.panes[session_name] = PaneSample(**base)
        return self.panes[session_name]

    def run_program(self, session_name, command, *, cpu=0.0, children=()):
        """Put *command* in the foreground under the pane's shell.

        *children* are ``(command, elapsed seconds)`` processes it started.
        """
        pane = self.panes[session_name]
        program = pane.pane_pid + 1
        kids = {program + 1 + index: child for index, child in enumerate(children)}
        self.snapshot = ProcessSnapshot(
            {pane.pane_pid: [program], program: list(kids)},
            {program: command, **{pid: child[0] for pid, child in kids.items()}},
            {program: program},
            {pane.pane_pid: program},
            {pane.pane_pid: 0.0, program: cpu},
            {program: 3600.0, **{pid: child[1] for pid, child in kids.items()}},
        )

    def at_prompt(self, session_name, *, cpu=0.0, helpers=(), jobs=(), job_holds_terminal=False):
        """Put the pane's interactive login shell under it.

        *helpers* run in the shell's own process group (profile scripts,
        prompt renderers); *jobs* are ``(command, args)`` in groups of their
        own. The shell holds the terminal unless *job_holds_terminal*, when
        the first job does.
        """
        shell = self.panes[session_name].pane_pid
        kids = [shell + 1 + index for index in range(len(helpers) + len(jobs))]
        commands = {shell: "-zsh"}
        args = {shell: "-zsh"}
        pgid = {shell: shell}
        for pid, helper in zip(kids, helpers):
            commands[pid] = args[pid] = helper
            pgid[pid] = shell
        job_pids = kids[len(helpers) :]
        for pid, (command, job_args) in zip(job_pids, jobs):
            commands[pid], args[pid] = command, job_args
            pgid[pid] = pid
        self.snapshot = ProcessSnapshot(
            {shell: kids},
            commands,
            pgid,
            {shell: job_pids[0] if job_holds_terminal else shell},
            {shell: cpu},
            {},
            args,
        )

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


_REAL_START = TmuxMonitor.start


class TestTmuxMonitorRefresh:
    def _world(self):
        from contextlib import ExitStack

        stack = ExitStack()
        world = _FakeTmux()
        world.install(stack)
        return stack, world

    def test_a_local_change_withholds_older_samples_until_a_new_one(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/alpha/shell/1", command="zsh", description="old")
            monitor.refresh()
            assert monitor.entries()[0]["description"] == "old"

            monitor.invalidate()
            # Until a sample begun after the change is in, there is nothing to trust.
            assert monitor.entries() is None
            world.pane("gd/alpha/shell/1", command="zsh", description="new")
            monitor.refresh()
            assert monitor.entries()[0]["description"] == "new"

    def test_a_sample_begun_before_the_change_is_withheld(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/alpha/shell/1", command="zsh", description="old")
            listed = dict(world.panes)

            def list_then_change():
                # The console changes tmux while this sample is under way.
                monitor.invalidate()
                return listed

            with patch("gitdirector.integrations.tmux.monitor._list_gd_panes", list_then_change):
                monitor.refresh()
            assert monitor.entries() is None

    def test_invalidate_wakes_the_sampler(self):
        monitor = TmuxMonitor()
        monitor._wake.clear()
        monitor.invalidate()
        assert monitor._wake.is_set()

    def test_start_discards_what_was_sampled_before(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/alpha/shell/1", command="zsh")
            monitor.refresh()
            # conftest stubs start() for every test; this one needs the real one.
            with (
                patch.object(TmuxMonitor, "start", _REAL_START),
                patch.object(monitor, "_sync_sessions"),
            ):
                monitor.start()
                assert monitor.entries() is None
                monitor.stop()

    def test_a_restart_forgets_what_was_on_screen_before_the_pause(self):
        """The console pauses its monitor while a session is attached."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/agent/1")
            world.content["gd/repo/agent/1"] = "before"
            world.run_program("gd/repo/agent/1", "some-agent", cpu=1.0)
            monitor.refresh()
            with (
                patch.object(TmuxMonitor, "start", _REAL_START),
                patch.object(monitor, "_sync_sessions"),
            ):
                monitor.start()
                monitor.stop()
            # A minute of work while paused, all of it long over.
            world.advance(60.0, "gd/repo/agent/1", "after")
            world.pane("gd/repo/agent/1", activity=int(world.now) - 30)
            world.run_program("gd/repo/agent/1", "some-agent", cpu=40.0)
            with (
                patch.object(TmuxMonitor, "start", _REAL_START),
                patch.object(monitor, "_sync_sessions"),
            ):
                monitor.start()
                assert monitor.refresh()["gd/repo/agent/1"] == "idle"
                monitor.stop()

    def test_a_stale_cpu_sample_is_no_baseline(self):
        activity = _SessionActivity()
        now = 1_000.0
        assert not TmuxMonitor._cpu_active(activity, 1.0, now)
        later = now + 2 * _CPU_WINDOW_SECS + 1
        assert not TmuxMonitor._cpu_active(activity, 30.0, later)
        assert TmuxMonitor._cpu_active(activity, 31.0, later + 1)

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
        """A program silent for a minute is idle immediately, a busy one running."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", activity=int(world.now) - 60)
            world.content["gd/repo/shell/1"] = "listening on :5173"
            world.run_program("gd/repo/shell/1", "node")
            assert monitor.refresh() == {"gd/repo/shell/1": "idle"}

            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1")
            assert monitor.refresh() == {"gd/repo/shell/1": "running"}

    def test_fresh_shell_is_idle_from_its_first_sample(self):
        """Regression: an untouched new shell showed running, then settled on idle.

        tmux's activity stamp of a new session is the moment its prompt was
        drawn, and while the profile loads its helpers (completion scripts,
        prompt renderers) are the processes in the foreground, burning CPU.
        None of that is a job the user started.
        """
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/shell/1"
            world.pane(name, command="zsh")
            world.content[name] = ""
            world.at_prompt(name, cpu=0.1, helpers=("Python",))
            assert monitor.refresh() == {name: "idle"}

            world.advance(0.5)
            world.at_prompt(name, cpu=0.9, helpers=("oh-my-posh",))
            assert monitor.refresh() == {name: "idle"}

            world.advance(0.5, name, "\u276f\u276f repo  00:45")
            world.at_prompt(name, cpu=1.2)
            assert monitor.refresh() == {name: "idle"}

            for _ in range(5):
                world.advance(1.0)
                assert monitor.refresh() == {name: "idle"}

    def test_the_prompt_itself_is_never_work(self):
        """Typing, redrawing, finished output and background jobs at a prompt."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/shell/1"
            world.pane(name, command="zsh")
            world.content[name] = "$ "
            world.at_prompt(name)
            monitor.refresh()
            for frame in ("$ l", "$ ls", "$ ls\nREADME.md\n$ ", "$ ls\nREADME.md\n$ \u2026"):
                world.advance(0.5, name, frame)
                assert monitor.refresh() == {name: "idle"}, frame

            cpu = 0.0
            for _ in range(4):
                world.advance(1.0, name, f"$ sleep 100 &\n[1] {cpu}\n$ ")
                cpu += 1.0
                world.at_prompt(name, cpu=cpu, jobs=(("gitstatusd", "gitstatusd -s -1"),))
                assert monitor.refresh() == {name: "idle"}

    def test_a_command_run_at_the_prompt_is_running_until_it_returns(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/shell/1"
            world.pane(name, command="zsh", activity=int(world.now) - 60)
            world.content[name] = "$ "
            world.at_prompt(name)
            assert monitor.refresh() == {name: "idle"}

            world.advance(1.0, name, "$ sleep 5; echo done")
            world.at_prompt(name, jobs=(("sleep", "sleep 5"),), job_holds_terminal=True)
            assert monitor.refresh() == {name: "running"}
            world.advance(1.0)
            assert monitor.refresh() == {name: "running"}

            world.advance(3.0, name, "$ sleep 5; echo done\ndone\n$ ")
            world.at_prompt(name)
            assert monitor.refresh() == {name: "idle"}

    def test_a_shell_script_job_is_a_program_not_a_prompt(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/shell/1"
            world.pane(name, command="zsh", activity=int(world.now) - 60)
            world.content[name] = "$ ./build.sh"
            world.at_prompt(name, jobs=(("bash", "bash ./build.sh"),), job_holds_terminal=True)
            monitor.refresh()
            for step in range(3):
                world.advance(1.0, name, f"$ ./build.sh\nstep {step}")
                assert monitor.refresh() == {name: "running"}
            world.advance(_SILENCE_THRESHOLD_SECS)
            assert monitor.refresh() == {name: "idle"}

    def test_agent_launched_through_sh_c_is_watched_like_any_program(self):
        """The launcher is a non-interactive shell: its child is the program."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/codex/1"
            world.pane(name, command="sh", activity=int(world.now) - 60)
            world.content[name] = "working \u280b"
            world.snapshot = _snapshot(
                children={100: [101]},
                commands={100: "sh", 101: "codex"},
                pgid={100: 100, 101: 100},
                tpgid={100: 100},
                args={100: "sh -c clear; sh -lc codex", 101: "codex"},
            )
            monitor.refresh()
            for frame in ("working \u2819", "working \u2839", "done.\n> "):
                world.advance(1.0, name, frame)
                assert monitor.refresh() == {name: "running"}
            world.advance(_SILENCE_THRESHOLD_SECS)
            assert monitor.refresh() == {name: "idle"}

    def test_redraw_after_a_resize_is_not_work(self):
        """A client attaching at another size makes the program redraw."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/agent/1"
            world.pane(name, activity=int(world.now) - 60)
            world.content[name] = "> "
            world.run_program(name, "some-agent")
            assert monitor.refresh() == {name: "idle"}

            world.advance(0.5, name, "> \n\n")
            world.panes[name] = replace(world.panes[name], size="120x40")
            assert monitor.refresh() == {name: "idle"}
            # The program may finish redrawing only after the sample saw the resize.
            world.advance(1.0, name, ">   \n\n\n")
            assert monitor.refresh() == {name: "idle"}

            world.advance(_RESIZE_REDRAW_SECS, name, "working \u280b")
            assert monitor.refresh() == {name: "running"}

    def test_program_running_then_idle(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/agent/1")
            world.content["gd/repo/agent/1"] = "working \u280b"
            world.run_program("gd/repo/agent/1", "some-agent")
            monitor.refresh()
            for frame in ("working \u2819", "working \u2839", "done.\n> "):
                world.advance(1.0, "gd/repo/agent/1", frame)
                assert monitor.refresh()["gd/repo/agent/1"] == "running"

            world.advance(_SILENCE_THRESHOLD_SECS)
            assert monitor.refresh()["gd/repo/agent/1"] == "idle"

    def test_quiet_server_is_idle(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1")
            world.content["gd/repo/shell/1"] = "listening on :5173"
            world.run_program("gd/repo/shell/1", "node")
            monitor.refresh()
            world.advance(_SILENCE_THRESHOLD_SECS + 1)
            assert monitor.refresh()["gd/repo/shell/1"] == "idle"

    def test_sustained_cpu_counts_as_running_without_output(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1")
            world.content["gd/repo/shell/1"] = "compiling..."
            cpu = 1.0
            world.run_program("gd/repo/shell/1", "cc", cpu=cpu)
            monitor.refresh()
            world.advance(_SILENCE_THRESHOLD_SECS + 1)
            assert monitor.refresh()["gd/repo/shell/1"] == "idle"
            # A compiler pegs the CPU: a full second of work every second.
            for _ in range(3):
                world.advance(1.0)
                cpu += 1.0
                world.run_program("gd/repo/shell/1", "cc", cpu=cpu)
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
            world.run_program("gd/repo/agent/1", "some-agent", cpu=cpu)
            monitor.refresh()
            world.advance(_SILENCE_THRESHOLD_SECS + 1)
            assert monitor.refresh()["gd/repo/agent/1"] == "idle"
            for burst in (0.0, 0.07, 0.01, 0.0, 0.02, 0.0, 0.09, 0.01):
                world.advance(1.0)
                cpu += burst
                world.run_program("gd/repo/agent/1", "some-agent", cpu=cpu)
                assert monitor.refresh()["gd/repo/agent/1"] == "idle", burst

    def test_self_drawn_cursor_blink_does_not_count_as_activity(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/agent/1")
            world.content["gd/repo/agent/1"] = "> \u258c"
            world.run_program("gd/repo/agent/1", "some-agent")
            monitor.refresh()
            frames = ["> ", "> \u258c"] * 4
            for frame in frames:
                world.advance(1.0, "gd/repo/agent/1", frame)
                monitor.refresh()
            assert monitor.status_for("gd/repo/agent/1") == "idle"

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

    def test_a_bell_is_taken_off_the_session_once(self):
        """tmux never clears the flag of a session seen only through a view."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/agent/1")
            world.content["gd/repo/agent/1"] = "done"
            world.run_program("gd/repo/agent/1", "some-agent")
            monitor.refresh()
            world.pane("gd/repo/agent/1", bell=True)
            world.advance(1.0)
            assert monitor.refresh()["gd/repo/agent/1"] == "waiting"
            assert world.consumed == ["gd/repo/agent/1"]
            assert world.panes["gd/repo/agent/1"].bell is False

            world.advance(_BELL_GRACE_SECS, "gd/repo/agent/1", "working again \u2819")
            assert monitor.refresh()["gd/repo/agent/1"] == "running"
            # The next bell rises again.
            world.pane("gd/repo/agent/1", bell=True, activity=int(world.now))
            world.advance(1.0)
            assert monitor.refresh()["gd/repo/agent/1"] == "waiting"
            assert world.consumed == ["gd/repo/agent/1"] * 2

    def test_a_bell_another_monitor_took_still_makes_waiting(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/agent/1", activity=int(world.now) - 60)
            world.content["gd/repo/agent/1"] = "Allow? (y/n)"
            world.run_program("gd/repo/agent/1", "some-agent")
            monitor.refresh()
            world.advance(1.0)
            world.pane("gd/repo/agent/1", activity=int(world.now) - 61, bell_mark=f"{world.now}")
            assert monitor.refresh()["gd/repo/agent/1"] == "waiting"
            assert world.consumed == []

    def test_a_fresh_monitor_reads_a_bell_mark_by_the_output_since(self):
        stack, world = self._world()
        with stack:
            world.pane(
                "gd/repo/agent/1", activity=int(world.now) - 60, bell_mark=f"{world.now - 30}"
            )
            world.content["gd/repo/agent/1"] = "Allow? (y/n)"
            world.run_program("gd/repo/agent/1", "some-agent")
            assert TmuxMonitor().refresh()["gd/repo/agent/1"] == "waiting"

            # Output after the mark: the prompt was answered.
            world.pane(
                "gd/repo/agent/1", activity=int(world.now) - 10, bell_mark=f"{world.now - 30}"
            )
            assert TmuxMonitor().refresh()["gd/repo/agent/1"] == "idle"

    def test_a_shell_session_is_never_waiting(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", command="zsh")
            world.content["gd/repo/shell/1"] = "$ "
            world.at_prompt("gd/repo/shell/1")
            monitor.refresh()
            # A bell at the prompt (say, a failed completion) is still idle.
            world.pane("gd/repo/shell/1", command="zsh", bell=True)
            assert monitor.refresh()["gd/repo/shell/1"] == "idle"
            assert monitor.get_bell_state("gd/repo/shell/1") is False

    def test_a_shell_session_running_a_command_is_running_despite_a_bell(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1")
            world.content["gd/repo/shell/1"] = "building \u280b"
            world.run_program("gd/repo/shell/1", "make")
            monitor.refresh()
            world.pane("gd/repo/shell/1", bell=True)
            world.advance(1.0, "gd/repo/shell/1", "building \u2819")
            assert monitor.refresh()["gd/repo/shell/1"] == "running"

    def test_agent_reported_state_wins_over_heuristics(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/claude/1", agent_state="idle")
            world.content["gd/repo/claude/1"] = "spinning \u280b"
            world.run_program("gd/repo/claude/1", "claude", cpu=50.0)
            world.pane("gd/repo/claude/1", agent_state="idle", bell=True)
            monitor.refresh()
            # Fresh output, CPU, even a bell: the hook report is the truth.
            world.advance(1.0, "gd/repo/claude/1", "spinning \u2819")
            assert monitor.refresh()["gd/repo/claude/1"] == "idle"
            assert monitor.get_bell_state("gd/repo/claude/1") is False

            world.pane("gd/repo/claude/1", agent_state="waiting")
            assert monitor.refresh()["gd/repo/claude/1"] == "waiting"
            world.pane("gd/repo/claude/1", agent_state="running")
            assert monitor.refresh()["gd/repo/claude/1"] == "running"

    def test_unknown_agent_state_falls_back_to_heuristics(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", command="zsh", agent_state="bogus")
            world.content["gd/repo/shell/1"] = "$ "
            world.at_prompt("gd/repo/shell/1")
            assert monitor.refresh()["gd/repo/shell/1"] == "idle"

    def test_reported_running_is_trusted_however_still_the_pane(self):
        """A report is never second-guessed from the screen."""
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/opencode/1", agent_state="running")
            world.content["gd/repo/opencode/1"] = "thinking"
            world.run_program("gd/repo/opencode/1", "opencode")
            monitor.refresh()
            world.advance(_SILENCE_THRESHOLD_SECS * 10)
            assert monitor.refresh()["gd/repo/opencode/1"] == "running"

    def test_dead_pane_is_idle(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            world.pane("gd/repo/shell/1", dead=True, pane_pid=0, bell=True)
            assert monitor.refresh() == {"gd/repo/shell/1": "idle"}


def _transcript_line(kind: str, text: str, when: str) -> str:
    return json.dumps(
        {
            "type": kind,
            "timestamp": when,
            "message": {"role": kind, "content": [{"type": "text", "text": text}]},
        }
    )


class TestLastInterrupt:
    def test_finds_the_latest_interrupt(self, tmp_path):
        transcript = tmp_path / "s.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    _transcript_line(
                        "user", "[Request interrupted by user]", "2026-09-24T18:39:44.780Z"
                    ),
                    _transcript_line("assistant", "sure", "2026-09-24T18:39:50.000Z"),
                    _transcript_line(
                        "user",
                        "[Request interrupted by user for tool use]",
                        "2026-09-24T18:40:08.690Z",
                    ),
                    json.dumps({"type": "system", "subtype": "turn_duration"}),
                ]
            )
            + "\n"
        )
        assert _last_interrupt(str(transcript)) == pytest.approx(1790275208.69)

    def test_a_quoted_marker_is_not_an_interrupt(self, tmp_path):
        # Claude reading this very code must not end its own turn.
        transcript = tmp_path / "s.jsonl"
        transcript.write_text(
            _transcript_line("assistant", "[Request interrupted by user]", "2026-09-24T18:39:44Z")
            + "\n"
            + _transcript_line(
                "user", "grep '[Request interrupted by user'", "2026-09-24T18:39:45Z"
            )
            + "\n"
        )
        assert _last_interrupt(str(transcript)) is None

    def test_missing_or_garbled_files_say_nothing(self, tmp_path):
        assert _last_interrupt(str(tmp_path / "missing.jsonl")) is None
        garbled = tmp_path / "g.jsonl"
        garbled.write_text("{not json [Request interrupted by user\n")
        assert _last_interrupt(str(garbled)) is None


class TestStartedAToolSince:
    def _snapshot(self, *children):
        kids = {11 + index: child for index, child in enumerate(children)}
        return _snapshot(
            children={1: [10], 10: list(kids)},
            commands={10: "claude", **{pid: child[0] for pid, child in kids.items()}},
            elapsed={10: 600.0, **{pid: child[1] for pid, child in kids.items()}},
        )

    def test_a_lasting_process_started_after_the_prompt_is_the_approved_tool(self):
        # Asked at 100; at 110 a shell has run for 5 s, so it started at 105.
        assert _started_a_tool_since(1, self._snapshot(("zsh", 5.0)), 100.0, 110.0)

    def test_older_processes_and_helpers_do_not_count(self):
        assert not _started_a_tool_since(1, self._snapshot(("node", 60.0)), 100.0, 110.0)
        assert not _started_a_tool_since(1, self._snapshot(("caffeinate", 5.0)), 100.0, 110.0)

    def test_a_short_lived_hook_does_not_count(self):
        young = _APPROVED_TOOL_MIN_SECS / 2
        assert not _started_a_tool_since(1, self._snapshot(("sh", young)), 100.0, 110.0)

    def test_a_background_task_spawning_processes_does_not_count(self):
        # A background shell started before the prompt keeps starting jobs.
        snapshot = _snapshot(
            children={1: [10], 10: [11], 11: [12]},
            commands={10: "claude", 11: "zsh", 12: "sleep"},
            elapsed={10: 600.0, 11: 60.0, 12: 3.0},
        )
        assert not _started_a_tool_since(1, snapshot, 100.0, 110.0)


class TestResolveAgentStatus:
    def _status(self, **overrides):
        kwargs = {
            "reported": "running",
            "reported_at": 100.0,
            "waiter_at": None,
            "interrupted_at": None,
            "started_tool": False,
        }
        kwargs.update(overrides)
        return resolve_agent_status(**kwargs)

    def test_the_report_is_trusted(self):
        for status in ("running", "waiting", "idle"):
            assert self._status(reported=status) == status

    def test_an_interrupt_after_the_report_ends_the_turn(self):
        assert self._status(interrupted_at=101.0) == "idle"
        assert self._status(reported="waiting", interrupted_at=101.0) == "idle"

    def test_an_interrupt_before_the_report_is_history(self):
        assert self._status(interrupted_at=99.0) == "running"

    def test_an_approved_prompt_is_running(self):
        assert self._status(reported="waiting", started_tool=True) == "running"
        # Only a pending prompt can be approved.
        assert self._status(reported="idle", started_tool=True) == "idle"

    def test_a_subagent_waiting_makes_the_session_wait(self):
        assert self._status(reported="idle", waiter_at=150.0) == "waiting"
        assert self._status(reported="running", waiter_at=150.0) == "waiting"

    def test_a_subagent_approval_hands_back_to_the_main_thread(self):
        assert self._status(reported="idle", waiter_at=150.0, started_tool=True) == "idle"

    def test_an_interrupt_after_the_subagent_asked_dismisses_it(self):
        assert self._status(reported="running", waiter_at=150.0, interrupted_at=160.0) == "idle"
        assert self._status(reported="running", waiter_at=150.0, interrupted_at=120.0) == "waiting"


class TestAgentRefresh:
    def _world(self):
        from contextlib import ExitStack

        stack = ExitStack()
        world = _FakeTmux()
        world.install(stack)
        return stack, world

    def _iso(self, epoch: float) -> str:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")

    def test_escape_recorded_in_the_transcript_is_idle(self, tmp_path):
        stack, world = self._world()
        with stack:
            transcript = tmp_path / "s.jsonl"
            transcript.write_text("")
            monitor = TmuxMonitor()
            name = "gd/repo/claude-auto/1"
            reported = world.now - 10
            world.pane(name, agent_state=f"running {reported}", agent_transcript=str(transcript))
            world.run_program(name, "claude")
            assert monitor.refresh()[name] == "running"
            transcript.write_text(
                _transcript_line("user", "[Request interrupted by user]", self._iso(world.now - 1))
                + "\n"
            )
            assert monitor.refresh()[name] == "idle"
            # The next prompt re-stamps the report and the old interrupt is history.
            world.pane(
                name, agent_state=f"running {world.now + 1}", agent_transcript=str(transcript)
            )
            world.advance(2)
            assert monitor.refresh()[name] == "running"

    def test_an_approved_command_is_running_while_it_works(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/claude-default/1"
            world.pane(name, agent_state=f"waiting {world.now} approval")
            world.run_program(name, "claude", children=[("caffeinate", 30.0)])
            assert monitor.refresh()[name] == "waiting"
            world.advance(5)
            world.run_program(name, "claude", children=[("caffeinate", 35.0), ("zsh", 3.0)])
            assert monitor.refresh()[name] == "running"

    def test_a_question_stays_waiting_while_other_processes_start(self):
        # Answering fires PostToolUse; a process starting says nothing about it.
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/claude-auto/1"
            world.pane(name, agent_state=f"waiting {world.now}")
            world.run_program(name, "claude")
            assert monitor.refresh()[name] == "waiting"
            world.advance(5)
            world.run_program(name, "claude", children=[("zsh", 3.0)])
            assert monitor.refresh()[name] == "waiting"

    def _subagent_transcript(self, tmp_path, world, agent, quiet, entries=()):
        transcript = tmp_path / "session.jsonl"
        transcript.write_text("")
        subagent = tmp_path / "session" / "subagents" / f"agent-{agent}.jsonl"
        subagent.parent.mkdir(parents=True, exist_ok=True)
        subagent.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
        os.utime(subagent, (world.now - quiet, world.now - quiet))
        return str(transcript)

    # Entries as Claude Code 2.1.282 writes them to a subagent's transcript.
    _TOOL_CALL = {
        "type": "assistant",
        "message": {"stop_reason": None, "content": [{"type": "tool_use", "name": "Bash"}]},
    }
    _TOOL_RESULT = {"type": "user", "message": {"content": [{"type": "tool_result"}]}}
    _LAST_WORD = {
        "type": "assistant",
        "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Done."}]},
    }
    _NOTIFICATION = {
        "type": "user",
        "message": {"content": "[SYSTEM NOTIFICATION - NOT USER INPUT]"},
    }

    def test_an_idle_agent_with_a_working_subagent_is_pending(self, tmp_path):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/claude-auto/1"
            transcript = self._subagent_transcript(tmp_path, world, "a1", quiet=60)
            world.pane(
                name,
                agent_state=f"idle {world.now}",
                agent_helpers="a1",
                agent_transcript=transcript,
            )
            world.run_program(name, "claude")
            assert monitor.refresh()[name] == "pending"
            world.pane(name, agent_state=f"idle {world.now}", agent_transcript=transcript)
            assert monitor.refresh()[name] == "idle"

    def test_opencode_reports_pending_itself(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/opencode/1"
            world.pane(name, agent_state="pending")
            world.run_program(name, "opencode")
            assert monitor.refresh()[name] == "pending"

    def test_running_and_waiting_outrank_pending(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/claude-auto/1"
            world.pane(name, agent_state=f"running {world.now}", agent_helpers="a1")
            world.run_program(name, "claude")
            assert monitor.refresh()[name] == "running"
            world.pane(name, agent_state=f"waiting {world.now}", agent_helpers="a1")
            assert monitor.refresh()[name] == "waiting"

    def test_a_subagent_quiet_for_long_is_taken_as_gone(self, tmp_path):
        # A killed subagent never sends its SubagentStop; a working one writes
        # its transcript at every tool call.
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/claude-auto/1"
            entries = (self._TOOL_CALL, self._TOOL_RESULT, self._TOOL_CALL)
            transcript = self._subagent_transcript(tmp_path, world, "a1", 90, entries)
            world.pane(
                name,
                agent_state=f"idle {world.now}",
                agent_helpers="a1",
                agent_transcript=transcript,
            )
            world.run_program(name, "claude")
            assert monitor.refresh()[name] == "pending"
            world.advance(31)
            assert monitor.refresh()[name] == "idle"

    def test_a_subagent_that_ended_its_turn_is_gone_at_once(self, tmp_path):
        # Seen live: a hand-back landed, the SubagentStop hook never took
        # effect, and the session stayed pending. The transcript's last
        # message says the turn is over.
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/claude-auto/1"
            entries = (self._TOOL_CALL, self._TOOL_RESULT, self._LAST_WORD)
            transcript = self._subagent_transcript(tmp_path, world, "a1", 1, entries)
            pane = dict(agent_state=f"idle {world.now}", agent_helpers="a1")
            world.pane(name, agent_transcript=transcript, **pane)
            world.run_program(name, "claude")
            assert monitor.refresh()[name] == "idle"
            # A notification appended after the end changes nothing.
            entries += (self._NOTIFICATION,)
            self._subagent_transcript(tmp_path, world, "a1", 0, entries)
            world.advance(1)
            assert monitor.refresh()[name] == "idle"
            # Sent a new message, the subagent works again.
            entries += (self._TOOL_CALL,)
            self._subagent_transcript(tmp_path, world, "a1", 0, entries)
            world.advance(1)
            assert monitor.refresh()[name] == "pending"

    def test_one_working_subagent_among_finished_ones_keeps_it_pending(self, tmp_path):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/claude-auto/1"
            self._subagent_transcript(tmp_path, world, "a1", 1, (self._LAST_WORD,))
            transcript = self._subagent_transcript(tmp_path, world, "a2", 1, (self._TOOL_CALL,))
            world.pane(
                name,
                agent_state=f"idle {world.now}",
                agent_helpers="a1 a2 a3",
                agent_transcript=transcript,
            )
            world.run_program(name, "claude")
            assert monitor.refresh()[name] == "pending"

    def test_a_listed_subagent_without_a_transcript_is_gone(self, tmp_path):
        # The list comes from best-effort hooks; nothing to wait for is idle,
        # never pending for good.
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/claude-auto/1"
            transcript = str(tmp_path / "session.jsonl")
            world.pane(
                name,
                agent_state=f"idle {world.now}",
                agent_helpers="a1",
                agent_transcript=transcript,
            )
            world.run_program(name, "claude")
            assert monitor.refresh()[name] == "idle"
            world.pane(name, agent_state=f"idle {world.now}", agent_helpers="a1")
            assert monitor.refresh()[name] == "idle"

    def test_a_background_subagent_asking_makes_it_wait(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/claude-auto/1"
            world.pane(name, agent_state=f"idle {world.now}", agent_waiter=f"ab12 {world.now + 1}")
            world.run_program(name, "claude")
            assert monitor.refresh()[name] == "waiting"
            world.pane(name, agent_state=f"idle {world.now}", agent_waiter="")
            assert monitor.refresh()[name] == "idle"

    def test_a_report_left_by_an_agent_that_exited_is_ignored(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/shell/1"
            world.pane(name, command="zsh", agent_state="running 1", activity=int(world.now) - 60)
            world.content[name] = "$ "
            assert monitor.refresh()[name] == "idle"

    def test_a_reporting_agent_costs_no_pane_captures(self):
        stack, world = self._world()
        with stack:
            monitor = TmuxMonitor()
            name = "gd/repo/opencode/1"
            world.pane(name, agent_state="running")
            world.run_program(name, "opencode")
            for _ in range(3):
                world.advance(1.0, name, "frame")
                monitor.refresh()
            assert world.captures == []


class TestSyncLoop:
    def test_sync_loop_survives_errors(self):
        monitor = TmuxMonitor()
        stop_event = threading.Event()
        calls = []

        def refresh():
            calls.append(1)
            if len(calls) == 2:
                stop_event.set()
            raise RuntimeError("boom")

        monitor.refresh = refresh
        with patch("gitdirector.integrations.tmux.monitor._POLL_SECS", 0):
            monitor._sync_sessions(stop_event)

        assert len(calls) == 2


class TestConsumeBell:
    @patch("gitdirector.integrations.tmux.monitor._run_tmux")
    def test_clears_alerts_and_leaves_a_mark_without_killing(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        assert _consume_bell("gd/repo/agent/1", 1234.5) == "1234.500"
        assert mock_run.call_args.args[0] == [
            "set-option",
            "-t",
            "=gd/repo/agent/1:",
            "@gd_bell_at",
            "1234.500",
            ";",
            "kill-session",
            "-C",
            "-t",
            "=gd/repo/agent/1",
        ]

    @patch("gitdirector.integrations.tmux.monitor._run_tmux")
    def test_failure_leaves_no_mark(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 1, "", "no session")
        assert _consume_bell("gd/repo/agent/1", 1234.5) is None


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
