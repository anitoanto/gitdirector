"""Tests for terminal widget PTY lifecycle and selection behavior."""

from unittest.mock import MagicMock, PropertyMock, patch

import pyte
import pytest
from textual.app import App, ComposeResult

from gitdirector.commands.tui.terminal_widget import TerminalWidget, _Emulator


def _make_widget_with_screen(ncol: int = 20, nrow: int = 5) -> TerminalWidget:
    widget = TerminalWidget.__new__(TerminalWidget)
    widget._sel_start = None
    widget._sel_end = None
    widget._selecting = False
    widget._suppress_next_click = False
    widget._mouse_tracking = False
    widget._screen = pyte.Screen(ncol, nrow)
    widget._stream = pyte.Stream(widget._screen)
    return widget


def _write(widget: TerminalWidget, text: str) -> None:
    widget._stream.feed(text)


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

    def test_mouse_down_ignored_when_mouse_tracking_on(self, widget):
        widget._mouse_tracking = True
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()
        widget.on_mouse_down(self._event(3, 2))
        assert widget._selecting is False
        assert widget._sel_start is None

    def test_mouse_down_ignored_for_non_left_button(self, widget):
        widget._render_screen = MagicMock()
        widget.refresh = MagicMock()
        widget.on_mouse_down(self._event(3, 2, button=2))
        assert widget._selecting is False


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
