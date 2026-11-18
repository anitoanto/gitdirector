"""tmux's resource failures are told to the user in words they can act on."""

import subprocess

import pytest

from gitdirector.integrations.tmux import TmuxError, explain_tmux_failure


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("create window failed: fork failed: Device not configured", "No free terminal (pty)"),
        ("create window failed: fork failed: No space left on device", "No free terminal (pty)"),
        ("open terminal failed: not a terminal", "No free terminal (pty)"),
        ("fork failed: Resource temporarily unavailable", "The limit on running processes"),
        ("Too many open files", "Too many files are open"),
        ("server exited unexpectedly", "tmux stopped while the session was starting"),
    ],
)
def test_resource_failures_are_explained(stderr, expected):
    error = TmuxError("tmux new-session failed", returncode=1, stderr=stderr)
    assert explain_tmux_failure(error).startswith(expected)


def test_anything_else_is_passed_on():
    error = TmuxError("tmux new-session failed", returncode=1, stderr="duplicate session: x")
    assert explain_tmux_failure(error) == str(error)


def test_a_failed_subprocess_is_explained_from_its_stderr():
    import subprocess

    error = subprocess.CalledProcessError(
        1,
        ["tmux", "split-window", "-h"],
        stderr="create pane failed: fork failed: No space left on device",
    )
    assert explain_tmux_failure(error).startswith("No free terminal (pty)")


def test_a_failed_subprocess_keeps_its_stderr_when_unexplained():
    import subprocess

    error = subprocess.CalledProcessError(1, ["tmux", "x"], stderr="no such window: 7")
    assert explain_tmux_failure(error).endswith("no such window: 7")


class TestEveryTmuxFailureIsAResult:
    """Callers that check the return code must never get an exception instead."""

    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (BlockingIOError(35, "Resource temporarily unavailable"), "Resource temporarily"),
            (__import__("subprocess").TimeoutExpired("tmux", 10), "timed out"),
        ],
    )
    def test_without_check(self, monkeypatch, error, reason):
        from gitdirector.integrations.tmux import core

        def fail(*_args, **_kwargs):
            raise error

        monkeypatch.setattr(core.subprocess, "run", fail)
        result = core._run_tmux(["list-sessions"], text=True)
        assert result.returncode == 1
        assert reason in result.stderr

    def test_with_check(self, monkeypatch):
        from gitdirector.integrations.tmux import core

        def fail(*_args, **_kwargs):
            raise BlockingIOError(35, "Resource temporarily unavailable")

        monkeypatch.setattr(core.subprocess, "run", fail)
        with pytest.raises(TmuxError, match="Resource temporarily"):
            core._run_tmux(["list-sessions"], check=True)


class TestAServerThatIsExiting:
    """Its last session just closed: a client that reaches it then gets nothing done."""

    def _tmux(self, monkeypatch, *stderrs: str):
        from gitdirector.integrations.tmux import core

        calls: list[list[str]] = []
        results = list(stderrs)

        def run(command, **_kwargs):
            calls.append(command)
            stderr = results.pop(0) if results else ""
            return subprocess.CompletedProcess(command, 1 if stderr else 0, "", stderr)

        monkeypatch.setattr(core.subprocess, "run", run)
        return core, calls

    def test_a_new_session_starts_a_new_server_instead(self, monkeypatch):
        core, calls = self._tmux(monkeypatch, "server exited unexpectedly\n")

        core._run_tmux(["new-session", "-d", "-s", "x"], check=True, text=True)

        assert len(calls) == 2

    def test_a_command_for_a_session_gets_the_servers_answer(self, monkeypatch):
        core, calls = self._tmux(monkeypatch, "server exited unexpectedly\n")

        result = core._run_tmux(["kill-session", "-t", "=x"], text=True)

        assert (result.returncode, len(calls)) == (1, 1)

    def test_any_other_failure_is_final(self, monkeypatch):
        core, calls = self._tmux(monkeypatch, "duplicate session: x\n")

        with pytest.raises(TmuxError):
            core._run_tmux(["new-session", "-d", "-s", "x"], check=True, text=True)
        assert len(calls) == 1


class TestAnInteractiveClientThatSuspends:
    """prefix C-z stops the client; whoever waits on it must not hang."""

    @pytest.fixture
    def suspending_tmux(self, tmp_path, monkeypatch):
        fake = tmp_path / "bin" / "tmux"
        fake.parent.mkdir()
        fake.write_text("#!/bin/sh\nkill -STOP $$\nexit 3\n")
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake.parent}:{__import__('os').environ['PATH']}")
        from gitdirector.integrations.tmux import core

        stops: list[bool] = []
        monkeypatch.setattr(core, "_stop_with_the_client", lambda: stops.append(True))
        return core, stops

    def test_started_by_a_shell_it_stops_too_then_continues_the_client(
        self, suspending_tmux, monkeypatch
    ):
        core, stops = suspending_tmux
        monkeypatch.setattr(core.os, "getsid", lambda pid: core.os.getpgrp() + 1)
        result = core.run_tmux_client(["attach-session"])
        assert result.returncode == 3
        assert stops == [True]

    def test_started_by_a_terminal_it_continues_the_client_at_once(
        self, suspending_tmux, monkeypatch
    ):
        core, stops = suspending_tmux
        monkeypatch.setattr(core.os, "getsid", lambda pid: core.os.getpgrp())
        result = core.run_tmux_client(["attach-session"])
        assert result.returncode == 3
        assert stops == []
