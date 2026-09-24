"""The attaching commands restart away from the directory they were launched in."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from gitdirector import launch_context
from gitdirector.cli import cli


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(launch_context.Path, "home", lambda: home)
    return home


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(launch_context.sys.stdin, "isatty", lambda: True, raising=False)


class TestNeutralDirectory:
    def test_is_home(self, home):
        assert launch_context.neutral_directory() == str(home)

    def test_falls_back_to_root_without_a_home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(launch_context.Path, "home", lambda: tmp_path / "missing")
        assert launch_context.neutral_directory() == "/"


class TestLeaveLaunchDirectory:
    def test_restarts_from_home_without_launch_variables(self, home, tty, tmp_path, monkeypatch):
        origin = tmp_path / "origin"
        origin.mkdir()
        monkeypatch.chdir(origin)
        monkeypatch.setenv("PWD", str(origin))
        monkeypatch.setenv("OLDPWD", "/elsewhere")
        monkeypatch.setenv("KEEP_ME", "1")
        execve = MagicMock()
        monkeypatch.setattr(launch_context.os, "execve", execve)

        launch_context.leave_launch_directory(["cd", "/abs/repo"])

        executable, argv, env = execve.call_args.args
        assert argv[1:] == ["-m", "gitdirector", "cd", "/abs/repo"]
        assert "PWD" not in env and "OLDPWD" not in env
        assert env["KEEP_ME"] == "1"
        assert os.getcwd() == os.path.realpath(home)

    def test_noop_once_neutral(self, home, tty, monkeypatch):
        monkeypatch.chdir(home)
        monkeypatch.setattr(launch_context, "neutral_directory", lambda: os.getcwd())
        monkeypatch.delenv("PWD", raising=False)
        monkeypatch.delenv("OLDPWD", raising=False)
        execve = MagicMock()
        monkeypatch.setattr(launch_context.os, "execve", execve)

        launch_context.leave_launch_directory()

        execve.assert_not_called()

    def test_noop_without_a_terminal(self, home, monkeypatch):
        monkeypatch.setattr(launch_context.sys.stdin, "isatty", lambda: False, raising=False)
        execve = MagicMock()
        monkeypatch.setattr(launch_context.os, "execve", execve)

        launch_context.leave_launch_directory()

        execve.assert_not_called()


class TestCommandsLeaveTheLaunchDirectory:
    def test_console(self):
        with (
            patch("gitdirector.launch_context.leave_launch_directory") as leave,
            patch("gitdirector.commands.tui.app._run_console"),
        ):
            result = CliRunner().invoke(cli, ["console"])
        assert result.exit_code == 0, result.output
        leave.assert_called_once_with()

    def test_cd_passes_the_resolved_path(self, tmp_path):
        repo = tmp_path / "repo"
        manager = MagicMock()
        manager.resolve_repository_target.return_value = (repo, [], False)
        with (
            patch("gitdirector.commands.cd.RepositoryManager", return_value=manager),
            patch("gitdirector.launch_context.leave_launch_directory") as leave,
            patch("gitdirector.integrations.tmux.open_in_tmux"),
        ):
            result = CliRunner().invoke(cli, ["cd", "repo"])
        assert result.exit_code == 0, result.output
        leave.assert_called_once_with(["cd", str(repo)])
