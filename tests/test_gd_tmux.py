"""Tests for the ``gd-tmux`` command.

The command creates a gd tmux session for a tracked repository and runs a
caller-supplied command inside it. The tests below cover the CLI surface —
argument parsing, path/name resolution, the empty-command guard, the missing
tmux integration case, and the wiring of the three tmux integration calls.
The tmux integration itself is exercised by the existing tmux/monitor tests;
we mock it here so the CLI tests do not need a live tmux server.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from gitdirector.cli import cli


@pytest.fixture
def gd_tmux_cli(config, monkeypatch):
    """Wire a real CLI invocation but route Config through a temp dir."""
    monkeypatch.setattr("gitdirector.manager.Config", lambda: config)
    return CliRunner(), cli


def _seed_repo(config, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    config.add_repository(path)


@pytest.fixture
def mock_tmux_integration(monkeypatch):
    """Patch the three tmux functions imported by the gd-tmux command.

    ``create_tmux_session`` returns a name derived from its inputs (mirroring
    the real function) so downstream assertions on the launch/attach calls
    can use the same session name the production code would have generated.

    Returns a dict of MagicMock objects so individual tests can assert on
    call arguments.
    """

    def fake_create(repo_name, path, purpose="shell"):
        return f"gd/{repo_name}/{purpose}/1"

    mocks = {
        "create_tmux_session": MagicMock(side_effect=fake_create),
        "launch_command_in_tmux_session": MagicMock(),
        "attach_tmux_session": MagicMock(),
    }
    fake_module = MagicMock()
    fake_module.create_tmux_session = mocks["create_tmux_session"]
    fake_module.launch_command_in_tmux_session = mocks["launch_command_in_tmux_session"]
    fake_module.attach_tmux_session = mocks["attach_tmux_session"]
    monkeypatch.setitem(sys.modules, "gitdirector.integrations.tmux", fake_module)
    return mocks


# ---------------------------------------------------------------------------
# Happy path — by name and by path
# ---------------------------------------------------------------------------


class TestGdTmuxByName:
    def test_runs_command_in_session(self, gd_tmux_cli, config, mock_tmux_integration, tmp_path):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "myapp", "pytest -q"])

        assert result.exit_code == 0, result.output
        mock_tmux_integration["create_tmux_session"].assert_called_once_with(
            "myapp", repo, purpose="pytest -q"
        )
        mock_tmux_integration["launch_command_in_tmux_session"].assert_called_once_with(
            "gd/myapp/pytest -q/1", "pytest -q"
        )
        mock_tmux_integration["attach_tmux_session"].assert_called_once_with(
            "gd/myapp/pytest -q/1", skip_config_sync=True
        )

    def test_command_with_quotes_preserved(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "demo"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "demo", 'echo "hello world"'])

        assert result.exit_code == 0, result.output
        # The command (with its quotes) is passed through verbatim to the
        # launch function. The fake create_tmux_session echoes the purpose
        # back into the session name, so we assert on that mirror — the
        # important behaviour is that the original string is preserved.
        mock_tmux_integration["create_tmux_session"].assert_called_once_with(
            "demo", repo, purpose='echo "hello world"'
        )
        mock_tmux_integration["launch_command_in_tmux_session"].assert_called_once_with(
            'gd/demo/echo "hello world"/1', 'echo "hello world"'
        )


class TestGdTmuxByPath:
    def test_runs_command_for_absolute_path(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", str(repo), "ls -la"])

        assert result.exit_code == 0, result.output
        mock_tmux_integration["create_tmux_session"].assert_called_once_with(
            "myapp", repo, purpose="ls -la"
        )

    def test_path_with_separator_routes_through_path_branch(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        """A target containing a ``/`` must take the path branch, not the name branch.

        The error message must mention a path lookup, not a name lookup.
        The unlink command has dedicated tests for ``.``, ``..``, ``~`` and
        separator heuristics; this test asserts the same heuristic is used
        by gd-tmux.
        """
        runner, _ = gd_tmux_cli
        result = runner.invoke(cli, ["gd-tmux", str(tmp_path / "definitely-not-tracked"), "ls"])
        assert result.exit_code != 0
        assert "No tracked repository at path" in result.output
        mock_tmux_integration["create_tmux_session"].assert_not_called()


# ---------------------------------------------------------------------------
# Repositories with spaces in their directory name
# ---------------------------------------------------------------------------


class TestGdTmuxWithSpaceInName:
    """The directory name on disk is what the by-name lookup matches
    (verbatim, via ``Path.name``). A repo at ``/Users/me/My Repo`` is
    looked up by ``"My Repo"``, not by some slug variant — the user
    passes the name in ``"..."`` to keep the shell from splitting it.
    """

    def test_runs_command_for_repo_name_with_space(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "My Repo"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "My Repo", "echo hi"])

        assert result.exit_code == 0, result.output
        mock_tmux_integration["create_tmux_session"].assert_called_once_with(
            "My Repo", repo, purpose="echo hi"
        )

    def test_runs_command_for_absolute_path_with_space(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "My Repo"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", str(repo), "echo hi"])

        assert result.exit_code == 0, result.output
        mock_tmux_integration["create_tmux_session"].assert_called_once_with(
            "My Repo", repo, purpose="echo hi"
        )

    def test_repo_name_with_space_does_not_collapse_to_basename(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        """The by-name lookup uses the *whole* directory name including the
        space — it must not silently match a different repo that happens
        to share the first word.
        """
        runner, _ = gd_tmux_cli
        other = tmp_path / "My"
        _seed_repo(config, other)

        result = runner.invoke(cli, ["gd-tmux", "My Repo", "echo hi"])

        assert result.exit_code != 0
        assert "No tracked repository named: My Repo" in result.output
        mock_tmux_integration["create_tmux_session"].assert_not_called()


# ---------------------------------------------------------------------------
# Resolution errors
# ---------------------------------------------------------------------------


class TestGdTmuxResolution:
    def test_unknown_name_fails(self, gd_tmux_cli, config, mock_tmux_integration):
        runner, _ = gd_tmux_cli
        result = runner.invoke(cli, ["gd-tmux", "no-such-repo", "ls"])
        assert result.exit_code != 0
        assert "no-such-repo" in result.output
        mock_tmux_integration["create_tmux_session"].assert_not_called()

    def test_unknown_path_fails(self, gd_tmux_cli, config, mock_tmux_integration, tmp_path):
        runner, _ = gd_tmux_cli
        result = runner.invoke(cli, ["gd-tmux", str(tmp_path / "missing-repo"), "ls"])
        assert result.exit_code != 0
        assert "missing-repo" in result.output
        mock_tmux_integration["create_tmux_session"].assert_not_called()

    def test_ambiguous_name_lists_paths(self, gd_tmux_cli, config, mock_tmux_integration, tmp_path):
        runner, _ = gd_tmux_cli
        a = tmp_path / "dup"
        b = tmp_path / "elsewhere" / "dup"
        _seed_repo(config, a)
        _seed_repo(config, b)

        result = runner.invoke(cli, ["gd-tmux", "dup", "pytest"])

        assert result.exit_code != 0
        collapsed = "".join(result.output.split())
        assert str(a) in collapsed, f"expected {a} in output; got: {result.output!r}"
        assert str(b) in collapsed, f"expected {b} in output; got: {result.output!r}"
        mock_tmux_integration["create_tmux_session"].assert_not_called()


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


class TestGdTmuxArgumentValidation:
    def test_empty_command_fails(self, gd_tmux_cli, config, mock_tmux_integration, tmp_path):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "myapp", ""])

        assert result.exit_code != 0
        assert "empty" in result.output.lower()
        mock_tmux_integration["create_tmux_session"].assert_not_called()

    def test_whitespace_only_command_fails(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "myapp", "   "])

        assert result.exit_code != 0
        assert "empty" in result.output.lower()
        mock_tmux_integration["create_tmux_session"].assert_not_called()

    def test_missing_command_argument_fails(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "myapp"])

        assert result.exit_code != 0
        mock_tmux_integration["create_tmux_session"].assert_not_called()

    def test_missing_target_argument_fails(self, gd_tmux_cli, config, mock_tmux_integration):
        runner, _ = gd_tmux_cli

        result = runner.invoke(cli, ["gd-tmux"])

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# tmux integration availability
# ---------------------------------------------------------------------------


class TestGdTmuxIntegrationUnavailable:
    def test_missing_tmux_module_fails(self, gd_tmux_cli, config, monkeypatch, tmp_path):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        # Setting the module entry to None causes the import inside gd_tmux
        # to raise ImportError, mirroring the unavailable-integration case.
        monkeypatch.setitem(sys.modules, "gitdirector.integrations.tmux", None)

        result = runner.invoke(cli, ["gd-tmux", "myapp", "pytest"])

        assert result.exit_code != 0
        assert "tmux integration" in result.output
