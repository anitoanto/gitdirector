"""The console reflects its own session changes at once, and a tmux sample
taken before a change never undoes it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from textual.widgets import DataTable

from gitdirector.commands.tui import GitDirectorConsole

from .conftest import _make_info, _mock_manager, patch_sessions, sample_sessions

NEW = "gd/alpha/codex/1"


def _stale_sample(app: GitDirectorConsole, entries: list[dict[str, str]]) -> None:
    """Leave the monitor holding a sample taken before whatever happens next."""
    monitor = app._monitor
    monitor._entries = entries
    monitor._entries_generation = monitor._generation


def _rows(app: GitDirectorConsole) -> list[str]:
    return [str(key.value) for key in app.query_one("#sessions-table", DataTable).rows]


class TestNewSession:
    @patch_sessions()
    async def test_a_created_session_is_listed_at_once(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._show_attach_loading_screen = MagicMock()
            with (
                patch("gitdirector.integrations.tmux.create_tmux_session", return_value=NEW),
                patch("gitdirector.integrations.tmux.launch_command_in_tmux_session"),
            ):
                app.action_open_tmux("codex", purpose="codex")

            # Listed before tmux is asked again, on whichever tab comes next.
            assert NEW in [entry["session_name"] for entry in app._sessions_entries]
            await pilot.press("2")
            await pilot.pause()
            assert NEW in _rows(app)

    @patch_sessions()
    async def test_a_stale_sample_does_not_hide_it(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            app.action_tab_sessions()
            await pilot.pause()
            _stale_sample(app, sample_sessions())

            app._show_new_session(NEW, "alpha", None)
            app._poll_session_statuses()
            await pilot.pause()

            assert NEW in _rows(app)

    @patch_sessions()
    async def test_a_failed_launch_takes_it_off_again(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            with (
                patch("gitdirector.integrations.tmux.create_tmux_session", return_value=NEW),
                patch(
                    "gitdirector.integrations.tmux.launch_command_in_tmux_session",
                    side_effect=RuntimeError("no such agent"),
                ),
                patch("gitdirector.integrations.tmux.kill_tmux_session"),
            ):
                app.action_open_tmux("codex", purpose="codex")

            assert NEW not in [entry["session_name"] for entry in app._sessions_entries]


class TestLaunchFailure:
    @patch_sessions()
    async def test_running_out_of_ptys_is_shown_plainly(self, _mock_list):
        from gitdirector.integrations.tmux import TmuxError

        app = GitDirectorConsole()
        app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.notify = MagicMock()
            error = TmuxError(
                "tmux new-session failed", returncode=1, stderr="fork failed: Device not configured"
            )
            with patch("gitdirector.integrations.tmux.create_tmux_session", side_effect=error):
                app.action_open_tmux()

            message = app.notify.call_args.args[0]
            assert message.startswith("No free terminal (pty) is left")
            assert app.notify.call_args.kwargs["severity"] == "error"
            assert app.notify.call_args.kwargs["title"] == "Couldn't start the session"


class TestDescription:
    @patch_sessions()
    async def test_an_edit_shows_at_once_and_survives_a_stale_sample(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            app.action_tab_sessions()
            await pilot.pause()
            _stale_sample(app, sample_sessions())

            with patch("gitdirector.integrations.tmux.core._set_session_description"):
                app._handle_description_edit("gd/alpha/shell/1", "fix the login flow")
            app._poll_session_statuses()
            await pilot.pause()

            entry = next(
                e for e in app._sessions_entries if e["session_name"] == "gd/alpha/shell/1"
            )
            assert entry["description"] == "fix the login flow"


class TestRemoval:
    @patch_sessions()
    async def test_a_stale_sample_does_not_bring_it_back(self, _mock_list):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            app.action_tab_sessions()
            await pilot.pause()
            _stale_sample(app, sample_sessions())

            with (
                patch("gitdirector.integrations.tmux.kill_tmux_session"),
                patch("gitdirector.integrations.tmux.sync_panel_tmux_config"),
            ):
                app._do_remove(True, "gd/alpha/shell/1")
            app._poll_session_statuses()
            await pilot.pause()

            assert "gd/alpha/shell/1" not in _rows(app)
