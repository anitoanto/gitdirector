import json
import os
import sys
import unicodedata
from unittest.mock import patch

import pytest
from rich.cells import cell_len
from rich.text import Text

import gitdirector.commands  # noqa: F401  (gives Rich the agreed widths)
from gitdirector import cell_widths, paths
from gitdirector.cell_widths import (
    TMUX_FIRST_SLOT,
    overridden,
    spacing_mark_ranges,
    tmux_codepoint_width_commands,
)

from .conftest import REAL_TMUX_IDENTITY

# What tmux 3.8 measured with the overrides, and Ghostty 1.3 draws.
SPACING_MARK_TEXT = {
    "നി": 2,
    "കാ": 2,
    "നിങ്ങൾ": 5,
    "മലയാളം": 6,
    "കേരളം": 5,
    "ം": 1,
    "தமிழ்": 4,
    "का": 2,
}
UNCHANGED_TEXT = {
    "abc": 3,
    "é": 1,
    "नमस्ते": 4,
    "ภาษาไทย": 7,
    "日本": 4,
    "👨‍👩‍👧": 2,
    "❤️": 2,
    "👍🏽": 2,
    "🇮🇳": 2,
    "म\ufe0f": 2,
    "日\ufe0f": 2,
}


@pytest.fixture
def spacing_marks_only():
    """Rich with the widths it gets when tmux cannot be measured."""
    previous = cell_widths._rich_spans
    cell_widths._give_rich(cell_widths._spacing_mark_spans_for_rich())
    yield
    cell_widths._give_rich(previous or [])


def test_ranges_cover_every_spacing_mark():
    covered = {code for start, end in spacing_mark_ranges() for code in range(start, end + 1)}
    every = {code for code in range(sys.maxunicode + 1) if unicodedata.category(chr(code)) == "Mc"}
    assert covered == every


def test_ranges_are_merged():
    ranges = spacing_mark_ranges()
    assert all(previous[1] + 1 < current[0] for previous, current in zip(ranges, ranges[1:]))


def test_without_tmux_spacing_marks_and_emoji_parts_get_their_widths():
    commands = tmux_codepoint_width_commands()
    assert commands[0].startswith(f"set-option -s codepoint-widths[{TMUX_FIRST_SLOT}] U+")
    assert any(command.endswith(" U+0D3E-U+0D40=1") for command in commands)
    assert any(command.endswith(" U+1F1E6-U+1F1FF=2") for command in commands)
    assert any(command.endswith(" U+1F3FB-U+1F3FF=2") for command in commands)
    assert len(commands) == len(spacing_mark_ranges()) + 2


def test_spans_take_precedence_over_the_table():
    table = [(10, 20, 0), (30, 40, 2)]
    assert overridden(table, [(12, 13, 1), (20, 32, 1)]) == [
        (10, 11, 0),
        (12, 13, 1),
        (14, 19, 0),
        (20, 32, 1),
        (33, 40, 2),
    ]
    assert overridden(table, []) == table


def test_rich_gives_spacing_marks_a_cell(spacing_marks_only):
    for text, width in SPACING_MARK_TEXT.items():
        assert cell_len(text) == width, text


def test_rich_measures_everything_else_as_before(spacing_marks_only):
    for text, width in UNCHANGED_TEXT.items():
        assert cell_len(text) == width, text


def test_truncation_fits_the_counted_width(spacing_marks_only):
    text = Text("മലയാളം tail")
    text.truncate(4, overflow="ellipsis")
    assert text.cell_len == 4


@pytest.mark.parametrize(
    ("text", "width"),
    [
        ("a🏽", 3),
        ("👍🏽", 2),
        ("🏽👍", 2),
        ("🇮", 2),
        ("🇮🇳", 2),
        ("🇮🇳🇮", 4),
        ("ന്\u200d x", 3),
        ("ന്\u200dറ", 1),
        ("a\ufe0f", 2),
        ("日\ufe0f", 2),
        ("\u200da", 1),
        ("\u3164", 0),
        ("a\u3164b", 2),
        ("\u0301a", 1),
        ("a\u200d\u3164മ", 1),
        ("🇮்🇮", 2),
        ("🏽👩👧", 2),
        ("👍🏽🏽", 2),
        ("\U00012e78\u200d\ua964", 3),
        ("👨\u200d👩\u200d👧\u200d👦\u200d👨\u200d👩", 2),
        ("👨\u200d👩\u200d👧\u200d👦\u200d👨\u200d日日", 4),
        ("👨\u200d👩\u200d👧\u200d👦\u200d👨\u200dx", 3),
        ("👨\u200d👩\u200d👧\u200d👦\u200d👨\u200d\u0301日", 4),
        ("🏽" + "👨" * 8, 4),
        ("\u1100\u1161\u11a8", 2),
        ("x" + "\u200d" * 12 + "日", 3),
        ("ä" + "\u0301" * 15 + "\u200d日", 3),
    ],
)
def test_text_joins_into_cells_as_in_tmux(spacing_marks_only, text, width):
    spans, total = cell_widths.tmux_graphemes(text)
    assert total == width == cell_len(text)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    assert all(previous[1] == current[0] for previous, current in zip(spans, spans[1:]))


def test_the_server_binary_is_found_from_its_pid():
    assert cell_widths._executable(os.getpid()) == os.path.realpath(sys.executable)
    assert cell_widths._executable(2**22 + 12345) is None


@pytest.fixture
def fake_tmux(tmp_path, monkeypatch):
    monkeypatch.setattr(cell_widths, "_identity", REAL_TMUX_IDENTITY)
    monkeypatch.setattr(cell_widths, "_measured", {})
    tmux = tmp_path / "tmux"
    tmux.write_text("")
    return tmux


MEASURED = {"tmux": [(0x1FAE8, 0x1FAE8, 2)], "rich": [(0x261D, 0x261D, 2)]}


def test_the_server_build_is_measured_once_and_remembered(fake_tmux, monkeypatch):
    with patch.object(cell_widths, "_measure", return_value=MEASURED) as measure:
        assert cell_widths._widths(str(fake_tmux)) == MEASURED
        monkeypatch.setattr(cell_widths, "_measured", {})
        assert cell_widths._widths(str(fake_tmux)) == MEASURED
        assert cell_widths._widths(None) == MEASURED
    measure.assert_called_once_with(str(fake_tmux))
    cached = json.loads((paths.cache_dir() / "cell-widths.json").read_text())
    assert json.loads(cached["current"])["tmux"] == str(fake_tmux)


def test_a_rebuilt_tmux_is_measured_again(fake_tmux, monkeypatch):
    with patch.object(cell_widths, "_measure", return_value=MEASURED):
        cell_widths._widths(str(fake_tmux))
    fake_tmux.write_text("a new build")
    monkeypatch.setattr(cell_widths, "_measured", {})
    assert cell_widths._widths(None) is None
    with patch.object(cell_widths, "_measure", return_value=None) as measure:
        assert cell_widths._widths(str(fake_tmux)) is None
    measure.assert_called_once()


def test_a_failed_measurement_falls_back(fake_tmux, monkeypatch):
    monkeypatch.setattr(cell_widths, "_executable", lambda _pid: str(fake_tmux))
    with patch.object(cell_widths, "measure_in_tmux", return_value=None):
        commands = tmux_codepoint_width_commands(12345)
    assert len(commands) == len(spacing_mark_ranges()) + 2
    assert not (paths.cache_dir() / "cell-widths.json").exists()
