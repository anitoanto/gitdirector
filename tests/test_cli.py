from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from gitdirector.cli import (
    _changes_text,
    _format_size,
    _path_text,
    _status_text,
    cli,
    main,
)
from gitdirector.repo import RepositoryInfo, RepoStatus


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


class TestFormatSize:
    def test_none(self):
        assert _format_size(None).plain == "—"

    def test_bytes(self):
        assert "B" in _format_size(500).plain

    def test_kb(self):
        assert "KB" in _format_size(2048).plain

    def test_mb(self):
        assert "MB" in _format_size(2 * 1024 * 1024).plain

    def test_gb(self):
        assert "GB" in _format_size(2 * 1024 * 1024 * 1024).plain


class TestStatusText:
    @pytest.mark.parametrize(
        "status,expected",
        [
            (RepoStatus.UP_TO_DATE, "up to date"),
            (RepoStatus.BEHIND, "behind"),
            (RepoStatus.AHEAD, "ahead"),
            (RepoStatus.DIVERGED, "diverged"),
            (RepoStatus.UNKNOWN, "unknown"),
        ],
    )
    def test_labels(self, status, expected):
        assert _status_text(status).plain == expected


class TestChangesText:
    def test_none(self):
        assert _changes_text(False, False).plain == "—"

    def test_staged(self):
        assert "staged" in _changes_text(True, False).plain

    def test_unstaged(self):
        assert "unstaged" in _changes_text(False, True).plain

    def test_both(self):
        text = _changes_text(True, True).plain
        assert "staged" in text and "unstaged" in text


class TestPathText:
    def test_short_path(self):
        text = _path_text("/a/b")
        assert "/a/b" in text.plain

    def test_long_path_truncated(self):
        long = "/very/long/path/" + "x" * 200
        text = _path_text(long)
        assert text.plain.startswith("\u2026")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def _mock_manager(**overrides):
    mgr = MagicMock()
    mgr.config.repositories = []
    mgr.config.max_workers = 2
    for key, val in overrides.items():
        setattr(mgr, key, MagicMock(return_value=val))
    return mgr


class TestAddCommand:
    def test_add_success(self, runner, tmp_path):
        repo = tmp_path / "my-repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        mgr = _mock_manager(add_repository=(True, f"Added repository: {repo}", [repo], []))
        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["add", str(repo)])
        assert result.exit_code == 0
        assert "Added" in result.output

    def test_add_failure(self, runner, tmp_path):
        mgr = _mock_manager(add_repository=(False, "Not a git repository: /x", [], []))
        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["add", str(tmp_path)])
        assert result.exit_code == 1

    def test_add_discover(self, runner, tmp_path):
        mgr = _mock_manager(
            add_repository=(True, "Added 2 repositories", [tmp_path / "a", tmp_path / "b"], [])
        )
        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["add", str(tmp_path), "--discover"])
        assert result.exit_code == 0
        assert "Added 2" in result.output


class TestRemoveCommand:
    def test_remove_success(self, runner, tmp_path):
        mgr = _mock_manager(remove_repository=(True, "Removed repository: /r", [tmp_path]))
        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["remove", str(tmp_path)])
        assert result.exit_code == 0
        assert "Removed" in result.output

    def test_remove_failure(self, runner, tmp_path):
        mgr = _mock_manager(remove_repository=(False, "Repository not tracked: /x", []))
        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["remove", str(tmp_path)])
        assert result.exit_code == 1


class TestListCommand:
    def test_empty(self, runner):
        mgr = _mock_manager()
        mgr.config.repositories = []
        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "No repositories tracked" in result.output

    def test_with_repos(self, runner, fake_git_repo):
        info = RepositoryInfo(
            fake_git_repo,
            fake_git_repo.name,
            RepoStatus.UP_TO_DATE,
            "main",
            last_updated="1 hour ago",
            size=1024,
        )
        mgr = _mock_manager(get_repository_status=info)
        mgr.config.repositories = [fake_git_repo]
        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert fake_git_repo.name in result.output


class TestStatusCommand:
    def test_empty(self, runner):
        mgr = _mock_manager()
        mgr.config.repositories = []
        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "No repositories tracked" in result.output

    def test_all_clean(self, runner, fake_git_repo):
        info = RepositoryInfo(
            fake_git_repo,
            fake_git_repo.name,
            RepoStatus.UP_TO_DATE,
            "main",
        )
        mgr = _mock_manager(get_repository_status=info)
        mgr.config.repositories = [fake_git_repo]
        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "clean" in result.output.lower()

    def test_dirty(self, runner, fake_git_repo):
        info = RepositoryInfo(
            fake_git_repo,
            fake_git_repo.name,
            RepoStatus.UP_TO_DATE,
            "main",
            staged=True,
            staged_files=["a.py"],
        )
        mgr = _mock_manager(get_repository_status=info)
        mgr.config.repositories = [fake_git_repo]
        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "changed" in result.output.lower()


class TestPullCommand:
    def test_empty(self, runner):
        mgr = _mock_manager()
        mgr.config.repositories = []
        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            result = runner.invoke(cli, ["pull"])
        assert result.exit_code == 0
        assert "No repositories tracked" in result.output

    def test_all_success(self, runner, fake_git_repo):
        mgr = _mock_manager()
        mgr.config.repositories = [fake_git_repo]
        mgr.config.max_workers = 2

        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            with patch(
                "gitdirector.cli._pull_one",
                return_value=(fake_git_repo.name, True, "Already up to date."),
            ):
                result = runner.invoke(cli, ["pull"])
        assert result.exit_code == 0
        assert "1 repository" in result.output

    def test_failure_exits_1(self, runner, fake_git_repo):
        mgr = _mock_manager()
        mgr.config.repositories = [fake_git_repo]
        mgr.config.max_workers = 2

        with patch("gitdirector.cli.RepositoryManager", return_value=mgr):
            with patch(
                "gitdirector.cli._pull_one",
                return_value=(fake_git_repo.name, False, "Cannot fast-forward"),
            ):
                result = runner.invoke(cli, ["pull"])
        assert result.exit_code == 1
        assert "failed" in result.output.lower()


class TestHelpCommand:
    def test_help(self, runner):
        result = runner.invoke(cli, ["help"])
        assert result.exit_code == 0
        assert "GITDIRECTOR" in result.output

    def test_no_args_shows_help(self, runner):
        result = runner.invoke(cli, [])
        assert result.exit_code == 0
        assert "GITDIRECTOR" in result.output


class TestMain:
    def test_main_catches_exception(self):
        with patch("gitdirector.cli.cli", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
