"""Tests for the ``gd-tmux`` command.

The command creates a gd tmux session for a tracked repository and runs a
caller-supplied command inside it. The tests below cover the CLI surface —
argument parsing, path/name resolution, the empty-command guard, the missing
tmux integration case, and the create/launch wiring.
The tmux integration itself is exercised by the existing tmux/monitor tests;
we mock it here so the CLI tests do not need a live tmux server.
"""

from __future__ import annotations

import re
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
    """Patch the tmux functions imported by the gd-tmux command.

    ``create_tmux_session`` returns a parseable name derived from its repo and purpose
    so downstream assertions on the launch call can use the same value returned by
    the integration while still asserting the original command is passed through
    verbatim.

    Returns a dict of MagicMock objects so individual tests can assert on
    call arguments.
    """

    def clean_segment(value: str, fallback: str) -> str:
        value = re.sub(r"[^a-z0-9-]", "-", value.lower())
        value = re.sub(r"-+", "-", value).strip("-")
        return value or fallback

    def fake_create(repo_name, path, purpose="shell", description=None):
        return f"gd/{clean_segment(repo_name, 'repo')}/{clean_segment(purpose, 'cmd')}/1"

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
            "myapp", repo, purpose="shell", description=None
        )
        mock_tmux_integration["launch_command_in_tmux_session"].assert_called_once_with(
            "gd/myapp/shell/1", "pytest -q"
        )
        mock_tmux_integration["attach_tmux_session"].assert_not_called()

    def test_command_with_quotes_preserved(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "demo"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "demo", 'echo "hello world"'])

        assert result.exit_code == 0, result.output
        # The command (with its quotes) is passed through verbatim to the
        # launch function while gd-tmux keeps the tmux session purpose as shell.
        mock_tmux_integration["create_tmux_session"].assert_called_once_with(
            "demo", repo, purpose="shell", description=None
        )
        mock_tmux_integration["launch_command_in_tmux_session"].assert_called_once_with(
            "gd/demo/shell/1", 'echo "hello world"'
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
            "myapp", repo, purpose="shell", description=None
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
            "My Repo", repo, purpose="shell", description=None
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
            "My Repo", repo, purpose="shell", description=None
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
# --description flag
# ---------------------------------------------------------------------------


class TestGdTmuxDescriptionFlag:
    def test_description_long_flag_passes_value(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "myapp", "pytest", "--description=ready to ship"])

        assert result.exit_code == 0, result.output
        mock_tmux_integration["create_tmux_session"].assert_called_once_with(
            "myapp", repo, purpose="shell", description="ready to ship"
        )

    def test_description_short_flag_passes_value(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "myapp", "pytest", "-d", "wip feature"])

        assert result.exit_code == 0, result.output
        mock_tmux_integration["create_tmux_session"].assert_called_once_with(
            "myapp", repo, purpose="shell", description="wip feature"
        )

    def test_description_defaults_to_none(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "myapp", "pytest"])

        assert result.exit_code == 0, result.output
        mock_tmux_integration["create_tmux_session"].assert_called_once_with(
            "myapp", repo, purpose="shell", description=None
        )

    def test_description_supports_spaces(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(
            cli, ["gd-tmux", "myapp", "pytest", "--description", "ready to ship"]
        )

        assert result.exit_code == 0, result.output
        mock_tmux_integration["create_tmux_session"].assert_called_once_with(
            "myapp", repo, purpose="shell", description="ready to ship"
        )


# ---------------------------------------------------------------------------
# Session name output — callers (humans, agents) need the name to pass to
# `gitdirector gd-capture` later, so it is printed to stdout right after
# the background session is created.
# ---------------------------------------------------------------------------


class TestGdTmuxPrintsSessionName:
    def test_session_name_is_printed_to_stdout(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "myapp", "pytest -q"])

        assert result.exit_code == 0, result.output
        assert "gd/myapp/shell/1" in result.output

    def test_output_is_captureable_by_shell(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        """The session name on stdout must be cleanly captureable by
        ``SESSION=$(gitdirector gd-tmux ...)`` — no leading prefix, label,
        or trailing junk that would break shell substitution.
        """
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "myapp", "pytest"])

        assert result.exit_code == 0, result.output
        # The output should contain the bare session name on a line of
        # its own. ``runner.invoke`` may or may not add a trailing newline;
        # a regex anchored to a line is the safest assertion.
        match = re.search(r"^gd/myapp/shell/1\s*$", result.output, re.MULTILINE)
        assert match is not None, f"expected bare session name in output: {result.output!r}"

    def test_does_not_attach_after_launch(
        self, gd_tmux_cli, config, mock_tmux_integration, tmp_path
    ):
        """gd-tmux launches the command in the background and returns."""
        runner, _ = gd_tmux_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)

        result = runner.invoke(cli, ["gd-tmux", "myapp", "pytest"])

        assert result.exit_code == 0, result.output
        assert "gd/myapp/shell/1" in result.output
        mock_tmux_integration["attach_tmux_session"].assert_not_called()


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
