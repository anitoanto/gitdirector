"""Shared table text wrapping helpers."""

from __future__ import annotations

import textwrap


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
