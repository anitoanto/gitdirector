"""tmux and Rich agree on the width of any text, once tmux has been measured."""

import random
import shutil
import unicodedata

import pytest
from rich.cells import cell_len

from gitdirector import cell_widths
from gitdirector.cell_widths import measure_in_tmux, tmux_codepoint_width_commands

from ..conftest import REAL_TMUX_IDENTITY
from ..test_cell_widths import SPACING_MARK_TEXT, UNCHANGED_TEXT
from ._shared import _cleanup_tmux_tmpdir, _make_short_tmux_tmpdir, _tmux_integration_lock

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")

EMOJI_SEQUENCES = [
    "👍🏽",
    "✌🏽",
    "☝🏽",
    "❤️",
    "☺️",
    "✔️",
    "⚠️",
    "1️⃣",
    "🇮🇳",
    "🇺🇸",
    "👨‍👩‍👧",
    "🏳️‍🌈",
    "🧑🏽‍💻",
    "👩‍❤️‍👨",
    "🏋️‍♀️",
    "⛹🏽‍♀️",
    "🇮",
    "🇮🇳🇮",
    "🫨",
    "🪿",
    "🫠",
]
TEXTS = [
    *SPACING_MARK_TEXT,
    *UNCHANGED_TEXT,
    *EMOJI_SEQUENCES,
    "Know_kerala: മലയാളം വീഡിയോ ✓ 🎬",
    "സ്ത്രീ ക്ഷ ന്‍ ൻ ർ ൽ ൾ",
    "·…→①○●✕◧«▌─│",
    "�",
    "한국어 ﾊﾝｶｸ Ｆｕｌｌ",
    "مرحبًا שָׁלוֹם",
]


@pytest.fixture
def measured(monkeypatch):
    with _tmux_integration_lock():
        tmux_dir = _make_short_tmux_tmpdir()
        monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.setattr(cell_widths, "_identity", REAL_TMUX_IDENTITY)
        previous = cell_widths._rich_spans
        try:
            assert cell_widths._widths(shutil.which("tmux")) is not None
            commands = tmux_codepoint_width_commands()
            yield shutil.which("tmux"), commands
        finally:
            cell_widths._give_rich(previous or [])
            _cleanup_tmux_tmpdir(tmux_dir)


def _mismatches(tmux: str, commands: list[str], texts: list[str]) -> list[tuple[str, int, int]]:
    widths = measure_in_tmux(tmux, texts, setup=commands, width=500)
    assert widths is not None
    return [
        (text, cell_len(text), width)
        for text, width in zip(texts, widths)
        if cell_len(text) != width
    ]


def test_tmux_was_measured(measured):
    _tmux, commands = measured
    assert cell_widths._widths(None) is not None
    assert all(" U+" in command for command in commands)


def test_tmux_draws_text_as_wide_as_rich_counts_it(measured):
    assert _mismatches(*measured, TEXTS) == []


def test_fuzzed_text_is_as_wide_in_tmux_as_rich_counts_it(measured):
    seed = random.randrange(1 << 32)
    rng = random.Random(seed)
    letters = [
        chr(code)
        for code in range(0xA0, 0x323B0)
        if not 0xD800 <= code <= 0xDFFF
        and chr(code) not in "‍"
        and ord(chr(code)) not in cell_widths._JOINERS
        and unicodedata.category(chr(code)) not in ("Cc", "Cs")
    ]
    tricky = [char for text in TEXTS for char in text] + list("\u200d\ufe0f\ufe0e\u3164🏽🇮🇳")
    pools = [letters, tricky, tricky, EMOJI_SEQUENCES, list("abc xyz")]
    texts = [
        "".join(rng.choice(rng.choice(pools)) for _ in range(rng.randint(1, 12)))
        for _ in range(5000)
    ]
    mismatches = _mismatches(*measured, texts)
    report = [
        (rich, tmux, " ".join(f"U+{ord(char):04X}" for char in text))
        for text, rich, tmux in mismatches[:10]
    ]
    assert mismatches == [], (seed, len(mismatches), report)
