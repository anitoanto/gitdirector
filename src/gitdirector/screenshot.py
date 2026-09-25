"""Render a captured tmux screen to a PNG, cell by cell, the way a terminal draws it."""

from __future__ import annotations

import io
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rich.cells import get_character_cell_size
from rich.console import Console
from rich.style import Style
from rich.terminal_theme import TerminalTheme
from rich.text import Text

if TYPE_CHECKING:
    from PIL.ImageFont import FreeTypeFont, ImageFont

    from .integrations.tmux.core import ScreenCapture

    Font = FreeTypeFont | ImageFont

FONT_SIZE = 16
PADDING = 12

RGB = tuple[int, int, int]

# (regular, bold) as (file, face index); .ttc collections hold several faces.
_KNOWN_FONTS: tuple[tuple[tuple[str, int], tuple[str, int]], ...] = (
    (("/System/Library/Fonts/Menlo.ttc", 0), ("/System/Library/Fonts/Menlo.ttc", 1)),
    (
        ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 0),
    ),
    (
        ("/usr/share/fonts/TTF/DejaVuSansMono.ttf", 0),
        ("/usr/share/fonts/TTF/DejaVuSansMono-Bold.ttf", 0),
    ),
)


# Tried in order for a character the monospace font has no glyph for.
_FALLBACK_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Apple Symbols.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
)


@dataclass
class _Cell:
    char: str = " "
    width: int = 1
    style: Style = Style.null()


def _fc_match(pattern: str) -> str | None:
    if shutil.which("fc-match") is None:
        return None
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", pattern],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _font_candidates():
    yield from _KNOWN_FONTS
    regular = _fc_match("monospace")
    if regular:
        yield (regular, 0), (_fc_match("monospace:bold") or regular, 0)


def _load_fonts(size: int) -> tuple[Font, Font]:
    """A monospace (regular, bold) pair; Pillow's own font when none is installed."""
    from PIL import ImageFont

    for (regular_path, regular_index), (bold_path, bold_index) in _font_candidates():
        try:
            regular = ImageFont.truetype(regular_path, size, index=regular_index)
        except OSError:
            continue
        try:
            bold = ImageFont.truetype(bold_path, size, index=bold_index)
        except OSError:
            bold = regular
        return regular, bold
    # Proportional, but every glyph is placed in its own cell, so the grid holds.
    fallback = ImageFont.load_default(size)
    return fallback, fallback


def _mask(font: Font, char: str) -> tuple[tuple[int, int], bytes]:
    mask = font.getmask(char)
    return mask.size, bytes(mask)


class _Fonts:
    """The monospace pair plus, per character it lacks, a font that has it."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.regular, self.bold = _load_fonts(size)
        self._notdef: dict[int, tuple[tuple[int, int], bytes]] = {}
        self._missing: dict[tuple[int, str], bool] = {}
        self._loaded: dict[str, Font | None] = {}
        self._fallback: dict[str, Font | None] = {}

    def for_char(self, char: str, *, bold: bool) -> Font:
        font = self.bold if bold else self.regular
        if char.isascii() or not self._lacks(font, char):
            return font
        if char not in self._fallback:
            self._fallback[char] = self._find_fallback(char)
        return self._fallback[char] or font

    def _lacks(self, font: Font, char: str) -> bool:
        """True when *font* would draw its "missing glyph" box for *char*."""
        key = (id(font), char)
        if key not in self._missing:
            if id(font) not in self._notdef:
                self._notdef[id(font)] = _mask(font, "\U0010fffd")
            self._missing[key] = _mask(font, char) == self._notdef[id(font)]
        return self._missing[key]

    def _load(self, path: str) -> Font | None:
        if path not in self._loaded:
            from PIL import ImageFont

            try:
                self._loaded[path] = ImageFont.truetype(path, self.size)
            except OSError:
                self._loaded[path] = None
        return self._loaded[path]

    def _find_fallback(self, char: str) -> Font | None:
        matched = _fc_match(f":charset={ord(char):x}")
        for path in (*([matched] if matched else []), *_FALLBACK_FONTS):
            font = self._load(path)
            if font is not None and not self._lacks(font, char):
                return font
        return None


def _parse(screen: ScreenCapture) -> list[list[_Cell]]:
    """The screen as a grid of cells; a wide character's second cell is empty."""
    console = Console(file=io.StringIO(), width=screen.width, color_system="truecolor")
    grid = [[_Cell() for _ in range(screen.width)] for _ in range(screen.height)]
    for row, line in zip(grid, screen.lines):
        column = 0
        for segment in Text.from_ansi(line, end="").render(console):
            style = segment.style or Style.null()
            for char in segment.text:
                width = get_character_cell_size(char)
                if width == 0 or column + width > screen.width:
                    continue
                row[column] = _Cell(char, width, style)
                if width == 2:
                    row[column + 1] = _Cell("", 0, style)
                column += width
    return grid


def _blend(color: RGB, other: RGB, amount: float) -> RGB:
    return tuple(round(c + (o - c) * amount) for c, o in zip(color, other))  # type: ignore[return-value]


def _hex_rgb(value: str) -> RGB:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


# VS Code's terminal palette: readable on dark and light backgrounds alike.
_ANSI_NORMAL = [
    (0, 0, 0),
    (205, 49, 49),
    (13, 188, 121),
    (229, 229, 16),
    (36, 114, 200),
    (188, 63, 188),
    (17, 168, 205),
    (229, 229, 229),
]
_ANSI_BRIGHT = [
    (102, 102, 102),
    (241, 76, 76),
    (35, 209, 139),
    (245, 245, 67),
    (59, 142, 234),
    (214, 112, 214),
    (41, 184, 219),
    (255, 255, 255),
]


def terminal_theme(background: str, foreground: str) -> TerminalTheme:
    """The ANSI palette on the given default background and foreground."""
    return TerminalTheme(_hex_rgb(background), _hex_rgb(foreground), _ANSI_NORMAL, _ANSI_BRIGHT)


def _colors(style: Style, theme: TerminalTheme) -> tuple[RGB, RGB]:
    default_fg = tuple(theme.foreground_color)
    default_bg = tuple(theme.background_color)
    fg = tuple(style.color.get_truecolor(theme, True)) if style.color else default_fg
    bg = tuple(style.bgcolor.get_truecolor(theme, False)) if style.bgcolor else default_bg
    if style.reverse:
        fg, bg = bg, fg
    if style.dim:
        fg = _blend(fg, bg, 0.45)
    return fg, bg  # type: ignore[return-value]


def render_png(screen: ScreenCapture, path: Path, theme: TerminalTheme) -> tuple[int, int]:
    """Draw *screen* into a PNG at *path*; returns the image's (width, height)."""
    from PIL import Image, ImageDraw

    fonts = _Fonts(FONT_SIZE)
    ascent, descent = fonts.regular.getmetrics()
    cell_w = max(1, round(fonts.regular.getlength("M")))
    cell_h = ascent + descent + 2
    size = (PADDING * 2 + screen.width * cell_w, PADDING * 2 + screen.height * cell_h)
    background = tuple(theme.background_color)
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)

    for y, row in enumerate(_parse(screen)):
        top = PADDING + y * cell_h
        baseline = top + 1 + ascent
        for x, cell in enumerate(row):
            if cell.width == 0:
                continue
            fg, bg = _colors(cell.style, theme)
            if screen.cursor == (x, y):
                fg, bg = bg, fg
            left = PADDING + x * cell_w
            right = left + cell.width * cell_w
            if bg != background:
                draw.rectangle((left, top, right - 1, top + cell_h - 1), fill=bg)
            if cell.char.strip() and not cell.style.conceal:
                font = fonts.for_char(cell.char, bold=bool(cell.style.bold))
                draw.text((left, baseline), cell.char, font=font, fill=fg, anchor="ls")
            if cell.style.underline:
                draw.line((left, baseline + 2, right - 1, baseline + 2), fill=fg)
            if cell.style.strike:
                middle = top + cell_h // 2
                draw.line((left, middle, right - 1, middle), fill=fg)

    image.save(path, format="PNG")
    return size
