"""Regression tests for the table-text helpers."""

from __future__ import annotations

from gitdirector.commands.tui.table_text import wrap_table_cell_text


class TestWrapTableCellText:
    def test_empty_string_returned_unchanged(self):
        assert wrap_table_cell_text("", 10) == ""

    def test_zero_or_negative_width_returns_input_unchanged(self):
        """Without a sensible width the helper must NOT crash or mutate
        the input — callers might pass 0 if width sensing is unavailable.
        """
        assert wrap_table_cell_text("hello world", 0) == "hello world"
        assert wrap_table_cell_text("hello world", -3) == "hello world"

    def test_short_text_without_newlines_returned_unchanged(self):
        assert wrap_table_cell_text("hi", 10) == "hi"

    def test_long_text_wraps_using_textwrap(self):
        wrapped = wrap_table_cell_text("abcdefghij", 4)
        assert wrapped == "abcd\nefgh\nij"

    def test_newlines_in_text_are_preserved(self):
        text = "alpha\nbeta\ngamma"
        wrapped = wrap_table_cell_text(text, 100)
        assert wrapped == text

    def test_long_word_does_not_get_lost_when_narrower_than_width(self):
        """A line whose first chunk can't fit the requested width must
        fall back to the full line, not silently disappear.

        ``textwrap.wrap(' ', width=2)`` returns ``[]`` because the
        whitespace-only token is dropped. The helper guards for this so
        we never lose content; we fall back to the original line.
        """
        text = " "
        wrapped = wrap_table_cell_text(text, 2)
        assert wrapped == text, (
            f"expected the helper to fall back to the original text, got {wrapped!r}"
        )
