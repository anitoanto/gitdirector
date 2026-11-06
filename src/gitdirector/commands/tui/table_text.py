"""Shared table text helpers, measured in terminal cells rather than characters."""

from __future__ import annotations

from rich.cells import cell_len, chop_cells, set_cell_size


def pad_cells(text: str, width: int) -> str:
    """*text* padded with spaces to *width* cells; longer text is left alone."""
    return text + " " * max(width - cell_len(text), 0)


def truncate_cells(text: str, width: int) -> str:
    """*text* cut to at most *width* cells, ending in an ellipsis when cut."""
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    if width == 1:
        return "…"
    return set_cell_size(text, width - 1) + "…"


def wrap_table_cell_text(text: str, max_width: int) -> str:
    if not text or max_width <= 0:
        return text
    if cell_len(text) <= max_width and "\n" not in text:
        return text
    return "\n".join(
        wrapped for line in text.split("\n") for wrapped in _wrap_line(line, max_width)
    )


def _wrap_line(line: str, width: int) -> list[str]:
    """Greedy word wrap; a word wider than the line is cut into line-sized pieces."""
    lines: list[str] = []
    current = ""
    for word in line.split():
        candidate = f"{current} {word}" if current else word
        if cell_len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        *full, current = chop_cells(word, width) or [""]
        lines.extend(full)
    if current or not lines:
        lines.append(current)
    return lines
