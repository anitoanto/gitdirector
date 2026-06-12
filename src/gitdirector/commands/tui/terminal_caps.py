"""Detect host terminal capabilities for graceful degradation.

The TUI's animated/visual elements (truecolor, hatch backgrounds, alpha
modulated surfaces, the embedded terminal pane) silently break on
terminals that don't advertise support. The defaults in Textual and
Rich already auto-detect, but a few code paths force-enable features
(``force_terminal=True``, ``color_system="truecolor"``) which makes
those paths misbehave on minimal hosts.

Use :func:`host_color_system` instead of hard-coding ``"truecolor"``,
and :func:`host_supports_hatch` / :func:`host_supports_alpha` to
conditionally enable visual flourishes.
"""

from __future__ import annotations

import os
import re
import shutil
import sys

_DUMB_TERMS = frozenset({"dumb", ""})


def is_dumb_terminal() -> bool:
    """Return ``True`` if ``TERM`` is unset or set to a value that implies
    no terminal capability negotiation (``dumb``, ``unknown``)."""
    term = (os.environ.get("TERM") or "").strip().lower()
    return term in _DUMB_TERMS or term == "unknown"


def no_color_requested() -> bool:
    """Return ``True`` if the user has asked for no color output.

    Honours the de-facto ``NO_COLOR`` convention (any non-empty value
    disables color) and the older ``TERM=dumb`` convention.
    """
    if is_dumb_terminal():
        return True
    return bool(os.environ.get("NO_COLOR", "").strip())


def is_ci_environment() -> bool:
    """Return ``True`` when running under a known CI runner."""
    return bool(os.environ.get("CI")) or bool(os.environ.get("GITHUB_ACTIONS"))


def host_color_system() -> str | None:
    """Best-effort host color system: ``"truecolor"``, ``"256"``, ``"8"``,
    or ``None`` for no color.

    Returns ``None`` when ``NO_COLOR`` is set or ``TERM=dumb`` is detected
    so that the caller can fall back to a colorless render. Otherwise
    returns the same value Rich would auto-detect, exposed so call sites
    that need to *force* color (e.g. the embedded terminal widget which
    renders to a Rich ``Console`` that is later consumed by Textual) can
    pick a sensible level.
    """
    if no_color_requested():
        return None

    if sys.platform == "win32":
        if "WT_SESSION" in os.environ or "TERMINUS_SUBTITLE" in os.environ:
            return "truecolor"
        return "256"

    term = (os.environ.get("TERM") or "").lower()
    if "truecolor" in term or "24bit" in term:
        return "truecolor"
    if "256color" in term:
        return "256"
    if term in {"xterm", "screen", "tmux", "tmux-256color"}:
        return "256"
    if "ansi" in term:
        return "8"
    return "256"


def host_supports_truecolor() -> bool:
    """Return ``True`` if the host advertises 24-bit color support."""
    return host_color_system() == "truecolor"


def host_supports_hatch() -> bool:
    """Return ``True`` if the host can render Textual ``hatch:`` patterns.

    Hatch requires a Unicode-aware terminal with decent box-drawing
    support. On ``TERM=dumb`` or Windows legacy console the hatch
    characters render as ``?`` and should be suppressed.
    """
    if is_dumb_terminal():
        return False
    encoding = (sys.stdout.encoding or "").lower()
    if encoding in {"ascii", "us-ascii"}:
        return False
    if sys.platform == "win32" and "WT_SESSION" not in os.environ:
        # Legacy conhost.exe doesn't draw hatch reliably.
        return False
    return True


def host_supports_alpha() -> bool:
    """Return ``True`` if the host supports alpha-blended backgrounds.

    Alpha (``background: $panel 80%;``) degrades to opaque on terminals
    that don't support it, so it's mostly safe — but skipping it on
    dumb terminals avoids a visible flash of nothing when the
    background is computed.
    """
    if is_dumb_terminal():
        return False
    if not shutil.get_terminal_size((80, 24)).columns:
        return False
    return host_supports_truecolor()


def strip_unsupported_css(css: str) -> str:
    """Return ``css`` with directives removed for features the host
    doesn't support.

    Currently this strips ``hatch: right $primary 30%;`` and
    ``background: $panel 80%;`` (alpha) on hosts that can't render
    them. The host's own degradation already produces a reasonable
    fallback, so this is purely a perf/clarity tweak — it prevents
    the user from seeing ``?`` boxes where hatch characters should be.
    """
    if not css:
        return css
    if not host_supports_hatch():
        css = re.sub(r"\s*hatch:\s*right\s+\$[a-zA-Z_-]+\s+\d+%;", "", css)
    if not host_supports_alpha():
        css = re.sub(r"\s*background:\s*\$[a-zA-Z_-]+\s+\d+%;", "", css)
    return css
