"""Tests for the ``gd-send`` command."""

from __future__ import annotations

from unittest.mock import MagicMock

from click.testing import CliRunner

from gitdirector.cli import cli
from gitdirector.integrations.tmux import send_key_to_session, send_text_to_session


def test_help_lists_gd_send():
    runner = CliRunner()
    result = runner.invoke(cli, ["help"])

    assert result.exit_code == 0, result.output
    assert "gd-send" in result.output


class TestGdSendCLIShape:
    def test_requires_text_or_key(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-send", "gd/myrepo/shell/1"])

        assert result.exit_code != 0
        assert "TEXT or --key is required" in result.output

    def test_rejects_non_gd_session_name(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-send", "my-session", "hello"])

        assert result.exit_code != 0
        assert "gd/<repo>/<purpose>/<N>" in result.output

    def test_rejects_text_with_key(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-send", "gd/myrepo/shell/1", "hello", "--key", "C-c"])

        assert result.exit_code != 0
        assert "TEXT cannot be used with --key" in result.output

    def test_rejects_enter_with_key(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-send", "gd/myrepo/shell/1", "--key", "C-c", "--enter"])

        assert result.exit_code != 0
        assert "--enter cannot be used with --key" in result.output

    def test_rejects_unsupported_key(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["gd-send", "gd/myrepo/shell/1", "--key", "C-d"])

        assert result.exit_code != 0
        assert "Invalid value" in result.output


class TestGdSendCLIExecution:
    def test_sends_text_without_enter(self, monkeypatch):
        fake_send = MagicMock(return_value=True)
        monkeypatch.setattr("gitdirector.commands.gd_send.send_text_to_session", fake_send)
        runner = CliRunner()

        result = runner.invoke(cli, ["gd-send", "gd/myrepo/opencode/1", "continue"])

        assert result.exit_code == 0, result.output
        assert "Sent text" in result.output
        fake_send.assert_called_once_with("gd/myrepo/opencode/1", "continue", enter=False)

    def test_sends_text_with_enter(self, monkeypatch):
        fake_send = MagicMock(return_value=True)
        monkeypatch.setattr("gitdirector.commands.gd_send.send_text_to_session", fake_send)
        runner = CliRunner()

        result = runner.invoke(cli, ["gd-send", "gd/myrepo/shell/1", "npm test", "--enter"])

        assert result.exit_code == 0, result.output
        assert "Sent text and Enter" in result.output
        fake_send.assert_called_once_with("gd/myrepo/shell/1", "npm test", enter=True)

    def test_sends_key(self, monkeypatch):
        fake_send = MagicMock(return_value=True)
        monkeypatch.setattr("gitdirector.commands.gd_send.send_key_to_session", fake_send)
        runner = CliRunner()

        result = runner.invoke(cli, ["gd-send", "gd/myrepo/shell/1", "--key", "C-c"])

        assert result.exit_code == 0, result.output
        assert "Sent key C-c" in result.output
        fake_send.assert_called_once_with("gd/myrepo/shell/1", "C-c")

    def test_reports_missing_session(self, monkeypatch):
        fake_send = MagicMock(return_value=False)
        monkeypatch.setattr("gitdirector.commands.gd_send.send_text_to_session", fake_send)
        runner = CliRunner()

        result = runner.invoke(cli, ["gd-send", "gd/myrepo/shell/1", "hello"])

        assert result.exit_code != 0
        assert "not running" in result.output


class TestSendKeyToSession:
    def test_returns_false_when_session_missing(self, monkeypatch):
        fake_exists = MagicMock(return_value=False)
        monkeypatch.setattr("gitdirector.integrations.tmux.core._session_exists", fake_exists)
        fake_run = MagicMock()
        monkeypatch.setattr("gitdirector.integrations.tmux.core.subprocess.run", fake_run)

        assert send_key_to_session("gd/repo/shell/1", "C-c") is False
        fake_run.assert_not_called()

    def test_sends_key_to_active_pane(self, monkeypatch):
        monkeypatch.setattr(
            "gitdirector.integrations.tmux.core._session_exists", lambda _name: True
        )
        fake_run = MagicMock(return_value=MagicMock(returncode=0))
        monkeypatch.setattr("gitdirector.integrations.tmux.core.subprocess.run", fake_run)

        assert send_key_to_session("gd/repo/shell/1", "C-c") is True

        fake_run.assert_called_once_with(
            ["tmux", "send-keys", "-t", "=gd/repo/shell/1:", "C-c"],
            capture_output=True,
            text=True,
        )


class TestSendTextToSession:
    def test_returns_false_when_session_missing(self, monkeypatch):
        fake_exists = MagicMock(return_value=False)
        monkeypatch.setattr("gitdirector.integrations.tmux.core._session_exists", fake_exists)
        fake_run = MagicMock()
        monkeypatch.setattr("gitdirector.integrations.tmux.core.subprocess.run", fake_run)

        assert send_text_to_session("gd/repo/shell/1", "hello") is False
        fake_run.assert_not_called()

    def test_pastes_text_through_tmux_buffer(self, monkeypatch):
        monkeypatch.setattr(
            "gitdirector.integrations.tmux.core._session_exists", lambda _name: True
        )
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return MagicMock(returncode=0)

        monkeypatch.setattr("gitdirector.integrations.tmux.core.subprocess.run", fake_run)

        assert send_text_to_session("gd/repo/shell/1", "hello") is True

        assert calls[0][0][:3] == ["tmux", "load-buffer", "-b"]
        assert calls[0][0][3].startswith("gitdirector-send-")
        assert calls[0][0][-1] == "-"
        assert calls[0][1]["input"] == "hello"
        assert calls[1][0][:3] == ["tmux", "paste-buffer", "-b"]
        assert calls[1][0][-2:] == ["-t", "=gd/repo/shell/1:"]
        assert calls[2][0][:3] == ["tmux", "delete-buffer", "-b"]

    def test_enter_sends_enter_after_paste(self, monkeypatch):
        monkeypatch.setattr(
            "gitdirector.integrations.tmux.core._session_exists", lambda _name: True
        )
        fake_run = MagicMock(return_value=MagicMock(returncode=0))
        monkeypatch.setattr("gitdirector.integrations.tmux.core.subprocess.run", fake_run)

        assert send_text_to_session("gd/repo/shell/1", "npm test", enter=True) is True

        assert fake_run.call_args_list[-1].args[0] == [
            "tmux",
            "send-keys",
            "-t",
            "=gd/repo/shell/1:",
            "Enter",
        ]
