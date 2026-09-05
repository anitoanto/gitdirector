from pathlib import Path
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
        ok, msg, added, _ = manager.add_repository(tmp_path, discover=True)
        assert ok is True
        assert added == []
        assert "no git repositories" in msg.lower()

    def test_discover_all_existing(self, manager, tmp_path):
        repos = self._make_repos(tmp_path, 1)
        manager.add_repository(repos[0])
        ok, msg, added, skipped = manager.add_repository(tmp_path, discover=True)
        assert ok is True
        assert added == []
        assert skipped == repos
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

    def test_remove_discover_includes_the_target_repository(self, manager, tmp_path):
        repo = tmp_path / "project"
        repo.mkdir()
        (repo / ".git").mkdir()
        manager.add_repository(repo)

        ok, _msg, removed = manager.remove_repository(repo, discover=True)

        assert ok is True
        assert removed == [repo.resolve()]

    def test_remove_discover_resolves_relative_path_from_current_directory(
        self, manager, tmp_path, monkeypatch
    ):
        project_repo = tmp_path / "projects" / "app"
        other_repo = tmp_path / "other" / "app"
        for repo in (project_repo, other_repo):
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            manager.add_repository(repo)
        monkeypatch.chdir(tmp_path)

        ok, _msg, removed = manager.remove_repository(Path("projects"), discover=True)

        assert ok is True
        assert removed == [project_repo.resolve()]
        assert manager.config.repositories == [other_repo.resolve()]

    def test_remove_discover_does_not_match_a_path_with_the_same_prefix(self, manager, tmp_path):
        project_repo = tmp_path / "project" / "app"
        similarly_named_repo = tmp_path / "project-old" / "app"
        for repo in (project_repo, similarly_named_repo):
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            manager.add_repository(repo)

        ok, _msg, removed = manager.remove_repository(tmp_path / "project", discover=True)

        assert ok is True
        assert removed == [project_repo.resolve()]
        assert manager.config.repositories == [similarly_named_repo.resolve()]

    def test_remove_discover_removes_a_tracked_repo_when_path_no_longer_exists(
        self, manager, tmp_path
    ):
        stale_repo = tmp_path / "missing" / "repo"
        manager.config.add_repository(stale_repo)

        ok, _msg, removed = manager.remove_repository(tmp_path / "missing", discover=True)

        assert ok is True
        assert removed == [stale_repo.resolve()]


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
# `link --discover` walk semantics
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


class TestDiscoverWalkSemantics:
    def test_finds_nested_git_marker_in_vendor(self, manager, tmp_path):
        """A ``.git`` directory nested inside vendor/submodules is still discovered."""
        _make_real_git_repo(tmp_path, "app")
        _make_nested_git_marker(tmp_path, "vendor", "third-party", "widget")
        ok, _msg, added, _skipped = manager.add_repository(tmp_path, discover=True)
        assert ok is True
        names = {p.name for p in added}
        assert names == {"app", "widget"}

    def test_discovers_directories_previously_hard_coded_as_noise(self, manager, tmp_path):
        repos = [
            _make_nested_git_marker(tmp_path, directory, "package")
            for directory in (".venv", "node_modules", "target")
        ]

        ok, _msg, added, _skipped = manager.add_repository(tmp_path, discover=True)
        assert ok is True
        assert set(added) == {repo.resolve() for repo in repos}

    def test_prunes_directories_ignored_by_root_gitignore(self, manager, tmp_path):
        (tmp_path / ".gitignore").write_text("generated/\n")
        kept = _make_real_git_repo(tmp_path, "app")
        _make_nested_git_marker(tmp_path, "generated", "dependency")

        ok, _msg, added, _skipped = manager.add_repository(tmp_path, discover=True)

        assert ok is True
        assert added == [kept.resolve()]

    def test_applies_nested_gitignore_relative_to_its_directory(self, manager, tmp_path):
        (tmp_path / "services").mkdir()
        (tmp_path / "services" / ".gitignore").write_text("generated/\n")
        _make_nested_git_marker(tmp_path, "services", "generated", "hidden")
        discovered = _make_nested_git_marker(tmp_path, "docs", "generated", "visible")

        ok, _msg, added, _skipped = manager.add_repository(tmp_path, discover=True)

        assert ok is True
        assert added == [discovered.resolve()]

    def test_nested_gitignore_overrides_parent_rule(self, manager, tmp_path):
        (tmp_path / ".gitignore").write_text("*.generated/\n")
        (tmp_path / "projects").mkdir()
        (tmp_path / "projects" / ".gitignore").write_text("!keep.generated/\n")
        kept = _make_nested_git_marker(tmp_path, "projects", "keep.generated")
        _make_nested_git_marker(tmp_path, "projects", "discard.generated")

        ok, _msg, added, _skipped = manager.add_repository(tmp_path, discover=True)

        assert ok is True
        assert added == [kept.resolve()]

    def test_does_not_descend_into_an_ignored_parent_for_a_negated_child(self, manager, tmp_path):
        (tmp_path / ".gitignore").write_text("cache/\n!cache/keep/\n")
        kept = _make_real_git_repo(tmp_path, "app")
        _make_nested_git_marker(tmp_path, "cache", "keep", "dependency")

        ok, _msg, added, _skipped = manager.add_repository(tmp_path, discover=True)

        assert ok is True
        assert added == [kept.resolve()]

    def test_supports_gitignore_comments_and_escaped_patterns(self, manager, tmp_path):
        (tmp_path / ".gitignore").write_text("# explanation\n\\#generated/\n")
        kept = _make_real_git_repo(tmp_path, "app")
        _make_nested_git_marker(tmp_path, "#generated", "dependency")

        ok, _msg, added, _skipped = manager.add_repository(tmp_path, discover=True)

        assert ok is True
        assert added == [kept.resolve()]
