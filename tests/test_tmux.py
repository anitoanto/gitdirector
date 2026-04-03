"""Tests for gitdirector.integrations.tmux – all subprocess calls are mocked."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from gitdirector.integrations.tmux import (
    _alphanumeric_name,
    _make_session_name,
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


class TestAlphanumericName:
    def test_strips_hyphens_and_dots(self):
        assert _alphanumeric_name("my-repo.name") == "myreponame"

    def test_strips_spaces_and_special(self):
        assert _alphanumeric_name("a b@c!d") == "abcd"

    def test_leaves_alphanumeric_untouched(self):
        assert _alphanumeric_name("abc123") == "abc123"

    def test_empty_string(self):
        assert _alphanumeric_name("") == ""


class TestMakeSessionName:
    @patch("gitdirector.integrations.tmux.coolname.generate_slug", return_value="happy-panda")
    def test_format(self, _mock_slug):
        name = _make_session_name("my-repo")
        assert name == "gd-myrepo-happy-panda"

    @patch("gitdirector.integrations.tmux.coolname.generate_slug", return_value="cool-slug")
    def test_special_chars_stripped(self, _mock_slug):
        name = _make_session_name("foo.bar/baz")
        assert name == "gd-foobarbaz-cool-slug"


# ---------------------------------------------------------------------------
# Subprocess-based functions
# ---------------------------------------------------------------------------


class TestSessionExists:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert _session_exists("gd-repo-slug") is True
        mock_run.assert_called_once_with(
            ["tmux", "has-session", "-t", "gd-repo-slug"],
            capture_output=True,
        )

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_not_exists(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert _session_exists("gd-repo-slug") is False


class TestListRepoSessions:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_returns_matching_sessions(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="gd-myrepo-happy-panda\ngd-myrepo-cool-slug\ngd-other-xyz\n",
        )
        result = list_repo_sessions("my-repo")
        assert result == ["gd-myrepo-cool-slug", "gd-myrepo-happy-panda"]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_no_sessions_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert list_repo_sessions("my-repo") == []

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_no_matching_sessions(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="gd-other-abc\n")
        assert list_repo_sessions("my-repo") == []


class TestCreateTmuxSession:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    @patch("gitdirector.integrations.tmux._session_exists", return_value=False)
    @patch(
        "gitdirector.integrations.tmux._make_session_name",
        return_value="gd-myrepo-happy-panda",
    )
    def test_creates_and_returns_name(self, _mock_name, _mock_exists, mock_run):
        path = Path("/tmp/my-repo")
        name = create_tmux_session("my-repo", path)
        assert name == "gd-myrepo-happy-panda"
        mock_run.assert_called_once_with(
            ["tmux", "new-session", "-d", "-s", "gd-myrepo-happy-panda", "-c", "/tmp/my-repo"],
            check=True,
        )

    @patch("gitdirector.integrations.tmux.subprocess.run")
    @patch(
        "gitdirector.integrations.tmux._session_exists",
        side_effect=[True, True, False],
    )
    @patch(
        "gitdirector.integrations.tmux._make_session_name",
        side_effect=["gd-r-a", "gd-r-b", "gd-r-c"],
    )
    def test_retries_on_collision(self, _mock_name, _mock_exists, mock_run):
        name = create_tmux_session("r", Path("/tmp/r"))
        # Should have picked the third name (first two "existed")
        assert name == "gd-r-c"


class TestKillTmuxSession:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert kill_tmux_session("gd-repo-slug") is True

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert kill_tmux_session("gd-repo-slug") is False


class TestAttachTmuxSession:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_inside_tmux_switches_client(self, mock_run):
        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,12345,0"}):
            attach_tmux_session("gd-repo-slug")
        mock_run.assert_called_once_with(["tmux", "switch-client", "-t", "gd-repo-slug"])

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_outside_tmux_attaches(self, mock_run):
        with patch.dict("os.environ", {}, clear=True):
            attach_tmux_session("gd-repo-slug")
        mock_run.assert_called_once_with(["tmux", "attach-session", "-t", "gd-repo-slug"])


class TestOpenInTmux:
    @patch("gitdirector.integrations.tmux.attach_tmux_session")
    @patch(
        "gitdirector.integrations.tmux.create_tmux_session",
        return_value="gd-myrepo-slug",
    )
    def test_creates_then_attaches(self, mock_create, mock_attach):
        path = Path("/tmp/my-repo")
        open_in_tmux("my-repo", path)
        mock_create.assert_called_once_with("my-repo", path)
        mock_attach.assert_called_once_with("gd-myrepo-slug")
