"""One width per character for tmux, Rich and the terminal.

When the terminal draws a line wider than tmux counts it, a pane on the
right edge wraps the excess onto the next row, over the pane on the left,
and tmux never redraws those cells. When Rich (and so Textual) counts a line
narrower than tmux, a console row overflows its own pane. Both happened:
tmux gives spacing marks (the vowel signs in Malayalam "കാ") and emoji newer
than its C library no cell, or one, where terminals draw one, or two; Rich
has its own tables.

The installed tmux is measured once, in a throwaway server, for every
codepoint. Each gets the wider of tmux's and Rich's widths, and at least one
cell for a spacing mark; tmux is told where that differs from its own, and
so is Rich, which also joins characters into cells by tmux's rules. A
terminal that draws a character narrower only leaves a line short inside its
pane.
"""

from __future__ import annotations

import bisect
import ctypes
import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from functools import cache
from pathlib import Path

from . import paths
from .storage import atomic_write_text

logger = logging.getLogger(__name__)

# (first codepoint, last codepoint, width)
Span = tuple[int, int, int]

# Slots of tmux's server-wide codepoint-widths array that gitdirector owns;
# the user's own entries sit below them.
TMUX_FIRST_SLOT = 1000
# No spacing mark lies beyond the Supplementary Multilingual Plane.
_SCAN_END = 0x20000
_MEASURED = (range(0xA0, 0x323B0), range(0xE0000, 0xE01F0))
# Joined to the character before them, so their width depends on it.
_JOINERS = frozenset(
    [
        0x200D,
        *range(0xFE00, 0xFE10),
        *range(0xE0100, 0xE01F0),
        *range(0x1F1E6, 0x1F200),
        *range(0x1F3FB, 0x1F400),
    ]
)
_PROBE_TIMEOUT_SECS = 10.0
_CACHE_FILE = "cell-widths.json"
_CACHE_ENTRIES = 4

# tmux 3.7's joining rules (screen_write_combine, utf8_should_combine).
_ZWJ = "\u200d"
_VS16 = "\ufe0f"
_HANGUL_FILLER = "\u3164"
_REGIONAL_INDICATORS = range(0x1F1E6, 0x1F200)
_SKIN_TONES = range(0x1F3FB, 0x1F400)
_SKIN_TONE_BASES = frozenset(
    [
        *range(0x1F44B, 0x1F451),
        *range(0x1F466, 0x1F46A),
        0x1F46E,
        *range(0x1F470, 0x1F479),
        0x1F47C,
        *range(0x1F481, 0x1F484),
        *range(0x1F485, 0x1F488),
        0x1F4AA,
        0x1F575,
        0x1F57A,
        0x1F590,
        0x1F595,
        0x1F596,
        *range(0x1F645, 0x1F648),
        *range(0x1F64B, 0x1F650),
        *range(0x1F6B4, 0x1F6B7),
        0x1F926,
        *range(0x1F937, 0x1F93A),
        0x1F93D,
        0x1F93E,
        0x1F9B5,
        0x1F9B6,
        0x1F9B8,
        0x1F9B9,
        *range(0x1F9CD, 0x1F9D0),
        *range(0x1F9D1, 0x1F9E0),
    ]
)
_CELL_BYTES = 32
# A lone flag letter or skin tone is drawn as an emoji, two cells wide.
_EMOJI_PARTS = [(0x1F1E6, 0x1F1FF, 2), (0x1F3FB, 0x1F3FF, 2)]

_measured: dict[str, dict[str, list[Span]] | None] = {}
_rich_spans: list[Span] | None = None


@cache
def spacing_mark_ranges() -> tuple[tuple[int, int], ...]:
    """Inclusive codepoint ranges of every spacing mark."""
    ranges: list[list[int]] = []
    for codepoint in range(_SCAN_END):
        if unicodedata.category(chr(codepoint)) != "Mc":
            continue
        if ranges and ranges[-1][1] == codepoint - 1:
            ranges[-1][1] = codepoint
        else:
            ranges.append([codepoint, codepoint])
    return tuple((start, end) for start, end in ranges)


def tmux_codepoint_width_commands(server_pid: int | None = None) -> list[str]:
    """``set-option`` lines giving tmux the agreed widths.

    The tmux binary running as *server_pid* is measured first if it never
    was; without it, only spacing marks and emoji parts get their widths.
    """
    widths = _widths(_executable(server_pid) if server_pid else None)
    spans = widths["tmux"] if widths else _fallback_tmux_spans()
    if _rich_spans is not None:
        _give_rich(widths["rich"] if widths else _spacing_mark_spans_for_rich())
    lines = []
    for slot, (start, end, width) in enumerate(spans, TMUX_FIRST_SLOT):
        span = f"U+{start:04X}" if start == end else f"U+{start:04X}-U+{end:04X}"
        lines.append(f"set-option -s codepoint-widths[{slot}] {span}={width}")
    return lines


def match_rich_to_tmux() -> None:
    """Make Rich, and so Textual, measure text as tmux does."""
    widths = _widths(None)
    _give_rich(widths["rich"] if widths else _spacing_mark_spans_for_rich())


def tmux_graphemes(text: str, unicode_version: str = "auto") -> tuple[list[Span], int]:
    """*text* cut into the cells tmux puts it in: ``(start, end, width)`` spans and the total.

    Every index is covered, and a character tmux drops is a span of width 0.
    The text is taken to start a line.
    """
    from rich.cells import get_character_cell_size

    spans: list[Span] = []
    cell: int | None = None
    cell_text = ""
    # A ZWJ that finds its two-cell-wide cell exactly full makes tmux 3.7
    # drop the next character (measured; the code does not say why).
    swallow = False

    def wide() -> bool:
        return spans[cell][2] == 2

    def extend(widen: bool = False) -> None:
        start, _end, cells = spans[cell]
        spans[cell] = (start, index + 1, 2 if widen and cells == 1 else cells)

    def begin(width: int) -> None:
        nonlocal cell, cell_text
        spans.append((index, index + 1, width))
        if width:
            cell, cell_text = len(spans) - 1, char

    for index, char in enumerate(text):
        code = ord(char)
        if code in _REGIONAL_INDICATORS or code in _SKIN_TONES:
            width = 2
        elif char in (_ZWJ, _VS16, _HANGUL_FILLER):
            width = 0
        else:
            width = get_character_cell_size(char, unicode_version)
        if cell is None:
            begin(width)
            continue
        if char == _HANGUL_FILLER:
            extend()
            continue
        swallowed, swallow = swallow, False
        if code < 0x80:
            if width:
                begin(width)
            else:
                extend()
            continue
        pair = False
        if width:
            if swallowed:
                extend()
                continue
            jamo = _jamo_class(code)
            if jamo == "initial":
                begin(width)
                continue
            if jamo is not None:
                if _jamo_class(ord(cell_text[-1])) != _JAMO_BEFORE[jamo]:
                    extend()
                    continue
            else:
                pair = _joins(cell_text, char) or _joins(char, cell_text)
                if not pair and not cell_text.endswith(_ZWJ):
                    begin(width)
                    continue
        if len((cell_text + char).encode()) > _CELL_BYTES:
            if width:
                begin(width)
            else:
                swallow = char == _ZWJ and len(cell_text.encode()) == _CELL_BYTES and wide()
                extend()
            continue
        cell_text += char
        extend(widen=pair or char == _VS16)
    return spans, sum(width for _start, _end, width in spans)


_JAMO_BEFORE = {"medial": "initial", "final": "medial"}


def _jamo_class(code: int) -> str | None:
    """Where a conjoining Hangul jamo goes in a syllable, as tmux classes it."""
    if 0x1100 <= code <= 0x115F or 0xA960 <= code <= 0xA97C:
        return "initial"
    if 0x1160 <= code <= 0x11A7 or 0xD7B0 <= code <= 0xD7C6:
        return "medial"
    if 0x11A8 <= code <= 0x11FF or 0xD7CB <= code <= 0xD7FB:
        return "final"
    return None


def _joins(cell: str, add: str) -> bool:
    """tmux's utf8_should_combine, which reads only the first character of each."""
    first, added = ord(cell[0]), ord(add[0])
    if added in _REGIONAL_INDICATORS and first in _REGIONAL_INDICATORS:
        return _regional_count(cell) == 1 and _regional_count(add) == 1
    return added in _SKIN_TONE_BASES and first in _SKIN_TONES


def _regional_count(text: str) -> int:
    return sum(ord(char) in _REGIONAL_INDICATORS for char in text)


def overridden(table: Sequence[Span], spans: Sequence[Span]) -> list[Span]:
    """Rich width *table* with *spans* taking precedence over it."""
    spans = sorted(spans)
    starts = [start for start, _end, _width in spans]
    result: list[Span] = []
    for start, end, width in table:
        cursor = start
        for span_start, span_end, _width in spans[max(bisect.bisect_right(starts, start) - 1, 0) :]:
            if span_start > end:
                break
            if span_end < cursor:
                continue
            if span_start > cursor:
                result.append((cursor, span_start - 1, width))
            cursor = span_end + 1
        if cursor <= end:
            result.append((cursor, end, width))
    return sorted([*result, *spans])


def _give_rich(spans: list[Span]) -> None:
    global _rich_spans
    import rich.cells

    if _rich_spans is None:
        load = rich.cells.load_cell_table

        @cache
        def load_with_agreed_widths(unicode_version: str = "auto") -> rich.cells.CellTable:
            table = load(unicode_version)
            return table._replace(widths=overridden(table.widths, _rich_spans or []))

        rich.cells.load_cell_table = load_with_agreed_widths
        rich.cells._cell_len = lambda text, unicode_version="auto": tmux_graphemes(
            text, unicode_version
        )[1]
        rich.cells.split_graphemes = tmux_graphemes
    elif spans == _rich_spans:
        return
    else:
        rich.cells.load_cell_table.cache_clear()  # type: ignore[attr-defined]
    _rich_spans = spans
    rich.cells.get_character_cell_size.cache_clear()
    rich.cells.cached_cell_len.cache_clear()


def _rich_width() -> Callable[[int], int]:
    """Rich's own width of a codepoint, before any of this."""
    from rich._unicode_data import load

    table = load("auto").widths
    starts = [start for start, _end, _width in table]

    def width(codepoint: int) -> int:
        index = bisect.bisect_right(starts, codepoint) - 1
        if index >= 0 and codepoint <= table[index][1]:
            return table[index][2]
        return 1

    return width


def _spacing_mark_spans_for_rich() -> list[Span]:
    width = _rich_width()
    return _spans(
        {
            code: 1
            for start, end in spacing_mark_ranges()
            for code in range(start, end + 1)
            if width(code) == 0
        }
    )


def _spans(widths: dict[int, int]) -> list[Span]:
    spans: list[list[int]] = []
    for code in sorted(widths):
        if spans and spans[-1][1] == code - 1 and spans[-1][2] == widths[code]:
            spans[-1][1] = code
        else:
            spans.append([code, code, widths[code]])
    return [(start, end, width) for start, end, width in spans]


def _fallback_tmux_spans() -> list[Span]:
    return overridden([(start, end, 1) for start, end in spacing_mark_ranges()], _EMOJI_PARTS)


def _executable(pid: int) -> str | None:
    """The program running as *pid*: the tmux server's build, which may not be the one on PATH."""
    try:
        if sys.platform == "darwin":
            buffer = ctypes.create_string_buffer(4096)
            length = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidpath(pid, buffer, len(buffer))
            path = buffer.value.decode() if length > 0 else ""
        else:
            path = os.readlink(f"/proc/{pid}/exe")
    except (OSError, AttributeError, UnicodeDecodeError):
        return None
    # Replaced since the server started: what is there now is another build.
    if not path or path.endswith(" (deleted)") or not os.path.isfile(path):
        return None
    return path


def _identity(tmux: str) -> dict[str, object] | None:
    """What the measured widths depend on: the tmux binary and Rich's tables."""
    from importlib.metadata import version

    try:
        stat = os.stat(tmux)
        rich_version = version("rich")
    except (OSError, ValueError):
        return None
    return {
        "tmux": os.path.realpath(tmux),
        "tmux_mtime_ns": stat.st_mtime_ns,
        "tmux_size": stat.st_size,
        "rich": rich_version,
        "rich_unicode": os.environ.get("UNICODE_VERSION", ""),
        "python_unicode": unicodedata.unidata_version,
    }


def _widths(tmux: str | None) -> dict[str, list[Span]] | None:
    """The agreed widths for *tmux*, measuring it if needed; ``None`` reads the last one used."""
    cache = _read_cache()
    if tmux is None:
        current = cache.get("current")
        if not isinstance(current, str):
            return None
        try:
            tmux = str(json.loads(current)["tmux"])
        except (ValueError, KeyError, TypeError):
            return None
        identity = _identity(tmux)
        if identity is None or json.dumps(identity, sort_keys=True) != current:
            return None
    else:
        identity = _identity(tmux)
        if identity is None:
            return None
    key = json.dumps(identity, sort_keys=True)
    if key not in _measured:
        stored = cache.get("entries", {}).get(key)
        _measured[key] = _parse(stored) if stored is not None else _measure(tmux)
    widths = _measured[key]
    if widths is not None and cache.get("current") != key:
        entries = {k: v for k, v in cache.get("entries", {}).items() if k != key}
        entries = dict(list(entries.items())[-(_CACHE_ENTRIES - 1) :])
        _write_cache({"current": key, "entries": {**entries, key: widths}})
    return widths


def _parse(stored: object) -> dict[str, list[Span]] | None:
    try:
        return {
            side: [(start, end, width) for start, end, width in stored[side]]  # type: ignore[index]
            for side in ("tmux", "rich")
        }
    except (KeyError, TypeError, ValueError):
        return None


def _read_cache() -> dict:
    try:
        data = json.loads((paths.cache_dir() / _CACHE_FILE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("entries", {}), dict) else {}


def _write_cache(data: dict) -> None:
    try:
        path = paths.cache_dir() / _CACHE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(data))
    except OSError:
        logger.debug("could not cache the measured widths", exc_info=True)


def _measure(tmux: str) -> dict[str, list[Span]] | None:
    codepoints = [
        code
        for codes in _MEASURED
        for code in codes
        if not 0xD800 <= code <= 0xDFFF
        and code not in _JOINERS
        and unicodedata.category(chr(code)) != "Cc"
    ]
    native = measure_in_tmux(tmux, [chr(code) for code in codepoints])
    if native is None or len(native) != len(codepoints):
        return None
    rich_width = _rich_width()
    tmux_widths: dict[int, int] = {}
    rich_widths: dict[int, int] = {}
    for code, tmux_width in zip(codepoints, native):
        own = rich_width(code)
        agreed = max(own, tmux_width, int(unicodedata.category(chr(code)) == "Mc"))
        if agreed != tmux_width:
            tmux_widths[code] = agreed
        if agreed != own:
            rich_widths[code] = agreed
    return {"tmux": overridden(_spans(tmux_widths), _EMOJI_PARTS), "rich": _spans(rich_widths)}


def measure_in_tmux(
    tmux: str, texts: list[str], *, setup: Sequence[str] = (), width: int = 20
) -> list[int] | None:
    """tmux's width of each text, from a private server that reads no config.

    *setup* are config lines sourced first; *width* must exceed every text.
    """
    server = [tmux, "-L", f"gd-widths-{os.getpid()}-{uuid.uuid4().hex[:8]}", "-f", os.devnull]
    env = {name: value for name, value in os.environ.items() if name not in ("TMUX", "TMUX_PANE")}
    with tempfile.TemporaryDirectory(prefix="gd-widths-") as directory:
        source = Path(directory, "texts.json")
        result = Path(directory, "widths.json")
        source.write_text(json.dumps(texts))
        config = Path(directory, "setup.conf")
        config.write_text("".join(f"{line}\n" for line in setup))
        probe = Path(__file__).with_name("width_probe.py")
        command = shlex.join([sys.executable, "-I", "-S", str(probe), str(source), str(result)])
        try:
            started = subprocess.run(
                [
                    *server,
                    "source-file",
                    str(config),
                    ";",
                    "new-session",
                    "-d",
                    "-x",
                    str(width),
                    "-y",
                    "2",
                    command,
                ],
                env=env,
                capture_output=True,
                timeout=_PROBE_TIMEOUT_SECS,
                check=False,
            )
            if started.returncode != 0:
                return None
            deadline = time.monotonic() + _PROBE_TIMEOUT_SECS
            while not result.exists():
                if time.monotonic() > deadline:
                    return None
                time.sleep(0.02)
            return json.loads(result.read_text())
        except (OSError, ValueError, subprocess.SubprocessError):
            logger.debug("could not measure tmux's widths", exc_info=True)
            return None
        finally:
            try:
                subprocess.run(
                    [*server, "kill-server"],
                    env=env,
                    capture_output=True,
                    timeout=_PROBE_TIMEOUT_SECS,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
