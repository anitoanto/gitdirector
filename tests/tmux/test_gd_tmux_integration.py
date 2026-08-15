"""Real tmux integration tests for the ``gitdirector gd-tmux`` flow.

These tests start a private tmux server, invoke the same code path the
``gd-tmux`` CLI command uses, and verify the user's command actually runs
inside the new session with all quote/escape sequences preserved.

The command runs in the background; cleanup behaviour is in the wrapped
``sh -lc`` script.
"""

from __future__ import annotations

import shutil
import subprocess
import time

import pytest

from gitdirector.integrations.tmux import (
    create_tmux_session,
    launch_command_in_tmux_session,
    sync_panel_tmux_config,
)

from ._shared import _cleanup_tmux_tmpdir, _make_short_tmux_tmpdir, _tmux_integration_lock


def _run_tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=10,
    )


def _wait_for(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Poll *predicate* until it returns truthy or *timeout* elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
class TestGdTmuxCommandRunsInSession:
    """End-to-end: a real tmux server runs the user's command verbatim."""

    def test_quoted_command_runs_and_cleans_up(
        self,
        tmp_path,
        monkeypatch,
    ):
        """The full flow: create session, send quoted command, session kills
        itself when the command exits, side-effect of the command is visible
        on disk.
        """
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            repo = home_dir / "demo"
            repo.mkdir()
            output_file = tmp_path / "captured_output"
            sync_panel_tmux_config()

            try:
                session_name = create_tmux_session("demo", repo, purpose="shell")
                assert (
                    _run_tmux("has-session", "-t", f"={session_name}", check=False).returncode == 0
                )

                cmd = f'echo "hello world" > "{output_file}"'
                launch_command_in_tmux_session(session_name, cmd)

                assert _wait_for(
                    lambda: (
                        _run_tmux("has-session", "-t", f"={session_name}", check=False).returncode
                        != 0
                    ),
                    timeout=5.0,
                ), "session did not self-destruct after command"

                assert output_file.exists(), (
                    "command did not run; expected output file was not created"
                )
                assert output_file.read_text() == "hello world\n"
            finally:
                _run_tmux("kill-server", check=False)
                _cleanup_tmux_tmpdir(tmux_dir)

    def test_command_with_single_quotes_runs(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Single quotes inside the command survive the ``shlex.quote`` wrap
        and are interpreted by the inner shell as a quoted argument.
        """
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            repo = home_dir / "demo"
            repo.mkdir()
            output_file = tmp_path / "captured_output"
            sync_panel_tmux_config()

            try:
                session_name = create_tmux_session("demo", repo, purpose="shell")
                cmd = f'printf \'%s\\n\' "don\'t worry" > "{output_file}"'
                launch_command_in_tmux_session(session_name, cmd)

                assert _wait_for(
                    lambda: (
                        _run_tmux("has-session", "-t", f"={session_name}", check=False).returncode
                        != 0
                    ),
                    timeout=5.0,
                ), "session did not self-destruct"

                assert output_file.exists()
                assert output_file.read_text() == "don't worry\n"
            finally:
                _run_tmux("kill-server", check=False)
                _cleanup_tmux_tmpdir(tmux_dir)

    def test_command_with_backslashes_runs(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Backslashes embedded in the command are preserved through the
        inner shell's interpretation.
        """
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            repo = home_dir / "demo"
            repo.mkdir()
            output_file = tmp_path / "captured_output"
            sync_panel_tmux_config()

            try:
                session_name = create_tmux_session("demo", repo, purpose="shell")
                # Python source ``\\\\`` -> string ``\\`` -> inner shell
                # collapses ``\\`` to a single ``\`` inside double quotes.
                cmd = f'echo "C:\\\\Users" > "{output_file}"'
                launch_command_in_tmux_session(session_name, cmd)

                assert _wait_for(
                    lambda: (
                        _run_tmux("has-session", "-t", f"={session_name}", check=False).returncode
                        != 0
                    ),
                    timeout=5.0,
                ), "session did not self-destruct"

                assert output_file.exists()
                assert output_file.read_text() == "C:\\Users\n"
            finally:
                _run_tmux("kill-server", check=False)
                _cleanup_tmux_tmpdir(tmux_dir)

    def test_repo_directory_name_with_space(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A repository whose directory name contains a space must work.

        The by-name lookup uses ``Path.name`` verbatim, and tmux's
        ``new-session -c <path>`` accepts paths with spaces. The user
        passes the name in ``"..."`` to keep the shell from splitting it.
        """
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            repo = home_dir / "My Repo"
            repo.mkdir()
            output_file = tmp_path / "captured_output"
            sync_panel_tmux_config()

            try:
                session_name = create_tmux_session("My Repo", repo, purpose="shell")
                assert (
                    _run_tmux("has-session", "-t", f"={session_name}", check=False).returncode == 0
                )

                cmd = f'echo "ran in space repo" > "{output_file}"'
                launch_command_in_tmux_session(session_name, cmd)

                assert _wait_for(
                    lambda: (
                        _run_tmux("has-session", "-t", f"={session_name}", check=False).returncode
                        != 0
                    ),
                    timeout=5.0,
                ), "session did not self-destruct for repo with space in name"

                assert output_file.exists()
                assert output_file.read_text() == "ran in space repo\n"
            finally:
                _run_tmux("kill-server", check=False)
                _cleanup_tmux_tmpdir(tmux_dir)

    def test_failing_command_still_cleans_up(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A command that exits non-zero must still let the cleanup run.

        The cleanup script captures ``$?`` and runs the kill / detach steps
        regardless of the command's exit status, so the session always
        self-destructs and we don't leave orphan sessions behind.
        """
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            repo = home_dir / "demo"
            repo.mkdir()
            sync_panel_tmux_config()

            try:
                session_name = create_tmux_session("demo", repo, purpose="shell")
                launch_command_in_tmux_session(session_name, "false")

                assert _wait_for(
                    lambda: (
                        _run_tmux("has-session", "-t", f"={session_name}", check=False).returncode
                        != 0
                    ),
                    timeout=5.0,
                ), "session did not self-destruct after a failing command"
            finally:
                _run_tmux("kill-server", check=False)
                _cleanup_tmux_tmpdir(tmux_dir)

    def test_command_with_trailing_comment_still_cleans_up(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A shell comment in the user command must not comment out cleanup."""
        with _tmux_integration_lock():
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            tmux_dir = _make_short_tmux_tmpdir()
            monkeypatch.setenv("HOME", str(home_dir))
            monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
            monkeypatch.delenv("TMUX", raising=False)

            repo = home_dir / "demo"
            repo.mkdir()
            output_file = tmp_path / "captured_output"
            sync_panel_tmux_config()

            try:
                session_name = create_tmux_session("demo", repo, purpose="shell")
                cmd = f'printf "%s\\n" "comment safe" > "{output_file}"; # trailing comment'
                launch_command_in_tmux_session(session_name, cmd)

                assert _wait_for(
                    lambda: (
                        _run_tmux("has-session", "-t", f"={session_name}", check=False).returncode
                        != 0
                    ),
                    timeout=5.0,
                ), "session did not self-destruct after command with trailing comment"

                assert output_file.exists()
                assert output_file.read_text() == "comment safe\n"
            finally:
                _run_tmux("kill-server", check=False)
                _cleanup_tmux_tmpdir(tmux_dir)
