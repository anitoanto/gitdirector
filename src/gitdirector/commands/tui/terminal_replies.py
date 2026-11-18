"""Answers the terminal still owes tmux never reach the console as keystrokes.

tmux asks the terminal things as its client starts (device attributes, its
version, colours, the dark or light theme). An answer that arrives once the
client is gone is read by the console, and Textual turns any reply it does
not know into keys: ``ESC[?997;1n``, a theme report, is ``1`` (the
repositories tab) and ``n`` (a new panel).
"""

from __future__ import annotations

import os
import re
import select
import sys
import termios
import time
import tty

# A cursor position report: tmux never asks for one, so this answer is ours.
_QUERY = b"\x1b[6n"
_ANSWER = re.compile(rb"\x1b\[\d+;\d+R")
# Every terminal answers at once; this only bounds one that never does.
_TIMEOUT_SECS = 0.5


def drain_terminal_replies(
    reader: int | None = None, writer: int | None = None, timeout: float = _TIMEOUT_SECS
) -> None:
    """Read away every reply the terminal still owes, before the console reads again.

    Terminals answer in order, so everything read up to the answer to a
    query sent now is earlier replies, or keys pressed before the console
    was back. *reader* and *writer* default to stdin and stdout.
    """
    try:
        reader = sys.stdin.fileno() if reader is None else reader
        writer = sys.stdout.fileno() if writer is None else writer
    except (AttributeError, OSError, ValueError):
        return
    if not (isinstance(reader, int) and isinstance(writer, int)):
        return
    if not (os.isatty(reader) and os.isatty(writer)):
        return
    try:
        saved = termios.tcgetattr(reader)
    except termios.error:
        return
    try:
        tty.setraw(reader, termios.TCSANOW)
        os.write(writer, _QUERY)
        received = b""
        deadline = time.monotonic() + timeout
        while not _ANSWER.search(received):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([reader], [], [], remaining)[0]:
                break
            received += os.read(reader, 4096)
    except OSError:
        pass
    finally:
        termios.tcsetattr(reader, termios.TCSANOW, saved)
