"""Regressions for tmux failure handling: hung servers and concurrent sends."""

from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from gitdirector.integrations.tmux.core import (
    TMUX_COMMAND_TIMEOUT,
    TmuxError,
    _run_tmux,
    respawn_pane,
    send_text_to_session,
)

from ._shared import _cleanup_tmux_tmpdir, _make_short_tmux_tmpdir, _tmux_integration_lock


class TestTmuxCommandTimeout:
    """A wedged tmux server must not block the caller forever.

    Every tmux invocation used to run without a timeout, so an unresponsive
    server hung whichever thread made the call -- fatal on the monitor thread,
    which then stopped updating session status with no visible error.
    """

    def test_run_tmux_passes_a_timeout(self):
        with patch(
            "gitdirector.integrations.tmux.core.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as run:
            _run_tmux(["list-sessions"])

        assert run.call_args.kwargs["timeout"] == TMUX_COMMAND_TIMEOUT

    def test_timeout_becomes_a_failed_result_not_an_exception(self):
        """Callers branch on returncode, so a hang must look like a failure."""
        with patch(
            "gitdirector.integrations.tmux.core.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=TMUX_COMMAND_TIMEOUT),
        ):
            result = _run_tmux(["list-sessions"], text=True)

        assert result.returncode != 0
        assert result.stdout == ""

    def test_timeout_returns_bytes_when_not_in_text_mode(self):
        with patch(
            "gitdirector.integrations.tmux.core.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=TMUX_COMMAND_TIMEOUT),
        ):
            result = _run_tmux(["list-sessions"])

        assert result.stdout == b""

    def test_timeout_raises_for_callers_that_asked_to_check(self):
        with patch(
            "gitdirector.integrations.tmux.core.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=TMUX_COMMAND_TIMEOUT),
        ):
            with pytest.raises(TmuxError, match="timed out"):
                _run_tmux(["kill-session"], check=True)

    def test_list_sessions_survives_a_hung_server(self):
        from gitdirector.integrations.tmux.core import _list_sessions

        with patch(
            "gitdirector.integrations.tmux.core.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="tmux", timeout=TMUX_COMMAND_TIMEOUT),
        ):
            assert _list_sessions() == []


class TestSendTextBufferIsolation:
    """Concurrent sends must not share one server-wide tmux buffer.

    The buffer name was derived from the pid alone, so two overlapping sends
    used the same named slot: the second load-buffer overwrote the first, and
    the first delete-buffer pulled the slot out from under the second paste.
    """

    def test_each_send_uses_a_distinct_buffer(self, monkeypatch):
        monkeypatch.setattr(
            "gitdirector.integrations.tmux.core._session_exists", lambda _name: True
        )
        buffers = []

        def fake_run(args, **_kwargs):
            if len(args) > 3 and args[1] == "load-buffer":
                buffers.append(args[3])
            return MagicMock(returncode=0)

        monkeypatch.setattr("gitdirector.integrations.tmux.core.subprocess.run", fake_run)

        assert send_text_to_session("gd/repo/shell/1", "first") is True
        assert send_text_to_session("gd/repo/shell/2", "second") is True

        assert len(buffers) == 2
        assert buffers[0] != buffers[1]
        assert all(name.startswith("gitdirector-send-") for name in buffers)


def _user_process_count() -> int:
    """What RLIMIT_NPROC counts for this user: processes, but threads on Linux."""
    columns = ["-L", "-o", "lwp="] if sys.platform.startswith("linux") else ["-o", "pid="]
    listing = subprocess.run(
        ["ps", "-U", str(os.getuid()), *columns], capture_output=True, text=True, check=True
    )
    return len(listing.stdout.split())


@pytest.mark.skipif(
    shutil.which("tmux") is None or os.geteuid() == 0,
    reason="needs tmux, and a user RLIMIT_NPROC applies to (not root)",
)
def test_a_pane_left_broken_by_a_failed_respawn_does_not_take_the_server_down(monkeypatch):
    """tmux < 3.8 segfaults respawning a pane whose previous respawn could not fork.

    The server here may only fork a few more processes, so its spawns fail
    the way they do on a machine out of ptys.
    """
    with _tmux_integration_lock():
        tmux_dir = _make_short_tmux_tmpdir()
        monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
        monkeypatch.delenv("TMUX", raising=False)
        session = "gd/probe_aaaaa/shell/1"
        limit = _user_process_count() + 24
        _, hard = resource.getrlimit(resource.RLIMIT_NPROC)
        try:
            started = subprocess.run(
                ["tmux", "-f", "/dev/null", "new-session", "-d", "-s", session, "sleep 600"],
                capture_output=True,
                timeout=TMUX_COMMAND_TIMEOUT,
                preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_NPROC, (limit, hard)),
            )
            if started.returncode != 0:
                pytest.skip("could not start a process-limited tmux server")
            pane = f"={session}:^.0"
            # Other processes of this user come and go, so fill up to the
            # limit again until the respawn itself is the fork that fails.
            for _ in range(20):
                for _ in range(64):
                    if _run_tmux(["new-window", "-d", "-t", f"={session}", "sleep 600"]).returncode:
                        break
                _run_tmux(["respawn-pane", "-k", "-t", pane, "sleep 600"])
                probe = _run_tmux(["display-message", "-p", "-t", pane, "#{pane_pid}"], text=True)
                if probe.stdout.strip() == "-1":
                    break
            else:
                pytest.skip("the process limit never made a respawn fail")

            with pytest.raises(TmuxError, match="broken"):
                respawn_pane(pane, "sleep 600")

            assert _run_tmux(["list-sessions"]).returncode == 0, "the tmux server died"
            assert (
                "-1"
                not in _run_tmux(
                    ["list-panes", "-a", "-F", "#{pane_pid}"], text=True
                ).stdout.split()
            )
        finally:
            _run_tmux(["kill-server"])
            _cleanup_tmux_tmpdir(tmux_dir)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
class TestGroupedSessionEnd:
    """A session shown in decks or panels must end without taking tmux down.

    Decks and panels group views with a session. In tmux 3.7c a window of a
    session group closing on its own could segfault the server (found by the
    stress harness): every session was lost. The window is now kept open by
    remain-on-exit and the session and its views removed by a hook instead.
    """

    @pytest.mark.parametrize("ending", ["exit", "sigkill"])
    def test_the_server_survives_and_the_views_go(self, tmp_path, monkeypatch, ending):
        from pathlib import Path

        from gitdirector.integrations.tmux.core import create_tmux_session

        from ._shared import _make_shell_home

        with _tmux_integration_lock():
            monkeypatch.setenv("HOME", str(_make_shell_home(tmp_path / "home")))
            tmux_dir = _make_short_tmux_tmpdir()
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)
            try:
                _run_tmux(["new-session", "-d", "-s", "keepalive", "sleep 100000"], check=True)
                session = create_tmux_session("alpha", Path(tmp_path))
                for view in ("gd/view/deck-1", "gd/view/panel-2"):
                    _run_tmux(["new-session", "-d", "-t", f"={session}", "-s", view], check=True)

                target = f"={session}:"
                if ending == "exit":
                    _run_tmux(["send-keys", "-t", target, "exit", "Enter"], check=True)
                else:
                    pid = _run_tmux(
                        ["display-message", "-p", "-t", target, "#{pane_pid}"], text=True
                    )
                    os.kill(int(pid.stdout.strip()), 9)

                def sessions() -> list[str]:
                    return _run_tmux(
                        ["list-sessions", "-F", "#{session_name}"], text=True
                    ).stdout.split()

                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and len(sessions()) > 1:
                    time.sleep(0.1)
                assert sessions() == ["keepalive"]
            finally:
                subprocess.run(
                    ["tmux", "kill-server"], capture_output=True, check=False, timeout=10
                )
                _cleanup_tmux_tmpdir(tmux_dir)
