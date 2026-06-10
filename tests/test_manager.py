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

    def test_remove_by_name_config_exception(self, manager, fake_git_repo, mocker):
        manager.add_repository(fake_git_repo)
        mocker.patch.object(
            manager.config, "remove_repository", side_effect=Exception("Write failed")
        )
        ok, msg, removed = manager.remove_by_name(fake_git_repo.name)
        assert ok is False
        assert "Error removing repository" in msg
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

    def test_excludes_size_by_default(self, manager, fake_git_repo, mocker):
        repo = MagicMock()
        repo.get_status.return_value = RepositoryInfo(
            fake_git_repo,
            fake_git_repo.name,
            RepoStatus.UP_TO_DATE,
            "main",
        )
        mocker.patch("gitdirector.manager.Repository", return_value=repo)

        manager.get_repository_status(fake_git_repo)

        repo.get_status.assert_called_once_with(fetch=False, include_size=False)

    def test_can_include_size(self, manager, fake_git_repo, mocker):
        repo = MagicMock()
        repo.get_status.return_value = RepositoryInfo(
            fake_git_repo,
            fake_git_repo.name,
            RepoStatus.UP_TO_DATE,
            "main",
            size=1024,
        )
        mocker.patch("gitdirector.manager.Repository", return_value=repo)

        info = manager.get_repository_status(fake_git_repo, fetch=True, include_size=True)

        assert info.size == 1024
        repo.get_status.assert_called_once_with(fetch=True, include_size=True)

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
# `link --discover` walk semantics — locks down the noisy-tree behaviour
# ---------------------------------------------------------------------------
#
# The current discover walker only removes ``.git`` from the directory list
# before recursing. That means ``node_modules/``, ``target/`` and other vendor
# trees are walked in full. We pin the *current* behaviour here so the
# eventual fix (skip-list) doesn't accidentally drop the contract of
# "find every repo, including nested ones".
# ---------------------------------------------------------------------------


def _make_real_git_repo(parent, name):
    repo = parent / name
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def _make_nested_git_marker(parent, *parts):
    """Drop a ``.git`` directory inside a non-repo path (vendor / submodule)."""
    nested = parent.joinpath(*parts)
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()
    return nested


def _make_noise_dir(parent, name):
    d = parent / name
    d.mkdir()
    (d / "stuff.txt").write_text("x" * 1024)
    return d


class TestDiscoverWalkSemantics:
    def test_finds_nested_git_marker_in_vendor(self, manager, tmp_path):
        """A ``.git`` directory nested inside vendor/submodules is still discovered."""
        _make_real_git_repo(tmp_path, "app")
        _make_nested_git_marker(tmp_path, "vendor", "third-party", "widget")
        ok, _msg, added, _skipped = manager.add_repository(tmp_path, discover=True)
        assert ok is True
        names = {p.name for p in added}
        assert names == {"app", "widget"}

    def test_descends_into_noisy_directories(self, manager, tmp_path):
        """Current behaviour: discovery walks *into* node_modules/target/.venv.

        The bug is that noisy dirs are not skipped. The test locks the
        current behaviour down so a future fix has a regression net: any
        change that stops descending into ``node_modules`` *and* also drops
        the nested repo would fail this test.
        """
        _make_real_git_repo(tmp_path, "app")
        node_modules = _make_noise_dir(tmp_path, "node_modules")
        nested = node_modules / "some-pkg"
        nested.mkdir()
        (nested / ".git").mkdir()

        ok, _msg, added, _skipped = manager.add_repository(tmp_path, discover=True)
        assert ok is True
        names = {p.name for p in added}
        # Both the top-level app AND the nested node_modules/some-pkg are found.
        assert "app" in names
        assert "some-pkg" in names
