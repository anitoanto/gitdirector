"""File list for the ``DiffReviewScreen``.

Each changed file is a two-line tile: its status letter, name and ``+N -M``
on the first line, the folder it sits in under it. The selected tile takes
the console's row tint, so the list reads like every other list.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import ListItem, ListView, Static

from ..diff_renderer import ChangedFile, status_letter


@dataclass(frozen=True)
class _FileTileSpec:
    file: ChangedFile
    repo_dir: str  # absolute repo path

    def filename(self) -> str:
        if self.file.is_rename and self.file.old_path:
            return f"{self.file.old_path} \u2192 {self.file.path}"
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


class FileTile(Static):
    """A single file row in the diff file list."""

    DEFAULT_CSS = """
    FileTile {
        width: 1fr;
        height: 2;
        padding: 0 2;
    }
    FileTile:hover {
        background: $primary 12%;
    }
    FileTile.--selected {
        background: $primary 30%;
    }
    FileTile .tile-title-row {
        width: 1fr;
        height: 1;
    }
    FileTile .tile-icon {
        width: 2;
        height: 1;
    }
    FileTile .tile-title {
        width: 1fr;
        height: 1;
        color: $text;
        text-style: bold;
        text-overflow: ellipsis;
    }
    FileTile .tile-stats {
        width: auto;
        height: 1;
        padding: 0 0 0 1;
    }
    FileTile .tile-subtitle {
        width: 1fr;
        height: 1;
        padding: 0 0 0 2;
        color: $text-muted;
        text-overflow: ellipsis;
    }
    """

    selected = reactive(False)

    def __init__(self, spec: _FileTileSpec, **kwargs) -> None:
        super().__init__(**kwargs)
        self._spec = spec

    def compose(self):
        with Horizontal(classes="tile-title-row"):
            yield Static(status_letter(self._spec.file.status), classes="tile-icon")
            yield Static(self._spec.filename(), classes="tile-title", markup=False)
            yield Static(self._stats_text(), classes="tile-stats")
        yield Static(self._spec.subtitle(), classes="tile-subtitle", markup=False)

    def _stats_text(self) -> Text:
        text = Text(justify="right")
        if self._spec.file.is_binary:
            text.append("binary", style="#8b949e")
            return text
        if self._spec.file.is_image:
            text.append("image", style="#8b949e")
            return text
        text.append(f"+{self._spec.file.additions}", style="bold #3fb950")
        text.append(" ")
        text.append(f"-{self._spec.file.deletions}", style="bold #f85149")
        return text

    def watch_selected(self, _old: bool, new: bool) -> None:
        self.set_class(new, "--selected")

    def set_selected(self, value: bool) -> None:
        self.selected = value


class FileTileList(ListView):
    """List of :class:`FileTile` rows with vertical/horizontal navigation."""

    DEFAULT_CSS = """
    FileTileList {
        width: 1fr;
        height: 1fr;
        background: $surface;
        padding: 0 0;
    }
    FileTileList > ListItem {
        padding: 0 0;
        height: 2;
        background: transparent;
    }
    /* The tile shows the selection; ListView's own highlight would double it. */
    FileTileList > ListItem.-highlight,
    FileTileList:focus > ListItem.-highlight {
        background: transparent;
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
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._specs: list[_FileTileSpec] = []

    async def set_files(self, files: list[ChangedFile], repo_dir: str = "") -> None:
        """Replace the list's files and select the first one.

        Awaiting the removal and the mount means the tiles exist by the time
        the index is set, so selecting needs no deferral or retry.
        """
        self._specs = [_FileTileSpec(f, repo_dir) for f in files]
        await self.clear()
        if self._specs:
            await self.extend(ListItem(FileTile(spec)) for spec in self._specs)
            self.index = 0

    def watch_index(self, old: int | None, new: int | None) -> None:
        # ListView highlights the item and scrolls it into view.
        super().watch_index(old, new)
        for index, selected in ((old, False), (new, True)):
            if self._is_valid_index(index):
                for tile in self._nodes[index].query(FileTile):
                    tile.set_selected(selected)
        self.post_message(self.FileSelected(self.selected_file()))

    def action_cursor_down(self) -> None:
        if self.index is None:
            self.index = 0
            return
        if self.index < len(self._specs) - 1:
            self.index += 1

    def action_cursor_up(self) -> None:
        if self.index is None:
            self.index = 0
            return
        if self.index > 0:
            self.index -= 1

    def selected_file(self) -> ChangedFile | None:
        if self.index is None or not self._specs:
            return None
        if 0 <= self.index < len(self._specs):
            return self._specs[self.index].file
        return None


__all__ = [
    "FileTile",
    "FileTileList",
]
