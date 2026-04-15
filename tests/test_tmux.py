"""Tests for gitdirector.integrations.tmux – all subprocess calls are mocked."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from gitdirector.integrations.tmux import (
    _SHELL_COMMANDS,
    _SILENCE_THRESHOLD_SECS,
    _make_session_name,
    _sanitize_repo_name,
    _session_exists,
    attach_tmux_session,
    create_tmux_session,
    get_all_session_statuses,
    kill_tmux_session,
    list_repo_sessions,
    open_in_tmux,
    resolve_pane_status,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestSanitizeRepoName:
    def test_lowercases(self):
        assert _sanitize_repo_name("MyRepo") == "myrepo"

    def test_keeps_hyphens(self):
        assert _sanitize_repo_name("my-repo") == "my-repo"

    def test_replaces_dots_and_slashes(self):
        assert _sanitize_repo_name("foo.bar/baz") == "foo-bar-baz"

    def test_strips_special_chars(self):
        assert _sanitize_repo_name("a b@c!d") == "a-b-c-d"

    def test_collapses_hyphens(self):
        assert _sanitize_repo_name("a--b---c") == "a-b-c"

    def test_strips_leading_trailing_hyphens(self):
        assert _sanitize_repo_name("-repo-") == "repo"

    def test_leaves_alphanumeric_untouched(self):
        assert _sanitize_repo_name("abc123") == "abc123"

    def test_empty_string(self):
        assert _sanitize_repo_name("") == ""


class TestMakeSessionName:
    @patch(
        "gitdirector.integrations.tmux._list_sessions",
        return_value=[],
    )
    def test_first_session(self, _mock_list):
        name = _make_session_name("my-repo")
        assert name == "gd/my-repo/shell/1"

    @patch(
        "gitdirector.integrations.tmux._list_sessions",
        return_value=["gd/my-repo/shell/1", "gd/my-repo/shell/2"],
    )
    def test_increments_past_existing(self, _mock_list):
        name = _make_session_name("my-repo")
        assert name == "gd/my-repo/shell/3"

    @patch(
        "gitdirector.integrations.tmux._list_sessions",
        return_value=["gd/my-repo/shell/1", "gd/my-repo/shell/3"],
    )
    def test_increments_past_max_with_gap(self, _mock_list):
        name = _make_session_name("my-repo")
        assert name == "gd/my-repo/shell/4"

    @patch(
        "gitdirector.integrations.tmux._list_sessions",
        return_value=["gd/my-repo/claude/1"],
    )
    def test_purpose_shell_independent_of_agent(self, _mock_list):
        name = _make_session_name("my-repo", "shell")
        assert name == "gd/my-repo/shell/1"

    @patch(
        "gitdirector.integrations.tmux._list_sessions",
        return_value=["gd/my-repo/claude/1"],
    )
    def test_purpose_agent(self, _mock_list):
        name = _make_session_name("my-repo", "claude")
        assert name == "gd/my-repo/claude/2"

    @patch(
        "gitdirector.integrations.tmux._list_sessions",
        return_value=[],
    )
    def test_special_chars_sanitized(self, _mock_list):
        name = _make_session_name("foo.bar/baz")
        assert name == "gd/foo-bar-baz/shell/1"


# ---------------------------------------------------------------------------
# Subprocess-based functions
# ---------------------------------------------------------------------------


class TestSessionExists:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert _session_exists("gd/repo/shell/1") is True
        mock_run.assert_called_once_with(
            ["tmux", "has-session", "-t", "gd/repo/shell/1"],
            capture_output=True,
        )

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_not_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert _session_exists("gd/repo/shell/1") is False


class TestListRepoSessions:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_returns_matching_sessions(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="gd/my-repo/shell/1\ngd/my-repo/claude/1\ngd/other/shell/1\n",
        )
        result = list_repo_sessions("my-repo")
        assert result == ["gd/my-repo/claude/1", "gd/my-repo/shell/1"]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_no_sessions_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert list_repo_sessions("my-repo") == []

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_no_matching_sessions(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="gd/other/shell/1\n")
        assert list_repo_sessions("my-repo") == []


class TestCreateTmuxSession:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    @patch("gitdirector.integrations.tmux._session_exists", return_value=False)
    @patch(
        "gitdirector.integrations.tmux._make_session_name",
        return_value="gd/my-repo/shell/1",
    )
    def test_creates_and_returns_name(self, _mock_name, _mock_exists, mock_run):
        path = Path("/tmp/my-repo")
        name = create_tmux_session("my-repo", path)
        assert name == "gd/my-repo/shell/1"
        mock_run.assert_called_once_with(
            ["tmux", "new-session", "-d", "-s", "gd/my-repo/shell/1", "-c", "/tmp/my-repo"],
            check=True,
        )

    @patch("gitdirector.integrations.tmux.subprocess.run")
    @patch(
        "gitdirector.integrations.tmux._session_exists",
        side_effect=[True, True, False],
    )
    @patch(
        "gitdirector.integrations.tmux._make_session_name",
        side_effect=["gd/r/shell/1", "gd/r/shell/2", "gd/r/shell/3"],
    )
    def test_retries_on_collision(self, _mock_name, _mock_exists, mock_run):
        name = create_tmux_session("r", Path("/tmp/r"))
        assert name == "gd/r/shell/3"

    @patch("gitdirector.integrations.tmux.subprocess.run")
    @patch("gitdirector.integrations.tmux._session_exists", return_value=False)
    @patch(
        "gitdirector.integrations.tmux._make_session_name",
        return_value="gd/my-repo/claude/1",
    )
    def test_creates_with_purpose(self, _mock_name, _mock_exists, mock_run):
        path = Path("/tmp/my-repo")
        name = create_tmux_session("my-repo", path, purpose="claude")
        assert name == "gd/my-repo/claude/1"
        _mock_name.assert_called_with("my-repo", "claude")


class TestKillTmuxSession:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert kill_tmux_session("gd/repo/shell/1") is True

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert kill_tmux_session("gd/repo/shell/1") is False


class TestAttachTmuxSession:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_inside_tmux_switches_client(self, mock_run):
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            attach_tmux_session("gd/repo/shell/1")
        mock_run.assert_called_once_with(["tmux", "switch-client", "-t", "gd/repo/shell/1"])

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_outside_tmux_attaches(self, mock_run):
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd/repo/shell/1")
        mock_run.assert_called_once_with(["tmux", "attach-session", "-t", "gd/repo/shell/1"])


class TestOpenInTmux:
    @patch("gitdirector.integrations.tmux.attach_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.create_tmux_session",
        return_value="gd/my-repo/shell/1",
    )
    def test_creates_then_attaches(self, mock_create, mock_attach):
        path = Path("/tmp/my-repo")
        open_in_tmux("my-repo", path)
        mock_create.assert_called_once_with("my-repo", path)
        mock_attach.assert_called_once_with("gd/my-repo/shell/1")


class TestGetAllSessionStatuses:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_parses_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "gd/alpha/shell/1|0|zsh|0|1700000000\n"
                "gd/beta/claude/1|1|claude|0|1700000010\n"
                "other-session|0|bash|0|1700000000\n"
            ),
        )
        result = get_all_session_statuses()
        assert result == {
            "gd/alpha/shell/1": {
                "bell": False,
                "command": "zsh",
                "dead": False,
                "activity": 1700000000,
            },
            "gd/beta/claude/1": {
                "bell": True,
                "command": "claude",
                "dead": False,
                "activity": 1700000010,
            },
        }

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert get_all_session_statuses() == {}

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_dead_pane(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="gd/repo/shell/1|0|zsh|1|1700000000\n",
        )
        result = get_all_session_statuses()
        assert result["gd/repo/shell/1"]["dead"] is True

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_skips_malformed_lines(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="gd/repo/bad\ngd/repo/shell/1|0|zsh|0|1700000000\n",
        )
        result = get_all_session_statuses()
        assert len(result) == 1
        assert "gd/repo/shell/1" in result

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_invalid_activity_defaults_to_zero(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="gd/repo/shell/1|0|zsh|0|badnum\n",
        )
        result = get_all_session_statuses()
        assert result["gd/repo/shell/1"]["activity"] == 0


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

    @patch("gitdirector.integrations.tmux.time")
    def test_agent_silent_returns_waiting(self, mock_time):
        mock_time.time.return_value = 1700000020.0
        old_activity = 1700000020 - _SILENCE_THRESHOLD_SECS
        assert (
            resolve_pane_status("opencode", "opencode", dead=False, last_activity=old_activity)
            == "waiting"
        )

    @patch("gitdirector.integrations.tmux.time")
    def test_agent_recent_activity_returns_running(self, mock_time):
        mock_time.time.return_value = 1700000020.0
        recent = 1700000020 - _SILENCE_THRESHOLD_SECS + 1
        assert (
            resolve_pane_status("claude", "claude", dead=False, last_activity=recent) == "running"
        )

    @patch("gitdirector.integrations.tmux.time")
    def test_shell_purpose_never_silence_waiting(self, mock_time):
        mock_time.time.return_value = 1700000100.0
        assert (
            resolve_pane_status("shell", "python", dead=False, last_activity=1700000000)
            == "running"
        )

    def test_zero_activity_no_waiting(self):
        assert resolve_pane_status("opencode", "opencode", dead=False, last_activity=0) == "running"

    @patch("gitdirector.integrations.tmux.time")
    def test_exactly_at_threshold(self, mock_time):
        mock_time.time.return_value = 1700000010.0
        activity = 1700000010 - _SILENCE_THRESHOLD_SECS
        assert (
            resolve_pane_status("opencode", "opencode", dead=False, last_activity=activity)
            == "waiting"
        )
