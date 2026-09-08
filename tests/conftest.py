from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gitdirector.config import Config


@pytest.fixture(autouse=True)
def _no_tmux_monitor():
    with patch("gitdirector.integrations.tmux.TmuxMonitor.start"):
        with patch("gitdirector.integrations.tmux.TmuxMonitor.stop"):
            yield


@pytest.fixture(autouse=True)
def _reset_tmux_server_prepared_latch():
    """Reset the once-per-process ``tmux start-server`` latch between tests.

    ``_ensure_clean_tmux_server`` sets a module global the first time it runs and
    then short-circuits forever. Left alone, whether it actually runs in a given
    test depends on which tests happened to precede it in the same worker
    process, which ``pytest -n auto`` reshuffles on every run.
    """
    import gitdirector.integrations.tmux.core as tmux_core

    tmux_core._TMUX_SERVER_ENVIRONMENT_PREPARED = False
    yield
    tmux_core._TMUX_SERVER_ENVIRONMENT_PREPARED = False


@pytest.fixture(autouse=True)
def _isolate_version_check_cache(monkeypatch, tmp_path):
    cache_dir = tmp_path / ".gitdirector"
    monkeypatch.setattr(
        "gitdirector.version_check._cache_paths",
        lambda: (cache_dir / "version_check.yaml", cache_dir / "version_check.lock"),
    )
    monkeypatch.setattr("gitdirector.version_check._fetch_latest_version", lambda: None)


@pytest.fixture(autouse=True)
def _no_ssh_probe(monkeypatch):
    """Resolve the default ``GIT_SSH_COMMAND`` without spawning ssh.

    The real probe runs ``ssh -G`` once per process; tests that patch
    ``subprocess`` would otherwise see it as an unexpected command, and the
    cached answer must not leak between tests that stub it differently.
    """
    from gitdirector import repo

    repo._default_ssh_command.cache_clear()
    monkeypatch.setattr(repo, "_ssh_accepts", lambda options: True)
    yield
    repo._default_ssh_command.cache_clear()


@pytest.fixture
def config_dir(tmp_path):
    """Return a temporary directory to use as ~/.gitdirector."""
    return tmp_path / ".gitdirector"


@pytest.fixture
def config(config_dir, monkeypatch):
    """Return a Config instance backed by a temporary directory."""
    monkeypatch.setattr(Path, "home", lambda: config_dir.parent)
    return Config()


@pytest.fixture
def fake_git_repo(tmp_path):
    """Create a temporary directory that looks like a git repo."""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


@pytest.fixture
def runner():
    return CliRunner()
