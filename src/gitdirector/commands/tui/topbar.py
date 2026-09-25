"""The console's top bar: name, tab switcher, and version and clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.content import Content
from textual.events import Click, Resize
from textual.widgets import Static

_BRAND = "◆ GitDirector"
_BRAND_COMPACT = "◆"
# (tab id, label, compact label)
_TABS = (
    ("repos", "Repositories", "Repos"),
    ("sessions", "Sessions", "Sessions"),
    ("panels", "Panels", "Panels"),
)
_DIVIDER = "│"
# Columns taken by padding, dividers and the palette button, beside the text.
_BRAND_PADDING = 3
_TAB_PADDING = 4
_META_PADDING = 2
_PALETTE_WIDTH = 5


@dataclass(frozen=True)
class NavState:
    active: str
    repos: int = 0
    sessions: int = 0
    waiting: int = 0
    panels: int = 0
    attention: str = "yellow"


def _tab_markup(label: str, count: int, waiting: int, attention: str) -> str:
    markup = label
    if count:
        markup += f" [$text-muted]{count}[/]"
    if waiting:
        markup += f" [bold {attention}]●{waiting}[/]"
    return markup


class NavTab(Static):
    def __init__(self, tab_id: str) -> None:
        super().__init__(id=f"nav-{tab_id}", classes="nav-tab")
        self.tab_id = tab_id

    async def on_click(self, event: Click) -> None:
        event.stop()
        await self.app.run_action(f"tab_{self.tab_id}")


class PaletteButton(Static):
    async def on_click(self, event: Click) -> None:
        event.stop()
        await self.app.run_action("command_palette")


class TopBar(Horizontal):
    DEFAULT_CSS = """
    TopBar {
        dock: top;
        height: 3;
        background: $panel;
    }
    TopBar > Static {
        height: 100%;
        content-align-vertical: middle;
    }
    TopBar #brand {
        width: auto;
        padding: 0 2 0 1;
        color: $accent;
        text-style: bold;
    }
    TopBar .nav-tab {
        width: auto;
        padding: 0 2;
        color: $text-muted;
    }
    TopBar .nav-divider {
        width: 1;
        color: $foreground 15%;
    }
    TopBar .nav-tab:hover {
        background: $primary 12%;
        color: $text;
    }
    TopBar .nav-tab.-active {
        background: $primary 30%;
        color: $text;
        text-style: bold;
    }
    TopBar #top-spacer {
        width: 1fr;
    }
    TopBar #top-meta {
        width: auto;
        padding: 0 1;
        color: $text-muted;
    }
    TopBar #palette {
        width: 5;
        text-align: center;
        color: $text-muted;
    }
    TopBar #palette:hover {
        background: $primary 30%;
        color: $text;
    }
    """

    def __init__(self, version: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._version = version
        self._state = NavState(active="repos")
        self._shown: dict[str, str] = {}
        # Held rather than queried: the console reports state while the bar
        # is still composing and after its items are gone at shutdown.
        self._items: dict[str, Static] = {"brand": Static(_BRAND, id="brand")}
        self._items.update({f"nav-{tab_id}": NavTab(tab_id) for tab_id, *_ in _TABS})
        self._items["top-meta"] = Static(id="top-meta")

    def compose(self) -> ComposeResult:
        yield self._items["brand"]
        for index, (tab_id, *_) in enumerate(_TABS):
            if index:
                yield Static(_DIVIDER, classes="nav-divider")
            yield self._items[f"nav-{tab_id}"]
        yield Static(id="top-spacer")
        yield self._items["top-meta"]
        if self.app.ENABLE_COMMAND_PALETTE:
            yield PaletteButton("≡", id="palette")

    def on_mount(self) -> None:
        self._render_state()
        self.set_interval(1, self._render_state)

    def on_resize(self, event: Resize) -> None:
        self._render_state()

    def show(self, state: NavState) -> None:
        self._state = state
        self._render_state()

    def _texts(self, compact: bool) -> dict[str, str]:
        state = self._state
        counts = {"repos": state.repos, "sessions": state.sessions, "panels": state.panels}
        texts = {"brand": _BRAND_COMPACT if compact else _BRAND}
        for tab_id, label, short in _TABS:
            waiting = state.waiting if tab_id == "sessions" else 0
            texts[f"nav-{tab_id}"] = _tab_markup(
                short if compact else label, counts[tab_id], waiting, state.attention
            )
        clock = datetime.now().strftime("%H:%M")
        texts["top-meta"] = clock if compact else f"v{self._version}  {clock}"
        return texts

    def _fits(self, texts: dict[str, str]) -> bool:
        used = sum(Content.from_markup(text).cell_length for text in texts.values())
        used += _BRAND_PADDING + _META_PADDING
        used += len(_TABS) * _TAB_PADDING + len(_TABS) - 1
        if self.app.ENABLE_COMMAND_PALETTE:
            used += _PALETTE_WIDTH
        return used <= self.size.width

    def _render_state(self) -> None:
        texts = self._texts(compact=False)
        if self.size.width and not self._fits(texts):
            texts = self._texts(compact=True)
        for widget_id, text in texts.items():
            if self._shown.get(widget_id) != text:
                self._shown[widget_id] = text
                self._items[widget_id].update(text)
        for tab_id, *_ in _TABS:
            self._items[f"nav-{tab_id}"].set_class(tab_id == self._state.active, "-active")
