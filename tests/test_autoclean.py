"""Tests for the autoclean command (links and sessions)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from gitdirector.cli import cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_manager(**overrides):
    mgr = MagicMock()
    mgr.config.repositories = []
    mgr.config.max_workers = 2
    for key, val in overrides.items():
        setattr(mgr, key, MagicMock(return_value=val))
    return mgr


# ---------------------------------------------------------------------------
# autoclean links
# ---------------------------------------------------------------------------


class TestAutocleanLinks:
    def test_no_broken_links(self, runner, tmp_path, monkeypatch):
        """When all links are valid, prints success message."""
        repo = tmp_path / "repo"
        repo.mkdir()

        config = MagicMock()
        config.repositories = [repo]

        with patch("gitdirector.commands.autoclean.Config", return_value=config):
            result = runner.invoke(cli, ["autoclean", "links"])
        assert result.exit_code == 0
        assert "All links are valid" in result.output

    def test_broken_links_confirmed(self, runner, tmp_path, monkeypatch):
        """When broken links exist and user confirms, they are removed."""
        existing = tmp_path / "existing"
        existing.mkdir()
        broken1 = tmp_path / "gone1"
        broken2 = tmp_path / "gone2"

        config = MagicMock()
        config.repositories = [existing, broken1, broken2]

        with patch("gitdirector.commands.autoclean.Config", return_value=config):
            result = runner.invoke(cli, ["autoclean", "links"], input="y\n")
        assert result.exit_code == 0
        assert "2" in result.output
        assert "Removed" in result.output
        config.remove_repositories.assert_called_once_with([broken1, broken2])

    def test_broken_links_cancelled(self, runner, tmp_path):
        """When user declines, no links are removed."""
        broken = tmp_path / "gone"

        config = MagicMock()
        config.repositories = [broken]

        with patch("gitdirector.commands.autoclean.Config", return_value=config):
            result = runner.invoke(cli, ["autoclean", "links"], input="n\n")
        assert result.exit_code == 0
        assert "Cancelled" in result.output
        config.remove_repository.assert_not_called()

    def test_broken_links_displays_paths(self, runner, tmp_path):
        """Broken link paths are printed so the user can review them."""
        from gitdirector.commands.autoclean import console

        broken = tmp_path / "vanished"

        config = MagicMock()
        config.repositories = [broken]

        original_width = console.width
        console.width = 20
        try:
            with patch("gitdirector.commands.autoclean.Config", return_value=config):
                result = runner.invoke(cli, ["autoclean", "links"], input="y\n")
        finally:
            console.width = original_width

        assert "vanished" in result.output


# ---------------------------------------------------------------------------
# autoclean sessions
# ---------------------------------------------------------------------------


class TestAutocleanSessions:
    def test_no_sessions(self, runner):
        """When no gd/ sessions exist, prints no-sessions message."""
        with patch("gitdirector.commands.autoclean._list_gd_sessions", return_value=[]):
            result = runner.invoke(cli, ["autoclean", "sessions"])
        assert result.exit_code == 0
        assert "No gitdirector tmux sessions" in result.output

    def test_sessions_confirmed(self, runner):
        """When user confirms, all gd/ sessions are killed."""
        sessions = ["gd/repo1/shell/1", "gd/repo2/claude/1"]

        with patch("gitdirector.commands.autoclean._list_gd_sessions", return_value=sessions):
            with patch(
                "gitdirector.commands.autoclean._kill_session", return_value=True
            ) as mock_kill:
                result = runner.invoke(cli, ["autoclean", "sessions"], input="y\n")
        assert result.exit_code == 0
        assert "Killed 2" in result.output
        assert mock_kill.call_count == 2
        mock_kill.assert_any_call("gd/repo1/shell/1")
        mock_kill.assert_any_call("gd/repo2/claude/1")

    def test_sessions_cancelled(self, runner):
        """When user declines, no sessions are killed."""
        sessions = ["gd/repo1/shell/1"]

        with patch("gitdirector.commands.autoclean._list_gd_sessions", return_value=sessions):
            with patch("gitdirector.commands.autoclean._kill_session") as mock_kill:
                result = runner.invoke(cli, ["autoclean", "sessions"], input="n\n")
        assert result.exit_code == 0
        assert "Cancelled" in result.output
        mock_kill.assert_not_called()

    def test_sessions_displayed(self, runner):
        """Session names are shown to the user before confirmation."""
        sessions = ["gd/myrepo/shell/1"]

        with patch("gitdirector.commands.autoclean._list_gd_sessions", return_value=sessions):
            with patch("gitdirector.commands.autoclean._kill_session", return_value=True):
                result = runner.invoke(cli, ["autoclean", "sessions"], input="y\n")
        assert "gd/myrepo/shell/1" in result.output

    def test_sessions_kill_failure(self, runner):
        """When a session fails to kill, it is reported."""
        sessions = ["gd/repo1/shell/1", "gd/repo2/claude/1"]

        def selective_kill(name):
            return name != "gd/repo2/claude/1"

        with patch("gitdirector.commands.autoclean._list_gd_sessions", return_value=sessions):
            with patch("gitdirector.commands.autoclean._kill_session", side_effect=selective_kill):
                result = runner.invoke(cli, ["autoclean", "sessions"], input="y\n")
        assert result.exit_code == 0
        assert "Killed 1" in result.output
        assert "Failed to kill" in result.output
        assert "gd/repo2/claude/1" in result.output


# ---------------------------------------------------------------------------
# autoclean – invalid target
# ---------------------------------------------------------------------------


class TestAutocleanInvalidTarget:
    def test_invalid_target(self, runner):
        """An invalid target argument shows an error."""
        result = runner.invoke(cli, ["autoclean", "foobar"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Unit tests for _list_gd_sessions and _kill_session
# ---------------------------------------------------------------------------


class TestListGdSessions:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_returns_gd_sessions(self, mock_run):
        from gitdirector.commands.autoclean import _list_gd_sessions

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=("gd/repo1/shell/1\nother-session\ngd/repo2/claude/1\ngd-legacy-session\n"),
        )
        result = _list_gd_sessions()
        assert result == ["gd/repo1/shell/1", "gd/repo2/claude/1"]

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_no_tmux_server(self, mock_run):
        from gitdirector.commands.autoclean import _list_gd_sessions

        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _list_gd_sessions() == []

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_no_gd_sessions(self, mock_run):
        from gitdirector.commands.autoclean import _list_gd_sessions

        mock_run.return_value = MagicMock(returncode=0, stdout="other-session\ngd-legacy-session\n")
        assert _list_gd_sessions() == []

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_includes_temp_panel_sessions(self, mock_run):
        from gitdirector.commands.autoclean import _list_gd_sessions

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="gd/repo1/shell/1\ngd/temp/panel/repo1/shell/1\nother-session\n",
        )
        result = _list_gd_sessions()
        assert "gd/temp/panel/repo1/shell/1" in result
        assert result == ["gd/repo1/shell/1", "gd/temp/panel/repo1/shell/1"]


class TestKillSession:
    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_kill_success(self, mock_run):
        from gitdirector.commands.autoclean import _kill_session

        mock_run.return_value = MagicMock(returncode=0)
        assert _kill_session("gd/repo/shell/1") is True
        mock_run.assert_called_once_with(
            ["tmux", "kill-session", "-t", "=gd/repo/shell/1"],
            capture_output=True,
        )

    @patch("gitdirector.integrations.tmux.subprocess.run")
    def test_kill_failure(self, mock_run):
        from gitdirector.commands.autoclean import _kill_session

        mock_run.return_value = MagicMock(returncode=1)
        assert _kill_session("gd/repo/shell/1") is False
        mock_run.assert_called_once_with(
            ["tmux", "kill-session", "-t", "=gd/repo/shell/1"],
            capture_output=True,
        )


# ---------------------------------------------------------------------------
# Mixed kill results — high-value integration of the full command
# ---------------------------------------------------------------------------
#
# The unit tests above mock `subprocess.run` directly, so they verify
# "kill returns True/False". The end-to-end command path also formats
# output, increments counters, and prints failed session names. We
# exercise that here with a real click runner against a stubbed helper
# surface.
# ---------------------------------------------------------------------------


class TestAutocleanSessionsCommandEndToEnd:
    def test_mixed_kill_results_reports_both_counts(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from gitdirector.cli import cli
        from gitdirector.commands import autoclean as autoclean_mod
        from gitdirector.config import Config

        cfg_dir = tmp_path / ".gitdirector"
        cfg_dir.mkdir()
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        Config()

        monkeypatch.setattr(
            autoclean_mod,
            "_list_gd_sessions",
            lambda: ["gd/keep/one", "gd/bad/two", "gd/also-good/three"],
        )
        kill_iter = iter([True, False, True])
        monkeypatch.setattr(autoclean_mod, "_kill_session", lambda _n: next(kill_iter))

        result = CliRunner().invoke(cli, ["autoclean", "sessions"], input="y\n")
        assert result.exit_code == 0, result.output
        assert "Killed 2" in result.output
        # The failed session name should be surfaced in the output.
        assert "gd/bad/two" in result.output

    def test_decline_at_confirm_does_not_kill_anything(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from gitdirector.cli import cli
        from gitdirector.commands import autoclean as autoclean_mod
        from gitdirector.config import Config

        kills: list[str] = []
        cfg_dir = tmp_path / ".gitdirector"
        cfg_dir.mkdir()
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        Config()

        monkeypatch.setattr(
            autoclean_mod,
            "_list_gd_sessions",
            lambda: ["gd/should-not-kill/one"],
        )
        monkeypatch.setattr(
            autoclean_mod,
            "_kill_session",
            lambda name: kills.append(name) or True,
        )

        result = CliRunner().invoke(cli, ["autoclean", "sessions"], input="n\n")
        assert result.exit_code == 0
        assert kills == [], "no sessions should have been killed when user declines"
        assert "Cancelled" in result.output
