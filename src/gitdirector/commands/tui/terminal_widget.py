"""Minimal terminal emulator widget for Textual using pyte + pty."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import pty
import re
import shutil
import signal
import struct
import subprocess
import termios
import weakref

import pyte
from pyte.screens import Char
from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

from .terminal_caps import host_color_system, no_color_requested

logger = logging.getLogger(__name__)

_RE_ANSI_SEQUENCE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_DECSET_PREFIX = "\x1b[?"
_MOUSE_TRACKING_MODES = ("1000", "1002", "1003", "1006", "1015")

_EMULATOR_TERM_WAIT_SECONDS = 2.0
_EMULATOR_KILL_WAIT_SECONDS = 2.0


def _copy_to_system_clipboard(text: str) -> bool:
    for command in (
        ["pbcopy"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["clip.exe"],
    ):
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(
                command,
                input=text,
                text=True,
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("%s clipboard copy failed: %s", command[0], exc)
            continue
        return True
    return False


def _pid_is_alive(pid: int) -> bool:
    """Return True if *pid* still exists (no signal delivery)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _reap_zombie(pid: int) -> bool:
    """Non-blocking reap. Returns True if *pid* was reaped."""
    try:
        waited_pid, _status = os.waitpid(pid, os.WNOHANG)
        return waited_pid == pid
    except ChildProcessError:
        return True
    except OSError:
        return False


def _terminate_and_reap(pid: int) -> None:
    """Reliably terminate *pid* and reap the zombie.

    Sequence: SIGTERM → wait up to ``_EMULATOR_TERM_WAIT_SECONDS`` →
    SIGKILL → blocking ``waitpid``. The blocking wait is the critical
    step — without it the kernel keeps the zombie (and its slave PTY
    fd) alive until something else reaps it.
    """
    import time as _time

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _reap_zombie(pid)
        return
    except OSError as exc:
        logger.debug("SIGTERM to pid %s failed: %s", pid, exc)

    term_deadline = _time.monotonic() + _EMULATOR_TERM_WAIT_SECONDS
    while _time.monotonic() < term_deadline:
        if not _pid_is_alive(pid) or _reap_zombie(pid):
            return
        _time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        _reap_zombie(pid)
        return
    except OSError as exc:
        logger.debug("SIGKILL to pid %s failed: %s", pid, exc)

    kill_deadline = _time.monotonic() + _EMULATOR_KILL_WAIT_SECONDS
    while _time.monotonic() < kill_deadline:
        if _reap_zombie(pid):
            return
        _time.sleep(0.05)

    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        return
    except OSError as exc:
        logger.warning("blocking waitpid for pid %s failed: %s", pid, exc)


def _finalize_emulator(pid: int, fd: int) -> None:
    """Best-effort cleanup when ``_Emulator`` is GC'd without ``stop``."""
    try:
        os.close(fd)
    except OSError:
        pass
    if pid > 0:
        _terminate_and_reap(pid)


def _render_console_kwargs(width: int) -> dict:
    """Build kwargs for the render Rich ``Console`` used to format cells.

    We still set ``force_terminal=True`` because the output is consumed
    by Textual (which paints the strips itself), but we let the colour
    system degrade to whatever the host actually supports instead of
    unconditionally forcing truecolor. When ``NO_COLOR`` is set we drop
    the colour system entirely so we don't emit unrenderable escapes.
    """
    if no_color_requested():
        return {"force_terminal": True, "color_system": None, "width": width}
    system = host_color_system() or "256"
    return {"force_terminal": True, "color_system": system, "width": width}


class _Emulator:
    """Manages a pty subprocess and async I/O queues.

    Owns one PTY per instance. :meth:`stop` always terminates the child
    (SIGTERM then SIGKILL) and **blocking** ``os.waitpid`` so the kernel
    reaps the zombie and releases the slave end of the PTY. A
    ``weakref.finalize`` backup runs ``stop`` if the instance is garbage
    collected without ``stop`` having been called explicitly — the PTY is
    only freed when both the master and slave fds close, and a forgotten
    instance would otherwise leak the slave indefinitely.
    """

    def __init__(self, command: str) -> None:
        self.ncol = 80
        self.nrow = 24
        self.recv_queue: asyncio.Queue = asyncio.Queue()
        self.send_queue: asyncio.Queue = asyncio.Queue()
        self._event = asyncio.Event()
        self._data_or_disconnect: str | None = None
        self._run_task: asyncio.Task | None = None
        self._send_task: asyncio.Task | None = None
        self._pid, self._fd = self._open_pty(command)
        self._p_out = os.fdopen(self._fd, "w+b", 0)
        self._reader_installed = False
        self._stopped = False
        self._finalizer = weakref.finalize(self, _finalize_emulator, self._pid, self._fd)

    def _open_pty(self, command: str) -> tuple[int, int]:
        import shlex
        from pathlib import Path

        argv = shlex.split(command)
        pid, fd = pty.fork()
        if pid == 0:
            env = dict(os.environ)
            env.update(TERM="xterm-256color", HOME=str(Path.home()))
            os.execvpe(argv[0], argv, env)
        return pid, fd

    def start(self) -> None:
        self._run_task = asyncio.create_task(self._run())
        self._send_task = asyncio.create_task(self._send_data())

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self._run_task:
            self._run_task.cancel()
        if self._send_task:
            self._send_task.cancel()
        if self._reader_installed:
            try:
                loop = asyncio.get_running_loop()
                loop.remove_reader(self._fd)
            except (RuntimeError, ValueError):
                pass
            self._reader_installed = False
        try:
            self._p_out.close()
        except Exception:
            pass
        if self._pid > 0:
            _terminate_and_reap(self._pid)
        self._finalizer.detach()
        self._pid = 0
        self._fd = -1

    def resize(self, nrow: int, ncol: int) -> None:
        self.nrow = nrow
        self.ncol = ncol

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()

        def on_output():
            try:
                data = self._p_out.read(65536)
                if not data:
                    raise EOFError()
                self._data_or_disconnect = data.decode(errors="replace")
                self._event.set()
            except Exception:
                if self._reader_installed:
                    try:
                        loop.remove_reader(self._fd)
                    except ValueError:
                        pass
                    self._reader_installed = False
                self._data_or_disconnect = None
                self._event.set()

        loop.add_reader(self._fd, on_output)
        self._reader_installed = True
        await self.send_queue.put(("setup", {}))

        try:
            while True:
                msg = await self.recv_queue.get()
                cmd = msg[0]
                if cmd == "stdin":
                    self._p_out.write(msg[1].encode())
                elif cmd == "set_size":
                    winsize = struct.pack("HH", msg[1], msg[2])
                    fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)
                elif cmd == "click":
                    x, y, button = msg[1] + 1, msg[2] + 1, msg[3]
                    if button == 1:
                        self._p_out.write(f"\x1b[M {chr(32 + x)}{chr(32 + y)}".encode())
        except asyncio.CancelledError:
            pass

    async def _send_data(self) -> None:
        try:
            while True:
                self._event.clear()
                await self._event.wait()
                data = self._data_or_disconnect
                if data is not None:
                    await self.send_queue.put(("stdout", data))
                else:
                    await self.send_queue.put(("disconnect", 1))
        except asyncio.CancelledError:
            pass


class TerminalWidget(Widget, can_focus=True):
    """A terminal emulator widget that runs a command in a pseudo-terminal."""

    class Disconnected(Message):
        def __init__(self) -> None:
            super().__init__()

    class Copied(Message):
        """Posted after text has been copied to the OS clipboard."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    DEFAULT_CSS = """
    TerminalWidget {
        height: 1fr;
        width: 1fr;
    }
    """

    BINDINGS = [
        Binding("y", "copy_visible", "Copy", show=False, priority=True),
    ]
    """``y`` is intercepted (priority=True) so it reaches this widget's
    copy action even when other widgets inside the pane would otherwise
    handle it. Acts as a fallback to mouse-drag selection (always copies
    the visible pane contents)."""

    _started = reactive(False)

    _RESIZE_DEBOUNCE_SECONDS = 0.08
    _RENDER_DEBOUNCE_SECONDS = 1 / 30

    def __init__(self, command: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._command = command
        self._emulator: _Emulator | None = None
        self._screen: pyte.Screen | None = None
        self._stream: pyte.Stream | None = None
        self._lines: list[Text] = []
        self._render_console = Console(**_render_console_kwargs(80))
        self._recv_task: asyncio.Task | None = None
        self._mouse_tracking = False
        self._pending_tty_size: tuple[int, int] | None = None
        self._tty_resize_timer = None
        self._pending_output: list[str] = []
        self._render_timer = None
        self._applied_tty_size: tuple[int, int] | None = None
        self._current_size: tuple[int, int] | None = None
        self._sel_start: tuple[int, int] | None = None
        self._sel_end: tuple[int, int] | None = None
        self._selecting: bool = False
        self._suppress_next_click: bool = False

    def start(self) -> None:
        if self._started:
            return
        self._emulator = _Emulator(self._command)
        self._emulator.start()
        self._recv_task = asyncio.create_task(self._recv())
        self._started = True

    def stop(self) -> None:
        self._started = False
        if self._tty_resize_timer is not None:
            try:
                self._tty_resize_timer.stop()
            except Exception:
                pass
            self._tty_resize_timer = None
        if self._render_timer is not None:
            try:
                self._render_timer.stop()
            except Exception:
                pass
            self._render_timer = None
        self._pending_output.clear()
        self._pending_tty_size = None
        if self._recv_task:
            self._recv_task.cancel()
            self._recv_task = None
        if self._emulator:
            self._emulator.stop()
            self._emulator = None

    def on_unmount(self) -> None:
        self.stop()

    def on_resize(self, event: events.Resize) -> None:
        nrow = event.size.height
        ncol = event.size.width
        if nrow < 1 or ncol < 1:
            return
        if self._current_size == (nrow, ncol):
            return
        self._current_size = (nrow, ncol)

        if self._render_console.width != ncol:
            self._render_console = Console(**_render_console_kwargs(ncol))
        if self._screen is not None and (
            self._screen.columns != ncol or self._screen.lines != nrow
        ):
            try:
                self._screen.resize(nrow, ncol)
            except Exception:
                pass
            self._render_screen()

        self._pending_tty_size = (nrow, ncol)
        if self._tty_resize_timer is not None:
            try:
                self._tty_resize_timer.stop()
            except Exception:
                pass
            self._tty_resize_timer = None
        self._tty_resize_timer = self.set_timer(
            self._RESIZE_DEBOUNCE_SECONDS, self._commit_pending_tty_resize
        )
        self.refresh()

    def _commit_pending_tty_resize(self) -> None:
        self._tty_resize_timer = None
        pending = self._pending_tty_size
        self._pending_tty_size = None
        if pending is None or self._emulator is None:
            return
        if self._applied_tty_size == pending:
            return
        self._applied_tty_size = pending
        nrow, ncol = pending
        self._emulator.resize(nrow, ncol)
        asyncio.create_task(self._emulator.recv_queue.put(("set_size", nrow, ncol)))

    async def _recv(self) -> None:
        if not self._emulator:
            return
        try:
            while True:
                msg = await self._emulator.send_queue.get()
                cmd = msg[0]
                if cmd == "setup":
                    nrow = self.size.height or 24
                    ncol = self.size.width or 80
                    self._screen = pyte.Screen(ncol, nrow)
                    self._stream = pyte.Stream(self._screen)
                    self._emulator.resize(nrow, ncol)
                    await self._emulator.recv_queue.put(("set_size", nrow, ncol))
                    self._applied_tty_size = (nrow, ncol)
                    self._current_size = (nrow, ncol)
                    self._render_console = Console(**_render_console_kwargs(ncol))
                elif cmd == "stdout":
                    self._queue_output(msg[1])
                elif cmd == "disconnect":
                    self._flush_pending_output()
                    self.post_message(self.Disconnected())
                    if self._emulator is not None:
                        self._emulator.stop()
                        self._emulator = None
                    self._started = False
                    break
        except asyncio.CancelledError:
            pass

    def _queue_output(self, chars: str) -> None:
        self._pending_output.append(chars)
        if self._render_timer is None:
            self._render_timer = self.set_timer(
                self._RENDER_DEBOUNCE_SECONDS,
                self._flush_pending_output,
            )

    def _flush_pending_output(self) -> None:
        self._render_timer = None
        if not self._pending_output or self._stream is None:
            self._pending_output.clear()
            return
        chars = "".join(self._pending_output)
        self._pending_output.clear()
        for match in _RE_ANSI_SEQUENCE.finditer(chars):
            seq = match.group(0)
            if seq.startswith(_DECSET_PREFIX):
                if seq.endswith("h") and any(mode in seq for mode in _MOUSE_TRACKING_MODES):
                    self._mouse_tracking = True
                if seq.endswith("l") and any(mode in seq for mode in _MOUSE_TRACKING_MODES):
                    self._mouse_tracking = False
        try:
            self._stream.feed(chars)
        except Exception:
            pass
        self._render_screen()
        self.refresh()

    def _render_screen(self) -> None:
        if not self._screen:
            return
        lines: list[Text] = []
        for y in range(self._screen.lines):
            line_text = Text()
            row = self._screen.buffer[y]
            seg_start = 0
            prev_style: Style | None = None
            for x in range(self._screen.columns):
                char: Char = row[x]
                style = self._char_to_style(char)
                if prev_style is not None and style != prev_style:
                    line_text.stylize(prev_style, seg_start, x)
                    seg_start = x
                line_text.append(char.data)
                prev_style = style
                if self._screen.cursor.x == x and self._screen.cursor.y == y and self.has_focus:
                    line_text.stylize("reverse", x, x + 1)
            if prev_style is not None:
                line_text.stylize(prev_style, seg_start, self._screen.columns)
            lines.append(line_text)
        self._lines = lines

    @staticmethod
    def _color_to_rich(color) -> str | None:
        if color == "default" or color is None:
            return None
        if isinstance(color, tuple):
            r, g, b = color
            return f"#{r:02x}{g:02x}{b:02x}"
        if isinstance(color, int):
            return f"color({color})"
        return str(color)

    @staticmethod
    def _char_to_style(char: Char) -> Style:
        fg = TerminalWidget._color_to_rich(char.fg)
        bg = TerminalWidget._color_to_rich(char.bg)
        try:
            return Style(
                color=fg,
                bgcolor=bg,
                bold=char.bold,
                italic=char.italics,
                underline=char.underscore,
                strike=char.strikethrough,
                reverse=char.reverse,
            )
        except (TypeError, ValueError):
            # Bad color value from the child process. Render the cell with
            # the foreground only so the rest of the pane stays legible
            # rather than collapsing the whole cell to an unstyled glyph.
            try:
                return Style(color=fg)
            except (TypeError, ValueError):
                return Style()

    def render_line(self, y: int) -> Strip:
        cell_length = max(self.size.width, 1)
        if y < len(self._lines):
            line = self._lines[y]
            segments = list(line.render(self._render_console))
            return Strip.from_lines([segments], cell_length=cell_length)[0]
        return Strip.blank(cell_length)

    def _selection_rect(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        """Return a normalized ``((row, col), (row, col))`` rect, or None."""
        if self._sel_start is None or self._sel_end is None:
            return None
        r1, c1 = self._sel_start
        r2, c2 = self._sel_end
        if (r1, c1) > (r2, c2):
            r1, c1, r2, c2 = r2, c2, r1, c1
        return ((r1, c1), (r2, c2))

    @staticmethod
    def _cell_in_rect(row: int, col: int, rect: tuple[tuple[int, int], tuple[int, int]]) -> bool:
        (r1, c1), (r2, c2) = rect
        if row < r1 or row > r2:
            return False
        if row == r1 and row == r2:
            return c1 <= col <= c2
        if row == r1:
            return col >= c1
        if row == r2:
            return col <= c2
        return True

    def _extract_text(
        self,
        rect: tuple[tuple[int, int], tuple[int, int]] | None,
        *,
        full_rows: bool,
    ) -> str:
        """Extract text from the pyte screen.

        When ``rect`` is None and ``full_rows`` is True the whole screen
        is returned. When ``rect`` is set, only its rectangle is returned
        — all rows between start and end inclusive, but each row is
        clipped to the column range. Trailing whitespace is stripped
        from each row.
        """
        if self._screen is None:
            return ""
        if rect is None and not full_rows:
            return ""
        last_col = self._screen.columns - 1
        lines: list[str] = []
        if rect is None:
            rows_to_emit = range(self._screen.lines)
            col_start, col_end = 0, last_col
        else:
            (r1, c1), (r2, c2) = rect
            row_start, row_end = min(r1, r2), max(r1, r2)
            rows_to_emit = range(row_start, row_end + 1)
            col_start, col_end = min(c1, c2), max(c1, c2)

        for r in rows_to_emit:
            row = self._screen.buffer[r]
            chars = [row[c].data for c in range(col_start, col_end + 1)]
            lines.append("".join(chars).rstrip())
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines)

    def _visible_text(self) -> str:
        return self._extract_text(None, full_rows=True)

    def _selection_text(self) -> str:
        return self._extract_text(self._selection_rect(), full_rows=False)

    def _copy_text(self, text: str) -> None:
        if not text:
            return
        if not _copy_to_system_clipboard(text):
            try:
                self.app.copy_to_clipboard(text)
            except Exception as exc:
                logger.debug("copy_to_clipboard failed: %s", exc)
                try:
                    self.notify("Clipboard not supported in this terminal", severity="warning")
                except Exception:
                    pass
                return
        self.post_message(self.Copied(text))

    def _clear_selection(self) -> None:
        if self._sel_start is None and self._sel_end is None and not self._selecting:
            return
        self._sel_start = None
        self._sel_end = None
        self._selecting = False
        self._render_screen()
        self.refresh()

    def action_copy_visible(self) -> None:
        """Copy the current selection if any, otherwise the visible pane."""
        if self._screen is None:
            return
        if self._sel_start is not None or self._sel_end is not None:
            self._copy_text(self._selection_text())
            return
        self._copy_text(self._visible_text())

    def on_key(self, event: events.Key) -> None:
        if not self._emulator or not self._started:
            return

        char = None
        if event.key == "enter":
            char = "\r"
        elif event.key == "tab":
            char = "\t"
        elif event.key == "backspace":
            char = "\x7f"
        elif event.key == "escape":
            return
        elif event.key == "up":
            char = "\x1b[A"
        elif event.key == "down":
            char = "\x1b[B"
        elif event.key == "right":
            char = "\x1b[C"
        elif event.key == "left":
            char = "\x1b[D"
        elif event.key == "home":
            char = "\x1b[H"
        elif event.key == "end":
            char = "\x1b[F"
        elif event.key == "pageup" or event.key == "page_up":
            char = "\x1b[5~"
        elif event.key == "pagedown" or event.key == "page_down":
            char = "\x1b[6~"
        elif event.key == "insert":
            char = "\x1b[2~"
        elif event.key == "delete":
            char = "\x1b[3~"
        elif event.key.startswith("f") and event.key[1:].isdigit():
            fn = int(event.key[1:])
            fmap = {
                1: "\x1bOP",
                2: "\x1bOQ",
                3: "\x1bOR",
                4: "\x1bOS",
                5: "\x1b[15~",
                6: "\x1b[17~",
                7: "\x1b[18~",
                8: "\x1b[19~",
                9: "\x1b[20~",
                10: "\x1b[21~",
                11: "\x1b[23~",
                12: "\x1b[24~",
            }
            char = fmap.get(fn)
        elif event.key.startswith("ctrl+"):
            letter = event.key[5:]
            if len(letter) == 1 and letter.isalpha():
                char = chr(ord(letter.lower()) - ord("a") + 1)
        elif event.character:
            char = event.character

        if char is not None:
            event.stop()
            event.prevent_default()
            asyncio.create_task(self._emulator.recv_queue.put(("stdin", char)))

    def on_click(self, event: events.Click) -> None:
        if self._suppress_next_click:
            self._suppress_next_click = False
            event.stop()
            event.prevent_default()
            return
        if not self._emulator or not self._mouse_tracking:
            return
        asyncio.create_task(
            self._emulator.recv_queue.put(("click", event.x, event.y, event.button))
        )

    def _clamp_to_screen(self, x: int | float, y: int | float) -> tuple[int, int] | None:
        if self._screen is None:
            return None
        if self._screen.lines < 1 or self._screen.columns < 1:
            return None
        row = max(0, min(int(y), self._screen.lines - 1))
        col = max(0, min(int(x), self._screen.columns - 1))
        return (row, col)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if self._screen is None or event.button != 1:
            return
        clamped = self._clamp_to_screen(event.x, event.y)
        if clamped is None:
            return
        self._selecting = True
        self._sel_start = clamped
        self._sel_end = clamped
        event.stop()
        event.prevent_default()
        self.capture_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._selecting or self._screen is None:
            return
        event.stop()
        event.prevent_default()
        clamped = self._clamp_to_screen(event.x, event.y)
        if clamped is None or clamped == self._sel_end:
            return
        self._sel_end = clamped

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if not self._selecting or event.button != 1:
            return
        event.stop()
        event.prevent_default()
        self._suppress_next_click = True
        self.release_mouse()
        clamped = self._clamp_to_screen(event.x, event.y)
        if clamped is not None:
            self._sel_end = clamped
        text = self._selection_text()
        self._selecting = False
        self._copy_text(text)

    def on_scroll_up(self, event: events.ScrollUp) -> None:
        if not self._emulator or not self._mouse_tracking:
            return
        asyncio.create_task(
            self._emulator.recv_queue.put(
                ("stdin", "\x1b[M`" + chr(32 + event.x + 1) + chr(32 + event.y + 1))
            )
        )

    def on_scroll_down(self, event: events.ScrollDown) -> None:
        if not self._emulator or not self._mouse_tracking:
            return
        asyncio.create_task(
            self._emulator.recv_queue.put(
                ("stdin", "\x1b[Ma" + chr(32 + event.x + 1) + chr(32 + event.y + 1))
            )
        )
