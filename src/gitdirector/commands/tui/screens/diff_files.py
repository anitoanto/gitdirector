"""File-list tile widget for the ``DiffReviewScreen``.

Renders each changed file as a "list tile": a fixed-width status icon on the
left, a title (the filename) and subtitle (the full path) stacked on the
right, and an aligned ``+N -M`` stats block on the far right.

The widget is a custom subclass of ``ListView`` so that selection, focus,
and arrow-key navigation all work out of the box. The colour choices for
the selected state are picked to be legible against the status icon's
background — see :mod:`gitdirector.commands.tui.diff_renderer` for the
palette.
"""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import MouseEvent
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import ListItem, ListView, Static

from ..diff_renderer import (
    STATUS_PILL_BG,
    ChangedFile,
)

# ---------------------------------------------------------------------------
# Tile data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FileTileSpec:
    file: ChangedFile
    repo_dir: str  # absolute repo path; used to compute the relative subtitle

    def title(self) -> str:
        if self.file.is_rename and self.file.old_path:
            return f"{self.file.old_path} \u2192 {self.file.path}"
        return self.file.path

    def filename(self) -> str:
        if self.file.is_rename and self.file.old_path:
            return f"{self.file.old_path} \u2192 {self.file.path}"
        path = self.file.path
        if "/" in path:
            return path.rsplit("/", 1)[-1]
        return path

    def subtitle(self) -> str:
        path = self.repo_dir.rstrip("/") + "/" + self.file.path
        return path

    def icon_letter(self) -> str:
        status = self.file.status
        if status == "?":
            return "U"
        if status in ("A", "M", "D", "R"):
            return status
        return status[:1].upper() if status else "\u00b7"

    def icon_bg(self) -> str:
        return STATUS_PILL_BG.get(self.file.status, "#6e7681")

    def icon_fg(self) -> str:
        return "#ffffff"

    def status_label(self) -> str:
        if self.file.status == "A":
            return "new file"
        if self.file.status == "D":
            return "deleted"
        if self.file.status == "R":
            return "renamed"
        if self.file.status == "?":
            return "untracked"
        if self.file.is_binary:
            return "binary"
        return "modified"


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _parse_hex(color: str) -> tuple[float, float, float] | None:
    """Convert ``#rgb`` / ``#rrggbb`` to an ``(r, g, b)`` tuple in 0..1.

    Returns ``None`` for anything that isn't a hex literal (CSS named
    colours, ``$surface`` theme tokens, etc.) so callers can fall back to a
    sensible default.
    """
    if not color:
        return None
    match = _HEX_COLOR_RE.match(color.strip())
    if not match:
        return None
    raw = match.group(1)
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    r = int(raw[0:2], 16) / 255.0
    g = int(raw[2:4], 16) / 255.0
    b = int(raw[4:6], 16) / 255.0
    return r, g, b


def _to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = (max(0.0, min(1.0, v)) for v in rgb)
    return f"#{int(round(r * 255)):02x}{int(round(g * 255)):02x}{int(round(b * 255)):02x}"


def _luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = (max(0.0, min(1.0, v)) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG-style relative luminance (sRGB linearised)."""

    def linearise(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (max(0.0, min(1.0, v)) for v in rgb)
    return 0.2126 * linearise(r) + 0.7152 * linearise(g) + 0.0722 * linearise(b)


def _contrast_ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la = _relative_luminance(a)
    lb = _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _darken(rgb: tuple[float, float, float], amount: float) -> tuple[float, float, float]:
    h, lit, s = colorsys.rgb_to_hls(*rgb)
    lit = max(0.0, lit - amount)
    return colorsys.hls_to_rgb(h, lit, s)


def _lighten(rgb: tuple[float, float, float], amount: float) -> tuple[float, float, float]:
    h, lit, s = colorsys.rgb_to_hls(*rgb)
    lit = min(1.0, lit + amount)
    return colorsys.hls_to_rgb(h, lit, s)


def _shift_hue(rgb: tuple[float, float, float], delta: float) -> tuple[float, float, float]:
    h, lit, s = colorsys.rgb_to_hls(*rgb)
    h = (h + delta) % 1.0
    return colorsys.hls_to_rgb(h, lit, s)


def selection_colors(icon_bg: str) -> tuple[str, str, str, str]:
    """Pick an intelligent selection palette given the status icon bg.

    Returns ``(tile_bg, border, title_fg, subtitle_fg)``. The result is
    always high-contrast against ``icon_bg`` *and* the white text the body
    of the tile uses, so we never end up with the previous
    near-illegible combinations (e.g. white text on a dark-grey selection
    sitting next to a yellow ``#9e6a03`` modified icon).

    The strategy:

    1. If ``icon_bg`` parses as a hex colour, blend the icon's hue into
       a darkened surface so the selection reads as a tinted
       continuation of the icon — but only when the icon isn't
       achromatic. This keeps "modified" rows warm, "added" rows
       greenish, "deleted" rows reddish, etc.
    2. Pick the title/subtitle foregrounds from a small palette of
       pre-validated, AA-grade combinations so the final render is
       always legible regardless of the tile's background.

    The return value is a deterministic function of ``icon_bg`` so the
    tests can pin it down.
    """
    rgb = _parse_hex(icon_bg)
    if rgb is None:
        return ("#1f2937", "#1f6feb", "#f0f6fc", "#8b949e")

    lum = _luminance(rgb)
    _, _, sat = colorsys.rgb_to_hls(*rgb)

    # Achromatic icons (greys) → fall back to a neutral selection
    # instead of a tinted one so we don't end up with a "muted
    # brown" selection that fights the surrounding surface.
    if sat < 0.08:
        tile_bg = _to_hex(_darken(rgb, 0.18))
        return (tile_bg, _to_hex(_lighten(rgb, 0.18)), "#f0f6fc", "#c9d1d9")

    # Bright icons: darken heavily so the white text wins.
    if lum > 0.45:
        base = _darken(rgb, 0.30)
    else:
        base = _darken(rgb, 0.10)
    tile_bg = _to_hex(base)

    # Border colour: a brighter, more saturated echo of the icon. We
    # nudge the hue by a small amount so it doesn't blur into the bg.
    border_rgb = _lighten(_shift_hue(rgb, 0.02), 0.15)
    border = _to_hex(border_rgb)

    # Title + subtitle. We always reach for a high-contrast
    # foreground; the only thing we vary is whether the subtitle leans
    # a touch toward the icon's hue so the row feels like part of a
    # set.
    title_fg = "#f0f6fc"
    sub_rgb = _shift_hue(rgb, -0.02) if lum > 0.45 else _shift_hue(rgb, 0.02)
    sub_h, _, sub_s = colorsys.rgb_to_hls(*sub_rgb)
    sub_lit = 0.72 if _luminance(sub_rgb) < 0.55 else 0.85
    sub_rgb = colorsys.hls_to_rgb(sub_h, sub_lit, sub_s)
    subtitle_fg = _to_hex(sub_rgb)
    # Final legibility check: if the title / bg contrast slipped below
    # WCAG AA for body text we fall back to a known-safe pair. This
    # matters for very-saturated bgs (e.g. pure green / red pills).
    if _contrast_ratio(base, _parse_hex(title_fg) or (0.0, 0.0, 0.0)) < 4.5:
        title_fg = "#ffffff"
        subtitle_fg = "#c9d1d9"
    return (tile_bg, border, title_fg, subtitle_fg)


# ---------------------------------------------------------------------------
# Individual tile widget
# ---------------------------------------------------------------------------


class FileTile(Static):
    """A single file row in the diff file list.

    Layout:

    * 3-cell-wide status icon on the left
    * Title row (filename + right-aligned ``+N -M`` stats)
    * Subtitle row (full path)
    """

    DEFAULT_CSS = """
    FileTile {
        width: 1fr;
        height: 3;
        padding: 0 1;
        background: $surface;
    }
    FileTile:hover {
        background: $boost;
    }
    FileTile.--selected {
        background: #1f2937;
    }
    FileTile .tile-row {
        width: 1fr;
        height: 3;
    }
    FileTile .tile-icon {
        width: 3;
        height: 3;
        content-align: center middle;
        text-align: center;
    }
    FileTile .tile-body {
        width: 1fr;
        height: 3;
        padding: 0 1;
    }
    FileTile .tile-title-row {
        width: 1fr;
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
        padding: 0 1;
        content-align: right middle;
    }
    FileTile .tile-subtitle {
        width: 1fr;
        height: 1;
        color: $text-muted;
        text-overflow: ellipsis;
    }
    FileTile .tile-stats-add {
        color: #3fb950;
        text-style: bold;
    }
    FileTile .tile-stats-del {
        color: #f85149;
        text-style: bold;
    }
    """

    selected = reactive(False)

    class Clicked(Message):
        def __init__(self, tile: "FileTile") -> None:
            super().__init__()
            self.tile = tile

    def __init__(self, spec: _FileTileSpec, **kwargs) -> None:
        super().__init__(**kwargs)
        self._spec = spec
        self._icon: Static | None = None
        self._title: Static | None = None
        self._subtitle: Static | None = None
        self._stats: Static | None = None
        self._selection_palette: tuple[str, str, str, str] = (
            "#1f2937",
            "#1f6feb",
            "#f0f6fc",
            "#8b949e",
        )

    def compose(self):
        with Horizontal(classes="tile-row"):
            yield Static(self._icon_text(), classes="tile-icon", markup=False)
            with Vertical(classes="tile-body"):
                with Horizontal(classes="tile-title-row"):
                    yield Static(self._spec.filename(), classes="tile-title")
                    yield Static(self._stats_text(), classes="tile-stats")
                yield Static(self._spec.subtitle(), classes="tile-subtitle")

    def on_mount(self) -> None:
        self._icon = self.query_one(".tile-icon")
        self._title = self.query_one(".tile-title")
        self._subtitle = self.query_one(".tile-subtitle")
        self._stats = self.query_one(".tile-stats")
        self._selection_palette = selection_colors(self._spec.icon_bg())
        self._refresh_icon()
        self._refresh_styles()

    def _icon_text(self) -> Text:
        letter = self._spec.icon_letter()
        bg = self._spec.icon_bg()
        fg = self._spec.icon_fg()
        text = Text(f" {letter} ", style=f"bold {fg} on {bg}")
        return text

    def _stats_text(self) -> Text:
        text = Text(justify="right")
        if self._spec.file.is_binary:
            text.append("[binary]", style="#8b949e")
            return text
        if self._spec.file.is_image:
            text.append("[image]", style="#8b949e")
            return text
        additions = self._spec.file.additions
        deletions = self._spec.file.deletions
        text.append(f"+{additions}", style="bold #3fb950")
        text.append(" ")
        text.append(f"-{deletions}", style="bold #f85149")
        return text

    def _refresh_icon(self) -> None:
        if self._icon is None:
            return
        icon_bg = self._spec.icon_bg()
        icon_fg = self._spec.icon_fg()
        self._icon.styles.background = icon_bg
        self._icon.styles.color = icon_fg

    def _refresh_styles(self) -> None:
        self.set_class(self.selected, "--selected")
        if self.selected:
            tile_bg, _border, title_fg, subtitle_fg = self._selection_palette
            self.styles.background = tile_bg
            if self._title is not None:
                self._title.styles.color = title_fg
            if self._subtitle is not None:
                self._subtitle.styles.color = subtitle_fg
        else:
            self.styles.background = None
            if self._title is not None:
                self._title.styles.color = None
            if self._subtitle is not None:
                self._subtitle.styles.color = None

    def watch_selected(self, _old: bool, _new: bool) -> None:
        self._refresh_styles()

    def set_selected(self, value: bool) -> None:
        self.selected = value

    def on_click(self, _event: MouseEvent) -> None:
        self.post_message(self.Clicked(self))

    @property
    def spec(self) -> _FileTileSpec:
        return self._spec

    @property
    def file(self) -> ChangedFile:
        return self._spec.file

    @property
    def selection_palette(self) -> tuple[str, str, str, str]:
        return self._selection_palette


# ---------------------------------------------------------------------------
# List container
# ---------------------------------------------------------------------------


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
        height: 3;
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
        self._repo_dir: str = ""
        self._pending_index: int | None = None
        self._pending_retry_count = 0
        self._PENDING_RETRY_LIMIT = 50
        self._SCROLL_RETRY_LIMIT = 10

    def set_files(self, files: list[ChangedFile], repo_dir: str = "") -> None:
        self._repo_dir = repo_dir
        self._specs = [_FileTileSpec(f, repo_dir) for f in files]
        self._suppress_watch = True
        self.clear()
        for spec in self._specs:
            tile = FileTile(spec)
            self.append(ListItem(tile, id=f"file-tile-{id(tile)}"))
        if self._specs:
            self._suppress_watch = True
            self._pending_index = 0
            self._pending_retry_count = 0
            self.call_after_refresh(self._apply_initial_selection)
        else:
            self._suppress_watch = False
            self._pending_index = None

    def _apply_initial_selection(self) -> None:
        pending = self._pending_index
        if pending is None:
            return
        if len(self._nodes) <= pending:
            self._pending_retry_count += 1
            if self._pending_retry_count > self._PENDING_RETRY_LIMIT:
                self._pending_index = None
                self._suppress_watch = False
                return
            self.call_after_refresh(self._apply_initial_selection)
            return
        self._pending_index = None
        self._pending_retry_count = 0
        self._suppress_watch = False
        self.index = pending

    def watch_index(self, old: int | None, new: int | None) -> None:
        if getattr(self, "_suppress_watch", False):
            if new is None or self._pending_index is None:
                return
            # An explicit selection arrived (keyboard, click, or a
            # caller assigning ``index``) while the deferred initial
            # selection from ``set_files`` was still queued. Honour the
            # explicit choice and drop the pending one; otherwise the
            # late ``_apply_initial_selection`` would clobber it back
            # to file 0 (this raced on slow CI runners).
            self._pending_index = None
            self._pending_retry_count = 0
            self._suppress_watch = False
        # Delegate to ListView's watch_index first so it can
        # ``scroll_to_widget`` and keep the highlighted tile in view
        # when the list overflows the available height.
        try:
            super().watch_index(old, new)
        except TypeError:
            # Some Textual versions pass different args; fall back
            # to no-op rather than blowing up.
            pass
        self._update_selection()
        self.post_message(self.FileSelected(self.selected_file()))
        self._ensure_index_visible(new)

    def _ensure_index_visible(self, index: int | None, attempt: int = 0) -> None:
        if index is None or index != self.index or not self._is_valid_index(index):
            return
        selected_widget = self._nodes[index]
        if selected_widget.region:
            self.scroll_to_widget(selected_widget, animate=False, immediate=True)
            return
        if attempt < self._SCROLL_RETRY_LIMIT:
            self.call_after_refresh(self._ensure_index_visible, index, attempt + 1)

    def _update_selection(self, attempt: int = 0) -> None:
        deferred = False
        for i, child in enumerate(self.children):
            if isinstance(child, ListItem):
                try:
                    tile = child.query_one(FileTile)
                except Exception:
                    # The ListItem hasn't mounted its FileTile yet
                    # (index was assigned in the same tick as
                    # ``set_files``). Re-apply once it has.
                    deferred = True
                    continue
                tile.set_selected(i == self.index)
        if deferred and attempt < self._SCROLL_RETRY_LIMIT:
            self.call_after_refresh(self._update_selection, attempt + 1)

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
    "selection_colors",
]
