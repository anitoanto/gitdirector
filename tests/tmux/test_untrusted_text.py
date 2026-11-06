"""Values set behind gitdirector's back cannot forge rows or restyle tmux."""

import os
import shutil
import subprocess

import pytest

from gitdirector import cell_widths
from gitdirector.integrations.tmux import create_tmux_session, list_all_gd_sessions
from gitdirector.integrations.tmux.core import _server_pid, _status_text

from .._timeouts import TMUX_CMD_TIMEOUT
from ._shared import _cleanup_tmux_tmpdir, _make_short_tmux_tmpdir, _tmux_integration_lock

SESSION = "gd/repo_abcde/claude/1"


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
def test_a_newline_in_a_description_cannot_add_a_session(monkeypatch):
    with _tmux_integration_lock():
        tmux_dir = _make_short_tmux_tmpdir()
        monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
        monkeypatch.delenv("TMUX", raising=False)

        def run_tmux(*args: str) -> None:
            subprocess.run(
                ["tmux", "-f", "/dev/null", *args],
                capture_output=True,
                check=True,
                timeout=TMUX_CMD_TIMEOUT,
            )

        try:
            run_tmux("new-session", "-d", "-s", SESSION, "sleep 600")
            run_tmux(
                "set-option",
                "-t",
                f"={SESSION}:",
                "@gitdirector_repo_label",
                "Repo\tshifted",
            )
            run_tmux(
                "set-option",
                "-t",
                f"={SESSION}:",
                "@gitdirector_description",
                "real\ngd/ghost_zzzzz/claude/7\tGhost\tspoofed",
            )
            entries = list_all_gd_sessions()
        finally:
            subprocess.run(["tmux", "kill-server"], capture_output=True, timeout=TMUX_CMD_TIMEOUT)
            _cleanup_tmux_tmpdir(tmux_dir)

    assert [entry["session_name"] for entry in entries] == [SESSION]
    assert entries[0]["repo"] == "Repo shifted"
    assert entries[0]["description"] == "real gd/ghost_zzzzz/claude/7 Ghost spoofed"


def test_status_text_is_drawn_as_written():
    assert _status_text("#[bg=red]x") == "'##[bg=red]x'"
    assert _status_text("a\nb\x1b[2J") == "'ab[2J'"


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
def test_the_running_server_build_is_the_one_measured(monkeypatch):
    with _tmux_integration_lock():
        tmux_dir = _make_short_tmux_tmpdir()
        monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
        monkeypatch.delenv("TMUX", raising=False)
        try:
            subprocess.run(
                ["tmux", "-f", "/dev/null", "new-session", "-d", "-s", SESSION, "sleep 600"],
                check=True,
                timeout=TMUX_CMD_TIMEOUT,
            )
            pid = _server_pid()
            executable = cell_widths._executable(pid) if pid else None
        finally:
            subprocess.run(["tmux", "kill-server"], capture_output=True, timeout=TMUX_CMD_TIMEOUT)
            _cleanup_tmux_tmpdir(tmux_dir)
    assert pid is not None
    assert executable == os.path.realpath(shutil.which("tmux"))


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
@pytest.mark.parametrize("name", ["മലയാളം-repo", "日本語-repo", "emoji-🚀-Repo"])
def test_a_session_keeps_its_repositorys_own_name(tmp_path, monkeypatch, name):
    repo = tmp_path / name
    repo.mkdir()
    with _tmux_integration_lock():
        tmux_dir = _make_short_tmux_tmpdir()
        monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
        monkeypatch.delenv("TMUX", raising=False)
        try:
            session = create_tmux_session(name, repo)
            entries = list_all_gd_sessions()
        finally:
            subprocess.run(["tmux", "kill-server"], capture_output=True, timeout=TMUX_CMD_TIMEOUT)
            _cleanup_tmux_tmpdir(tmux_dir)
    assert [(entry["session_name"], entry["repo"]) for entry in entries] == [(session, name)]
