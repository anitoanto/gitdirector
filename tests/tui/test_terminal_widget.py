"""Tests for terminal widget PTY lifecycle behavior."""

from unittest.mock import MagicMock, patch

from gitdirector.commands.tui.terminal_widget import _Emulator


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
