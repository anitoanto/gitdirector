"""Detect host terminal capabilities for graceful degradation.

The TUI's visual elements (truecolor, alpha-modulated surfaces, the embedded terminal pane) silently break on
terminals that don't advertise support. The defaults in Textual and
Rich already auto-detect, but a few code paths force-enable features
(``force_terminal=True``, ``color_system="truecolor"``) which makes
those paths misbehave on minimal hosts.

Use :func:`host_color_system` instead of hard-coding ``"truecolor"``,
and :func:`host_supports_alpha` to conditionally enable visual flourishes.
"""

from __future__ import annotations

import os
import re

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

    colorterm = (os.environ.get("COLORTERM") or "").lower()
    if "truecolor" in colorterm or "24bit" in colorterm:
        return "truecolor"

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


def host_supports_alpha() -> bool:
    """Return ``True`` if the host supports alpha-blended backgrounds.

    Alpha (``background: $panel 80%;``) degrades to opaque on terminals
    that don't support it, so it's mostly safe, but skipping it on
    dumb terminals avoids a visible flash of nothing when the
    background is computed.
    """
    if is_dumb_terminal():
        return False
    return host_supports_truecolor()


def strip_unsupported_css(css: str) -> str:
    """Return ``css`` without alpha backgrounds (``background: $panel 80%;``)
    on hosts that cannot blend them; the rest is left as it is."""
    if not css:
        return css
    if not host_supports_alpha():
        css = re.sub(r"\s*background:\s*\$[a-zA-Z_-]+\s+\d+%;", "", css)
    return css
