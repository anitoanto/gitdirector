"""Regressions for tmux failure handling: hung servers and concurrent sends."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from gitdirector.integrations.tmux.core import (
    TMUX_COMMAND_TIMEOUT,
    TmuxError,
    _run_tmux,
    send_text_to_session,
)


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
