import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gitdirector.config import Config


@pytest.fixture(scope="session", autouse=True)
def _reap_orphans_left_in_tmp(tmp_path_factory):
    """Hang up any orphan a test left running in its temp directory.

    A pane shell caught mid-spawn when a test's tmux server goes down can
    outlive it, re-parented to init and holding a pty; enough of them run
    the machine out of ptys, and then every tmux spawn fails. Only orphans
    (parent is init) whose working directory is this run's temp tree are
    touched.
    """
    yield
    root = os.path.realpath(tmp_path_factory.getbasetemp())
    for pid in _orphans_with_cwd_under(root):
        try:
            os.kill(pid, signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass


def _orphans_with_cwd_under(root: str) -> list[int]:
    cwds: dict[int, str] = {}
    if Path("/proc").is_dir():
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                try:
                    cwds[int(entry.name)] = os.readlink(entry / "cwd")
                except OSError:
                    continue
    elif shutil.which("lsof"):
        listing = subprocess.run(
            ["lsof", "-a", "-u", str(os.getuid()), "-d", "cwd", "-Fpn"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout
        pid = None
        for line in listing.splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
            elif line.startswith("n") and pid is not None:
                cwds[pid] = os.path.realpath(line[1:])
    orphans = []
    for pid, cwd in cwds.items():
        if not (cwd == root or cwd.startswith(root + os.sep)):
            continue
        parent = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, check=False
        ).stdout.strip()
        if parent == "1":
            orphans.append(pid)
    return orphans


@pytest.fixture(scope="session", autouse=True)
def _private_tmux_server():
    """Point every tmux call of the run at a private server, never the user's.

    A run started inside the user's tmux inherits ``$TMUX``, and tmux prefers
    it over ``TMUX_TMPDIR``: an unmocked call would read the user's sessions,
    or kill them. At the end every pane left on the private servers is hung
    up and the servers are killed by explicit socket, so a run leaves no
    shells holding ptys behind.
    """
    base = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
    tmux_dir = base / f"gd-run-{uuid.uuid4().hex[:8]}"
    tmux_dir.mkdir()
    saved = {name: os.environ.pop(name, None) for name in ("TMUX", "TMUX_PANE", "TMUX_TMPDIR")}
    os.environ["TMUX_TMPDIR"] = str(tmux_dir)
    try:
        yield tmux_dir
    finally:
        _shut_down_private_tmux(tmux_dir)
        os.environ.pop("TMUX_TMPDIR", None)
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value
        shutil.rmtree(tmux_dir, ignore_errors=True)


def _shut_down_private_tmux(tmux_dir: Path) -> None:
    if shutil.which("tmux") is None:
        return
    for socket in tmux_dir.glob("tmux-*/*"):
        command = ["tmux", "-S", str(socket)]
        panes = subprocess.run(
            [*command, "list-panes", "-a", "-F", "#{pane_pid}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        for pid in panes.stdout.split():
            try:
                os.kill(int(pid), signal.SIGHUP)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
        subprocess.run([*command, "kill-server"], capture_output=True, timeout=10, check=False)


@pytest.fixture(autouse=True)
def _no_tmux_monitor():
    with patch("gitdirector.integrations.tmux.TmuxMonitor.start"):
        with patch("gitdirector.integrations.tmux.TmuxMonitor.stop"):
            yield


@pytest.fixture(autouse=True)
def _no_launch_directory_reexec(monkeypatch):
    """A re-exec would replace the test process itself."""
    monkeypatch.setattr("gitdirector.launch_context.os.execve", _refuse_execve)


def _refuse_execve(*_args, **_kwargs):
    raise AssertionError("leave_launch_directory tried to re-exec during a test")


@pytest.fixture(autouse=True)
def _fixed_default_terminal(monkeypatch):
    """Keep the cached ``infocmp`` probe out of tests that count subprocess calls."""
    monkeypatch.setattr(
        "gitdirector.integrations.tmux.core._default_terminal", lambda: "tmux-256color"
    )


@pytest.fixture(autouse=True)
def _plain_attach_terminal(monkeypatch):
    """Tests never compile a terminal description into the real home."""
    monkeypatch.setattr("gitdirector.integrations.tmux.core._same_screen_env", {})


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
