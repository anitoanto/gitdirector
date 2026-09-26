"""File list for the ``DiffReviewScreen``.

Each changed file is a two-line tile: its status letter, name and ``+N -M``
on the first line, the folder it sits in under it. The selected tile takes
the console's row tint, so the list reads like every other list.

The list is one ``OptionList`` drawing only the rows on screen: a widget per
tile took minutes to mount and reflow for a diff of hundreds of files.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.binding import Binding
from textual.events import Resize
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ..constants import resolve_table_palette
from ..diff_renderer import ChangedFile, status_letter

_TILE_PADDING = 2
_STATS_GAP = 1
# The scrollbar keeps a column, so the stats never wrap when it appears.
_SCROLLBAR_WIDTH = 1
# Before the first layout: the pane is 48 wide, less padding and scrollbar.
_DEFAULT_WIDTH = 43
_MUTED = "#8b949e"


@dataclass(frozen=True)
class _FileTileSpec:
    file: ChangedFile
    repo_dir: str  # absolute repo path

    def filename(self) -> str:
        if self.file.is_rename and self.file.old_path:
            return f"{self.file.old_path} → {self.file.path}"
        path = self.file.path
        if "/" in path:
            return path.rsplit("/", 1)[-1]
        return path

    def subtitle(self) -> str:
        """The folder the file is in, relative to the repository."""
        folder = self.file.path.rpartition("/")[0]
        return f"{folder}/" if folder else "./"

    def icon_letter(self) -> str:
        return status_letter(self.file.status).plain

    def stats(self) -> Text:
        if self.file.is_binary:
            return Text("binary", style=_MUTED)
        if self.file.is_image:
            return Text("image", style=_MUTED)
        return Text.assemble(
            (f"+{self.file.additions}", "bold #3fb950"),
            " ",
            (f"-{self.file.deletions}", "bold #f85149"),
        )

    def prompt(self, width: int, muted: str) -> Text:
        """The two-line tile, *width* cells wide, stats against the right edge."""
        stats = self.stats()
        title = Text.assemble(status_letter(self.file.status), " ", (self.filename(), "bold"))
        room = width - stats.cell_len - _STATS_GAP
        if title.cell_len > room:
            title.truncate(max(room, 1), overflow="ellipsis")
        gap = max(width - title.cell_len - stats.cell_len, _STATS_GAP)
        subtitle = Text(self.subtitle(), style=muted)
        subtitle.truncate(max(width - 2, 1), overflow="ellipsis")
        return Text.assemble(title, " " * gap, stats, "\n  ", subtitle, no_wrap=True)


class FileTileList(OptionList):
    """List of file tiles with vertical/horizontal navigation."""

    DEFAULT_CSS = """
    FileTileList {
        width: 1fr;
        height: 1fr;
        background: $surface;
        padding: 0 0;
        border: none;
    }
    FileTileList:focus {
        border: none;
        background-tint: $foreground 0%;
    }
    FileTileList > .option-list--option {
        padding: 0 2;
    }
    FileTileList > .option-list--option-highlighted,
    FileTileList:focus > .option-list--option-highlighted {
        background: $primary 30%;
        color: $text;
        text-style: none;
    }
    FileTileList > .option-list--option-hover {
        background: $primary 12%;
    }
    """

    class FileSelected(Message):
        """Posted when the highlighted file in the list changes."""

        def __init__(self, file: ChangedFile | None) -> None:
            super().__init__()
            self.file = file

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._specs: list[_FileTileSpec] = []
        self._tile_width = _DEFAULT_WIDTH

    @property
    def index(self) -> int | None:
        return self.highlighted

    @index.setter
    def index(self, value: int | None) -> None:
        self.highlighted = value

    async def set_files(self, files: list[ChangedFile], repo_dir: str = "") -> None:
        """Replace the list's files and select the first one."""
        self._specs = [_FileTileSpec(f, repo_dir) for f in files]
        self._rebuild()
        self.highlighted = 0 if self._specs else None

    def _muted_style(self) -> str:
        try:
            return resolve_table_palette(self.app.get_css_variables()).muted
        except Exception:
            return _MUTED

    def _rebuild(self) -> None:
        muted = self._muted_style()
        width = self._tile_width
        options = [
            Option(spec.prompt(width, muted), id=str(index))
            for index, spec in enumerate(self._specs)
        ]
        keep = self.highlighted
        self.clear_options()
        if options:
            self.add_options(options)
        if keep is not None and self._specs:
            self.highlighted = min(keep, len(self._specs) - 1)

    def on_resize(self, event: Resize) -> None:
        # Stats sit against the right edge, so the tiles follow the width.
        width = max(event.size.width - 2 * _TILE_PADDING - _SCROLLBAR_WIDTH, 1)
        if width != self._tile_width:
            self._tile_width = width
            if self._specs:
                self._rebuild()

    def watch_highlighted(self, highlighted: int | None) -> None:
        super().watch_highlighted(highlighted)
        self.post_message(self.FileSelected(self.selected_file()))

    def _step(self, direction: int) -> None:
        # Stop at either end instead of wrapping.
        if not self._specs:
            return
        if self.highlighted is None:
            self.highlighted = 0
            return
        self.highlighted = max(0, min(len(self._specs) - 1, self.highlighted + direction))

    def action_cursor_down(self) -> None:
        self._step(1)

    def action_cursor_up(self) -> None:
        self._step(-1)

    def selected_file(self) -> ChangedFile | None:
        if self.highlighted is None or not self._specs:
            return None
        if 0 <= self.highlighted < len(self._specs):
            return self._specs[self.highlighted].file
        return None

    def tile_text(self, index: int) -> Text:
        """The rendered tile at *index*, for tests and screenshots."""
        prompt = self.get_option_at_index(index).prompt
        assert isinstance(prompt, Text)
        return prompt


__all__ = [
    "FileTileList",
]
