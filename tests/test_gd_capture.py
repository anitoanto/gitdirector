"""Tests for the ``gd-capture`` command.

The command wraps ``tmux capture-pane`` and prints the current
scrollback of a live gd tmux session. The tests below cover the CLI
surface — argument validation, the gd-name shape check, the
--lines/--full wiring, the live-vs-missing-session error path — and
the underlying ``capture_pane`` helper.

The tmux integration itself is exercised by the existing tmux tests;
we mock it here so the CLI tests do not need a live tmux server.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from click.testing import CliRunner

from gitdirector.cli import cli
from gitdirector.integrations.tmux import capture_pane

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_help_lists_gd_capture():
    """The user-visible help table should advertise the new command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["help"])
    assert result.exit_code == 0, result.output
    assert "gd-capture" in result.output


def test_gd_capture_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["gd-capture", "--help"])
    assert result.exit_code == 0, result.output
    assert "--lines" in result.output
    assert "--full" in result.output


class TestGdCaptureCLIShape:
    def test_requires_session_argument(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-capture"])
        assert result.exit_code != 0

    def test_rejects_non_gd_session_name(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-capture", "my-session"])
        assert result.exit_code != 0
        assert "gd/<repo>/<purpose>/<N>" in result.output

    def test_rejects_malformed_gd_name(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-capture", "gd/repo/only"])
        assert result.exit_code != 0
        assert "gd/<repo>/<purpose>/<N>" in result.output

    def test_rejects_non_numeric_sequence(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-capture", "gd/repo/c/latest"])
        assert result.exit_code != 0
        assert "gd/<repo>/<purpose>/<N>" in result.output

    def test_rejects_zero_sequence(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-capture", "gd/repo/c/0"])
        assert result.exit_code != 0
        assert "gd/<repo>/<purpose>/<N>" in result.output

    def test_rejects_zero_lines(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-capture", "gd/repo/c/1", "--lines", "0"])
        assert result.exit_code != 0
        assert "positive integer" in result.output

    def test_rejects_negative_lines(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-capture", "gd/repo/c/1", "--lines", "-3"])
        assert result.exit_code != 0
        assert "positive integer" in result.output


class TestGdCaptureCLIExecution:
    def test_prints_capture_output(self, monkeypatch):
        fake_capture = MagicMock(return_value="hello world\n")
        monkeypatch.setattr("gitdirector.commands.capture.capture_pane", fake_capture)
        runner = CliRunner()

        result = runner.invoke(cli, ["gd-capture", "gd/myrepo/opencode/1"])

        assert result.exit_code == 0, result.output
        assert result.output == "hello world\n"
        fake_capture.assert_called_once_with("gd/myrepo/opencode/1", lines=200, full=False)

    def test_passes_lines_override(self, monkeypatch):
        fake_capture = MagicMock(return_value="...")
        monkeypatch.setattr("gitdirector.commands.capture.capture_pane", fake_capture)
        runner = CliRunner()

        result = runner.invoke(cli, ["gd-capture", "gd/myrepo/opencode/1", "--lines", "42"])

        assert result.exit_code == 0, result.output
        fake_capture.assert_called_once_with("gd/myrepo/opencode/1", lines=42, full=False)

    def test_passes_full_flag(self, monkeypatch):
        fake_capture = MagicMock(return_value="...")
        monkeypatch.setattr("gitdirector.commands.capture.capture_pane", fake_capture)
        runner = CliRunner()

        result = runner.invoke(cli, ["gd-capture", "gd/myrepo/opencode/1", "--full"])

        assert result.exit_code == 0, result.output
        fake_capture.assert_called_once_with("gd/myrepo/opencode/1", lines=200, full=True)

    def test_reports_missing_session(self, monkeypatch):
        """A None return from capture_pane is surfaced as a friendly error."""
        fake_capture = MagicMock(return_value=None)
        monkeypatch.setattr("gitdirector.commands.capture.capture_pane", fake_capture)
        runner = CliRunner()

        result = runner.invoke(cli, ["gd-capture", "gd/myrepo/opencode/1"])

        assert result.exit_code != 0
        assert "not running" in result.output.lower() or "failed" in result.output.lower()

    def test_reports_capture_exception(self, monkeypatch):
        fake_capture = MagicMock(side_effect=RuntimeError("tmux is angry"))
        monkeypatch.setattr("gitdirector.commands.capture.capture_pane", fake_capture)
        runner = CliRunner()

        result = runner.invoke(cli, ["gd-capture", "gd/myrepo/opencode/1"])

        assert result.exit_code != 0
        assert "tmux is angry" in result.output


# ---------------------------------------------------------------------------
# capture_pane helper
# ---------------------------------------------------------------------------


class TestCapturePane:
    def test_returns_none_when_session_missing(self, monkeypatch):
        fake_exists = MagicMock(return_value=False)
        monkeypatch.setattr("gitdirector.integrations.tmux.core._session_exists", fake_exists)
        fake_run = MagicMock()
        monkeypatch.setattr("gitdirector.integrations.tmux.core.subprocess.run", fake_run)

        assert capture_pane("gd/repo/c/1") is None
        fake_run.assert_not_called()

    def test_default_call_uses_no_history_options(self, monkeypatch):
        fake_exists = MagicMock(return_value=True)
        monkeypatch.setattr("gitdirector.integrations.tmux.core._session_exists", fake_exists)
        fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout="pane text"))
        monkeypatch.setattr("gitdirector.integrations.tmux.core.subprocess.run", fake_run)

        result = capture_pane("gd/repo/c/1")

        assert result == "pane text"
        args = fake_run.call_args.args[0]
        assert args[:3] == ["tmux", "capture-pane", "-p"]
        # No -S / full when neither is requested
        assert "-S" not in args

    def test_lines_passes_negative_offset(self, monkeypatch):
        fake_exists = MagicMock(return_value=True)
        monkeypatch.setattr("gitdirector.integrations.tmux.core._session_exists", fake_exists)
        fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout=""))
        monkeypatch.setattr("gitdirector.integrations.tmux.core.subprocess.run", fake_run)

        capture_pane("gd/repo/c/1", lines=50)

        args = fake_run.call_args.args[0]
        assert "-S" in args
        assert args[args.index("-S") + 1] == "-50"

    def test_full_passes_dash_for_full_history(self, monkeypatch):
        fake_exists = MagicMock(return_value=True)
        monkeypatch.setattr("gitdirector.integrations.tmux.core._session_exists", fake_exists)
        fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout=""))
        monkeypatch.setattr("gitdirector.integrations.tmux.core.subprocess.run", fake_run)

        capture_pane("gd/repo/c/1", full=True)

        args = fake_run.call_args.args[0]
        assert "-S" in args
        assert args[args.index("-S") + 1] == "-"

    def test_returns_none_on_tmux_failure(self, monkeypatch):
        fake_exists = MagicMock(return_value=True)
        monkeypatch.setattr("gitdirector.integrations.tmux.core._session_exists", fake_exists)
        fake_run = MagicMock(return_value=MagicMock(returncode=1, stdout=""))
        monkeypatch.setattr("gitdirector.integrations.tmux.core.subprocess.run", fake_run)

        assert capture_pane("gd/repo/c/1", lines=10) is None
