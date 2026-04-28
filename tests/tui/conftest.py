"""Shared helpers for TUI tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual.css.query import NoMatches
from textual.widgets import Static
from textual.worker_manager import WorkerManager

from gitdirector.repo import RepositoryInfo, RepoStatus

_LOADING_STATUS_PREFIXES = ("Loading ", "Checking ")
_LOADING_STATUS_MARKERS = (" remaining...", " done, ")
_MAX_TUI_SETTLE_ROUNDS = 10


def _status_is_loading(app) -> bool:
    if app is None:
        return False
    try:
        status = str(app.query_one("#status-bar", Static).content)
    except NoMatches:
        return False
    if status.startswith(_LOADING_STATUS_PREFIXES):
        return True
    return any(marker in status for marker in _LOADING_STATUS_MARKERS)


@pytest.fixture(autouse=True)
def _stabilize_tui_worker_wait(monkeypatch):
    original_wait = WorkerManager.wait_for_complete

    async def settle_after_wait(self: WorkerManager, *args, **kwargs):
        explicit_workers = bool(args) or "workers" in kwargs
        result = None

        if explicit_workers:
            await asyncio.sleep(0)
            result = await original_wait(self, *args, **kwargs)
            await asyncio.sleep(0)
            return result

        app = getattr(self, "_app", None)

        for _ in range(_MAX_TUI_SETTLE_ROUNDS):
            await asyncio.sleep(0)
            result = await original_wait(self)
            if app is None:
                return result
            await asyncio.sleep(0)
            if len(self) == 0 and not _status_is_loading(app):
                break
        return result

    monkeypatch.setattr(WorkerManager, "wait_for_complete", settle_after_wait)


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

    def fake_status(path, fetch=False, include_size=False):
        for r in repos:
            if r.path == path:
                return r
        return _make_info(name=path.name, path=path, status=RepoStatus.UNKNOWN)

    mgr.get_repository_status.side_effect = fake_status
    return mgr


SAMPLE_SESSIONS = [
    {
        "session_name": "gd/alpha/shell/1",
        "repo": "alpha",
        "repo_slug": "alpha",
        "purpose": "shell",
    },
    {
        "session_name": "gd/beta/claude/1",
        "repo": "beta",
        "repo_slug": "beta",
        "purpose": "claude",
    },
    {
        "session_name": "gd/gamma/copilot/1",
        "repo": "gamma",
        "repo_slug": "gamma",
        "purpose": "copilot",
    },
]
