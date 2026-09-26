"""Tests for opening a folder in VS Code."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from gitdirector import editor


class TestVscodeCommand:
    def test_the_mac_app_wins_over_the_cli(self, tmp_path, monkeypatch):
        # LaunchServices starts it from the login environment: no trace of us.
        monkeypatch.setattr(editor.sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / "Applications" / "Visual Studio Code.app").mkdir(parents=True)
        with patch("shutil.which", return_value="/usr/local/bin/code"):
            command = editor.vscode_command(Path("/r"))
        assert command == ["open", "-a", "Visual Studio Code", "/r"]

    def test_the_cli_without_the_app(self, tmp_path, monkeypatch):
        monkeypatch.setattr(editor.sys, "platform", "linux")
        with patch("shutil.which", return_value="/usr/local/bin/code"):
            assert editor.vscode_command(Path("/r")) == ["/usr/local/bin/code", "/r"]

    def test_none_when_not_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(editor.sys, "platform", "linux")
        with patch("shutil.which", return_value=None):
            assert editor.vscode_command(Path("/r")) is None


class TestOpenInVscode:
    def test_raises_when_not_installed(self):
        with patch.object(editor, "vscode_command", return_value=None):
            with pytest.raises(FileNotFoundError):
                editor.open_in_vscode(Path("/r"))

    def test_the_cli_gets_no_trace_of_gitdirector(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TMUX", "/tmp/tmux-1/default,1,0")
        monkeypatch.setenv("TMUX_PANE", "%1")
        monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))
        monkeypatch.setenv("PATH", f"{tmp_path / '.venv' / 'bin'}:/usr/bin:/bin")
        monkeypatch.setenv("GITDIRECTOR_GITHUB_PAT", "secret")
        monkeypatch.setenv("GD_SOMETHING", "1")
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("PWD", "/somewhere/private")
        monkeypatch.setenv("HOME", str(tmp_path))
        done = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(editor, "vscode_command", return_value=["code", "/r"]),
            patch("subprocess.run", return_value=done) as run,
        ):
            editor.open_in_vscode(Path("/r"))
        env = run.call_args.kwargs["env"]
        for name in ("TMUX", "TMUX_PANE", "VIRTUAL_ENV", "GITDIRECTOR_GITHUB_PAT", "GD_SOMETHING"):
            assert name not in env
        assert "CLAUDECODE" not in env and "PWD" not in env
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == str(tmp_path)
        assert run.call_args.kwargs["cwd"] == editor.neutral_directory()
        assert run.call_args.kwargs["start_new_session"] is True
        assert run.call_args.args[0] == ["code", "/r"]

    def test_reports_the_cli_error(self):
        failed = subprocess.CompletedProcess([], 1, "", "boom\n")
        with (
            patch.object(editor, "vscode_command", return_value=["code", "/r"]),
            patch("subprocess.run", return_value=failed),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                editor.open_in_vscode(Path("/r"))
