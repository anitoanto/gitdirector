"""Tests for the host terminal capability detection helper.

Covers the cases the TUI's animation/visual code depends on:
hatch rendering, alpha backgrounds, truecolor, NO_COLOR/TERM=dumb
suppression, and the CSS-stripping wrapper.
"""

from __future__ import annotations

import pytest

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
        yield

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
        assert host_color_system() == "256"
        assert host_supports_truecolor() is False

    def test_truecolor_advertised(self, monkeypatch):
        monkeypatch.setenv("TERM", "xterm-truecolor")
        assert host_color_system() == "truecolor"
        assert host_supports_truecolor() is True

    def test_24bit_advertised(self, monkeypatch):
        monkeypatch.setenv("TERM", "screen-24bit")
        assert host_color_system() == "truecolor"

    def test_tmux_defaults_to_256(self, monkeypatch):
        monkeypatch.setenv("TERM", "tmux")
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
