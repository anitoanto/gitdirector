"""Tests for opening a folder in the configured editor."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from gitdirector import editor


class TestEditorCommand:
    def test_the_mac_app_wins_over_the_cli_for_the_default(self, tmp_path, monkeypatch):
        # LaunchServices starts it from the login environment: no trace of us.
        monkeypatch.setattr(editor.sys, "platform", "darwin")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / "Applications" / "Visual Studio Code.app").mkdir(parents=True)
        with patch("shutil.which", return_value="/usr/local/bin/code"):
            command = editor.editor_command(Path("/r"), "code")
        assert command == ["open", "-a", "Visual Studio Code", "/r"]

    def test_the_cli_without_the_app(self, monkeypatch):
        monkeypatch.setattr(editor.sys, "platform", "linux")
        with patch("shutil.which", return_value="/usr/local/bin/code"):
            assert editor.editor_command(Path("/r"), "code") == ["/usr/local/bin/code", "/r"]

    def test_a_configured_command_keeps_its_arguments(self, monkeypatch):
        monkeypatch.setattr(editor.sys, "platform", "darwin")
        with patch("shutil.which", return_value="/opt/bin/zed") as which:
            assert editor.editor_command(Path("/r"), "zed -n") == ["/opt/bin/zed", "-n", "/r"]
        which.assert_called_once_with("zed")

    def test_the_configured_command_is_used_by_default(self, monkeypatch):
        monkeypatch.setattr(editor.sys, "platform", "linux")
        with (
            patch.object(editor, "editor_setting", return_value="cursor"),
            patch("shutil.which", return_value="/usr/local/bin/cursor"),
        ):
            assert editor.editor_command(Path("/r")) == ["/usr/local/bin/cursor", "/r"]

    def test_none_when_not_installed_or_empty(self, monkeypatch):
        monkeypatch.setattr(editor.sys, "platform", "linux")
        with patch("shutil.which", return_value=None):
            assert editor.editor_command(Path("/r"), "code") is None
        assert editor.editor_command(Path("/r"), "   ") is None
        assert editor.editor_command(Path("/r"), "'unterminated") is None


class TestEditorLabel:
    def test_names_the_command(self):
        assert editor.editor_label("code") == "code"
        assert editor.editor_label("/opt/homebrew/bin/cursor --new-window") == "cursor"
        assert editor.editor_label("") == "code"

    def test_falls_back_when_the_config_cannot_be_read(self):
        with patch.object(editor.Config, "__init__", side_effect=ValueError("bad yaml")):
            assert editor.editor_setting() == "code"


class TestOpenInEditor:
    def test_raises_when_not_installed(self):
        with patch.object(editor, "editor_command", return_value=None):
            with pytest.raises(FileNotFoundError, match="cursor not found; set `editor`"):
                editor.open_in_editor(Path("/r"), "cursor")

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
            patch.object(editor, "editor_command", return_value=["code", "/r"]),
            patch("subprocess.run", return_value=done) as run,
        ):
            editor.open_in_editor(Path("/r"), "code")
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
            patch.object(editor, "editor_command", return_value=["code", "/r"]),
            patch("subprocess.run", return_value=failed),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                editor.open_in_editor(Path("/r"), "code")
