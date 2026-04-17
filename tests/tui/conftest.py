"""Shared helpers for TUI tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from gitdirector.repo import RepositoryInfo, RepoStatus


def _make_info(
    name: str = "my-repo",
    path: Path | None = None,
    status: RepoStatus = RepoStatus.UP_TO_DATE,
    branch: str = "main",
    staged: bool = False,
    unstaged: bool = False,
    last_updated: str = "2 hours ago",
    last_commit_timestamp: int | None = None,
) -> RepositoryInfo:
    return RepositoryInfo(
        path=path or Path(f"/tmp/{name}"),
        name=name,
        status=status,
        branch=branch,
        staged=staged,
        unstaged=unstaged,
        last_updated=last_updated,
        last_commit_timestamp=last_commit_timestamp,
    )


def _mock_manager(repos: list[RepositoryInfo] | None = None):
    """Return a mock RepositoryManager whose config lists the given repos."""
    if repos is None:
        repos = []
    mgr = MagicMock()
    mgr.config.repositories = [r.path for r in repos]
    mgr.config.max_workers = 2

    def fake_status(path, fetch=False):
        for r in repos:
            if r.path == path:
                return r
        return _make_info(name=path.name, path=path, status=RepoStatus.UNKNOWN)

    mgr.get_repository_status.side_effect = fake_status
    return mgr


SAMPLE_SESSIONS = [
    {"session_name": "gd/alpha/shell/1", "repo": "alpha", "purpose": "shell"},
    {"session_name": "gd/beta/claude/1", "repo": "beta", "purpose": "claude"},
    {"session_name": "gd/gamma/copilot/1", "repo": "gamma", "purpose": "copilot"},
]
