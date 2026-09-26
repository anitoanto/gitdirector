"""The terminal description a tmux attach client is given."""

import shutil
import subprocess

import pytest

from gitdirector.integrations.tmux import core


class TestTerminfoString:
    def test_escapes_what_terminfo_source_reserves(self):
        assert core._terminfo_string(b"\033[?1l\033>") == "\\E[?1l\\E>"
        assert core._terminfo_string(b"a,b:c^d\\e") == "a\\054b\\072c\\136d\\134e"
        assert core._terminfo_string(b" \x07") == "\\040\\007"


@pytest.mark.skipif(shutil.which("tic") is None, reason="tic required")
class TestAttachClientEnv:
    def _build(self, monkeypatch, tmp_path, term="xterm-256color"):
        monkeypatch.setattr(core, "_same_screen_env", None)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("TERM", term)
        monkeypatch.delenv("TERMINFO_DIRS", raising=False)
        return core.attach_client_env()

    def _capability(self, env, name):
        return subprocess.run(
            ["tput", "-T", env["TERM"], name],
            capture_output=True,
            env={"PATH": "/usr/bin:/bin", "TERMINFO_DIRS": env["TERMINFO_DIRS"]},
            check=False,
        )

    def test_the_client_keeps_the_screen_it_is_given(self, monkeypatch, tmp_path):
        env = self._build(monkeypatch, tmp_path)
        assert env["TERM"] == "xterm-256color-gdscreen"
        assert env["TERMINFO_DIRS"].startswith(
            str(tmp_path / ".gitdirector" / "cache" / "terminfo")
        )
        assert self._capability(env, "smcup").returncode != 0
        assert self._capability(env, "rmcup").returncode != 0
        # Everything else comes from the terminal's own entry.
        own_clear = subprocess.run(
            ["tput", "-T", "xterm-256color", "clear"], capture_output=True, check=False
        ).stdout
        assert own_clear and self._capability(env, "clear").stdout == own_clear
        assert not list((tmp_path / ".gitdirector" / "terminfo").glob("*.src"))

    def test_leaving_holds_the_last_frame(self, monkeypatch, tmp_path):
        # tmux sends rmkx on its way out, just before it clears the screen.
        env = self._build(monkeypatch, tmp_path)
        assert self._capability(env, "rmkx").stdout == b"\033[?1l\033>\033[?2026h"
        assert b"2026" not in self._capability(env, "smkx").stdout

    def test_an_unknown_terminal_attaches_as_before(self, monkeypatch, tmp_path):
        assert self._build(monkeypatch, tmp_path, term="no-such-terminal-xyz") == {}

    def test_is_built_once(self, monkeypatch, tmp_path):
        env = self._build(monkeypatch, tmp_path)
        monkeypatch.setattr(core.subprocess, "run", None)
        assert core.attach_client_env() is env

    def test_never_wraps_itself(self, monkeypatch, tmp_path):
        assert self._build(monkeypatch, tmp_path, term="xterm-256color-gdscreen") == {}
