"""A real tmux pane captured with its colours and drawn by ``gd-screenshot``."""

from __future__ import annotations

import shutil
import subprocess
import time

import pytest
from click.testing import CliRunner
from PIL import Image

from gitdirector.cli import cli
from gitdirector.integrations.tmux import (
    capture_screen,
    create_tmux_session,
    launch_command_in_tmux_session,
    sync_panel_tmux_config,
)

from .._timeouts import POLL_TIMEOUT, TMUX_CMD_TIMEOUT
from ._shared import (
    _cleanup_tmux_tmpdir,
    _make_shell_home,
    _make_short_tmux_tmpdir,
    _tmux_integration_lock,
)


def _wait_for(predicate, timeout: float = POLL_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    return predicate()


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
def test_screenshot_of_a_live_session(tmp_path, monkeypatch):
    with _tmux_integration_lock():
        home_dir = _make_shell_home(tmp_path / "home")
        tmux_dir = _make_short_tmux_tmpdir()
        monkeypatch.setenv("HOME", str(home_dir))
        monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
        monkeypatch.delenv("TMUX", raising=False)
        repo = home_dir / "demo"
        repo.mkdir()
        sync_panel_tmux_config()
        try:
            session = create_tmux_session("demo", repo, shell=False)
            launch_command_in_tmux_session(
                session, r"printf '\033[41mRED\033[0m ready'; exec sleep 30"
            )
            screen = _wait_for(
                lambda: (s := capture_screen(session)) and "ready" in s.lines[0] and s
            )

            assert screen, "the pane never showed its output"
            assert "\x1b[" in screen.lines[0] and "RED" in screen.lines[0]
            assert len(screen.lines) == screen.height
            assert screen.cursor == (9, 0)

            target = tmp_path / "shot.png"
            result = CliRunner().invoke(cli, ["gd-screenshot", session, str(target)])

            assert result.exit_code == 0, result.output
            with Image.open(target) as image:
                assert image.format == "PNG"
                assert (205, 49, 49) in {color for _, color in image.getcolors(1 << 16)}
        finally:
            subprocess.run(["tmux", "kill-server"], capture_output=True, timeout=TMUX_CMD_TIMEOUT)
            _cleanup_tmux_tmpdir(tmux_dir)
