"""Shared table text wrapping helpers."""

from __future__ import annotations

import textwrap


def resolve_wrapped_column_width(
    screen_width: int,
    *,
    min_width: int,
    max_width: int,
    divisor: int,
) -> int:
    if screen_width <= 0:
        return min_width
    target = screen_width // divisor
    return max(min_width, min(max_width, target))


def wrap_table_cell_text(text: str, max_width: int) -> str:
    if not text or max_width <= 0:
        return text
    if len(text) <= max_width and "\n" not in text:
        return text
    wrapped_chunks = []
    for line in text.split("\n"):
        if not line:
            wrapped_chunks.append("")
            continue
        if len(line) <= max_width:
            wrapped_chunks.append(line)
        else:
            wrapped_chunks.extend(textwrap.wrap(line, width=max_width) or [line])
    return "\n".join(wrapped_chunks)
