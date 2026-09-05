"""Shared helpers for TUI tests."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import MappingProxyType
from unittest.mock import MagicMock, patch

import pytest
from textual.css.query import NoMatches
from textual.widgets import Static
from textual.worker_manager import WorkerManager

from gitdirector.repo import RepositoryInfo, RepoStatus

from .._timeouts import SYNC_TIMEOUT

_LOADING_STATUS_PREFIXES = ("Loading ", "Checking ")
_LOADING_STATUS_MARKERS = (" remaining...", " done, ")

# A worker finishing can schedule follow-up work, so ``wait_for_complete`` has
# to be retried until the app is genuinely idle. This bound is a deadlock
# backstop, not a timing expectation: a fixed, small round count instead gave up
# while the app was still loading whenever a CI runner was slow enough, and the
# test then asserted against a half-populated screen. See tests/_timeouts.py.
_MAX_TUI_SETTLE_ROUNDS = 500


async def _wait_for_refresh(widget, timeout: float = SYNC_TIMEOUT) -> None:
    refreshed = asyncio.Event()
    widget.call_after_refresh(refreshed.set)
    await asyncio.wait_for(refreshed.wait(), timeout=timeout)


async def _wait_for_deferred_scroll(widget) -> None:
    await _wait_for_refresh(widget)
    await _wait_for_refresh(widget)


async def _wait_for_animated_scroll(pilot, widget) -> None:
    await _wait_for_refresh(widget)
    await pilot.wait_for_scheduled_animations()


@pytest.fixture(autouse=True)
def _isolate_tui_tmux_config(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Sampling would otherwise talk to whatever tmux server is running on
    # the developer's machine; tests hand the app statuses directly.
    monkeypatch.setattr(
        "gitdirector.integrations.tmux.monitor.TmuxMonitor.refresh",
        lambda self: self.statuses(),
    )
    monkeypatch.setattr(
        "gitdirector.integrations.tmux.sync_panel_tmux_config",
        lambda *_args, **_kwargs: tmp_path / ".gitdirector" / "tmux.conf",
    )


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
def _no_background_status_poll(monkeypatch):
    """Keep the Sessions tab's periodic status poll from firing mid-test.

    The app re-reads the session list from tmux every second while the
    Sessions tab is open and re-applies it to the table, restoring the
    cursor from the saved selection. A test that edits ``_sessions_entries``
    directly and then asserts on the table races that poll: on a slow CI
    runner the poll fires first and overwrites the edit, and the assertion
    sees the original list (observed as an off-by-one cursor row on the
    Python 3.14 job). No test relies on the timer itself -- the ones that
    cover polling call the worker directly -- so it is pushed out of reach.
    """
    monkeypatch.setattr("gitdirector.commands.tui.app._SESSION_STATUS_POLL_INTERVAL_SECS", 3600)


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

        deadline = time.monotonic() + SYNC_TIMEOUT
        for _ in range(_MAX_TUI_SETTLE_ROUNDS):
            await asyncio.sleep(0)
            result = await original_wait(self)
            if app is None:
                return result
            await asyncio.sleep(0)
            if len(self) == 0 and not _status_is_loading(app):
                break
            if time.monotonic() > deadline:
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
    mgr.config.repository_cache_token.return_value = {}
    mgr.config.reload_if_changed.return_value = False
    mgr.config.max_workers = 2

    def fake_status(path, fetch=False, include_size=False):
        for r in repos:
            if r.path == path:
                return r
        return _make_info(name=path.name, path=path, status=RepoStatus.UNKNOWN)

    mgr.get_repository_status.side_effect = fake_status
    return mgr


# Read-only master copy of the sample session entries.
#
# The app keeps the dicts it receives from ``list_all_gd_sessions`` by reference
# in ``app._sessions_entries`` and edits them in place (description edits, status
# merges). Handing these dicts straight to the app therefore leaks one test's
# mutations into every later test sharing the process, which surfaces as
# order-dependent failures under ``pytest -n auto``. The entries are mapping
# proxies so that any such write raises instead of silently corrupting state --
# use :func:`sample_sessions` or :func:`patch_sessions` to get mutable copies.
SAMPLE_SESSIONS = (
    MappingProxyType(
        {
            "session_name": "gd/alpha/shell/1",
            "repo": "alpha",
            "repo_slug": "alpha",
            "purpose": "shell",
            "description": "-",
        }
    ),
    MappingProxyType(
        {
            "session_name": "gd/beta/claude/1",
            "repo": "beta",
            "repo_slug": "beta",
            "purpose": "claude",
            "description": "-",
        }
    ),
    MappingProxyType(
        {
            "session_name": "gd/gamma/copilot/1",
            "repo": "gamma",
            "repo_slug": "gamma",
            "purpose": "copilot",
            "description": "-",
        }
    ),
)


def sample_sessions(entries=None) -> list[dict[str, str]]:
    """Return fresh, independently mutable copies of the sample session entries."""
    return [dict(entry) for entry in (SAMPLE_SESSIONS if entries is None else entries)]


def patch_sessions(entries=None):
    """Patch ``list_all_gd_sessions`` to return fresh entries on every call.

    Each call yields new dicts, so a test that edits them cannot affect any
    other test -- or even its own later calls.
    """
    return patch(
        "gitdirector.integrations.tmux.list_all_gd_sessions",
        side_effect=lambda *_args, **_kwargs: sample_sessions(entries),
    )
