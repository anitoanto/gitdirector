"""Modal screens related to panels (create, reconfigure, rename, action menu, session loading)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Input, LoadingIndicator, OptionList, Static
from textual.widgets.option_list import Option

from ..constants import _MODAL_BINDINGS, _MODAL_CSS
from ..panels import (
    Panel,
    PanelStore,
    get_create_panel_layouts,
    render_panel_layout_preview,
    resolve_panel_layout,
)
from ..terminal_caps import strip_unsupported_css as _safe_css
from ._shared import ConfirmScreen, SortMenuScreen  # re-export for backward compat

logger = logging.getLogger(__name__)

__all__ = [
    "AgentLoadingScreen",
    "ConfirmScreen",  # re-exported for backward compat
    "CreatePanelScreen",
    "PanelActionMenuScreen",
    "RenamePanelScreen",
    "SortMenuScreen",  # re-exported for backward compat
    "_render_grid_preview",
]


def _render_grid_preview(rows: int, cols: int, layout_key: str | None = None) -> str:
    layout = resolve_panel_layout(layout_key, rows, cols)
    return render_panel_layout_preview(layout, cell_width=7, cell_height=1)


class PanelActionMenuScreen(ModalScreen[str]):
    """Modal popup with actions for the selected panel."""

    BINDINGS = _MODAL_BINDINGS

    CSS = _safe_css(
        "PanelActionMenuScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }"
        + _MODAL_CSS
        + """
    PanelActionMenuScreen #menu-container {
        width: 72;
        padding: 1 1;
    }
    PanelActionMenuScreen #menu-title {
        padding: 0 1 0 1;
    }
    PanelActionMenuScreen #menu-branch {
        padding: 0 1 1 1;
    }
    #panel-action-layout {
        height: auto;
        align: left top;
    }
    #panel-action-main {
        width: 1fr;
        height: auto;
    }
    #panel-preview-pane {
        width: 27;
        height: auto;
        padding: 0 0 0 1;
        align: center top;
    }
    PanelActionMenuScreen #action-menu {
        height: 8;
        padding: 0 1;
        margin: 0 0 1 0;
    }
    PanelActionMenuScreen #menu-hint {
        padding: 0 1 0 1;
    }
    #panel-layout-preview {
        width: auto;
        height: auto;
        color: $text;
    }
    """
    )

    def __init__(self, panel: Panel) -> None:
        super().__init__()
        self.panel = panel

    def compose(self) -> ComposeResult:
        from ....integrations.tmux.core import make_panel_session_name

        session_name = make_panel_session_name(self.panel.name)

        with Vertical(id="menu-container"):
            yield Static(f"[bold white]{self.panel.name}[/bold white]", id="menu-title")
            yield Static(f"[dim]{session_name}[/dim]", id="menu-branch")
            with Horizontal(id="panel-action-layout"):
                with Vertical(id="panel-action-main"):
                    yield OptionList(
                        Option("[white]▶[/white] [bold]Open[/bold]", id="open"),
                        Option("[white]↺[/white] [bold]Reconfigure[/bold]", id="reconfigure"),
                        Option("[white]✎[/white] [bold]Rename[/bold]", id="rename"),
                        Option("", disabled=True),
                        Option("[red]✕[/red] [bold]Delete[/bold]", id="delete"),
                        id="action-menu",
                    )
                    yield Static(
                        "↑↓/jk select    \\[enter] confirm    \\[esc] close",
                        id="menu-hint",
                    )
                with Vertical(id="panel-preview-pane"):
                    yield Static(
                        _render_grid_preview(
                            self.panel.rows,
                            self.panel.cols,
                            self.panel.layout_key,
                        ),
                        id="panel-layout-preview",
                    )

    def on_mount(self) -> None:
        self.query_one("#action-menu", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_up()


class RenamePanelScreen(ModalScreen[str | None]):
    """Modal for renaming a panel."""

    BINDINGS = _MODAL_BINDINGS

    CSS = _safe_css(
        "RenamePanelScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(self, current_name: str) -> None:
        super().__init__()
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static("[bold white]Rename Panel[/bold white]", id="menu-title")
            yield Static(f"[dim]Current: {self.current_name}[/dim]", id="menu-branch")
            yield Input(value=self.current_name, id="rename-input")
            yield Static("\\[enter] confirm    \\[esc] cancel", id="menu-hint")

    def on_mount(self) -> None:
        inp = self.query_one("#rename-input", Input)
        inp.focus()
        inp.action_end()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        new_name = event.value.strip()
        if new_name:
            self.dismiss(new_name)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        pass

    def action_cursor_up(self) -> None:
        pass


class AgentLoadingScreen(ModalScreen[None]):
    """Full-screen loading overlay shown while a tmux session initialises."""

    _POLL_INTERVAL = 0.1
    _MIN_WAIT = 0.2
    _MAX_WAIT = 15.0

    DEFAULT_CSS = _safe_css("""
    AgentLoadingScreen {
        align: center middle;
        background: $panel 80%;
        hatch: right $primary 30%;
    }
    #loading-container {
        width: 50%;
        height: auto;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    #loading-container LoadingIndicator {
        height: 3;
        color: $primary;
    }
    #loading-text {
        text-align: center;
        color: white;
        padding: 1 0 0 0;
    }
    #loading-hint {
        text-align: center;
        padding: 1 1 1 1;
        color: $text-muted;
    }
    """)

    def __init__(
        self,
        agent_cmd: str,
        session_name: str,
        ready_marker: Path | None = None,
        *,
        loading_hint: str = "waiting for agent to initialize\u2026",
        on_attach: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._agent_cmd = agent_cmd
        self._session_name = session_name
        self._ready_marker = ready_marker
        self._done_marker = Path(f"{ready_marker}.done") if ready_marker else None
        self._failed_marker = Path(f"{ready_marker}.failed") if ready_marker else None
        self._loading_hint = loading_hint
        self._on_attach = on_attach
        self._dismissed = False
        self._start_time = 0.0

    def compose(self) -> ComposeResult:
        with Vertical(id="loading-container"):
            yield LoadingIndicator()
            yield Static(
                f"Launching [bold]{self._agent_cmd}[/bold]",
                id="loading-text",
            )
            yield Static(self._loading_hint, id="loading-hint")

    def on_mount(self) -> None:
        self._start_time = time.monotonic()
        if self._ready_marker is None:
            self._poll_timer = None
            self._timeout_timer = self.set_timer(self._MIN_WAIT, self._force_dismiss)
            return
        self._poll_timer = self.set_interval(self._POLL_INTERVAL, self._check_ready)
        self._timeout_timer = self.set_timer(self._MAX_WAIT, self._force_dismiss)
        self.call_after_refresh(self._check_ready)

    def _check_ready(self) -> None:
        if self._dismissed:
            return
        if self._ready_marker is None:
            return
        if time.monotonic() - self._start_time < self._MIN_WAIT:
            return
        if not self._ready_marker.exists():
            return
        self._dismissed = True
        self._poll_timer.stop()
        self._timeout_timer.stop()
        self._do_dismiss()

    def _force_dismiss(self) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        if self._ready_marker is not None:
            self._poll_timer.stop()
        self._do_dismiss()

    def _do_dismiss(self) -> None:
        import os
        import sys
        import termios

        from ....integrations.tmux import attach_tmux_session

        if self._on_attach is not None:
            self._on_attach()
            self.dismiss(None)
            return

        session_name = self._session_name
        for marker in (self._ready_marker, self._done_marker, self._failed_marker):
            if marker is None:
                continue
            try:
                marker.unlink()
            except FileNotFoundError:
                pass

        app = self.app
        app._pause_session_status_tracking()

        try:
            try:
                with app.suspend():
                    entered_manual_alt_screen = False
                    try:
                        if not os.environ.get("TMUX"):
                            sys.stdout.write("\033[?1049h\033[H\033[2J\033[?25l")
                            sys.stdout.flush()
                            entered_manual_alt_screen = True
                        attach_tmux_session(session_name, skip_config_sync=True)
                    finally:
                        if entered_manual_alt_screen:
                            sys.stdout.write("\033[?25h")
                            sys.stdout.flush()
                        try:
                            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
                        except (AttributeError, OSError):
                            pass
            except Exception as exc:
                # Headless / agent environments can't actually attach to tmux.
                # We must still restore the terminal and dismiss the modal so
                # the user is not left looking at a stuck spinner.
                logger.warning("tmux attach failed (headless environment?): %s", exc)
                try:
                    app._update_status(f"tmux attach failed: {exc}")
                except Exception:
                    pass
                try:
                    sys.stdout.write("\033[?25h\033[?1049l")
                    sys.stdout.flush()
                except Exception:
                    pass
        finally:
            try:
                app._arm_resume_new_panel_guard(app._active_tab)
                app._resume_session_status_tracking()
            except Exception:
                logger.debug("Failed to resume session status tracking", exc_info=True)

        self.dismiss(None)


class CreatePanelScreen(ModalScreen[tuple[str, str, dict[int, str | None]] | None]):
    """Two-step modal: 1) name + layout, 2) assign sessions with preview."""

    AUTO_ASSIGN_OPTION_ID = "__auto_assign__"
    LAYOUT_PREVIEW_PLACEHOLDER = "[dim]Choose a layout to preview[/dim]"

    BINDINGS = [
        *_MODAL_BINDINGS,
        ("tab", "focus_next_field", "Tab next"),
        ("shift+tab", "focus_prev_field", "Tab prev"),
        ("ctrl+o", "submit", "Create panel"),
    ]

    CSS = _safe_css(
        "CreatePanelScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }"
        """
    #create-panel-container {
        width: 108;
        height: auto;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    #create-panel-title {
        text-align: center;
        padding: 1 1 0 1;
        color: $text;
    }
    /* -- Step 1 -- */
    #step-1 { height: auto; padding: 0; }
    #panel-name-label {
        padding: 1 0 0 1;
        margin: 0 0 1 0;
        color: $text-muted;
    }
    #panel-name-input {
        width: 50;
        height: 3;
        margin: 0 0 1 1;
    }
    #panel-name-value {
        padding: 0 0 1 1;
        color: $text;
    }
    #step-1-columns {
        height: auto;
        padding: 0;
    }
    #step-1-left {
        width: 56;
        height: auto;
        padding: 0 1 0 0;
    }
    #step-1-right {
        width: 1fr;
        height: auto;
        padding: 0 0 0 1;
        align: center top;
    }
    /* -- Step 2 -- */
    #step-2 { height: auto; padding: 0; display: none; }
    #step-2-subtitle {
        text-align: center;
        padding: 0 1 1 1;
        color: $text-muted;
    }
    #step-2-columns { height: auto; padding: 0; }
    #step-2-left {
        width: 34;
        height: auto;
        padding: 0 1 0 0;
    }
    #step-2-right {
        width: 1fr;
        height: auto;
        padding: 0 0 0 1;
    }
    #grid-preview-2 {
        padding: 0;
        text-align: center;
        color: $text-muted;
    }
    #pane-session-placeholder {
        display: none;
        height: auto;
        padding: 1 1 0 0;
        color: $text-muted;
        content-align: center middle;
    }
    /* -- Shared -- */
    .section-label {
        padding: 0;
        color: $text-muted;
    }
    #layout-menu,
    #pane-slot-menu,
    #pane-session-menu {
        width: 100%;
        height: auto;
        border: none;
        padding: 0;
        margin: 0;
    }
    #layout-menu { max-height: 12; }
    #pane-slot-menu { max-height: 14; }
    #pane-session-menu {
        height: auto;
        max-height: 14;
    }
    #grid-preview {
        padding: 0;
        text-align: center;
        color: $text;
    }
    #create-panel-hint {
        text-align: center;
        padding: 1 1 0 1;
        color: $text-muted;
    }
    """
    )

    def __init__(
        self,
        panel_name: str | None = None,
        initial_layout_key: str | None = None,
        initial_panes: dict[int, str | None] | None = None,
        *,
        editing: bool = False,
    ) -> None:
        super().__init__()
        from ....integrations.tmux import list_all_gd_sessions

        self._step = 1
        self._editing = editing
        self._panel_name = (panel_name or "").strip()
        if self._editing and not self._panel_name:
            raise ValueError("Editing a panel requires a panel name")
        if self._editing and not initial_layout_key:
            raise ValueError("Editing a panel requires a layout key")
        self._layout_highlight_enabled = False
        self._selected_layout_key: str | None = initial_layout_key
        self._selected_pane_index = 1
        self._current_step2_field = "panes"
        self._validation_message: str | None = None
        self._session_entries = list_all_gd_sessions()
        self._session_option_ids = ["__clear__"] + [
            entry["session_name"] for entry in self._session_entries
        ]
        self._pane_assignments: dict[int, str | None] = {i: None for i in range(1, 10)}
        if initial_panes:
            for pane_index, session_name in initial_panes.items():
                if 1 <= pane_index <= 9:
                    self._pane_assignments[pane_index] = session_name or None
        self._clear_unavailable_assignments()

    def _clear_unavailable_assignments(self) -> None:
        available_sessions = set(self._session_option_ids[1:])
        for pane_index, session_name in self._pane_assignments.items():
            if session_name and session_name not in available_sessions:
                self._pane_assignments[pane_index] = None

    def compose(self) -> ComposeResult:
        with Vertical(id="create-panel-container"):
            yield Static(self._step_title_markup(), id="create-panel-title")
            with Vertical(id="step-1"):
                if self._editing:
                    yield Static("[dim]Panel[/dim]", id="panel-name-label")
                    yield Static(
                        f"[bold white]{self._panel_name}[/bold white]", id="panel-name-value"
                    )
                else:
                    yield Static("[dim]Name[/dim]", id="panel-name-label")
                    yield Input(placeholder="panel name...", id="panel-name-input")
                with Horizontal(id="step-1-columns"):
                    with Vertical(id="step-1-left"):
                        yield Static("[dim]Layout[/dim]", classes="section-label")
                        items = []
                        for layout in get_create_panel_layouts():
                            marker = (
                                "[cyan]● [/cyan]"
                                if layout.key == self._selected_layout_key
                                else "  "
                            )
                            items.append(
                                Option(
                                    f"{marker}{layout.menu_display_label}",
                                    id=f"layout:{layout.key}",
                                )
                            )
                        yield OptionList(*items, id="layout-menu")
                    with Vertical(id="step-1-right"):
                        yield Static("[dim]Preview[/dim]", classes="section-label")
                        yield Static(
                            self._layout_preview_markup(self._selected_layout_key),
                            id="grid-preview",
                        )
            with Vertical(id="step-2"):
                yield Static("", id="step-2-subtitle")
                with Horizontal(id="step-2-columns"):
                    with Vertical(id="step-2-left"):
                        yield Static("[dim]Pane slots[/dim]", classes="section-label")
                        yield OptionList(*self._slot_options(), id="pane-slot-menu")
                        yield Static(
                            self._layout_preview_markup(self._selected_layout_key),
                            id="grid-preview-2",
                        )
                    with Vertical(id="step-2-right"):
                        yield Static(
                            "[dim]Session for selected pane[/dim]",
                            id="pane-sessions-label",
                        )
                        yield OptionList(*self._session_options(), id="pane-session-menu")
                        yield Static(
                            "[dim]Inactive pane[/dim]",
                            id="pane-session-placeholder",
                        )
            yield Static("", id="create-panel-hint")

    def on_mount(self) -> None:
        layout_menu = self.query_one("#layout-menu", OptionList)
        if self._selected_layout_key:
            layout_menu.highlighted = next(
                index
                for index, layout in enumerate(get_create_panel_layouts())
                if layout.key == self._selected_layout_key
            )
        else:
            layout_menu.highlighted = None
        self.query_one("#pane-slot-menu", OptionList).highlighted = 0
        self._sync_session_menu_highlight()
        if self._editing:
            self._show_step(2)
            slot_menu = self.query_one("#pane-slot-menu", OptionList)
            slot_menu.highlighted = 1 if self._active_pane_count() > 0 else 0
            slot_menu.focus()
        else:
            self._show_step(1)
            self.query_one("#panel-name-input", Input).focus()
        self.call_after_refresh(self._enable_layout_highlight)

    def _enable_layout_highlight(self) -> None:
        self._layout_highlight_enabled = True

    def _current_panel_name(self) -> str:
        if self._editing:
            return self._panel_name
        return self.query_one("#panel-name-input", Input).value.strip()

    def _step_title_markup(self) -> str:
        if self._editing:
            return "[bold white]Reconfigure Panel[/bold white]"
        if self._step == 1:
            return "[bold white]Create Panel[/bold white]"
        return "[bold white]Configure Panes[/bold white]"

    def _step_1_hint(self) -> str:
        if self._editing:
            return "↑↓/jk navigate    \\[enter] next: adjust panes    \\[esc] cancel"
        return (
            "↑↓/jk navigate"
            "    \\[tab] switch fields"
            "    \\[enter] next: assign sessions"
            "    \\[esc] cancel"
        )

    def _step_2_hint(self) -> str:
        verb = "save and open" if self._editing else "create and open"
        return f"↑↓/jk navigate    \\[tab] switch lists    \\[ctrl+o] {verb}    \\[esc] back"

    @staticmethod
    def validate_new_panel_name(
        panel_store: PanelStore,
        name: str,
        *,
        current_name: str | None = None,
    ) -> str | None:
        from ....integrations.tmux.core import _session_exists, make_panel_session_name

        if panel_store.get(name) and name != current_name:
            return f"Panel '{name}' already exists"

        session_name = make_panel_session_name(name)
        if any(
            panel.name != current_name and make_panel_session_name(panel.name) == session_name
            for panel in panel_store.panels
        ):
            return f"Panel '{name}' conflicts with tmux session name '{session_name}'"

        if _session_exists(session_name) and (
            current_name is None or make_panel_session_name(current_name) != session_name
        ):
            return f"TMUX session '{session_name}' already exists"

        return None

    def _validate_current_panel_name(self, name: str) -> str | None:
        if self._editing:
            return None
        panel_store = getattr(self.app, "_panel_store", None)
        if panel_store is None:
            return None
        return self.validate_new_panel_name(panel_store, name)

    def _hint_markup(self, hint: str) -> str:
        if not self._validation_message:
            return hint
        return f"[red]{escape(self._validation_message)}[/red]\n{hint}"

    def _update_hint(self) -> None:
        hint = self._step_1_hint() if self._step == 1 else self._step_2_hint()
        self.query_one("#create-panel-hint", Static).update(self._hint_markup(hint))

    def _set_validation_message(self, message: str) -> None:
        self._validation_message = message
        self._update_hint()

    def _clear_validation_message(self) -> None:
        if not self._validation_message:
            return
        self._validation_message = None
        self._update_hint()

    def _show_step(self, step: int) -> None:
        self._step = step
        self.query_one("#step-1").display = step == 1
        self.query_one("#step-2").display = step == 2
        if step != 1:
            name = self._current_panel_name() or "unnamed"
            layout = resolve_panel_layout(self._selected_layout_key)
            self.query_one("#step-2-subtitle", Static).update(
                self._step2_subtitle_markup(name, layout.layout_label)
            )
            self._update_step2_preview()
            self._update_slot_markers()
            self._update_session_markers()
            self._update_session_visibility()
        self.query_one("#create-panel-title", Static).update(self._step_title_markup())
        self._update_hint()

    def _go_to_step_2(self) -> None:
        name = self._current_panel_name()
        if not name:
            if not self._editing:
                self.query_one("#panel-name-input", Input).focus()
            return
        validation_message = self._validate_current_panel_name(name)
        if validation_message:
            self._set_validation_message(validation_message)
            if not self._editing:
                self.query_one("#panel-name-input", Input).focus()
            return
        self._clear_validation_message()
        if not self._selected_layout_key:
            self._focus_layout_menu()
            return
        self._selected_pane_index = 1
        self._current_step2_field = "panes"
        self._show_step(2)
        slot_menu = self.query_one("#pane-slot-menu", OptionList)
        slot_menu.highlighted = 1 if self._editing else 0
        slot_menu.focus()

    def _focus_layout_menu(self) -> None:
        layout_menu = self.query_one("#layout-menu", OptionList)
        layout_menu.focus()
        if not self._selected_layout_key:
            self._apply_layout(get_create_panel_layouts()[0].key)
        highlighted = next(
            index
            for index, layout in enumerate(get_create_panel_layouts())
            if layout.key == self._selected_layout_key
        )
        layout_menu.highlighted = highlighted

    def _active_pane_count(self) -> int:
        if not self._selected_layout_key:
            return 0
        return resolve_panel_layout(self._selected_layout_key).total_panes

    def _pane_is_active(self, pane_index: int | None = None) -> bool:
        idx = self._selected_pane_index if pane_index is None else pane_index
        return idx <= self._active_pane_count()

    def _step2_fields(self) -> list[str]:
        fields = ["panes"]
        if self._pane_is_active():
            fields.append("sessions")
        return fields

    def _select_pane(self, pane_index: int) -> None:
        self._selected_pane_index = pane_index
        self._update_slot_markers()
        self._update_session_markers()
        self._sync_session_menu_highlight()
        self._update_session_visibility()

    @staticmethod
    def _step2_subtitle_markup(name: str, layout_label: str) -> str:
        return f'[bold white]"{name}"[/bold white]    [dim]{layout_label}[/dim]'

    @classmethod
    def _layout_preview_markup(cls, layout_key: str | None) -> str:
        if not layout_key:
            return cls.LAYOUT_PREVIEW_PLACEHOLDER
        layout = resolve_panel_layout(layout_key)
        return _render_grid_preview(layout.rows, layout.cols, layout.key)

    def _available_session_names(self) -> list[str]:
        available_sessions: list[str] = []
        seen: set[str] = set()
        for entry in self._session_entries:
            session_name = entry["session_name"]
            if session_name in seen:
                continue
            seen.add(session_name)
            available_sessions.append(session_name)
        return available_sessions

    def _auto_assign_panes(self) -> None:
        active = self._active_pane_count()
        available_sessions = [
            session_name
            for session_name in self._available_session_names()
            if session_name not in {self._pane_assignments[i] for i in range(1, active + 1)}
        ]
        for pane_index in range(1, 10):
            if pane_index > active:
                self._pane_assignments[pane_index] = None
                continue
            if self._pane_assignments[pane_index] is not None:
                continue
            if available_sessions:
                self._pane_assignments[pane_index] = available_sessions.pop(0)
            else:
                self._pane_assignments[pane_index] = None
        self._selected_pane_index = 1
        self._current_step2_field = "panes"
        self._update_slot_markers()
        self._update_session_markers()
        self._sync_session_menu_highlight()
        self._update_session_visibility()
        slot_menu = self.query_one("#pane-slot-menu", OptionList)
        slot_menu.highlighted = 1
        self._focus_step2_field()

    def _session_summary(self, session_name: str | None) -> str:
        if not session_name:
            return "[dim]unassigned[/dim]"
        parts = session_name.split("/")
        if len(parts) >= 4:
            return f"[bold]{parts[2]}[/bold] [dim]{parts[1]}[/dim]"
        return session_name

    def _slot_options(self) -> list[Option]:
        options: list[Option] = [
            Option(
                "[bold]Auto[/bold]  [dim]assign available sessions[/dim]",
                id=self.AUTO_ASSIGN_OPTION_ID,
            )
        ]
        active = self._active_pane_count()
        for pane_index in range(1, 10):
            marker = "[cyan]● [/cyan]" if pane_index == self._selected_pane_index else "  "
            if pane_index <= active:
                prompt = (
                    f"{marker}[bold]{pane_index}[/bold]  "
                    f"{self._session_summary(self._pane_assignments[pane_index])}"
                )
            else:
                prompt = f"{marker}[dim]{pane_index} inactive[/dim]"
            options.append(Option(prompt, id=f"pane:{pane_index}"))
        return options

    def _session_options(self) -> list[Option]:
        current = self._pane_assignments.get(self._selected_pane_index)
        options = [
            Option(
                ("[cyan]● [/cyan]" if current is None else "  ") + "[dim]Unassigned[/dim]",
                id="__clear__",
            )
        ]
        if not self._session_entries:
            options.append(
                Option(
                    "[dim]No active sessions[/dim]",
                    id="__empty__",
                    disabled=True,
                )
            )
            return options
        for entry in self._session_entries:
            sn = entry["session_name"]
            marker = "[cyan]● [/cyan]" if sn == current else "  "
            options.append(
                Option(
                    f"{marker}[bold]{entry['purpose']}[/bold] [dim]{entry['repo']}[/dim]  {sn}",
                    id=sn,
                )
            )
        return options

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "panel-name-input":
            self._go_to_step_2()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "panel-name-input":
            self._clear_validation_message()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "layout-menu":
            if not self._layout_highlight_enabled:
                return
            if self.focused is not event.option_list:
                return
            self._apply_layout(event.option.id.split(":", 1)[1])
        elif event.option_list.id == "pane-slot-menu":
            if event.option.id == self.AUTO_ASSIGN_OPTION_ID:
                return
            pane_index = int(event.option.id.split(":", 1)[1])
            self._select_pane(pane_index)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "layout-menu":
            self._apply_layout(event.option.id.split(":", 1)[1])
            self._go_to_step_2()
            return

        if event.option_list.id == "pane-slot-menu":
            if event.option.id == self.AUTO_ASSIGN_OPTION_ID:
                self._auto_assign_panes()
                return
            pane_index = int(event.option.id.split(":", 1)[1])
            self._select_pane(pane_index)
            if self._pane_is_active():
                self._current_step2_field = "sessions"
                self._focus_step2_field()
            return

        if event.option_list.id == "pane-session-menu":
            if self._selected_pane_index > self._active_pane_count():
                return
            self._pane_assignments[self._selected_pane_index] = (
                None if event.option.id == "__clear__" else event.option.id
            )
            self._update_slot_markers()
            self._update_session_markers()
            self._current_step2_field = "panes"
            self._focus_step2_field()

    def _apply_layout(self, layout_key: str | int, cols: int | None = None) -> None:
        if isinstance(layout_key, int):
            layout = resolve_panel_layout(rows=layout_key, cols=cols)
        else:
            layout = resolve_panel_layout(layout_key)
        self._selected_layout_key = layout.key
        active = self._active_pane_count()
        for pane_index in range(active + 1, 10):
            self._pane_assignments[pane_index] = None
        if self._selected_pane_index > active:
            self._selected_pane_index = active
        self._update_preview()
        self._update_layout_markers()

    def _update_preview(self) -> None:
        self.query_one("#grid-preview", Static).update(
            self._layout_preview_markup(self._selected_layout_key)
        )

    def _update_step2_preview(self) -> None:
        self.query_one("#grid-preview-2", Static).update(
            self._layout_preview_markup(self._selected_layout_key)
        )

    def _update_layout_markers(self) -> None:
        menu = self.query_one("#layout-menu", OptionList)
        for layout in get_create_panel_layouts():
            oid = f"layout:{layout.key}"
            if layout.key == self._selected_layout_key:
                menu.replace_option_prompt(oid, f"[cyan]● [/cyan]{layout.menu_display_label}")
            else:
                menu.replace_option_prompt(oid, f"  {layout.menu_display_label}")

    def _update_slot_markers(self) -> None:
        menu = self.query_one("#pane-slot-menu", OptionList)
        active = self._active_pane_count()
        for pane_index in range(1, 10):
            marker = "[cyan]● [/cyan]" if pane_index == self._selected_pane_index else "  "
            if pane_index <= active:
                prompt = (
                    f"{marker}[bold]{pane_index}[/bold]  "
                    f"{self._session_summary(self._pane_assignments[pane_index])}"
                )
            else:
                prompt = f"{marker}[dim]{pane_index} inactive[/dim]"
            menu.replace_option_prompt(f"pane:{pane_index}", prompt)

    def _update_session_markers(self) -> None:
        try:
            menu = self.query_one("#pane-session-menu", OptionList)
        except NoMatches:
            return
        current = self._pane_assignments.get(self._selected_pane_index)
        clear_prompt = ("[cyan]● [/cyan]" if current is None else "  ") + "[dim]Unassigned[/dim]"
        menu.replace_option_prompt("__clear__", clear_prompt)
        for entry in self._session_entries:
            sn = entry["session_name"]
            marker = "[cyan]● [/cyan]" if sn == current else "  "
            menu.replace_option_prompt(
                sn,
                f"{marker}[bold]{entry['purpose']}[/bold] [dim]{entry['repo']}[/dim]  {sn}",
            )

    def _update_session_visibility(self) -> None:
        is_active = self._pane_is_active()
        self.query_one("#pane-sessions-label", Static).display = is_active
        self.query_one("#pane-session-menu", OptionList).display = is_active
        placeholder = self.query_one("#pane-session-placeholder", Static)
        placeholder.display = not is_active
        if not is_active:
            placeholder.update(
                "[dim]This pane is inactive for the current layout.[/dim]\n\n"
                "[dim]Choose one of the highlighted pane slots"
                " to assign a session.[/dim]"
            )

    def _sync_session_menu_highlight(self) -> None:
        current = self._pane_assignments.get(self._selected_pane_index)
        oid = current if current in self._session_option_ids else "__clear__"
        self.query_one(
            "#pane-session-menu", OptionList
        ).highlighted = self._session_option_ids.index(oid)

    def _commit_highlighted_slot_selection(self) -> None:
        focused = self.focused
        slot_menu_focused = focused is not None and focused.id == "pane-slot-menu"
        if self._current_step2_field != "panes" and not slot_menu_focused:
            return

        menu = self.query_one("#pane-slot-menu", OptionList)
        highlighted = menu.highlighted
        if highlighted is None:
            return

        option = menu.get_option_at_index(highlighted)
        if option.id == self.AUTO_ASSIGN_OPTION_ID:
            self._auto_assign_panes()

    def _commit_highlighted_session_selection(self) -> None:
        focused = self.focused
        session_menu_focused = focused is not None and focused.id == "pane-session-menu"
        if not self._pane_is_active() or (
            self._current_step2_field != "sessions" and not session_menu_focused
        ):
            return

        menu = self.query_one("#pane-session-menu", OptionList)
        highlighted = menu.highlighted
        if highlighted is None:
            return

        option = menu.get_option_at_index(highlighted)
        if option.id == "__empty__":
            return

        self._pane_assignments[self._selected_pane_index] = (
            None if option.id == "__clear__" else option.id
        )
        self._update_slot_markers()
        self._update_session_markers()

    def _focus_step2_field(self) -> None:
        field = self._current_step2_field
        if field == "panes":
            self.query_one("#pane-slot-menu", OptionList).focus()
        elif field == "sessions" and self._pane_is_active():
            self.query_one("#pane-session-menu", OptionList).focus()
        else:
            self._current_step2_field = "panes"
            self.query_one("#pane-slot-menu", OptionList).focus()

    def action_focus_next_field(self) -> None:
        if self._step == 1:
            if self._editing:
                self._focus_layout_menu()
                return
            focused = self.focused
            if focused and focused.id == "panel-name-input":
                self._focus_layout_menu()
            else:
                self.query_one("#panel-name-input", Input).focus()
        else:
            fields = self._step2_fields()
            idx = (
                fields.index(self._current_step2_field)
                if self._current_step2_field in fields
                else -1
            )
            self._current_step2_field = fields[(idx + 1) % len(fields)]
            self._focus_step2_field()

    def action_focus_prev_field(self) -> None:
        if self._step == 1:
            if self._editing:
                self._focus_layout_menu()
                return
            self.action_focus_next_field()
        else:
            fields = self._step2_fields()
            idx = (
                fields.index(self._current_step2_field)
                if self._current_step2_field in fields
                else 0
            )
            self._current_step2_field = fields[(idx - 1) % len(fields)]
            self._focus_step2_field()

    def action_go_back(self) -> None:
        if self._step == 2:
            self._show_step(1)
            self._focus_layout_menu()

    def action_submit(self) -> None:
        if self._step == 1:
            self._go_to_step_2()
            return
        self._do_submit()

    def _do_submit(self) -> None:
        name = self._current_panel_name()
        if not name:
            self._show_step(1)
            if not self._editing:
                self.query_one("#panel-name-input", Input).focus()
            return
        layout_key = self._selected_layout_key
        if not layout_key:
            self._show_step(1)
            self.query_one("#layout-menu", OptionList).focus()
            return
        validation_message = self._validate_current_panel_name(name)
        if validation_message:
            self._show_step(1)
            self._set_validation_message(validation_message)
            if not self._editing:
                self.query_one("#panel-name-input", Input).focus()
            return
        self._clear_validation_message()
        self._commit_highlighted_slot_selection()
        self._commit_highlighted_session_selection()
        total_panes = self._active_pane_count()
        panes = {
            pane_index: self._pane_assignments[pane_index]
            for pane_index in range(1, total_panes + 1)
        }
        self.dismiss((name, layout_key, panes))

    def action_cancel(self) -> None:
        if self._step == 2:
            self._show_step(1)
            self._focus_layout_menu()
            return
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        if self._step == 1:
            try:
                self.query_one("#layout-menu", OptionList).action_cursor_down()
            except NoMatches:
                pass
        else:
            if self._current_step2_field == "panes":
                self.query_one("#pane-slot-menu", OptionList).action_cursor_down()
            elif self._current_step2_field == "sessions":
                self.query_one("#pane-session-menu", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        if self._step == 1:
            try:
                self.query_one("#layout-menu", OptionList).action_cursor_up()
            except NoMatches:
                pass
        else:
            if self._current_step2_field == "panes":
                self.query_one("#pane-slot-menu", OptionList).action_cursor_up()
            elif self._current_step2_field == "sessions":
                self.query_one("#pane-session-menu", OptionList).action_cursor_up()
