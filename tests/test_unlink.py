"""Tests for the ``unlink`` command's path/name disambiguation heuristic.

The command accepts a single string that may be either a filesystem path
(absolute, relative, ``~``-prefixed) or the basename of a tracked repo. The
heuristic lives in ``commands/unlink.py`` and has historically been untested.
The edge cases below are the ones that would cause silent data loss if
they regressed (a wrong "looks like a name" branch could remove a repo the
user didn't intend to remove).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner
from click.utils import strip_ansi

from gitdirector.cli import cli


@pytest.fixture
def unlink_cli(config, monkeypatch):
    """Wire a real CLI invocation but route Config through a temp dir."""
    monkeypatch.setattr("gitdirector.manager.Config", lambda: config)
    return CliRunner(), cli


def _seed_repo(config, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    config.add_repository(path)


class TestUnlinkByPath:
    def test_unlink_existing_path(self, unlink_cli, config, tmp_path):
        runner, _ = unlink_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)
        result = runner.invoke(cli, ["unlink", str(repo)])
        assert result.exit_code == 0, result.output
        assert not config.has_repository(repo)

    def test_unlink_absolute_path_with_special_chars(self, unlink_cli, config, tmp_path):
        runner, _ = unlink_cli
        repo = tmp_path / "weird-name.with.dots"
        _seed_repo(config, repo)
        result = runner.invoke(cli, ["unlink", str(repo)])
        assert result.exit_code == 0
        assert not config.has_repository(repo)


class TestUnlinkByName:
    def test_unlink_by_basename(self, unlink_cli, config, tmp_path):
        runner, _ = unlink_cli
        repo = tmp_path / "myapp"
        _seed_repo(config, repo)
        result = runner.invoke(cli, ["unlink", "myapp"])
        assert result.exit_code == 0, result.output
        assert not config.has_repository(repo)

    def test_unlink_by_nonexistent_name_fails(self, unlink_cli, config):
        runner, _ = unlink_cli
        result = runner.invoke(cli, ["unlink", "no-such-repo"])
        assert result.exit_code != 0
        assert "no tracked" in result.output.lower() or "not" in result.output.lower()


class TestUnlinkAmbiguity:
    def test_ambiguous_name_lists_all_matching_paths(self, unlink_cli, config, tmp_path):
        """Two repos with the same basename: the command must refuse and list both."""
        runner, _ = unlink_cli
        a = tmp_path / "dup"
        b = tmp_path / "elsewhere" / "dup"
        _seed_repo(config, a)
        _seed_repo(config, b)
        result = runner.invoke(cli, ["unlink", "dup"])
        assert result.exit_code != 0
        # Both repos must still be tracked — refusing is the correct behaviour.
        assert config.has_repository(a)
        assert config.has_repository(b)
        # Rich's console wraps long paths; the assertion must be made on the
        # output with all whitespace collapsed.
        collapsed = "".join(strip_ansi(result.output).split())
        assert str(a) in collapsed, f"expected path {a} in output; got: {result.output!r}"
        assert str(b) in collapsed, f"expected path {b} in output; got: {result.output!r}"


class TestUnlinkPathHeuristic:
    def test_tilde_prefix_treated_as_path_not_name(self, unlink_cli, config, tmp_path, monkeypatch):
        """A target starting with ``~`` is always a path, never a name."""
        runner, _ = unlink_cli
        home_repo = tmp_path / "homerepo"
        _seed_repo(config, home_repo)
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(cli, ["unlink", "~/homerepo"])
        assert result.exit_code == 0, result.output
        assert not config.has_repository(home_repo)

    def test_dot_is_treated_as_path_not_a_name(self, unlink_cli, config, tmp_path):
        """``unlink .`` is a path operation, not a name lookup.

        Edge case: if a repo were (pathologically) named ``.`` the heuristic
        must still not remove it via the name branch — ``.`` is a special
        path token and should only be resolved as a path.
        """
        runner, _ = unlink_cli
        before = list(config.repositories)
        result = runner.invoke(cli, ["unlink", "."], env={**os.environ, "PWD": str(tmp_path)})
        assert result.exit_code != 0
        # Nothing was removed.
        assert list(config.repositories) == before

    def test_dotdot_is_treated_as_path_not_a_name(self, unlink_cli, config):
        """``unlink ..`` is a path, never a name."""
        runner, _ = unlink_cli
        before = list(config.repositories)
        result = runner.invoke(cli, ["unlink", ".."])
        assert result.exit_code != 0
        assert list(config.repositories) == before

    def test_path_with_separator_is_treated_as_path(self, unlink_cli, config, tmp_path):
        """A target with ``/`` is always a path, even if the leaf looks like a name."""
        runner, _ = unlink_cli
        # Pass a non-existent absolute path: the error must be "not tracked"
        # (path branch), not "name not found" (name branch) and the config
        # must not be mutated.
        before = list(config.repositories)
        result = runner.invoke(cli, ["unlink", str(tmp_path / "does-not-exist")])
        assert result.exit_code != 0
        assert list(config.repositories) == before
