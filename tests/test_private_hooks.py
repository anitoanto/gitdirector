"""The private Rich and Textual internals GitDirector hooks into are still there.

pyproject caps both at the major version these were checked against; this
fails first when a release inside that cap moves one of them.
"""

import inspect

import rich.cells
from rich.console import Console
from rich.segment import Segment
from textual.strip import Strip

from gitdirector import cell_widths


def test_rich_cell_functions_cell_widths_replaces():
    for name in ("_cell_len", "split_graphemes", "load_cell_table", "cached_cell_len"):
        assert callable(getattr(rich.cells, name)), name
    assert callable(rich.cells.get_character_cell_size.cache_clear)
    assert hasattr(rich.cells.CellTable, "_replace")
    assert cell_widths._rich_width()(ord("a")) == 1


def test_rich_reads_them_through_the_module_at_call_time():
    # Patching rich.cells only works while these look the names up there.
    for function in (rich.cells.cell_len, rich.cells.chop_cells, rich.cells.set_cell_size):
        source = inspect.getsource(function)
        assert "_cell_len" in source or "split_graphemes" in source or "cell_len" in source


def test_textual_strip_internals_terminal_text_renders_with():
    strip = Strip([Segment("text")])
    assert hasattr(strip, "_render_cache") and hasattr(strip, "_segments")
    assert hasattr(Console(), "_color_system")
    assert callable(strip.render_style)
