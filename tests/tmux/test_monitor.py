"""Monitoring and pane-status tests for tmux integration."""

import shlex
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from gitdirector.integrations.tmux import (
    _AGENT_PURPOSES,
    _BELL_GRACE_SECS,
    _SHELL_COMMANDS,
    _SILENCE_THRESHOLD_SECS,
    TmuxMonitor,
    _capture_pane_text,
    _ControlModeReader,
    _get_process_snapshot,
    _hash_content,
    _make_agent_ready_marker,
    _normalize_process_command,
    _resolve_pane_command,
    get_all_session_statuses,
    launch_agent_in_tmux_session,
    resolve_pane_status,
)

from ._shared import REAL_TMUX_MONITOR_START, REAL_TMUX_MONITOR_STOP

class TestLaunchAgentInTmuxSession:
    @patch(
        "gitdirector.integrations.tmux.monitor._make_agent_ready_marker",
        return_value=Path("/tmp/gitdirector-agent.ready"),
    )
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_queues_cleanup_script(self, mock_run, _mock_marker):
        ready_marker = launch_agent_in_tmux_session("gd/my-repo/copilot/1", "copilot")
        cleanup_script = (
            "touch /tmp/gitdirector-agent.ready >/dev/null 2>&1 || true; "
            "clear; copilot; status=$?; "
            "rm -f /tmp/gitdirector-agent.ready >/dev/null 2>&1 || true; "
            f"tmux detach-client -s {shlex.quote('=gd/my-repo/copilot/1')} >/dev/null 2>&1 || true; "
            f"tmux kill-session -t {shlex.quote('=gd/my-repo/copilot/1')} >/dev/null 2>&1 || true; "
            "exit $status"
        )
        expected_command = f"sh -lc {shlex.quote(cleanup_script)}"
        assert ready_marker == Path("/tmp/gitdirector-agent.ready")
        mock_run.assert_called_once_with(
            [
                "tmux",
                "send-keys",
                "-t",
                "=gd/my-repo/copilot/1:",
                expected_command,
                "Enter",
            ],
            check=False,
        )


class TestMakeAgentReadyMarker:
    def test_returns_missing_marker_path(self):
        marker = _make_agent_ready_marker()

        assert marker.name.startswith("gitdirector-agent-")
        assert marker.suffix == ".ready"
        assert marker.exists() is False

    def test_ignores_missing_temp_file(self):
        with patch(
            "gitdirector.integrations.tmux.tempfile.mkstemp",
            return_value=(123, "/tmp/gitdirector-agent-test.ready"),
        ):
            with patch("gitdirector.integrations.tmux.os.close") as mock_close:
                with patch(
                    "gitdirector.integrations.tmux.Path.unlink", side_effect=FileNotFoundError
                ):
                    marker = _make_agent_ready_marker()

        assert marker == Path("/tmp/gitdirector-agent-test.ready")
        mock_close.assert_called_once_with(123)


class TestNormalizeProcessCommand:
    def test_empty_args_return_empty_string(self):
        assert _normalize_process_command("   ") == ""

    def test_returns_executable_basename(self):
        assert _normalize_process_command("/usr/local/bin/claude --model sonnet") == "claude"


class TestGetProcessSnapshot:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_failure_returns_empty_mappings(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")

        assert _get_process_snapshot() == ({}, {}, {}, {})

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_skips_malformed_rows(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="malformed row\n101 1 101 101 -zsh\n",
        )

        children_by_parent, commands_by_pid, pgid_by_pid, tpgid_by_pid = _get_process_snapshot()

        assert children_by_parent == {1: [101]}
        assert commands_by_pid == {101: "-zsh"}
        assert pgid_by_pid == {101: 101}
        assert tpgid_by_pid == {101: 101}


class TestResolvePaneCommand:
    def test_no_descendants_uses_fallback(self):
        assert _resolve_pane_command(1, "shell", "bash", {}, {}, {}, {}) == "bash"

    def test_cycle_skips_seen_pids(self):
        assert (
            _resolve_pane_command(
                1,
                "shell",
                "bash",
                {1: [2], 2: [1]},
                {2: "python"},
                {},
                {},
            )
            == "python"
        )

    def test_only_shell_descendants_pick_deepest_shell(self):
        assert (
            _resolve_pane_command(
                1,
                "shell",
                "bash",
                {1: [2], 2: [3]},
                {2: "-zsh", 3: "sh"},
                {},
                {},
            )
            == "sh"
        )

    def test_prefers_foreground_process_group(self):
        assert (
            _resolve_pane_command(
                1,
                "shell",
                "bash",
                {1: [2, 3]},
                {2: "git", 3: "python"},
                {2: 200, 3: 300},
                {1: 300},
            )
            == "python"
        )

    def test_falls_back_to_deepest_non_shell_without_foreground_match(self):
        assert (
            _resolve_pane_command(
                1,
                "shell",
                "bash",
                {1: [2, 3], 2: [4]},
                {2: "git", 3: "python", 4: "rg"},
                {2: 200, 3: 300, 4: 400},
                {1: 999},
            )
            == "rg"
        )


class TestGetAllSessionStatuses:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_empty_when_no_gd_panes_exist(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="other-session|bash|0|301\n")

        assert get_all_session_statuses() == {}

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_parses_output(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout=(
                    "gd/alpha/shell/1|zsh|0|101\n"
                    "gd/beta/claude/1|bash|0|201\n"
                    "other-session|bash|0|301\n"
                ),
            ),
            MagicMock(
                returncode=0,
                stdout=(
                    "201 1 201 202 -zsh\n202 201 202 202 sh -lc claude\n203 202 202 202 claude\n"
                ),
            ),
        ]
        result = get_all_session_statuses()
        assert result == {
            "gd/alpha/shell/1": {
                "command": "zsh",
                "dead": False,
            },
            "gd/beta/claude/1": {
                "command": "claude",
                "dead": False,
            },
        }

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_skips_panel_and_temp_wrapper_sessions(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout=(
                    "gd/panel/main|cat|0|101\n"
                    "gd/temp/panel/repo/shell/1|zsh|0|201\n"
                    "gd/repo/shell/1|zsh|0|301\n"
                ),
            ),
            MagicMock(returncode=0, stdout="301 1 301 301 zsh\n"),
        ]

        assert get_all_session_statuses() == {
            "gd/repo/shell/1": {
                "command": "zsh",
                "dead": False,
            }
        }

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert get_all_session_statuses() == {}

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_dead_pane(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="gd/repo/shell/1|zsh|1|101\n",
            ),
            MagicMock(returncode=0, stdout="101 1 101 101 zsh\n"),
        ]
        result = get_all_session_statuses()
        assert result["gd/repo/shell/1"]["dead"] is True

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_skips_malformed_lines(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="gd/repo/bad\ngd/repo/shell/1|zsh|0|101\n",
            ),
            MagicMock(returncode=0, stdout="101 1 101 101 zsh\n"),
        ]
        result = get_all_session_statuses()
        assert len(result) == 1
        assert "gd/repo/shell/1" in result

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_invalid_pid_defaults_to_zero(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="gd/repo/shell/1|zsh|0|badnum\n",
            ),
            MagicMock(returncode=0, stdout="101 1 101 101 zsh\n"),
        ]
        result = get_all_session_statuses()
        assert "gd/repo/shell/1" in result

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_prefers_agent_command_over_helper_descendant(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="gd/repo/copilot/1|bash|0|70539\n",
            ),
            MagicMock(
                returncode=0,
                stdout=(
                    "70539 1 70539 70619 -zsh\n"
                    "70619 70539 70619 70619 sh -lc copilot\n"
                    "70624 70619 70619 70619 copilot\n"
                    "70625 70624 70619 70619 git status\n"
                ),
            ),
        ]

        result = get_all_session_statuses()

        assert result["gd/repo/copilot/1"]["command"] == "copilot"


class TestResolvePaneStatus:
    def test_dead_returns_idle(self):
        assert resolve_pane_status("shell", "zsh", dead=True) == "idle"

    def test_shell_with_shell_purpose_returns_idle(self):
        assert resolve_pane_status("shell", "zsh", dead=False) == "idle"

    def test_shell_with_agent_purpose_returns_idle(self):
        assert resolve_pane_status("claude", "zsh", dead=False) == "idle"

    def test_agent_running_returns_running(self):
        assert resolve_pane_status("claude", "claude", dead=False) == "running"

    def test_login_shell_detected(self):
        assert resolve_pane_status("shell", "-zsh", dead=False) == "idle"

    def test_login_shell_with_agent_purpose(self):
        assert resolve_pane_status("opencode", "-bash", dead=False) == "idle"

    def test_non_shell_command_returns_running(self):
        assert resolve_pane_status("shell", "python", dead=False) == "running"

    def test_all_known_shells(self):
        for shell in _SHELL_COMMANDS:
            assert resolve_pane_status("shell", shell, dead=False) == "idle"

    def test_bell_returns_waiting(self):
        assert resolve_pane_status("shell", "zsh", dead=False, bell=True) == "waiting"

    def test_bell_overrides_idle(self):
        assert resolve_pane_status("shell", "zsh", dead=True, bell=True) == "waiting"

    def test_bell_overrides_running(self):
        assert resolve_pane_status("claude", "claude", dead=False, bell=True) == "waiting"

    @patch("gitdirector.integrations.tmux.monitor.time")
    def test_agent_silent_returns_idle(self, mock_time):
        mock_time.time.return_value = 1700000020.0
        old_output = 1700000020.0 - _SILENCE_THRESHOLD_SECS
        assert (
            resolve_pane_status("opencode", "opencode", dead=False, last_output_time=old_output)
            == "idle"
        )

    @patch("gitdirector.integrations.tmux.monitor.time")
    def test_agent_recent_activity_returns_running(self, mock_time):
        mock_time.time.return_value = 1700000020.0
        recent = 1700000020.0 - _SILENCE_THRESHOLD_SECS + 1
        assert (
            resolve_pane_status("claude", "claude", dead=False, last_output_time=recent)
            == "running"
        )

    @patch("gitdirector.integrations.tmux.monitor.time")
    def test_agent_child_command_ignores_silence_threshold(self, mock_time):
        mock_time.time.return_value = 1700000100.0
        assert (
            resolve_pane_status("copilot", "git", dead=False, last_output_time=1700000000.0)
            == "running"
        )

    @patch("gitdirector.integrations.tmux.monitor.time")
    def test_non_agent_purpose_ignores_silence_threshold(self, mock_time):
        mock_time.time.return_value = 1700000100.0
        assert (
            resolve_pane_status("lazygit", "lazygit", dead=False, last_output_time=1700000000.0)
            == "running"
        )

    def test_known_agent_purposes(self):
        assert _AGENT_PURPOSES == {"opencode", "claude", "copilot", "codex"}

    @patch("gitdirector.integrations.tmux.monitor.time")
    def test_shell_purpose_ignores_silence_threshold(self, mock_time):
        mock_time.time.return_value = 1700000100.0
        assert (
            resolve_pane_status("shell", "python", dead=False, last_output_time=1700000000.0)
            == "running"
        )

    def test_zero_output_time_no_idle(self):
        assert (
            resolve_pane_status("opencode", "opencode", dead=False, last_output_time=0.0)
            == "running"
        )

    @patch("gitdirector.integrations.tmux.monitor.time")
    def test_exactly_at_threshold_returns_idle(self, mock_time):
        mock_time.time.return_value = 1700000010.0
        output_time = 1700000010.0 - _SILENCE_THRESHOLD_SECS
        assert (
            resolve_pane_status("opencode", "opencode", dead=False, last_output_time=output_time)
            == "idle"
        )


class TestControlModeReader:
    @patch("gitdirector.integrations.tmux.threading.Thread")
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
        reader._process.wait.assert_called_once_with(timeout=2)

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

    def test_parse_output(self):
        events = []
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._parse_line("%output %0 some data here")
        assert events == [("gd/repo/shell/1", "output")]

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

    @patch("gitdirector.integrations.tmux.subprocess.Popen")
    def test_run_parses_output_and_cleans_up(self, mock_popen):
        events = []
        process = MagicMock()
        process.stdout = iter(["%bell @0 0\n", "%output %0 hello\n"])
        mock_popen.return_value = process
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: events.append((s, e)))
        reader._running = True

        reader._run()

        assert events == [
            ("gd/repo/shell/1", "bell"),
            ("gd/repo/shell/1", "output"),
        ]
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)
        assert reader._running is False
        assert reader._process is None

    @patch("gitdirector.integrations.tmux.subprocess.Popen")
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
        process.wait.assert_called_once_with(timeout=2)

    @patch("gitdirector.integrations.tmux.subprocess.Popen")
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

    @patch("gitdirector.integrations.tmux.subprocess.Popen", side_effect=RuntimeError("boom"))
    def test_run_ignores_popen_errors(self, _mock_popen):
        reader = _ControlModeReader("gd/repo/shell/1", lambda s, e: None)
        reader._running = True

        reader._run()

        assert reader._running is False
        assert reader._process is None


class TestTmuxMonitor:
    @patch("gitdirector.integrations.tmux.threading.Thread")
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
        reader_one.stop.assert_called_once_with()
        reader_two.stop.assert_called_once_with()

    def test_stop_waits_for_sync_thread_to_exit(self):
        monitor = TmuxMonitor()
        sync_thread = MagicMock()
        sync_thread.is_alive.return_value = True
        monitor._sync_thread = sync_thread

        REAL_TMUX_MONITOR_STOP(monitor)

        assert monitor._sync_thread is None
        sync_thread.join.assert_called_once_with(timeout=3)

    @patch("gitdirector.integrations.tmux.monitor._ControlModeReader")
    def test_add_reader_starts_control_reader(self, mock_reader_cls):
        monitor = TmuxMonitor()
        reader = MagicMock()
        mock_reader_cls.return_value = reader

        monitor._add_reader("gd/repo/shell/1")

        assert monitor._readers["gd/repo/shell/1"] is reader
        reader.start.assert_called_once_with()

    def test_bell_event_sets_state(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        assert monitor.get_bell_state("gd/repo/shell/1") is True

    def test_output_event_updates_time(self):
        monitor = TmuxMonitor()
        before = time.time()
        monitor._on_event("gd/repo/shell/1", "output")
        after = time.time()
        last_output = monitor.get_last_output_time("gd/repo/shell/1")
        assert before <= last_output <= after

    def test_output_clears_bell_after_grace_period(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        assert monitor.get_bell_state("gd/repo/shell/1") is True

        with patch("gitdirector.integrations.tmux.monitor.time") as mock_time:
            bell_time = monitor._bell_time["gd/repo/shell/1"]
            mock_time.time.return_value = bell_time + _BELL_GRACE_SECS + 0.1
            monitor._on_event("gd/repo/shell/1", "output")

        assert monitor.get_bell_state("gd/repo/shell/1") is False

    def test_output_does_not_clear_bell_during_grace_period(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        bell_time = monitor._bell_time["gd/repo/shell/1"]

        with patch("gitdirector.integrations.tmux.monitor.time") as mock_time:
            mock_time.time.return_value = bell_time + _BELL_GRACE_SECS - 0.1
            monitor._on_event("gd/repo/shell/1", "output")

        assert monitor.get_bell_state("gd/repo/shell/1") is True

    def test_clear_bell(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        assert monitor.get_bell_state("gd/repo/shell/1") is True
        monitor.clear_bell("gd/repo/shell/1")
        assert monitor.get_bell_state("gd/repo/shell/1") is False

    def test_default_states(self):
        monitor = TmuxMonitor()
        assert monitor.get_bell_state("nonexistent") is False
        assert monitor.get_last_output_time("nonexistent") == 0.0
        assert monitor.get_last_content_change_time("nonexistent") == 0.0

    def test_remove_reader_clears_state(self):
        monitor = TmuxMonitor()
        monitor._on_event("gd/repo/shell/1", "bell")
        monitor._on_event("gd/repo/shell/1", "output")
        monitor._content_hashes["gd/repo/shell/1"] = "abc"
        monitor._last_content_change_time["gd/repo/shell/1"] = 100.0
        reader = MagicMock()
        monitor._readers["gd/repo/shell/1"] = reader
        monitor._remove_reader("gd/repo/shell/1")
        assert monitor.get_bell_state("gd/repo/shell/1") is False
        assert monitor.get_last_output_time("gd/repo/shell/1") == 0.0
        assert monitor.get_last_content_change_time("gd/repo/shell/1") == 0.0
        reader.stop.assert_called_once()

    @patch("gitdirector.integrations.tmux.monitor._capture_pane_text")
    def test_poll_content_changes_detects_new_content(self, mock_capture):
        monitor = TmuxMonitor()
        mock_capture.return_value = "hello world"
        monitor._poll_content_changes({"gd/repo/shell/1"})
        assert monitor.get_last_content_change_time("gd/repo/shell/1") > 0.0
        assert monitor._content_hashes["gd/repo/shell/1"] == _hash_content("hello world")

    @patch("gitdirector.integrations.tmux.monitor._capture_pane_text")
    def test_poll_content_changes_ignores_same_content(self, mock_capture):
        monitor = TmuxMonitor()
        mock_capture.return_value = "static screen"
        monitor._poll_content_changes({"gd/repo/shell/1"})
        first_time = monitor.get_last_content_change_time("gd/repo/shell/1")

        monitor._poll_content_changes({"gd/repo/shell/1"})
        assert monitor.get_last_content_change_time("gd/repo/shell/1") == first_time

    @patch("gitdirector.integrations.tmux.monitor._capture_pane_text")
    def test_poll_content_changes_updates_on_change(self, mock_capture):
        monitor = TmuxMonitor()
        mock_capture.return_value = "screen v1"
        monitor._poll_content_changes({"gd/repo/shell/1"})
        first_time = monitor.get_last_content_change_time("gd/repo/shell/1")

        mock_capture.return_value = "screen v2"
        monitor._poll_content_changes({"gd/repo/shell/1"})
        assert monitor.get_last_content_change_time("gd/repo/shell/1") > first_time

    @patch("gitdirector.integrations.tmux.monitor._capture_pane_text")
    def test_poll_content_changes_skips_failed_capture(self, mock_capture):
        monitor = TmuxMonitor()
        mock_capture.return_value = None
        monitor._poll_content_changes({"gd/repo/shell/1"})
        assert monitor.get_last_content_change_time("gd/repo/shell/1") == 0.0

    @patch("gitdirector.integrations.tmux.monitor._list_sessions")
    def test_sync_sessions_adds_removes_restarts_and_polls(self, mock_list_sessions):
        monitor = TmuxMonitor()
        monitor._running = True
        stale_reader = MagicMock()
        existing_reader = MagicMock()
        existing_reader.is_alive.return_value = False
        monitor._readers = {
            "gd/stale/shell/1": stale_reader,
            "gd/existing/shell/1": existing_reader,
        }
        mock_list_sessions.return_value = [
            "gd/new/shell/1",
            "gd/existing/shell/1",
            "other-session",
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
        monitor._poll_content_changes = MagicMock(
            side_effect=lambda sessions: setattr(monitor, "_running", False)
        )

        monitor._sync_sessions()

        assert set(added) == {"gd/new/shell/1", "gd/existing/shell/1"}
        assert set(removed) == {"gd/stale/shell/1", "gd/existing/shell/1"}
        monitor._poll_content_changes.assert_called_once_with(
            {"gd/new/shell/1", "gd/existing/shell/1"}
        )

    @patch("gitdirector.integrations.tmux.monitor._list_sessions")
    def test_sync_sessions_skips_panel_and_temp_wrapper_sessions(self, mock_list_sessions):
        monitor = TmuxMonitor()
        monitor._running = True
        mock_list_sessions.return_value = [
            "gd/repo/shell/1",
            "gd/panel/main",
            "gd/temp/panel/repo/shell/1",
        ]

        added: list[str] = []

        def add_reader(session_name: str):
            added.append(session_name)
            monitor._readers[session_name] = MagicMock(is_alive=MagicMock(return_value=True))

        monitor._add_reader = MagicMock(side_effect=add_reader)
        monitor._remove_reader = MagicMock()
        monitor._poll_content_changes = MagicMock(
            side_effect=lambda sessions: setattr(monitor, "_running", False)
        )

        monitor._sync_sessions()

        assert added == ["gd/repo/shell/1"]
        monitor._poll_content_changes.assert_called_once_with({"gd/repo/shell/1"})

    @patch("gitdirector.integrations.tmux.monitor.time.sleep")
    @patch("gitdirector.integrations.tmux.monitor._list_sessions", side_effect=RuntimeError("boom"))
    def test_sync_sessions_ignores_list_errors(self, _mock_list_sessions, mock_sleep):
        monitor = TmuxMonitor()
        monitor._running = True

        def stop_after_first_sleep(_seconds: float):
            monitor._running = False

        mock_sleep.side_effect = stop_after_first_sleep

        monitor._sync_sessions()

        mock_sleep.assert_called()


class TestCapturePaneText:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_returns_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="pane content\nhere\n")
        assert _capture_pane_text("gd/repo/shell/1") == "pane content\nhere\n"

    @patch("gitdirector.integrations.tmux.subprocess.run")
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
