"""Tests for opening a folder in VS Code."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from gitdirector import editor


class TestVscodeCommand:
    def test_prefers_the_code_cli(self):
        with patch("shutil.which", return_value="/usr/local/bin/code"):
            assert editor.vscode_command(Path("/r")) == ["/usr/local/bin/code", "/r"]

    def test_falls_back_to_the_mac_app(self, tmp_path, monkeypatch):
        monkeypatch.setattr(editor.sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / "Applications" / "Visual Studio Code.app").mkdir(parents=True)
        with patch("shutil.which", return_value=None):
            command = editor.vscode_command(Path("/r"))
        assert command == ["open", "-a", "Visual Studio Code", "/r"]

    def test_none_when_not_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(editor.sys, "platform", "linux")
        with patch("shutil.which", return_value=None):
            assert editor.vscode_command(Path("/r")) is None


class TestOpenInVscode:
    def test_raises_when_not_installed(self):
        with patch.object(editor, "vscode_command", return_value=None):
            with pytest.raises(FileNotFoundError):
                editor.open_in_vscode(Path("/r"))

    def test_runs_without_the_tmux_environment(self, monkeypatch):
        monkeypatch.setenv("TMUX", "/tmp/tmux-1/default,1,0")
        monkeypatch.setenv("TMUX_PANE", "%1")
        done = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(editor, "vscode_command", return_value=["code", "/r"]),
            patch("subprocess.run", return_value=done) as run,
        ):
            editor.open_in_vscode(Path("/r"))
        env = run.call_args.kwargs["env"]
        assert "TMUX" not in env and "TMUX_PANE" not in env
        assert run.call_args.args[0] == ["code", "/r"]

    def test_reports_the_cli_error(self):
        failed = subprocess.CompletedProcess([], 1, "", "boom\n")
        with (
            patch.object(editor, "vscode_command", return_value=["code", "/r"]),
            patch("subprocess.run", return_value=failed),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                editor.open_in_vscode(Path("/r"))
