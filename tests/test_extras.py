"""Tests for coverage gaps - error paths and edge cases."""

from unittest.mock import MagicMock, patch

import pytest

from gitdirector.cli import main
from gitdirector.manager import RepositoryManager
from gitdirector.repo import Repository, RepoStatus

# ---------------------------------------------------------------------------
# Fixtures (copied from test_manager.py to avoid circular imports)
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(config, monkeypatch):
    """RepositoryManager backed by a temp config."""
    monkeypatch.setattr("gitdirector.manager.Config", lambda: config)
    return RepositoryManager()


# ---------------------------------------------------------------------------
# Manager – Error handling in add_repository
# ---------------------------------------------------------------------------


class TestManagerAddErrors:
    """Test error paths in RepositoryManager.add_repository."""

    def test_add_config_exception_on_add_single(self, manager, fake_git_repo, mocker):
        """When config.add_repository raises, error is caught and returned."""
        mocker.patch.object(
            manager.config, "add_repository", side_effect=Exception("Config write failed")
        )
        ok, msg, added, skipped = manager.add_repository(fake_git_repo)
        assert ok is False
        assert "Error adding repository" in msg

    def test_discover_config_save_failure(self, manager, tmp_path, mocker):
        """When config.save raises during discover, the exception propagates."""
        repos = []
        for i in range(2):
            r = tmp_path / f"repo-{i}"
            r.mkdir()
            (r / ".git").mkdir()
            repos.append(r)

        mocker.patch.object(manager.config, "save", side_effect=Exception("Write failed"))

        with pytest.raises(Exception, match="Write failed"):
            manager.add_repository(tmp_path, discover=True)

    def test_add_discover_not_a_directory(self, manager, tmp_path):
        """When discover path is not a directory, error is returned."""
        f = tmp_path / "file.txt"
        f.write_text("hi")
        ok, msg, added, skipped = manager.add_repository(f, discover=True)
        assert ok is False
        assert "not a directory" in msg.lower()


# ---------------------------------------------------------------------------
# Manager – Error handling in remove_repository
# ---------------------------------------------------------------------------


class TestManagerRemoveErrors:
    """Test error paths in RepositoryManager.remove_repository."""

    def test_remove_config_exception(self, manager, fake_git_repo, mocker):
        """When config.remove_repository raises, error is caught and returned."""
        manager.add_repository(fake_git_repo)
        mocker.patch.object(
            manager.config, "remove_repository", side_effect=Exception("Config write failed")
        )
        ok, msg, removed = manager.remove_repository(fake_git_repo)
        assert ok is False
        assert "Error removing repository" in msg

    def test_discover_and_remove_config_exception(self, manager, tmp_path, mocker):
        """When config exception occurs during discover remove, error is returned."""
        r = tmp_path / "repo"
        r.mkdir()
        (r / ".git").mkdir()
        manager.add_repository(r)

        mocker.patch.object(
            manager.config, "remove_repositories", side_effect=Exception("Write failed")
        )
        ok, msg, removed = manager.remove_repository(tmp_path, discover=True)
        assert ok is False
        assert "Error removing repositories" in msg


# ---------------------------------------------------------------------------
# Repo – Error handling in get_last_commit_timestamp
# ---------------------------------------------------------------------------


class TestRepoTimestampErrors:
    """Test error paths in Repository.get_last_commit_info."""

    def test_last_commit_info_non_integer_timestamp(self, fake_git_repo, mocker):
        """When git returns non-integer timestamp, None is returned for ts."""
        mocker.patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="2 days ago\nnot-an-int\n", stderr=""),
        )
        repo = Repository(fake_git_repo)
        date, ts = repo.get_last_commit_info()
        assert date == "2 days ago"
        assert ts is None

    def test_last_commit_info_empty_output(self, fake_git_repo, mocker):
        """When git returns empty output, None is returned."""
        mocker.patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        )
        repo = Repository(fake_git_repo)
        date, ts = repo.get_last_commit_info()
        assert date is None
        assert ts is None


# ---------------------------------------------------------------------------
# Repo – Error handling in get_tracked_size
# ---------------------------------------------------------------------------


class TestRepoTrackedSizeErrors:
    """Test error paths in Repository.get_tracked_size."""

    def test_tracked_size_git_failure(self, fake_git_repo, mocker):
        """When git ls-files fails, None is returned."""
        mocker.patch(
            "subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr="error"),
        )
        repo = Repository(fake_git_repo)
        size = repo.get_tracked_size()
        assert size is None

    def test_tracked_size_empty_output(self, fake_git_repo, mocker):
        """When git ls-files returns no files, None is returned."""
        mocker.patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        )
        repo = Repository(fake_git_repo)
        size = repo.get_tracked_size()
        assert size is None


# ---------------------------------------------------------------------------
# Repo – Error handling in get_status (pull command)
# ---------------------------------------------------------------------------


class TestRepoPullErrors:
    """Test error paths in Repository.pull method."""

    def test_pull_with_auth_error(self, fake_git_repo, mocker):
        """Pull returns auth error message when git pull fails with auth error."""
        mocker.patch(
            "subprocess.run",
            side_effect=[
                MagicMock(returncode=0, stdout="main\n", stderr=""),
                MagicMock(
                    returncode=128,
                    stdout="",
                    stderr="fatal: Authentication failed\n",
                ),
            ],
        )
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull()
        assert ok is False
        assert "authentication" in msg.lower()

    def test_pull_with_generic_error(self, fake_git_repo, mocker):
        """Pull returns generic error when git pull fails."""
        mocker.patch(
            "subprocess.run",
            side_effect=[
                MagicMock(returncode=0, stdout="main\n", stderr=""),
                MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="fatal: some error\n",
                ),
            ],
        )
        repo = Repository(fake_git_repo)
        ok, msg = repo.pull()
        assert ok is False
        assert "fatal" in msg


# ---------------------------------------------------------------------------
# CLI – main() exception handling
# ---------------------------------------------------------------------------


class TestMainExceptionHandling:
    """Test error handling in cli.main()."""

    @patch("gitdirector.cli.cli")
    def test_main_catches_exception(self, mock_cli, capsys):
        """main() catches exceptions from cli and exits with code 1."""
        mock_cli.side_effect = Exception("Test error")
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.out
        assert "Test error" in captured.out


# ---------------------------------------------------------------------------
# Manager – Get repository status error paths
# ---------------------------------------------------------------------------


class TestManagerRepositoryStatusErrors:
    """Test error paths in RepositoryManager.get_repository_status()."""

    def test_get_status_repo_exception(self, manager, fake_git_repo, mocker):
        """When Repository init or get_status fails, returns UNKNOWN status."""
        mocker.patch("gitdirector.manager.Repository", side_effect=Exception("Init failed"))
        result = manager.get_repository_status(fake_git_repo)
        assert result.status == RepoStatus.UNKNOWN
        assert result.message == "Init failed"

    def test_get_status_nonexistent_path(self, manager, tmp_path):
        """When path doesn't exist, returns UNKNOWN status."""
        fake_path = tmp_path / "nonexistent"
        result = manager.get_repository_status(fake_path)
        assert result.status == RepoStatus.UNKNOWN
        assert "not found" in result.message.lower()


# ---------------------------------------------------------------------------
# Repo – get_status with parsing errors
# ---------------------------------------------------------------------------


class TestRepoGetStatusParsingErrors:
    """Test error paths when parsing git status output."""

    def test_get_status_invalid_ahead_behind_format(self, fake_git_repo, mocker):
        """Sync status comes from origin/main even if branch.ab metadata is malformed."""

        def mock_run(*args, **kwargs):
            git_cmd = args[0]
            if "status" in git_cmd:
                v2 = "# branch.oid abc\n# branch.head main\n"
                v2 += "# branch.upstream origin/main\n"
                v2 += "# branch.ab invalid format\n"
                return MagicMock(returncode=0, stdout=v2, stderr="")
            if "show-ref" in git_cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "rev-list" in git_cmd:
                return MagicMock(returncode=0, stdout="0\t0\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run)
        repo = Repository(fake_git_repo)
        status = repo.get_status()
        assert status.status == RepoStatus.UP_TO_DATE

    def test_get_status_no_tracking_branch(self, fake_git_repo, mocker):
        """When origin/main does not exist, sync status is UNKNOWN."""

        def mock_run(*args, **kwargs):
            git_cmd = args[0]
            if "status" in git_cmd:
                v2 = "# branch.oid abc\n# branch.head main\n"
                return MagicMock(returncode=0, stdout=v2, stderr="")
            if "show-ref" in git_cmd:
                return MagicMock(returncode=1, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=mock_run)
        repo = Repository(fake_git_repo)
        status = repo.get_status()
        assert status.status == RepoStatus.UNKNOWN
        assert status.message == "No origin/main branch"


# ---------------------------------------------------------------------------
# pull._pull_one – direct unit tests
# ---------------------------------------------------------------------------


class TestPullOne:
    """Direct tests for _pull_one helper."""

    def test_path_not_found(self, tmp_path):
        from gitdirector.commands.pull import _pull_one

        name, ok, msg = _pull_one(tmp_path / "gone")
        assert ok is False
        assert "path not found" in msg

    def test_not_a_git_dir(self, tmp_path):
        from gitdirector.commands.pull import _pull_one

        d = tmp_path / "plain"
        d.mkdir()
        name, ok, msg = _pull_one(d)
        assert ok is False
        assert "path not found" in msg

    def test_success(self, fake_git_repo, mocker):
        from gitdirector.commands.pull import _pull_one

        mocker.patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="Already up to date.\n", stderr=""),
        )
        name, ok, msg = _pull_one(fake_git_repo)
        assert ok is True
        assert name == fake_git_repo.name

    def test_exception(self, fake_git_repo, mocker):
        from gitdirector.commands.pull import _pull_one

        mocker.patch("gitdirector.commands.pull.Repository", side_effect=Exception("boom"))
        name, ok, msg = _pull_one(fake_git_repo)
        assert ok is False
        assert "boom" in msg


# ---------------------------------------------------------------------------
# status._build_dirty_display – unstaged files
# ---------------------------------------------------------------------------


class TestBuildDirtyDisplay:
    """Direct tests for _build_dirty_display helper."""

    def test_unstaged_files_rendered(self):
        from pathlib import Path

        from gitdirector.commands.status import _build_dirty_display
        from gitdirector.repo import RepositoryInfo, RepoStatus

        info = RepositoryInfo(
            Path("/tmp/repo"),
            "repo",
            RepoStatus.UP_TO_DATE,
            "main",
            unstaged=True,
            unstaged_files=["file.py"],
        )
        output = _build_dirty_display([info])
        assert "unstaged:" in output.plain
        assert "file.py" in output.plain
