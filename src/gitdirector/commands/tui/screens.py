"""Modal screen classes for the TUI."""

from __future__ import annotations

import time
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import (
    Input,
    LoadingIndicator,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

from ...info import RepoInfoResult
from .constants import _MODAL_BINDINGS, _MODAL_CSS, _SORT_COLUMN_NAMES
from .panels import (
    Panel,
    get_create_panel_layouts,
    render_panel_layout_preview,
    resolve_panel_layout,
)


class ActionMenuScreen(ModalScreen[str]):
    """Modal popup with actions for the selected repository."""

    BINDINGS = _MODAL_BINDINGS

    CSS = (
        "ActionMenuScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(self, repo_name: str, repo_path: Path, branch: str | None = None) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path
        self.branch = branch

    def compose(self) -> ComposeResult:
        from ...integrations.tmux import list_repo_sessions

        sessions = list_repo_sessions(self.repo_name)

        with Vertical(id="menu-container"):
            yield Static(f"[bold white]{self.repo_name}[/bold white]", id="menu-title")
            yield Static(
                f"[dim]branch:[/dim] [cyan]{self.branch or '—'}[/cyan]",
                id="menu-branch",
            )
            items: list[Option] = [
                Option("[white]+[/white] [bold]TMUX Session[/bold]", id="new_session"),
            ]
            if sessions:
                items.append(
                    Option("", disabled=True),
                )
                count = len(sessions)
                label = "session" if count == 1 else "sessions"
                items.append(
                    Option(f"[dim]{count} active {label}[/dim]", disabled=True),
                )
                for s in sessions:
                    parts = s.split("/")
                    label = f"{parts[2]}/{parts[3]}" if len(parts) >= 4 else s
                    items.append(
                        Option(
                            f"[white]●[/white] [bold]{label}[/bold] [dim]{s}[/dim]",
                            id=f"attach:{s}",
                        )
                    )
            items.extend(
                [
                    Option("", disabled=True),
                    Option("[dim]Launch AI Agent[/dim]", disabled=True),
                    Option("[white]◆[/white] [bold]OpenCode[/bold]", id="agent:opencode"),
                    Option("[white]◆[/white] [bold]Claude Code[/bold]", id="agent:claude"),
                    Option("[white]◆[/white] [bold]GitHub Copilot[/bold]", id="agent:copilot"),
                    Option("[white]◆[/white] [bold]Codex[/bold]", id="agent:codex"),
                ]
            )
            if sessions:
                items.extend(
                    [
                        Option("", disabled=True),
                        Option(
                            "[white]✕[/white] [dim]Remove Session…[/dim]",
                            id="remove_session",
                        ),
                    ]
                )
            yield OptionList(*items, id="action-menu")
            yield Static("↑↓/jk select    \\[enter] confirm    \\[esc] close", id="menu-hint")

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


class RemoveSessionScreen(ModalScreen[str | None]):
    """Modal listing sessions available for removal."""

    BINDINGS = _MODAL_BINDINGS

    CSS = (
        "RemoveSessionScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(self, repo_name: str) -> None:
        super().__init__()
        self.repo_name = repo_name

    def compose(self) -> ComposeResult:
        from ...integrations.tmux import list_repo_sessions

        sessions = list_repo_sessions(self.repo_name)

        with Vertical(id="menu-container"):
            yield Static("[bold white]Select session to remove[/bold white]", id="menu-title")
            if sessions:
                options = [
                    Option(
                        f"[red]●[/red] [bold]"
                        f"{'/'.join(s.split('/')[2:]) if '/' in s else s}"
                        f"[/bold] [dim]{s}[/dim]",
                        id=s,
                    )
                    for s in sessions
                ]
                yield OptionList(*options, id="action-menu")
            else:
                yield Static("[dim]No active sessions[/dim]", id="menu-branch")
            yield Static("↑↓/jk select    \\[enter] confirm    \\[esc] cancel", id="menu-hint")

    def on_mount(self) -> None:
        menu = self.query("#action-menu")
        if menu:
            menu.first().focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        menu = self.query("#action-menu")
        if menu:
            menu.first().action_cursor_down()

    def action_cursor_up(self) -> None:
        menu = self.query("#action-menu")
        if menu:
            menu.first().action_cursor_up()


class ConfirmScreen(ModalScreen[bool]):
    """Simple yes/no confirmation dialog."""

    BINDINGS = _MODAL_BINDINGS

    CSS = (
        "ConfirmScreen { align: center middle; background: $panel 80%; hatch: right $primary 30%; }"
        + _MODAL_CSS
    )

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static(f"[bold white]{self.message}[/bold white]", id="menu-title")
            yield OptionList(
                Option("[dim]✗ No[/dim]", id="no"),
                Option("[white]✓[/white] [bold]Yes[/bold]", id="yes"),
                id="action-menu",
            )
            yield Static("↑↓/jk select    \\[enter] confirm    \\[esc] cancel", id="menu-hint")

    def on_mount(self) -> None:
        self.query_one("#action-menu", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id == "yes")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_cursor_down(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_up()


class SortMenuScreen(ModalScreen[tuple | None]):
    """Modal for selecting the sort column and direction."""

    BINDINGS = _MODAL_BINDINGS

    CSS = (
        "SortMenuScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(
        self, current_column: int, current_reverse: bool, column_names: dict[int, str] | None = None
    ) -> None:
        super().__init__()
        self._current_column = current_column
        self._current_reverse = current_reverse
        self._column_names = column_names or _SORT_COLUMN_NAMES

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static("[bold white]Sort by[/bold white]", id="menu-title")
            items: list[Option] = []
            for idx, name in self._column_names.items():
                if idx == self._current_column:
                    arrow = "▼" if self._current_reverse else "▲"
                    label = f"[cyan]● {name} {arrow}[/cyan]"
                else:
                    label = f"  {name}"
                items.append(Option(label, id=f"sort:{idx}"))
            yield OptionList(*items, id="action-menu")
            yield Static("↑↓/jk select    \\[enter] confirm    \\[esc] close", id="menu-hint")

    def on_mount(self) -> None:
        menu = self.query_one("#action-menu", OptionList)
        menu.focus()
        menu.highlighted = self._current_column

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        col_idx = int(event.option.id.split(":")[1])
        if col_idx == self._current_column:
            self.dismiss((col_idx, not self._current_reverse))
        else:
            self.dismiss((col_idx, False))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_up()


class AgentLoadingScreen(ModalScreen[None]):
    """Full-screen loading overlay shown while an agent initialises."""

    _POLL_INTERVAL = 0.1
    _MIN_WAIT = 1.0
    _MAX_WAIT = 15.0

    DEFAULT_CSS = """
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
    """

    def __init__(self, agent_cmd: str, session_name: str, ready_marker: Path) -> None:
        super().__init__()
        self._agent_cmd = agent_cmd
        self._session_name = session_name
        self._ready_marker = ready_marker
        self._dismissed = False
        self._start_time = 0.0

    def compose(self) -> ComposeResult:
        with Vertical(id="loading-container"):
            yield LoadingIndicator()
            yield Static(
                f"Launching [bold]{self._agent_cmd}[/bold]",
                id="loading-text",
            )
            yield Static("waiting for agent to initialize\u2026", id="loading-hint")

    def on_mount(self) -> None:
        self._start_time = time.monotonic()
        self._poll_timer = self.set_interval(self._POLL_INTERVAL, self._check_ready)
        self._timeout_timer = self.set_timer(self._MAX_WAIT, self._force_dismiss)
        self.call_after_refresh(self._check_ready)

    def _check_ready(self) -> None:
        if self._dismissed:
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
        self._poll_timer.stop()
        self._do_dismiss()

    def _do_dismiss(self) -> None:
        import subprocess
        import sys
        import termios

        from ...integrations.tmux import attach_tmux_session

        session_name = self._session_name
        pane_target = f"={session_name}:"
        try:
            self._ready_marker.unlink()
        except FileNotFoundError:
            pass

        app = self.app
        app._pause_session_status_tracking()

        try:
            with app.suspend():
                sys.stdout.write("\033[?1049h\033[H\033[2J\033[?25l")
                sys.stdout.flush()
                subprocess.run(["tmux", "send-keys", "-t", pane_target, "C-l", ""], check=False)
                subprocess.run(["tmux", "clear-history", "-t", pane_target], check=False)
                attach_tmux_session(session_name)
                sys.stdout.write("\033[?25h")
                sys.stdout.flush()
                try:
                    termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
                except (AttributeError, OSError):
                    pass
        finally:
            app._arm_resume_new_panel_guard(app._active_tab)
            app._resume_session_status_tracking()

        self.dismiss(None)


class RepoInfoScreen(ModalScreen[None]):
    """Modal popup showing repository file statistics."""

    BINDINGS = [_MODAL_BINDINGS[0]]

    CSS = (
        "RepoInfoScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }"
        """
    #info-container {
        width: 80;
        height: auto;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    #info-title {
        text-align: center;
        padding: 1 1 0 1;
        color: $text;
    }
    #info-path {
        text-align: center;
        padding: 0 1 1 1;
        color: $text-muted;
    }
    #info-loading {
        height: 3;
        padding: 1 0;
    }
    #info-stats {
        padding: 0 3;
        color: $text;
        text-align: center;
    }
    #info-table {
        padding: 1 3 0 3;
        color: $text;
        text-align: center;
    }
    #info-hint {
        text-align: center;
        padding: 1 1 1 1;
        color: $text-muted;
    }
    """
    )

    def __init__(self, repo_name: str, repo_path: Path) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path

    def compose(self) -> ComposeResult:
        with Vertical(id="info-container"):
            yield Static(
                f"[bold white]{self.repo_name}[/bold white]",
                id="info-title",
            )
            yield Static(
                f"[dim]{self.repo_path}[/dim]",
                id="info-path",
            )
            yield LoadingIndicator(id="info-loading")
            yield Static("", id="info-hint")

    def populate(self, result: RepoInfoResult) -> None:
        self.query_one("#info-loading", LoadingIndicator).remove()
        r = result
        stats = Static(
            f"[dim]Files[/dim]  [bold white]{r.total_files:,}[/bold white]    "
            f"[dim]Lines[/dim]  [bold white]{r.total_lines:,}[/bold white]\n"
            f"[dim]Tokens[/dim]  [bold white]{r.total_tokens:,}[/bold white]    "
            f"[dim]Max Depth[/dim]  [bold white]{r.max_depth}[/bold white]",
            id="info-stats",
        )
        hint = self.query_one("#info-hint", Static)
        hint.mount(stats, before=hint)
        if r.file_types:
            rows = (
                f"[dim]{'':>2}{'EXTENSION':<12} {'FILES':>6}   {'LINES':>8}"
                f"   {'TOKENS':>10}[/dim]\n"
            )
            for ft in r.file_types:
                lines_str = f"{ft.line_count:,}" if ft.line_count is not None else "-"
                tokens_str = f"{ft.token_count:,}" if ft.token_count is not None else "-"
                rows += (
                    f"[cyan]  {ft.extension:<12}[/cyan]"
                    f" [white]{ft.count:>6}[/white]"
                    f"   [dim]{lines_str:>8}[/dim]"
                    f"   [dim]{tokens_str:>10}[/dim]\n"
                )
            table = Static(rows.rstrip(), id="info-table")
            hint.mount(table, before=hint)
        hint.update("\\[esc] close")

    def action_cancel(self) -> None:
        self.dismiss(None)


def _render_grid_preview(rows: int, cols: int, layout_key: str | None = None) -> str:
    layout = resolve_panel_layout(layout_key, rows, cols)
    return render_panel_layout_preview(layout, cell_width=7, cell_height=1)


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

    CSS = (
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
        from ...integrations.tmux import list_all_gd_sessions

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
                    yield Input(placeholder="panel name…", id="panel-name-input")
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

    def _show_step(self, step: int) -> None:
        self._step = step
        self.query_one("#step-1").display = step == 1
        self.query_one("#step-2").display = step == 2
        if step == 1:
            hint = self._step_1_hint()
        else:
            name = self._current_panel_name() or "unnamed"
            layout = resolve_panel_layout(self._selected_layout_key)
            self.query_one("#step-2-subtitle", Static).update(
                self._step2_subtitle_markup(name, layout.layout_label)
            )
            self._update_step2_preview()
            self._update_slot_markers()
            self._update_session_markers()
            self._update_session_visibility()
            hint = self._step_2_hint()
        self.query_one("#create-panel-title", Static).update(self._step_title_markup())
        self.query_one("#create-panel-hint", Static).update(hint)

    def _go_to_step_2(self) -> None:
        name = self._current_panel_name()
        if not name:
            if not self._editing:
                self.query_one("#panel-name-input", Input).focus()
            return
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
        available_sessions = self._available_session_names()
        for pane_index in range(1, 10):
            if pane_index <= active and pane_index <= len(available_sessions):
                self._pane_assignments[pane_index] = available_sessions[pane_index - 1]
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


class PanelActionMenuScreen(ModalScreen[str]):
    """Modal popup with actions for the selected panel."""

    BINDINGS = _MODAL_BINDINGS

    CSS = (
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
        from ...integrations.tmux import make_panel_session_name

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

    CSS = (
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


class SelectSessionScreen(ModalScreen[str | None]):
    """Modal for selecting a tmux session to assign to a pane."""

    BINDINGS = _MODAL_BINDINGS

    CSS = (
        "SelectSessionScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(self, pane_index: int, current_session: str | None = None) -> None:
        super().__init__()
        self.pane_index = pane_index
        self.current_session = current_session

    def compose(self) -> ComposeResult:
        from ...integrations.tmux import list_all_gd_sessions

        sessions = list_all_gd_sessions()

        with Vertical(id="menu-container"):
            yield Static(
                f"[bold white]Assign Session to Pane {self.pane_index}[/bold white]",
                id="menu-title",
            )
            items: list[Option] = []
            if self.current_session:
                items.append(
                    Option("[red]✕[/red] [dim]Clear pane[/dim]", id="__clear__"),
                )
                items.append(Option("", disabled=True))
            if sessions:
                for entry in sessions:
                    sn = entry["session_name"]
                    repo = entry["repo"]
                    purpose = entry["purpose"]
                    current_marker = " [cyan]◄ current[/cyan]" if sn == self.current_session else ""
                    items.append(
                        Option(
                            f"[white]●[/white] [bold]{purpose}[/bold]"
                            f" [dim]{repo}[/dim]  {sn}{current_marker}",
                            id=sn,
                        )
                    )
            else:
                items.append(Option("[dim]No active sessions[/dim]", disabled=True))
            yield OptionList(*items, id="action-menu")
            yield Static("↑↓/jk select    \\[enter] confirm    \\[esc] cancel", id="menu-hint")

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
