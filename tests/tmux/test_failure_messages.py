"""tmux's resource failures are told to the user in words they can act on."""

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
