"""Tests for the host terminal capability detection helper.

Covers the cases the TUI's animation/visual code depends on:
hatch rendering, alpha backgrounds, truecolor, NO_COLOR/TERM=dumb
suppression, and the CSS-stripping wrapper.
"""

from __future__ import annotations

import os

import pytest

from gitdirector.commands.tui import GitDirectorConsole
from gitdirector.commands.tui.terminal_caps import (
    _DUMB_TERMS,
    host_color_system,
    host_supports_truecolor,
    is_ci_environment,
    is_dumb_terminal,
    no_color_requested,
    strip_unsupported_css,
)


class TestHostCapabilityDetection:
    """Pure-Python tests of the capability helper, no Textual app."""

    @pytest.fixture(autouse=True)
    def _restore_env(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)

    def test_dumb_terminal_detected(self, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        assert is_dumb_terminal() is True
        assert no_color_requested() is True
        assert host_color_system() is None

    def test_empty_terminal_detected_as_dumb(self, monkeypatch):
        monkeypatch.delenv("TERM", raising=False)
        assert is_dumb_terminal() is True

    def test_no_color_overrides_term(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.setenv("NO_COLOR", "1")
        assert no_color_requested() is True
        assert host_color_system() is None

    def test_xterm256_returns_256(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("COLORTERM", raising=False)
        assert host_color_system() == "256"
        assert host_supports_truecolor() is False

    def test_truecolor_advertised(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm-truecolor")
        assert host_color_system() == "truecolor"
        assert host_supports_truecolor() is True

    def test_colorterm_truecolor_advertised(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.setenv("COLORTERM", "truecolor")
        assert host_color_system() == "truecolor"
        assert host_supports_truecolor() is True

    def test_24bit_advertised(self, monkeypatch):
        monkeypatch.setenv("TERM", "screen-24bit")
        assert host_color_system() == "truecolor"

    def test_tmux_defaults_to_256(self, monkeypatch):
        monkeypatch.setenv("TERM", "tmux")
        monkeypatch.delenv("COLORTERM", raising=False)
        assert host_color_system() == "256"

    def test_dumb_terms_constant(self):
        assert "dumb" in _DUMB_TERMS
        assert "" in _DUMB_TERMS

    def test_ci_detection(self, monkeypatch):
        monkeypatch.setenv("CI", "true")
        assert is_ci_environment() is True
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        assert is_ci_environment() is True


class TestStripUnsupportedCss:
    """The CSS-stripping helper removes directives the host can't render."""

    def test_empty_input_returns_empty(self):
        assert strip_unsupported_css("") == ""

    def test_hatch_directive_intact_when_supported(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm-256color")
        css = "hatch: right $primary 30%;"
        # On a real terminal, nothing should be stripped.
        assert strip_unsupported_css(css) == css

    def test_no_color_strips_hatch_when_unsupported(self, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        css = "Foo { background: $panel 80%; hatch: right $primary 30%; }"
        out = strip_unsupported_css(css)
        assert "hatch" not in out
        assert "background: $panel 80%" not in out
        # The braces and rest of the CSS survive.
        assert "Foo {" in out
        assert "}" in out

    def test_no_color_keeps_text_rules(self, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        css = "Foo { color: $text; padding: 1 1; }"
        out = strip_unsupported_css(css)
        assert out == css


class TestGitDirectorConsoleTruecolor:
    """``GitDirectorConsole`` must seed ``COLORTERM=truecolor`` for Textual.

    Textual's ``App.console`` is constructed with ``color_system="auto"`` and
    reads from a snapshot of ``os.environ`` taken inside ``App.__init__``.
    Without ``COLORTERM=truecolor`` at that moment, Rich falls back to
    ``"256"`` and ``Strip.render()`` quantises every truecolor segment
    produced by child agents — visible gradient banding.
    """

    def test_sets_colorterm_when_host_advertises_truecolor(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.setenv("COLORTERM", "truecolor")
        monkeypatch.delenv("NO_COLOR", raising=False)

        GitDirectorConsole()

        assert os.environ.get("COLORTERM") == "truecolor"

    def test_sets_colorterm_when_host_advertises_truecolor_via_term(self, monkeypatch):
        # Some hosts announce truecolor via ``TERM`` (e.g. ``xterm-truecolor``)
        # without setting ``COLORTERM``. The fix must still seed it so
        # Textual's auto-detection picks truecolor for its render Console.
        monkeypatch.setenv("TERM", "xterm-truecolor")
        monkeypatch.delenv("COLORTERM", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)

        GitDirectorConsole()

        assert os.environ.get("COLORTERM") == "truecolor"

    def test_app_console_auto_detects_truecolor(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm-truecolor")
        monkeypatch.delenv("COLORTERM", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)

        app = GitDirectorConsole()

        from rich.color import ColorSystem

        assert app.console._color_system == ColorSystem.TRUECOLOR

    def test_does_not_force_colorterm_when_host_only_256(self, monkeypatch):
        # ``tmux-256color`` without ``COLORTERM`` advertises 256 colours
        # only — we must NOT claim truecolor or Textual will emit SGR
        # escapes that the host can't render.
        monkeypatch.setenv("TERM", "tmux-256color")
        monkeypatch.delenv("COLORTERM", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)

        GitDirectorConsole()

        assert os.environ.get("COLORTERM") is None

    def test_does_not_force_colorterm_when_no_color(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.setenv("COLORTERM", "truecolor")
        monkeypatch.setenv("NO_COLOR", "1")

        GitDirectorConsole()

        # NO_COLOR wins — we must not advertise color support downstream.
        assert os.environ.get("NO_COLOR") == "1"

    def test_does_not_force_colorterm_on_dumb_terminal(self, monkeypatch):
        monkeypatch.setenv("TERM", "dumb")
        monkeypatch.delenv("COLORTERM", raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)

        GitDirectorConsole()

        # ``TERM=dumb`` is a hard signal of no colour support — don't lie
        # to Textual by claiming truecolor is available.
        assert "COLORTERM" not in os.environ or os.environ.get("COLORTERM") != "truecolor"
