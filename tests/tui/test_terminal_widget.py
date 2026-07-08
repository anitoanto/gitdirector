"""Tests for terminal widget PTY lifecycle and selection behavior."""

from unittest.mock import MagicMock, PropertyMock, patch

import pyte
import pytest
from textual.app import App, ComposeResult

from gitdirector.commands.tui.terminal_widget import (
    TerminalWidget,
    _Emulator,
    _normalize_colon_color_sgr,
    _render_console_kwargs,
    _TerminalScreen,
)


def _make_widget_with_screen(ncol: int = 20, nrow: int = 5) -> TerminalWidget:
    widget = TerminalWidget.__new__(TerminalWidget)
    widget._sel_start = None
    widget._sel_end = None
    widget._selecting = False
    widget._suppress_next_click = False
    widget._mouse_tracking = False
    widget._ansi_tail = ""
    widget._screen = pyte.Screen(ncol, nrow)
    widget._stream = pyte.Stream(widget._screen)
    return widget


def _write(widget: TerminalWidget, text: str) -> None:
    widget._stream.feed(text)


def _char_from_ansi(text: str):
    screen = pyte.Screen(5, 1)
    stream = pyte.Stream(screen)
    stream.feed(text)
    return screen.buffer[0][0]


def _rgb(color) -> tuple[int, int, int]:
    triplet = color.triplet
    return (triplet.red, triplet.green, triplet.blue)


class TestEmulatorCleanup:
    @patch("gitdirector.commands.tui.terminal_widget._terminate_and_reap")
    def test_stop_detaches_finalizer_after_explicit_cleanup(self, mock_terminate):
        emulator = _Emulator.__new__(_Emulator)
        emulator._stopped = False
        emulator._run_task = None
        emulator._send_task = None
        emulator._reader_installed = False
        emulator._p_out = MagicMock()
        emulator._pid = 1234
        emulator._fd = 9
        emulator._finalizer = MagicMock()

        emulator.stop()
        emulator.stop()

        emulator._p_out.close.assert_called_once_with()
        mock_terminate.assert_called_once_with(1234)
        emulator._finalizer.detach.assert_called_once_with()
        assert emulator._pid == 0
        assert emulator._fd == -1


class TestTerminalColorRendering:
    def test_truecolor_foreground_becomes_rich_color(self):
        style = TerminalWidget._char_to_style(_char_from_ansi("\x1b[38;2;255;0;128mX"))

        assert _rgb(style.color) == (255, 0, 128)

    def test_truecolor_background_becomes_rich_color(self):
        style = TerminalWidget._char_to_style(_char_from_ansi("\x1b[48;2;4;5;6mX"))

        assert _rgb(style.bgcolor) == (4, 5, 6)

    def test_256_color_hex_from_pyte_becomes_rich_color(self):
        style = TerminalWidget._char_to_style(_char_from_ansi("\x1b[38;5;196mX"))

        assert _rgb(style.color) == (255, 0, 0)

    def test_bright_pyte_color_name_becomes_rich_color(self):
        style = TerminalWidget._char_to_style(_char_from_ansi("\x1b[91mX"))

        assert style.color.name == "bright_red"
        assert style.color.number == 9

    def test_brown_pyte_color_name_becomes_rich_yellow(self):
        style = TerminalWidget._char_to_style(_char_from_ansi("\x1b[33mX"))

        assert style.color.name == "yellow"

    def test_bright_background_pyte_color_name_becomes_rich_color(self):
        style = TerminalWidget._char_to_style(_char_from_ansi("\x1b[105mX"))

        assert style.bgcolor.name == "bright_magenta"

    def test_colon_truecolor_sgr_is_normalized_before_pyte(self):
        normalized = _normalize_colon_color_sgr("\x1b[1;38:2::1:2:3;48:2:4:5:6mX")
        style = TerminalWidget._char_to_style(_char_from_ansi(normalized))

        assert normalized == "\x1b[1;38;2;1;2;3;48;2;4;5;6mX"
        assert _rgb(style.color) == (1, 2, 3)
        assert _rgb(style.bgcolor) == (4, 5, 6)
        assert style.bold is True

    def test_split_colon_truecolor_sgr_waits_for_complete_sequence(self):
        widget = _make_widget_with_screen(ncol=5, nrow=1)
        widget._pending_output = ["\x1b[38:2::1"]
        widget._render_timer = None
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()

        widget._flush_pending_output()

        assert widget._ansi_tail == "\x1b[38:2::1"
        assert widget._screen.buffer[0][0].data == " "

        widget._pending_output = [":2:3mX"]
        widget._flush_pending_output()

        char = widget._screen.buffer[0][0]
        style = TerminalWidget._char_to_style(char)
        assert widget._ansi_tail == ""
        assert char.data == "X"
        assert _rgb(style.color) == (1, 2, 3)

    def test_invalid_color_values_do_not_break_rendering(self):
        char = MagicMock(
            fg="not-a-color",
            bg="also-not-a-color",
            bold=True,
            italics=True,
            underscore=False,
            strikethrough=True,
            reverse=False,
        )

        style = TerminalWidget._char_to_style(char)

        assert style.color is None
        assert style.bgcolor is None
        assert style.bold is True
        assert style.italic is True
        assert style.strike is True


class TestRenderConsoleKwargs:
    """The terminal widget's Rich console must always use truecolor.

    Quantising to 256 colours produces visible banding in agent
    gradients (Claude, OpenCode, Codex, etc.). ``NO_COLOR`` is the only
    exception — we drop the colour system entirely so Rich emits no
    escapes at all.
    """

    def test_force_truecolor_when_host_advertises_truecolor(self, monkeypatch):
        monkeypatch.setenv("COLORTERM", "truecolor")
        monkeypatch.delenv("NO_COLOR", raising=False)

        kwargs = _render_console_kwargs(80)

        assert kwargs["color_system"] == "truecolor"
        assert kwargs["force_terminal"] is True
        assert kwargs["width"] == 80

    def test_force_truecolor_when_host_advertises_only_256(self, monkeypatch):
        monkeypatch.setenv("TERM", "tmux-256color")
        monkeypatch.delenv("COLORTERM", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)

        kwargs = _render_console_kwargs(80)

        assert kwargs["color_system"] == "truecolor"

    def test_force_truecolor_when_host_advertises_only_8(self, monkeypatch):
        monkeypatch.setenv("TERM", "ansi")
        monkeypatch.delenv("COLORTERM", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)

        kwargs = _render_console_kwargs(80)

        assert kwargs["color_system"] == "truecolor"

    def test_drop_color_system_when_no_color_requested(self, monkeypatch):
        monkeypatch.setenv("COLORTERM", "truecolor")
        monkeypatch.setenv("NO_COLOR", "1")

        kwargs = _render_console_kwargs(80)

        assert kwargs["color_system"] is None

    def test_drop_color_system_when_term_is_dumb(self, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        monkeypatch.delenv("NO_COLOR", raising=False)

        kwargs = _render_console_kwargs(80)

        assert kwargs["color_system"] is None


class TestTruecolorGradientThroughConsole:
    """A full gradient rendered through the widget's console must stay truecolor."""

    def test_gradient_segments_carry_truecolor_styles(self):
        widget = _make_widget_with_screen(ncol=10, nrow=1)
        widget._render_console = None

        kwargs = _render_console_kwargs(10)
        from rich.console import Console

        console = Console(**kwargs)

        from rich.text import Text

        text = Text()
        for i in range(10):
            r = int(255 * i / 9)
            g = int(255 * (9 - i) / 9)
            b = 128
            text.append("X", style=f"#{r:02x}{g:02x}{b:02x}")

        segments = list(text.render(console))

        unique_colors = {
            seg.style.color.triplet
            for seg in segments
            if seg.style and seg.style.color and seg.style.color.triplet
        }

        assert len(unique_colors) == 10
        assert console.color_system == "truecolor"


class TestTerminalScreenPrivateQueries:
    """tmux 3.7+ probes clients with private DSR queries (e.g. the theme
    query ``CSI ? 996 n``); those must not abort the pyte feed and drop
    the rest of the batch."""

    def test_private_device_status_query_does_not_drop_batch(self):
        screen = _TerminalScreen(20, 2)
        stream = pyte.Stream(screen)

        stream.feed("BEFORE \x1b[?996n AFTER")

        row = "".join(screen.buffer[0][x].data for x in range(20))
        assert row.rstrip() == "BEFORE  AFTER"

    def test_non_private_device_status_still_handled(self):
        screen = _TerminalScreen(20, 2)
        replies: list[str] = []
        screen.write_process_input = replies.append
        stream = pyte.Stream(screen)

        stream.feed("AB\x1b[6n")

        assert replies == ["\x1b[1;3R"]


class TestSelectionRect:
    def test_returns_none_when_no_selection(self):
        widget = _make_widget_with_screen()
        assert widget._selection_rect() is None

    def test_normalizes_when_end_before_start(self):
        widget = _make_widget_with_screen()
        widget._sel_start = (2, 5)
        widget._sel_end = (1, 1)
        rect = widget._selection_rect()
        assert rect == ((1, 1), (2, 5))

    def test_keeps_order_when_start_before_end(self):
        widget = _make_widget_with_screen()
        widget._sel_start = (1, 1)
        widget._sel_end = (2, 5)
        assert widget._selection_rect() == ((1, 1), (2, 5))


class TestCellInRect:
    def test_returns_true_inside_rect(self):
        widget = _make_widget_with_screen()
        rect = ((1, 2), (3, 4))
        assert widget._cell_in_rect(2, 3, rect)

    def test_returns_false_outside_row(self):
        widget = _make_widget_with_screen()
        rect = ((1, 2), (3, 4))
        assert not widget._cell_in_rect(4, 3, rect)

    def test_returns_false_outside_first_row_upper_col(self):
        widget = _make_widget_with_screen()
        rect = ((1, 2), (3, 4))
        assert not widget._cell_in_rect(1, 1, rect)

    def test_returns_false_outside_last_row_lower_col(self):
        widget = _make_widget_with_screen()
        rect = ((1, 2), (3, 4))
        assert not widget._cell_in_rect(3, 5, rect)


class TestExtractText:
    def test_extracts_selection_rectangle_across_rows(self):
        widget = _make_widget_with_screen(ncol=10, nrow=3)
        _write(widget, "abcdefghij\r\n0123456789\r\nABCDEFGHIJ")
        rect = ((0, 2), (2, 4))
        text = widget._extract_text(rect, full_rows=False)
        assert text == "cde\n234\nCDE"

    def test_extracts_selection_single_row(self):
        widget = _make_widget_with_screen(ncol=10, nrow=3)
        _write(widget, "abcdefghij\r\n0123456789\r\nABCDEFGHIJ")
        rect = ((1, 2), (1, 4))
        text = widget._extract_text(rect, full_rows=False)
        assert text == "234"

    def test_extracts_full_screen_when_no_rect(self):
        widget = _make_widget_with_screen(ncol=5, nrow=3)
        _write(widget, "hel  \r\nwo   \r\nx")
        text = widget._extract_text(None, full_rows=True)
        assert text == "hel\nwo\nx"

    def test_strips_trailing_whitespace_per_row(self):
        widget = _make_widget_with_screen(ncol=10, nrow=2)
        _write(widget, "abc       \r\nxy  ")
        rect = ((0, 0), (1, 9))
        text = widget._extract_text(rect, full_rows=False)
        assert text == "abc\nxy"

    def test_extracts_two_line_block_with_partial_ends(self):
        widget = _make_widget_with_screen(ncol=10, nrow=2)
        _write(widget, "abcdefghij\r\n0123456789")
        rect = ((0, 2), (1, 4))
        text = widget._extract_text(rect, full_rows=False)
        assert text == "cde\n234"

    def test_normalizes_when_end_before_start(self):
        widget = _make_widget_with_screen(ncol=10, nrow=2)
        _write(widget, "abcdefghij\r\n0123456789")
        rect = ((1, 4), (0, 2))
        text = widget._extract_text(rect, full_rows=False)
        assert text == "cde\n234"


class TestCopyVisibleAction:
    def test_copies_visible_pane_when_no_selection(self):
        widget = _make_widget_with_screen(ncol=6, nrow=2)
        _write(widget, "hi\r\nbye")
        widget._copy_text = MagicMock()
        widget.action_copy_visible()
        widget._copy_text.assert_called_once()
        copied_arg = widget._copy_text.call_args.args[0]
        assert copied_arg == "hi\nbye"

    def test_copies_selection_when_present(self):
        widget = _make_widget_with_screen(ncol=10, nrow=2)
        _write(widget, "abcdefghij\r\n0123456789")
        widget._sel_start = (0, 2)
        widget._sel_end = (1, 4)
        widget._selecting = True
        widget._copy_text = MagicMock()
        widget.action_copy_visible()
        widget._copy_text.assert_called_once()
        copied_arg = widget._copy_text.call_args.args[0]
        assert copied_arg == "cde\n234"
        assert widget._sel_start == (0, 2)
        assert widget._sel_end == (1, 4)

    def test_no_op_when_screen_missing(self):
        widget = _make_widget_with_screen()
        widget._screen = None
        widget._copy_text = MagicMock()
        widget.action_copy_visible()
        widget._copy_text.assert_not_called()


class TestCopyText:
    def test_skips_empty_text(self):
        widget = _make_widget_with_screen()
        widget.post_message = MagicMock()
        widget.copy_to_clipboard = MagicMock()  # not present; uses app.copy_to_clipboard
        widget._copy_text("")
        widget.post_message.assert_not_called()

    @patch("gitdirector.commands.tui.terminal_widget._copy_to_system_clipboard", return_value=True)
    def test_prefers_system_clipboard_without_app_output(self, mock_system_copy):
        widget = _make_widget_with_screen()
        widget.post_message = MagicMock()
        app = MagicMock()
        with patch.object(TerminalWidget, "app", new_callable=PropertyMock, return_value=app):
            widget._copy_text("hello")
        mock_system_copy.assert_called_once_with("hello")
        app.copy_to_clipboard.assert_not_called()
        widget.post_message.assert_called_once()

    @patch("gitdirector.commands.tui.terminal_widget._copy_to_system_clipboard", return_value=False)
    def test_falls_back_to_textual_clipboard(self, mock_system_copy):
        widget = _make_widget_with_screen()
        widget.post_message = MagicMock()
        app = MagicMock()
        with patch.object(TerminalWidget, "app", new_callable=PropertyMock, return_value=app):
            widget._copy_text("hello")
        mock_system_copy.assert_called_once_with("hello")
        app.copy_to_clipboard.assert_called_once_with("hello")
        widget.post_message.assert_called_once()


class TestMouseHandlers:
    @pytest.fixture
    def widget(self):
        return _make_widget_with_screen(ncol=10, nrow=5)

    def _event(self, x, y, button=1):
        ev = MagicMock()
        ev.x = x
        ev.y = y
        ev.button = button
        return ev

    def test_mouse_down_starts_selection(self, widget):
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()
        widget.capture_mouse = MagicMock()
        ev = self._event(3, 2)
        widget.on_mouse_down(ev)
        assert widget._selecting is True
        assert widget._sel_start == (2, 3)
        assert widget._sel_end == (2, 3)
        widget.capture_mouse.assert_called_once_with()
        ev.stop.assert_called_once_with()
        ev.prevent_default.assert_called_once_with()
        widget._render_screen.assert_not_called()
        widget.refresh.assert_not_called()

    def test_mouse_move_extends_selection(self, widget):
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()
        widget._selecting = True
        widget._sel_start = (2, 3)
        widget._sel_end = (2, 3)
        ev = self._event(5, 2)
        widget.on_mouse_move(ev)
        assert widget._sel_end == (2, 5)
        ev.stop.assert_called_once_with()
        ev.prevent_default.assert_called_once_with()
        widget._render_screen.assert_not_called()
        widget.refresh.assert_not_called()

    def test_mouse_move_ignored_when_not_selecting(self, widget):
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()
        widget.on_mouse_move(self._event(5, 2))
        assert widget._sel_start is None
        assert widget._sel_end is None

    def test_mouse_move_clamps_outside_screen(self, widget):
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()
        widget._selecting = True
        widget._sel_start = (2, 3)
        widget._sel_end = (2, 3)
        widget.on_mouse_move(self._event(99, 99))
        assert widget._sel_end == (4, 9)

    def test_mouse_up_copies_without_redrawing(self, widget):
        _write(widget, "abcdefghij\r\n0123456789\r\nABCDEFGHIJ")
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()
        widget._copy_text = MagicMock()
        widget.release_mouse = MagicMock()
        widget._selecting = True
        widget._sel_start = (0, 0)
        widget._sel_end = (2, 9)
        ev = self._event(9, 2)
        widget.on_mouse_up(ev)
        widget._copy_text.assert_called_once()
        assert widget._selecting is False
        assert widget._suppress_next_click is True
        assert widget._sel_start == (0, 0)
        assert widget._sel_end == (2, 9)
        assert widget._copy_text.call_args.args[0] == "abcdefghij\n0123456789\nABCDEFGHIJ"
        widget.release_mouse.assert_called_once_with()
        ev.stop.assert_called_once_with()
        ev.prevent_default.assert_called_once_with()
        widget._render_screen.assert_not_called()
        widget.refresh.assert_not_called()

    def test_click_after_selection_is_suppressed(self, widget):
        widget._suppress_next_click = True
        ev = self._event(4, 1)
        widget.on_click(ev)
        assert widget._suppress_next_click is False
        ev.stop.assert_called_once_with()
        ev.prevent_default.assert_called_once_with()

    def test_mouse_down_starts_selection_when_mouse_tracking_on(self, widget):
        widget._mouse_tracking = True
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()
        widget.capture_mouse = MagicMock()
        widget.on_mouse_down(self._event(3, 2))
        assert widget._selecting is True
        assert widget._sel_start == (2, 3)

    def test_mouse_down_ignored_for_non_left_button(self, widget):
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()
        widget.on_mouse_down(self._event(3, 2, button=2))
        assert widget._selecting is False

    def test_mouse_tracking_detects_sgr_mouse_mode(self, widget):
        widget._pending_output = ["\x1b[?1006h"]
        widget._render_timer = None
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()
        widget._flush_pending_output()
        assert widget._mouse_tracking is True

    def test_mouse_tracking_detects_sgr_mouse_mode_off(self, widget):
        widget._mouse_tracking = True
        widget._pending_output = ["\x1b[?1006l"]
        widget._render_timer = None
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()
        widget._flush_pending_output()
        assert widget._mouse_tracking is False


class TestBindings:
    def test_y_binding_priority(self):
        assert any(b.key == "y" and b.priority for b in TerminalWidget.BINDINGS)


class _TerminalWidgetTestApp(App):
    def compose(self) -> ComposeResult:
        yield TerminalWidget(command="true", id="term")


def _set_widget_screen(widget: TerminalWidget, text: str, *, ncol: int = 20, nrow: int = 5) -> None:
    widget._screen = pyte.Screen(ncol, nrow)
    widget._stream = pyte.Stream(widget._screen)
    widget._stream.feed(text)
    widget._render_screen()


class TestTerminalWidgetClipboardIntegration:
    @pytest.mark.asyncio
    async def test_y_copies_visible_text_to_app_clipboard(self):
        app = _TerminalWidgetTestApp()

        with patch(
            "gitdirector.commands.tui.terminal_widget._copy_to_system_clipboard", return_value=False
        ):
            async with app.run_test(size=(40, 10)) as pilot:
                widget = app.query_one("#term", TerminalWidget)
                _set_widget_screen(widget, "hello\r\nworld")
                widget.focus()

                await pilot.press("y")

                assert app._clipboard == "hello\nworld"

    @pytest.mark.asyncio
    async def test_mouse_drag_copies_selection_to_app_clipboard(self):
        app = _TerminalWidgetTestApp()

        with patch(
            "gitdirector.commands.tui.terminal_widget._copy_to_system_clipboard", return_value=False
        ):
            async with app.run_test(size=(40, 10)) as pilot:
                widget = app.query_one("#term", TerminalWidget)
                _set_widget_screen(widget, "abcdefghij\r\n0123456789")

                await pilot.mouse_down(widget, offset=(2, 0))
                await pilot.hover(widget, offset=(4, 1))
                await pilot.mouse_up(widget, offset=(4, 1))

                assert app._clipboard == "cde\n234"
