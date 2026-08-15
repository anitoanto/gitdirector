"""Real tmux tests: a gitdirector session knows nothing about its launcher.

These start a private tmux server, deliberately pollute it the way a
``gd`` launched from inside an agent session would, and then assert that
neither the session's own shell nor an agent launched into it can see any
of it.

The polluted-server-first case is the important one: a tmux pane's
environment comes from the server's global environment merged with its
session's, and the server's copy is whatever the process that started it
happened to be holding. gitdirector does not always get to be that
process.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from gitdirector.integrations.tmux import (
    TmuxError,
    create_tmux_session,
    kill_tmux_session,
    launch_command_in_tmux_session,
    sync_panel_tmux_config,
)

from .._timeouts import TMUX_CMD_TIMEOUT
from ._shared import _cleanup_tmux_tmpdir, _make_short_tmux_tmpdir, _tmux_integration_lock

# Everything a `gd` started from inside an agent session would be holding.
LEAKY_ENV = {
    "CLAUDECODE": "1",
    "CLAUDE_CODE_SESSION_ID": "parent-session-must-not-leak",
    "CLAUDE_CODE_ENTRYPOINT": "cli",
    "CLAUDE_PID": "424242",
    "VIRTUAL_ENV": "/somewhere/else/.venv",
    "PYTHONPATH": "/leaky/python/path",
    "npm_package_name": "leaky-package",
}

# Deliberately preserved: scrubbing these would break the session rather
# than isolate it.
PRESERVED_ENV = {
    "ANTHROPIC_API_KEY": "sk-must-survive",
    "GITDIRECTOR_GITHUB_PAT": "pat-must-survive",
}


def _probe_script(destination: Path) -> str:
    """Shell that records the cwd and any leaked/preserved variable."""
    checks = " ".join(LEAKY_ENV)
    keeps = " ".join(PRESERVED_ENV)
    return (
        "{ "
        'echo "cwd=$(pwd -P)"; '
        f'for v in {checks}; do eval "val=\\$$v"; '
        '[ -n "$val" ] && echo "LEAK $v=$val"; done; '
        f'for v in {keeps}; do eval "val=\\$$v"; '
        '[ -n "$val" ] && echo "KEPT $v"; done; '
        'echo "END"; '
        f"}} > {destination} 2>&1"
    )


def _wait_for_probe(destination: Path, timeout: float = 8.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if destination.exists():
            text = destination.read_text()
            if "END" in text:
                return text
        time.sleep(0.05)
    return destination.read_text() if destination.exists() else ""


def _tmux(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=TMUX_CMD_TIMEOUT,
        env=env,
    )


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
class TestSessionEnvironmentIsolation:
    @pytest.fixture
    def polluted_server(self, tmp_path, monkeypatch):
        """A tmux server already running, started from a polluted environment.

        Mirrors the worst real case: the server predates gitdirector, so
        gitdirector never had a chance to give it a clean global
        environment.
        """
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            launch_dir = tmp_path / "where-gd-was-launched"
            launch_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()

            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)
            for name, value in {**LEAKY_ENV, **PRESERVED_ENV}.items():
                monkeypatch.setenv(name, value)

            # Start the server from the dirty environment, deliberately
            # bypassing gitdirector's sanitized client.
            _tmux(
                "new-session",
                "-d",
                "-s",
                "preexisting",
                "-c",
                str(launch_dir),
                "-x",
                "80",
                "-y",
                "24",
                env=dict(os.environ),
            )
            try:
                yield tmp_path, home_dir, launch_dir
            finally:
                _tmux("kill-server")
                _cleanup_tmux_tmpdir(tmux_dir)

    def test_server_global_environment_really_is_polluted(self, polluted_server):
        """Guard: without this the rest of the suite proves nothing."""
        shown = _tmux("show-environment", "-g").stdout
        assert "CLAUDE_CODE_SESSION_ID=parent-session-must-not-leak" in shown

    def test_session_shell_sees_no_launch_context(self, polluted_server):
        tmp_path, home_dir, _launch_dir = polluted_server
        repo = home_dir / "demo-repo"
        repo.mkdir()
        probe_file = tmp_path / "shell-probe.txt"
        sync_panel_tmux_config()

        session_name = create_tmux_session("demo-repo", repo, purpose="shell")
        try:
            _tmux("send-keys", "-t", f"={session_name}:", _probe_script(probe_file), "Enter")
            output = _wait_for_probe(probe_file)
        finally:
            kill_tmux_session(session_name)

        assert "END" in output, f"probe never ran: {output!r}"
        leaks = [line for line in output.splitlines() if line.startswith("LEAK ")]
        assert leaks == [], f"session shell inherited gitdirector's launch context: {leaks}"
        assert f"cwd={os.path.realpath(repo)}" in output

    def test_agent_launch_sees_no_launch_context(self, polluted_server):
        """The path an AI agent is actually started through."""
        tmp_path, home_dir, _launch_dir = polluted_server
        repo = home_dir / "agent-repo"
        repo.mkdir()
        probe_file = tmp_path / "agent-probe.txt"
        sync_panel_tmux_config()

        session_name = create_tmux_session("agent-repo", repo, purpose="claude")
        try:
            launch_command_in_tmux_session(session_name, _probe_script(probe_file))
            output = _wait_for_probe(probe_file)
        finally:
            kill_tmux_session(session_name)

        assert "END" in output, f"probe never ran: {output!r}"
        leaks = [line for line in output.splitlines() if line.startswith("LEAK ")]
        assert leaks == [], f"agent inherited gitdirector's launch context: {leaks}"
        assert f"cwd={os.path.realpath(repo)}" in output

    def test_credentials_still_reach_the_agent(self, polluted_server):
        """Isolation must not cost the session its credentials."""
        tmp_path, home_dir, _launch_dir = polluted_server
        repo = home_dir / "creds-repo"
        repo.mkdir()
        probe_file = tmp_path / "creds-probe.txt"
        sync_panel_tmux_config()

        session_name = create_tmux_session("creds-repo", repo, purpose="claude")
        try:
            launch_command_in_tmux_session(session_name, _probe_script(probe_file))
            output = _wait_for_probe(probe_file)
        finally:
            kill_tmux_session(session_name)

        for name in PRESERVED_ENV:
            assert f"KEPT {name}" in output, f"{name} was scrubbed but must survive"

    def test_unmanaged_sessions_are_left_alone(self, polluted_server):
        """Scrubbing is session-scoped, not a rewrite of the user's server."""
        tmp_path, home_dir, _launch_dir = polluted_server
        repo = home_dir / "scoped-repo"
        repo.mkdir()
        sync_panel_tmux_config()

        session_name = create_tmux_session("scoped-repo", repo, purpose="shell")
        kill_tmux_session(session_name)

        probe_file = tmp_path / "preexisting-probe.txt"
        _tmux("send-keys", "-t", "=preexisting:", _probe_script(probe_file), "Enter")
        output = _wait_for_probe(probe_file)

        assert "END" in output, f"probe never ran: {output!r}"
        assert "LEAK CLAUDECODE=1" in output, (
            "gitdirector reached into a session it does not manage"
        )


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
class TestSessionWorkingDirectory:
    """``-c`` failing silently is the other way an agent ends up adrift."""

    def test_missing_repository_path_is_refused(self, tmp_path, monkeypatch):
        """tmux would exit 0 and start in ``$HOME`` instead."""
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            missing = tmp_path / "was-moved-or-deleted"
            try:
                with pytest.raises(TmuxError, match="not a directory"):
                    create_tmux_session("gone", missing, purpose="claude")
            finally:
                _tmux("kill-server")
                _cleanup_tmux_tmpdir(tmux_dir)

    def test_file_instead_of_directory_is_refused(self, tmp_path, monkeypatch):
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            not_a_dir = tmp_path / "a-file"
            not_a_dir.write_text("")
            try:
                with pytest.raises(TmuxError, match="not a directory"):
                    create_tmux_session("afile", not_a_dir, purpose="shell")
            finally:
                _tmux("kill-server")
                _cleanup_tmux_tmpdir(tmux_dir)
