"""Minimal terminal emulator widget for Textual using pyte + pty."""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import re
import struct
import termios

import pyte
from pyte.screens import Char
from rich.console import Console
from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

_RE_ANSI_SEQUENCE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_DECSET_PREFIX = "\x1b[?"


class _Emulator:
    """Manages a pty subprocess and async I/O queues."""

    def __init__(self, command: str) -> None:
        self.ncol = 80
        self.nrow = 24
        self.recv_queue: asyncio.Queue = asyncio.Queue()
        self.send_queue: asyncio.Queue = asyncio.Queue()
        self._event = asyncio.Event()
        self._data_or_disconnect: str | None = None
        self._run_task: asyncio.Task | None = None
        self._send_task: asyncio.Task | None = None
        self._fd = self._open_pty(command)
        self._p_out = os.fdopen(self._fd, "w+b", 0)
        self._reader_installed = False

    def _open_pty(self, command: str) -> int:
        import shlex
        from pathlib import Path

        argv = shlex.split(command)
        pid, fd = pty.fork()
        if pid == 0:
            env = dict(os.environ)
            env.update(TERM="xterm-256color", HOME=str(Path.home()))
            os.execvpe(argv[0], argv, env)
        return fd

    def start(self) -> None:
        self._run_task = asyncio.create_task(self._run())
        self._send_task = asyncio.create_task(self._send_data())

    def stop(self) -> None:
        if self._run_task:
            self._run_task.cancel()
        if self._send_task:
            self._send_task.cancel()
        if self._reader_installed:
            try:
                asyncio.get_running_loop().remove_reader(self._fd)
            except (RuntimeError, ValueError):
                pass
            self._reader_installed = False
        try:
            self._p_out.close()
        except Exception:
            pass

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

    DEFAULT_CSS = """
    TerminalWidget {
        height: 1fr;
        width: 1fr;
    }
    """

    _started = reactive(False)

    _RESIZE_DEBOUNCE_SECONDS = 0.08

    def __init__(self, command: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._command = command
        self._emulator: _Emulator | None = None
        self._screen: pyte.Screen | None = None
        self._stream: pyte.Stream | None = None
        self._lines: list[Text] = []
        self._render_console = Console(force_terminal=True, color_system="truecolor", width=80)
        self._recv_task: asyncio.Task | None = None
        self._mouse_tracking = False
        self._pending_tty_size: tuple[int, int] | None = None
        self._tty_resize_timer = None
        self._applied_tty_size: tuple[int, int] | None = None
        self._current_size: tuple[int, int] | None = None

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
            self._render_console = Console(
                force_terminal=True,
                color_system="truecolor",
                width=ncol,
            )
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
                    self._render_console = Console(
                        force_terminal=True,
                        color_system="truecolor",
                        width=ncol,
                    )
                elif cmd == "stdout":
                    chars = msg[1]
                    for m in _RE_ANSI_SEQUENCE.finditer(chars):
                        seq = m.group(0)
                        if seq.startswith(_DECSET_PREFIX):
                            if "1000h" in seq:
                                self._mouse_tracking = True
                            if "1000l" in seq:
                                self._mouse_tracking = False
                    try:
                        self._stream.feed(chars)
                    except Exception:
                        pass
                    self._render_screen()
                    self.refresh()
                elif cmd == "disconnect":
                    self.post_message(self.Disconnected())
                    break
        except asyncio.CancelledError:
            pass

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
        except Exception:
            return Style()

    def render_line(self, y: int) -> Strip:
        cell_length = max(self.size.width, 1)
        if y < len(self._lines):
            line = self._lines[y]
            segments = list(line.render(self._render_console))
            return Strip.from_lines([segments], cell_length=cell_length)[0]
        return Strip.blank(cell_length)

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
        if not self._emulator or not self._mouse_tracking:
            return
        asyncio.create_task(
            self._emulator.recv_queue.put(("click", event.x, event.y, event.button))
        )

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
