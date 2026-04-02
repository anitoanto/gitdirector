from unittest.mock import MagicMock

import pytest

from gitdirector.manager import RepositoryManager
from gitdirector.repo import RepositoryInfo, RepoStatus


@pytest.fixture
def manager(config, monkeypatch):
    """RepositoryManager backed by a temp config."""
    monkeypatch.setattr("gitdirector.manager.Config", lambda: config)
    return RepositoryManager()


# ---------------------------------------------------------------------------
# add – single
# ---------------------------------------------------------------------------


class TestAddSingle:
    def test_add_valid_repo(self, manager, fake_git_repo):
        ok, msg, added, skipped = manager.add_repository(fake_git_repo)
        assert ok is True
        assert fake_git_repo.resolve() in manager.config.repositories

    def test_add_duplicate(self, manager, fake_git_repo):
        manager.add_repository(fake_git_repo)
        ok, msg, _, _ = manager.add_repository(fake_git_repo)
        assert ok is False
        assert "already tracked" in msg.lower()

    def test_add_nonexistent_path(self, manager, tmp_path):
        ok, msg, _, _ = manager.add_repository(tmp_path / "nope")
        assert ok is False
        assert "does not exist" in msg.lower()

    def test_add_not_a_directory(self, manager, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hi")
        ok, msg, _, _ = manager.add_repository(f)
        assert ok is False
        assert "not a directory" in msg.lower()

    def test_add_not_git_repo(self, manager, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        ok, msg, _, _ = manager.add_repository(d)
        assert ok is False
        assert "not a git repository" in msg.lower()


# ---------------------------------------------------------------------------
# add – discover
# ---------------------------------------------------------------------------


class TestAddDiscover:
    def _make_repos(self, root, count):
        repos = []
        for i in range(count):
            r = root / f"repo-{i}"
            r.mkdir()
            (r / ".git").mkdir()
            repos.append(r)
        return repos

    def test_discover_finds_repos(self, manager, tmp_path):
        self._make_repos(tmp_path, 3)
        ok, msg, added, skipped = manager.add_repository(tmp_path, discover=True)
        assert ok is True
        assert len(added) == 3
        assert "3" in msg

    def test_discover_skips_existing(self, manager, tmp_path):
        repos = self._make_repos(tmp_path, 2)
        # Pre-add one
        manager.add_repository(repos[0])
        ok, msg, added, skipped = manager.add_repository(tmp_path, discover=True)
        assert ok is True
        assert len(added) == 1
        assert len(skipped) == 1

    def test_discover_no_repos(self, manager, tmp_path):
        ok, msg, _, _ = manager.add_repository(tmp_path, discover=True)
        assert ok is False
        assert "no git repositories" in msg.lower()

    def test_discover_all_existing(self, manager, tmp_path):
        repos = self._make_repos(tmp_path, 1)
        manager.add_repository(repos[0])
        ok, msg, added, skipped = manager.add_repository(tmp_path, discover=True)
        assert ok is False
        assert "no new repositories" in msg.lower()

    def test_discover_nonexistent_path(self, manager, tmp_path):
        ok, msg, _, _ = manager.add_repository(tmp_path / "nope", discover=True)
        assert ok is False
        assert "does not exist" in msg.lower()


# ---------------------------------------------------------------------------
# remove – single
# ---------------------------------------------------------------------------


class TestRemoveSingle:
    def test_remove_tracked(self, manager, fake_git_repo):
        manager.add_repository(fake_git_repo)
        ok, msg, removed = manager.remove_repository(fake_git_repo)
        assert ok is True
        assert len(removed) == 1

    def test_remove_not_tracked(self, manager, tmp_path):
        ok, msg, _ = manager.remove_repository(tmp_path / "nope")
        assert ok is False
        assert "not tracked" in msg.lower()


# ---------------------------------------------------------------------------
# remove – by name
# ---------------------------------------------------------------------------


class TestRemoveByName:
    def test_remove_by_name_success(self, manager, fake_git_repo):
        manager.add_repository(fake_git_repo)
        ok, msg, removed = manager.remove_by_name(fake_git_repo.name)
        assert ok is True
        assert len(removed) == 1
        assert fake_git_repo.resolve() not in manager.config.repositories

    def test_remove_by_name_not_found(self, manager):
        ok, msg, removed = manager.remove_by_name("nonexistent-repo")
        assert ok is False
        assert "no tracked repository named" in msg.lower()
        assert removed == []

    def test_remove_by_name_ambiguous(self, manager, tmp_path):
        for folder in ("dir1", "dir2"):
            r = tmp_path / folder / "my-repo"
            r.mkdir(parents=True)
            (r / ".git").mkdir()
            manager.add_repository(r)

        ok, msg, removed = manager.remove_by_name("my-repo")
        assert ok is False
        assert "multiple" in msg.lower()
        assert removed == []


# ---------------------------------------------------------------------------
# remove – discover
# ---------------------------------------------------------------------------


class TestRemoveDiscover:
    def test_remove_discover(self, manager, tmp_path):
        for name in ("a", "b"):
            r = tmp_path / name
            r.mkdir()
            (r / ".git").mkdir()
            manager.add_repository(r)

        ok, msg, removed = manager.remove_repository(tmp_path, discover=True)
        assert ok is True
        assert len(removed) == 2

    def test_remove_discover_none_found(self, manager, tmp_path):
        ok, msg, _ = manager.remove_repository(tmp_path, discover=True)
        assert ok is False
        assert "no tracked repositories" in msg.lower()


# ---------------------------------------------------------------------------
# get_repository_status
# ---------------------------------------------------------------------------


class TestGetRepositoryStatus:
    def test_valid_repo(self, manager, fake_git_repo, mocker):
        mocker.patch(
            "gitdirector.manager.Repository",
            return_value=MagicMock(
                get_status=MagicMock(
                    return_value=RepositoryInfo(
                        fake_git_repo, fake_git_repo.name, RepoStatus.UP_TO_DATE, "main"
                    )
                )
            ),
        )
        info = manager.get_repository_status(fake_git_repo)
        assert info.status == RepoStatus.UP_TO_DATE

    def test_missing_path(self, manager, tmp_path):
        info = manager.get_repository_status(tmp_path / "gone")
        assert info.status == RepoStatus.UNKNOWN
        assert "not found" in info.message.lower()

    def test_not_a_git_repo(self, manager, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        info = manager.get_repository_status(d)
        assert info.status == RepoStatus.UNKNOWN


# ---------------------------------------------------------------------------
# list_repositories
# ---------------------------------------------------------------------------


class TestListRepositories:
    def test_empty(self, manager):
        assert manager.list_repositories() == []

    def test_returns_info(self, manager, fake_git_repo, mocker):
        manager.config.add_repository(fake_git_repo)
        mocker.patch(
            "gitdirector.manager.Repository",
            return_value=MagicMock(
                get_status=MagicMock(
                    return_value=RepositoryInfo(
                        fake_git_repo, fake_git_repo.name, RepoStatus.UP_TO_DATE, "main"
                    )
                )
            ),
        )
        result = manager.list_repositories()
        assert len(result) == 1
        assert result[0].name == fake_git_repo.name


# ---------------------------------------------------------------------------
# pull_all
# ---------------------------------------------------------------------------


class TestPullAll:
    def test_all_success(self, manager, fake_git_repo, mocker):
        manager.config.add_repository(fake_git_repo)
        mocker.patch(
            "gitdirector.manager.Repository",
            return_value=MagicMock(pull=MagicMock(return_value=(True, "Already up to date."))),
        )
        success, failed = manager.pull_all()
        assert len(success) == 1
        assert len(failed) == 0

    def test_partial_failure(self, manager, tmp_path, mocker):
        r1 = tmp_path / "good"
        r1.mkdir()
        (r1 / ".git").mkdir()
        r2 = tmp_path / "bad"
        r2.mkdir()
        (r2 / ".git").mkdir()

        manager.config.add_repository(r1)
        manager.config.add_repository(r2)

        call_count = 0

        def make_repo(path):
            nonlocal call_count
            call_count += 1
            m = MagicMock()
            if call_count == 1:
                m.pull.return_value = (True, "Updated.")
            else:
                m.pull.return_value = (False, "Cannot fast-forward")
            return m

        mocker.patch("gitdirector.manager.Repository", side_effect=make_repo)
        success, failed = manager.pull_all()
        assert len(success) == 1
        assert len(failed) == 1

    def test_missing_path_fails(self, manager, tmp_path):
        manager.config.repositories.append(tmp_path / "gone")
        success, failed = manager.pull_all()
        assert len(failed) == 1
        assert "not found" in failed[0].lower() or "invalid" in failed[0].lower()


# ---------------------------------------------------------------------------
# get_repository_count
# ---------------------------------------------------------------------------


class TestGetRepositoryCount:
    def test_empty(self, manager):
        assert manager.get_repository_count() == 0

    def test_with_repos(self, manager, fake_git_repo):
        manager.config.add_repository(fake_git_repo)
        assert manager.get_repository_count() == 1
