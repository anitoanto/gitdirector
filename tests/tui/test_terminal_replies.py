"""Replies the terminal owed tmux are read away before the console reads keys."""

import os
import select
import sys
import threading

import pytest

from gitdirector.commands.tui import terminal_replies


@pytest.fixture
def terminal():
    """A pty standing in for the user's terminal, answering cursor position queries."""
    main, side = os.openpty()
    asked = threading.Event()

    def answer():
        data = b""
        while b"\x1b[6n" not in data:
            if not select.select([main], [], [], 5)[0]:
                return
            data += os.read(main, 1024)
        asked.set()
        os.write(main, b"\x1b[12;40R")

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    yield main, side, asked
    thread.join(5)
    os.close(main)
    os.close(side)


def test_a_late_theme_report_is_read_away(terminal):
    main, side, asked = terminal
    # tmux asked for the theme and its version; the answers arrive after its
    # client is gone.
    os.write(main, b"\x1b[?997;1n\x1bP>|tmux 3.8\x1b\\")

    terminal_replies.drain_terminal_replies(side, side)

    assert asked.is_set()
    assert not select.select([side], [], [], 0.2)[0]


def test_nothing_happens_off_a_terminal(monkeypatch, tmp_path):
    plain = os.open(tmp_path / "file", os.O_RDWR | os.O_CREAT)
    try:
        terminal_replies.drain_terminal_replies(plain, plain, timeout=0.1)
    finally:
        os.close(plain)
    monkeypatch.setattr(sys, "stdin", None)
    terminal_replies.drain_terminal_replies()
