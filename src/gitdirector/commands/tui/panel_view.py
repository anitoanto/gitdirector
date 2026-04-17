"""Panel view screen with grid of pane widgets embedding live terminals."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Static

from .panels import Panel, PanelStore
from .terminal_widget import TerminalWidget


class PaneWidget(Widget):
    """A single pane in the panel grid. Embeds a live terminal when a session is assigned."""

    DEFAULT_CSS = """
    PaneWidget {
        border: round $primary-darken-2;
        height: 1fr;
        width: 1fr;
        overflow: hidden;
    }
    PaneWidget.pane-focused {
        border: round $accent;
    }
    PaneWidget .pane-header {
        dock: top;
        height: 1;
        background: $primary-darken-3;
        color: $text;
        padding: 0 1;
    }
    PaneWidget.pane-focused .pane-header {
        background: $accent;
        color: $text;
    }
    PaneWidget .pane-empty {
        height: 1fr;
        align: center middle;
        content-align: center middle;
        padding: 1 2;
    }
    PaneWidget TerminalWidget {
        height: 1fr;
        width: 1fr;
    }
    """

    pane_focused = reactive(False)

    def __init__(
        self,
        pane_index: int,
        session_name: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.pane_index = pane_index
        self.session_name = session_name
        self._terminal: TerminalWidget | None = None

    def compose(self) -> ComposeResult:
        yield Static(
            self._build_header_text(),
            classes="pane-header",
            id=f"pane-header-{self.pane_index}",
        )
        if self.session_name:
            terminal = TerminalWidget(
                command=f"tmux attach-session -t {self.session_name}",
                id=f"pane-term-{self.pane_index}",
            )
            self._terminal = terminal
            yield terminal
        else:
            yield Static(
                self._empty_body_text(),
                classes="pane-empty",
                id=f"pane-empty-{self.pane_index}",
            )

    def on_mount(self) -> None:
        if self._terminal:
            self._terminal.start()

    def _build_header_text(self) -> str:
        num = f"[bold]{self.pane_index}[/bold]"
        if self.session_name:
            parts = self.session_name.split("/")
            if len(parts) >= 4:
                label = f"{parts[2]}/{parts[3]}"
            else:
                label = self.session_name
            return f" {num}  {label}  [dim]{self.pane_index}[/dim]"
        return f" {num}  [dim]empty · {self.pane_index}[/dim]"

    def _empty_body_text(self) -> str:
        return "[dim]No session assigned[/dim]\n\n[dim]ctrl+a[/dim] assign session"

    def update_session(self, session_name: str | None) -> None:
        old_name = self.session_name
        self.session_name = session_name

        if old_name == session_name:
            return

        if self._terminal:
            self._terminal.stop()
            self._terminal.remove()
            self._terminal = None

        try:
            empty = self.query_one(f"#pane-empty-{self.pane_index}")
            empty.remove()
        except NoMatches:
            pass

        try:
            header = self.query_one(f"#pane-header-{self.pane_index}", Static)
            header.update(self._build_header_text())
        except NoMatches:
            pass

        if session_name:
            terminal = TerminalWidget(
                command=f"tmux attach-session -t {session_name}",
                id=f"pane-term-{self.pane_index}",
            )
            self._terminal = terminal
            self.mount(terminal)
            terminal.start()
        else:
            empty = Static(
                self._empty_body_text(),
                classes="pane-empty",
                id=f"pane-empty-{self.pane_index}",
            )
            self.mount(empty)

    def focus_terminal(self) -> None:
        if self._terminal:
            self._terminal.focus()

    def stop_terminal(self) -> None:
        if self._terminal:
            self._terminal.stop()

    def watch_pane_focused(self, focused: bool) -> None:
        if focused:
            self.add_class("pane-focused")
            if self._terminal:
                self._terminal.focus()
        else:
            self.remove_class("pane-focused")


class PanelViewScreen(Screen[None]):
    """Full screen showing a panel's grid of panes with live terminals."""

    BINDINGS = [
        Binding("escape", "detach", "Detach", show=True, priority=True),
        Binding("ctrl+q", "detach", "Detach", show=False, priority=True),
        Binding("ctrl+1", "focus_pane(1)", "1", show=False, priority=True),
        Binding("ctrl+2", "focus_pane(2)", "2", show=False, priority=True),
        Binding("ctrl+3", "focus_pane(3)", "3", show=False, priority=True),
        Binding("ctrl+4", "focus_pane(4)", "4", show=False, priority=True),
        Binding("ctrl+5", "focus_pane(5)", "5", show=False, priority=True),
        Binding("ctrl+6", "focus_pane(6)", "6", show=False, priority=True),
        Binding("ctrl+7", "focus_pane(7)", "7", show=False, priority=True),
        Binding("ctrl+8", "focus_pane(8)", "8", show=False, priority=True),
        Binding("ctrl+9", "focus_pane(9)", "9", show=False, priority=True),
        Binding("ctrl+a", "assign_session", "Assign", show=True, priority=True),
        Binding("ctrl+x", "clear_pane", "Clear", show=True, priority=True),
        Binding("ctrl+n", "next_pane", "Next", show=False, priority=True),
        Binding("ctrl+p", "prev_pane", "Prev", show=False, priority=True),
    ]

    CSS = """
    PanelViewScreen {
        background: $surface;
    }
    #panel-view-header {
        dock: top;
        height: 1;
        background: $accent 30%;
        color: $text;
        padding: 0 2;
    }
    #panel-grid {
        layout: grid;
        grid-gutter: 0;
        height: 1fr;
        padding: 0;
    }
    #panel-status-bar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: white;
        padding: 0 2;
    }
    """

    def __init__(self, panel: Panel, store: PanelStore) -> None:
        super().__init__()
        self._panel = panel
        self._store = store
        self._focused_pane: int = 1
        self._pane_widgets: dict[int, PaneWidget] = {}

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold]{self._panel.name}[/bold]  [dim]{self._panel.layout_label}[/dim]"
            f"  [dim]esc[/dim] detach  [dim]ctrl+a[/dim] assign  [dim]ctrl+x[/dim] clear"
            f"  [dim]ctrl+n/p[/dim] navigate  [dim]ctrl+1-{self._panel.total_panes}[/dim] focus",
            id="panel-view-header",
        )
        grid = Container(id="panel-grid")
        yield grid
        yield Static("", id="panel-status-bar")
        yield Footer()

    def on_mount(self) -> None:
        grid = self.query_one("#panel-grid", Container)
        grid.styles.grid_size_columns = self._panel.cols
        grid.styles.grid_size_rows = self._panel.rows

        for i in range(1, self._panel.total_panes + 1):
            session_name = self._panel.panes.get(i)
            pane = PaneWidget(
                pane_index=i,
                session_name=session_name,
                id=f"pane-{i}",
            )
            self._pane_widgets[i] = pane
            grid.mount(pane)

        self._focus_pane(1)
        self._update_status()
        self._poll_timer = self.set_interval(5, self._check_orphans)

    def _focus_pane(self, index: int) -> None:
        if index < 1 or index > self._panel.total_panes:
            return
        old = self._pane_widgets.get(self._focused_pane)
        if old:
            old.pane_focused = False
        self._focused_pane = index
        new = self._pane_widgets.get(index)
        if new:
            new.pane_focused = True
            new.focus_terminal()
        self._update_status()

    def action_focus_pane(self, index: int) -> None:
        self._focus_pane(index)

    def action_next_pane(self) -> None:
        nxt = self._focused_pane + 1
        if nxt > self._panel.total_panes:
            nxt = 1
        self._focus_pane(nxt)

    def action_prev_pane(self) -> None:
        prv = self._focused_pane - 1
        if prv < 1:
            prv = self._panel.total_panes
        self._focus_pane(prv)

    def action_assign_session(self) -> None:
        pane = self._pane_widgets.get(self._focused_pane)
        if not pane:
            return
        self._open_session_selector(self._focused_pane, pane.session_name)

    def action_clear_pane(self) -> None:
        pane = self._pane_widgets.get(self._focused_pane)
        if not pane or not pane.session_name:
            return
        self._store.update_pane(self._panel.name, self._focused_pane, None)
        self._panel.panes[self._focused_pane] = None
        pane.update_session(None)
        self._update_status()

    def _open_session_selector(self, pane_index: int, current: str | None = None) -> None:
        from .screens import SelectSessionScreen

        self.app.push_screen(
            SelectSessionScreen(pane_index, current),
            callback=lambda result: self._handle_session_selection(pane_index, result),
        )

    def _handle_session_selection(self, pane_index: int, result: str | None) -> None:
        if result is None:
            return
        if result == "__clear__":
            self._store.update_pane(self._panel.name, pane_index, None)
            self._panel.panes[pane_index] = None
            pane = self._pane_widgets.get(pane_index)
            if pane:
                pane.update_session(None)
        else:
            self._store.update_pane(self._panel.name, pane_index, result)
            self._panel.panes[pane_index] = result
            pane = self._pane_widgets.get(pane_index)
            if pane:
                pane.update_session(result)
        self._update_status()
        self._focus_pane(pane_index)

    @work(thread=True, exclusive=True, group="panel_orphan_check")
    def _check_orphans(self) -> None:
        from ...integrations.tmux import _session_exists

        orphaned_indices: list[int] = []
        for idx, session in list(self._panel.panes.items()):
            if session and not _session_exists(session):
                orphaned_indices.append(idx)

        if orphaned_indices:
            for idx in orphaned_indices:
                self._panel.panes[idx] = None
            self._store.cleanup_orphans()
            for idx in orphaned_indices:
                pane = self._pane_widgets.get(idx)
                if pane:
                    self.app.call_from_thread(pane.update_session, None)
            self.app.call_from_thread(self._update_status)

    def action_detach(self) -> None:
        if hasattr(self, "_poll_timer"):
            self._poll_timer.stop()
        for pane in self._pane_widgets.values():
            pane.stop_terminal()
        self.dismiss(None)

    def _update_status(self) -> None:
        filled = self._panel.filled_panes
        total = self._panel.total_panes
        pane = self._pane_widgets.get(self._focused_pane)
        session_info = ""
        if pane and pane.session_name:
            session_info = f"  │  {pane.session_name}"
        try:
            self.query_one("#panel-status-bar", Static).update(
                f"Pane {self._focused_pane}/{total}  "
                f"({filled} assigned){session_info}"
                f"   ctrl+1-{total} focus  ctrl+a assign  ctrl+x clear  esc detach"
            )
        except NoMatches:
            pass
