"""Panel pane widget with an embedded live terminal."""

from __future__ import annotations

from collections.abc import Callable

from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from ...integrations.tmux.core import _panel_session_label
from ...integrations.tmux.panels import _embedded_tmux_attach_command
from ...ui_theme import DEFAULT_THEME_NAME, resolve_panel_theme
from .terminal_widget import TerminalWidget


class PaneWidget(Widget):
    """A single pane in the panel grid. Embeds a live terminal when a session is assigned."""

    DEFAULT_CSS = """
    PaneWidget {
        height: 1fr;
        width: 1fr;
        overflow: hidden;
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
        theme_name: str = DEFAULT_THEME_NAME,
        panel_name: str | None = None,
        closed: bool = False,
        on_session_closed: Callable[[int], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.pane_index = pane_index
        self.session_name = session_name
        self._theme_name = theme_name
        self._panel_theme = resolve_panel_theme(theme_name)
        self._panel_name = panel_name
        self._on_session_closed = on_session_closed
        self._terminal: TerminalWidget | None = None
        self._empty_state = "closed" if closed and session_name is None else "empty"
        self._apply_border_style(False)

    def _apply_border_style(self, focused: bool) -> None:
        border_type = "thick" if focused else "round"
        border_color = self._panel_theme.accent if focused else self._panel_theme.border_inactive
        self.styles.border = (border_type, border_color)

    def _session_command(self, session_name: str) -> str:
        return _embedded_tmux_attach_command(
            session_name,
            panel_name=self._panel_name,
            pane_index=self.pane_index,
        )

    def compose(self):
        yield Static(
            self._build_header_text(),
            classes="pane-header",
            id=f"pane-header-{self.pane_index}",
        )
        if self.session_name:
            terminal = TerminalWidget(
                command=self._session_command(self.session_name),
                id=f"pane-term-{self.pane_index}",
            )
            self._terminal = terminal
            yield terminal
        else:
            yield Static(
                self._body_text(),
                classes="pane-empty",
                id=f"pane-empty-{self.pane_index}",
            )

    def on_mount(self) -> None:
        if self._terminal:
            self._terminal.start()

    @property
    def session_slug(self) -> str | None:
        if not self.session_name:
            return None
        if self.session_name.startswith("gd/"):
            return self.session_name[3:]
        return self.session_name

    @property
    def session_label(self) -> str | None:
        return _panel_session_label(self.session_name)

    def _build_header_text(self) -> str:
        badge_style = (
            f"bold {self._panel_theme.badge_active_fg} on {self._panel_theme.badge_active_bg}"
        )
        label_style = f"{self._panel_theme.label_active_fg} on {self._panel_theme.label_active_bg}"
        empty_style = f"{self._panel_theme.empty_fg} on {self._panel_theme.empty_bg}"
        label = self.session_label
        if label:
            return f" [{badge_style}] {self.pane_index} [/] [{label_style}] {label} [/]"
        return f" [{badge_style}] {self.pane_index} [/] [{empty_style}] empty [/]"

    def _empty_body_text(self) -> str:
        return "[dim]No session assigned[/dim]\n\n[dim]ctrl+a[/dim] assign session"

    def _closed_body_text(self) -> str:
        return "\n[dim]SESSION CLOSED[/dim]\n\n[dim]ctrl+a[/dim] assign session"

    def _body_text(self) -> str:
        if self._empty_state == "closed":
            return self._closed_body_text()
        return self._empty_body_text()

    def update_session(self, session_name: str | None, *, closed: bool = False) -> None:
        new_empty_state = "closed" if session_name is None and closed else "empty"
        old_name = self.session_name
        old_empty_state = self._empty_state
        self.session_name = session_name
        self._empty_state = new_empty_state

        if old_name == session_name and old_empty_state == new_empty_state:
            return

        if self._terminal:
            self.stop_terminal()
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
                command=self._session_command(session_name),
                id=f"pane-term-{self.pane_index}",
            )
            self._terminal = terminal
            self.mount(terminal)
            terminal.start()
        else:
            empty = Static(
                self._body_text(),
                classes="pane-empty",
                id=f"pane-empty-{self.pane_index}",
            )
            self.mount(empty)

    def show_session_closed(self) -> None:
        self.update_session(None, closed=True)

    def on_terminal_widget_disconnected(self, event: TerminalWidget.Disconnected) -> None:
        event.stop()
        if self._on_session_closed is not None:
            self._on_session_closed(self.pane_index)
            return
        self.show_session_closed()

    def focus_terminal(self) -> None:
        if self._terminal:
            self._terminal.focus()

    def stop_terminal(self) -> None:
        if self._terminal:
            self._terminal.stop()

    def watch_pane_focused(self, focused: bool) -> None:
        self._apply_border_style(focused)
        if focused:
            self.add_class("pane-focused")
            if self._terminal:
                self._terminal.focus()
        else:
            self.remove_class("pane-focused")
