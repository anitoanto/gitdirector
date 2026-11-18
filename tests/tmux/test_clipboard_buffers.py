"""Forwarding a copy to the terminal leaves the user's paste buffers as they were."""

import os
import shutil
import subprocess
import time

import pytest

from gitdirector.integrations import clipboard_forward as F

from .._timeouts import TMUX_CMD_TIMEOUT
from ._shared import _cleanup_tmux_tmpdir, _make_short_tmux_tmpdir, _tmux_integration_lock

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")


def _tmux(*args: str, data: bytes | None = None) -> str:
    return subprocess.run(
        ["tmux", *args],
        input=data,
        capture_output=True,
        timeout=TMUX_CMD_TIMEOUT,
        check=True,
    ).stdout.decode()


@pytest.fixture
def attached_client(monkeypatch):
    with _tmux_integration_lock():
        tmux_dir = _make_short_tmux_tmpdir()
        monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
        monkeypatch.delenv("TMUX", raising=False)
        master, slave = os.openpty()
        client = None
        try:
            _tmux("-f", os.devnull, "new-session", "-d", "-s", "outer", "sleep 600")
            client = subprocess.Popen(
                ["tmux", "attach", "-t", "=outer"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env={**os.environ, "TERM": "xterm-256color"},
                start_new_session=True,
            )
            deadline = time.monotonic() + TMUX_CMD_TIMEOUT
            while not _tmux("list-clients", "-F", "#{client_name}").strip():
                assert time.monotonic() < deadline, "the client never attached"
                time.sleep(0.05)
            yield (
                _tmux("display-message", "-p", "#{socket_path}").strip(),
                _tmux("list-clients", "-F", "#{client_name}").strip(),
            )
        finally:
            subprocess.run(["tmux", "kill-server"], capture_output=True, check=False)
            if client is not None:
                client.wait(TMUX_CMD_TIMEOUT)
            os.close(master)
            os.close(slave)
            _cleanup_tmux_tmpdir(tmux_dir)


def test_the_newest_copy_stays_on_top_and_nothing_piles_up(attached_client, monkeypatch):
    socket, client = attached_client
    _tmux("load-buffer", "-", data=b"first copy")
    _tmux("load-buffer", "-", data=b"second copy")
    before = _tmux("list-buffers", "-F", "#{buffer_name}")
    monkeypatch.setattr(F, "terminal_clients", lambda tty, panes, clients: [client])
    monkeypatch.setattr(F, "_native_copy", lambda data: None)

    F.forward(socket, "/dev/nested", time.time())

    assert _tmux("show-buffer") == "second copy"
    assert _tmux("list-buffers", "-F", "#{buffer_name}") == before
