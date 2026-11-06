"""Property tests: text measures, cuts and pads to exactly the cells it claims."""

import unicodedata

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rich._unicode_data import load as load_unpatched_table
from rich.cells import cell_len, chop_cells, get_character_cell_size, set_cell_size, split_graphemes
from rich.segment import Segment
from rich.text import Text
from textual.strip import Strip

import gitdirector.commands  # noqa: F401  (counts spacing marks in Rich)
from gitdirector import cell_widths
from gitdirector.cell_widths import spacing_mark_ranges
from gitdirector.commands import fit_left
from gitdirector.commands.tui.constants import TablePalette
from gitdirector.commands.tui.sidebar import (
    SidebarEntry,
    render_group,
    render_rail_session,
    render_session,
)
from gitdirector.commands.tui.table_text import pad_cells, truncate_cells, wrap_table_cell_text
from gitdirector.terminal_text import printable

SPACING_MARKS = [
    chr(code) for start, end in spacing_mark_ranges() for code in range(start, end + 1)
]
# Text that has broken terminal layouts: scripts with vowel signs and
# viramas, joiners, selectors, wide and ambiguous characters, emoji.
TRICKY = list(
    "aZ ~·…→①○●✕◧«▌─"
    "നിങ്ങൾമലയാളംകേരളം്ൾൻ"
    "தமிழ்नमस्तेকাකාกำภาษาไทยကာកាಕಾకాકા"
    "日本語한국어ｱｲ！"
    "́̈⃣​‌‍⁠­︎️"
    "각"
    "❤☺👍👨👩👧🏽🇮🇳🫨🪿�"
    "مرحبًاשָׁלוֹם"
)
text_strategy = st.text(
    alphabet=st.one_of(
        st.sampled_from(TRICKY),
        st.sampled_from(SPACING_MARKS),
        st.characters(codec="utf-8", exclude_categories=("Cc", "Cs")),
    ),
    max_size=40,
)
widths = st.integers(min_value=1, max_value=60)
fuzz = settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@pytest.fixture(scope="module", autouse=True)
def spacing_marks_only():
    previous = cell_widths._rich_spans
    cell_widths._give_rich(cell_widths._spacing_mark_spans_for_rich())
    yield
    cell_widths._give_rich(previous or [])


def _unpatched_width(character: str) -> int:
    codepoint = ord(character)
    for start, end, width in load_unpatched_table("auto").widths:
        if start <= codepoint <= end:
            return width
    return 1


@fuzz
@given(st.characters(codec="utf-8", exclude_categories=("Cc", "Cs")))
def test_only_spacing_marks_change_width(character):
    expected = 1 if unicodedata.category(character) == "Mc" else _unpatched_width(character)
    assert get_character_cell_size(character) == expected


@fuzz
@given(text_strategy)
def test_graphemes_add_up_to_the_measured_width(text):
    spans, total = split_graphemes(text)
    assert total == cell_len(text)
    assert sum(width for _start, _end, width in spans) == total
    assert "".join(text[start:end] for start, end, _width in spans) == text


@fuzz
@given(text_strategy, widths)
def test_set_cell_size_is_exact(text, width):
    assert cell_len(set_cell_size(text, width)) == width


@fuzz
@given(text_strategy, widths)
def test_chopped_lines_fit_and_keep_the_text(text, width):
    lines = chop_cells(text, width)
    # Rich drops a text that is nothing but zero-width characters.
    if cell_len(text):
        assert "".join(lines) == text
    # A grapheme wider than the line gets a line of its own.
    assert all(cell_len(line) <= width or len(split_graphemes(line)[0]) == 1 for line in lines)


@fuzz
@given(text_strategy, widths)
def test_truncation_never_overflows(text, width):
    cut = Text(text)
    cut.truncate(width, overflow="ellipsis")
    assert cut.cell_len <= width
    padded = Text(text)
    padded.truncate(width, overflow="ellipsis", pad=True)
    assert padded.cell_len == width


@fuzz
@given(text_strategy, st.integers(0, 50), st.integers(0, 50))
def test_strip_crop_is_exact(text, start, length):
    strip = Strip([Segment(text)])
    end = start + length
    cropped = strip.crop(start, end)
    assert cropped.cell_length == max(
        0, min(end, strip.cell_length) - min(start, strip.cell_length)
    )
    extended = strip.crop_extend(start, end, None)
    assert extended.cell_length == length


PALETTE = TablePalette(success="green", yellow="yellow", muted="grey50", primary="blue")


@fuzz
@given(
    repo=text_strategy,
    purpose=text_strategy,
    description=text_strategy,
    status=st.sampled_from([None, "running", "waiting", "idle"]),
    shown=st.booleans(),
    width=st.integers(min_value=1, max_value=60),
)
def test_sidebar_rows_fit_the_sidebar(repo, purpose, description, status, shown, width):
    entry = SidebarEntry(
        session_name="gd/repo_abcde/agent/1",
        repo=repo,
        repo_slug="repo_abcde",
        purpose=purpose,
        sequence=1,
        description=description,
        status=status,
    )
    for row in (
        render_session(entry, PALETTE, shown=shown, width=width),
        render_rail_session(entry, PALETTE, shown=shown, width=width),
        render_group(repo, PALETTE, width=width),
    ):
        for line in row.split("\n"):
            assert line.cell_len <= width, (line.plain, width)


@fuzz
@given(text_strategy, st.integers(min_value=-2, max_value=60))
def test_table_cells_fit_their_columns(text, width):
    cut = truncate_cells(text, width)
    assert cell_len(cut) <= max(width, 0)
    if width > 0:
        assert cell_len(pad_cells(cut, width)) == width
    for line in wrap_table_cell_text(text, width).split("\n"):
        assert width <= 0 or cell_len(line) <= width or len(split_graphemes(line)[0]) == 1


@fuzz
@given(text_strategy, st.integers(min_value=0, max_value=60))
def test_fit_left_fits(text, width):
    assert cell_len(fit_left(text, width)) <= width


@fuzz
@given(st.text(max_size=60))
def test_printable_leaves_no_control_characters(text):
    clean = printable(text)
    assert not any(unicodedata.category(char) == "Cc" for char in clean)
    assert cell_len(clean) == cell_len(text)
