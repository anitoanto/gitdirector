"""Text from repositories, agents and users, made safe to write to a terminal.

File contents, file names, commit messages and session descriptions can hold
escape sequences that would clear the screen, set the clipboard or restyle
the display. Control characters and bidi overrides are dropped: Rich gives
them no cell, so every layout measured with them stays exactly as measured.
"""

from __future__ import annotations

_UNPRINTABLE = dict.fromkeys(
    [*range(0x20), *range(0x7F, 0xA0), *range(0x202A, 0x202F), *range(0x2066, 0x206A)]
)


def printable(text: str) -> str:
    return text.translate(_UNPRINTABLE)


def drop_unprintable_from_textual_output() -> None:
    """Make every Textual app write strips without control characters.

    One place catches every source of text; the compositor's own cursor
    moves and the styles' escape sequences are added outside segment text.
    """
    from rich.color import ColorSystem
    from textual.strip import Strip

    if getattr(Strip.render, "_drops_unprintable", False):
        return

    def render(self: Strip, console) -> str:
        if self._render_cache is None:
            color_system = console._color_system or ColorSystem.TRUECOLOR
            self._render_cache = "".join(
                [
                    printable(text)
                    if style is None
                    else self.render_style(style, printable(text), color_system=color_system)
                    for text, style, _ in self._segments
                ]
            )
        return self._render_cache

    render._drops_unprintable = True  # type: ignore[attr-defined]
    Strip.render = render  # type: ignore[method-assign]
