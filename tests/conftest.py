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
