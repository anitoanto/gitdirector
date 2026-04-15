"""Tests for gitdirector.integrations.tmux – all subprocess calls are mocked."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from gitdirector.integrations.tmux import (
    _make_session_name,
    _sanitize_repo_name,
    _session_exists,
    attach_tmux_session,
    create_tmux_session,
    kill_tmux_session,
    list_repo_sessions,
    open_in_tmux,
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
