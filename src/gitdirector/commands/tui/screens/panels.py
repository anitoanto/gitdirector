"""Modal screens related to panels (create, reconfigure, rename, action menu, session loading)."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, LoadingIndicator, OptionList, Static
from textual.widgets.option_list import Option

from ..constants import _MODAL_BINDINGS, _MODAL_CSS
from ..panels import (
    DEFAULT_PANEL_LAYOUT_KEY,
    Panel,
    PanelStore,
    get_create_panel_layouts,
    render_panel_layout_preview,
    render_panel_layout_text,
    resolve_panel_layout,
)
from ..terminal_caps import strip_unsupported_css as _safe_css

__all__ = [
    "AgentLoadingScreen",
    "CreatePanelScreen",
    "PanelActionMenuScreen",
    "RenamePanelScreen",
    "_render_grid_preview",
]


def _render_grid_preview(rows: int, cols: int, layout_key: str | None = None) -> str:
    layout = resolve_panel_layout(layout_key, rows, cols)
    return render_panel_layout_preview(layout, cell_width=7, cell_height=1)


def _palette(app):
    from ..constants import resolve_table_palette

    palette = getattr(app, "_palette", None)
    return palette if palette is not None else resolve_table_palette(app.get_css_variables())


def _session_labels() -> dict[str, tuple[str, str]]:
    from ....integrations.tmux import list_all_gd_sessions

    try:
        entries = list_all_gd_sessions()
    except Exception:
        return {}
    return {
        entry["session_name"]: (
            f"{entry['purpose']}/{entry['session_name'].rsplit('/', 1)[-1]}",
            entry["repo"],
        )
        for entry in entries
    }


class PanelActionMenuScreen(ModalScreen[str]):
    """What a panel shows, and what can be done with it."""

    BINDINGS = _MODAL_BINDINGS

    CSS = _safe_css(
        "PanelActionMenuScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }"
        + _MODAL_CSS
        + """
    PanelActionMenuScreen #menu-container {
        width: 84;
        padding: 1 2;
    }
    PanelActionMenuScreen #menu-title {
        padding: 0 1 0 1;
    }
    PanelActionMenuScreen #menu-branch {
        padding: 0 1 1 1;
    }
    #panel-action-layout {
        height: auto;
    }
    #panel-action-main {
        width: 30;
        height: auto;
    }
    #panel-preview-pane {
        width: 1fr;
        height: auto;
        padding: 0 0 0 2;
    }
    PanelActionMenuScreen #action-menu {
        height: auto;
        padding: 0 1;
        margin: 0;
    }
    #panel-layout-preview {
        width: auto;
        height: auto;
    }
    #panel-sessions {
        height: auto;
        padding: 1 0 0 0;
    }
    """
    )

    def __init__(self, panel: Panel) -> None:
        super().__init__()
        self.panel = panel

    def compose(self) -> ComposeResult:
        from ....integrations.tmux.core import make_panel_session_name
        from ..app_panels import panel_map_text, panel_session_lines

        palette = _palette(self.app)
        labels = _session_labels()
        live = set(labels)
        session_name = make_panel_session_name(self.panel.name)
        with Vertical(id="menu-container"):
            yield Static(f"[bold $text]{escape(self.panel.name)}[/]", id="menu-title")
            yield Static(
                f"[dim]{escape(self.panel.layout_label)} · {escape(session_name)}[/dim]",
                id="menu-branch",
            )
            with Horizontal(id="panel-action-layout"):
                with Vertical(id="panel-action-main"):
                    yield OptionList(
                        Option("[$text]▶[/] [bold]Open[/bold]", id="open"),
                        Option("[$text]✎[/] [bold]Edit layout & sessions[/bold]", id="reconfigure"),
                        Option("[$text]Aa[/] [bold]Rename[/bold]", id="rename"),
                        Option("", disabled=True),
                        Option("[$text-error]✕[/] [bold]Delete[/bold]", id="delete"),
                        id="action-menu",
                    )
                with Vertical(id="panel-preview-pane"):
                    yield Static(
                        panel_map_text(self.panel, live, palette, cell_width=5),
                        id="panel-layout-preview",
                    )
                    yield Static(
                        panel_session_lines(self.panel, live, labels, palette),
                        id="panel-sessions",
                    )
            yield Static("↑↓ select    \\[enter] choose    \\[esc] close", id="menu-hint")

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
            yield Static("[bold $text]Rename Panel[/]", id="menu-title")
            yield Static(f"[dim]Current: {escape(self.current_name)}[/dim]", id="menu-branch")
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
        color: $text;
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
        self._loading_hint = loading_hint
        self._on_attach = on_attach
        self._dismissed = False
        self._start_time = 0.0

    def compose(self) -> ComposeResult:
        with Vertical(id="loading-container"):
            yield LoadingIndicator()
            yield Static(
                f"Launching [bold]{escape(self._agent_cmd)}[/bold]",
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
        # The marker only signals startup; once the screen goes away nothing
        # reads it, and the session's own cleanup cannot remove it if the
        # session is killed early.
        if self._ready_marker is not None:
            self._ready_marker.unlink(missing_ok=True)
        if self._on_attach is not None:
            self._on_attach()
        else:
            self.app._suspend_and_attach(self._session_name, skip_config_sync=True)
        self.dismiss(None)


_STEPS = ("Name", "Layout", "Sessions")
# Repo names beyond this are cut, so every list row stays on one line.
_REPO_WIDTH = 18


def _fit(text: str, width: int) -> str:
    if len(text) <= width:
        return text.ljust(width)
    return text[: max(width - 1, 0)] + "…"


_CREATE_ROW_ID = "__create__"
_EMPTY_ID = "__empty__"


class CreatePanelScreen(ModalScreen[tuple[str, str, dict[int, str | None]] | None]):
    """Build a panel in three steps -- name, layout, sessions -- over a live preview.

    The preview on the right always shows the panel as it will open: every
    pane numbered, with the session it will show. Editing an existing panel
    starts on the sessions step with the name fixed.
    """

    BINDINGS = [
        *_MODAL_BINDINGS,
        Binding("ctrl+o", "submit", "Create", show=False),
        Binding("a", "fill_empty", "Fill empty panes", show=False),
        Binding("x,delete,backspace", "clear_pane", "Clear pane", show=False),
    ]

    CSS = _safe_css(
        "CreatePanelScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }"
        """
    #create-panel-container {
        width: 104;
        height: auto;
        max-height: 100%;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    #create-panel-title {
        text-align: center;
        color: $text;
    }
    #create-panel-steps {
        text-align: center;
        padding: 0 0 1 0;
    }
    #create-panel-body {
        height: auto;
    }
    #create-panel-left {
        width: 50;
        height: auto;
    }
    #create-panel-right {
        width: 1fr;
        height: auto;
        padding: 0 0 0 2;
        align: center top;
    }
    .section-label {
        color: $text-muted;
        padding: 0 0 0 1;
    }
    #panel-name-input {
        width: 100%;
        margin: 0 0 0 0;
    }
    #panel-name-help {
        color: $text-muted;
        padding: 0 0 0 1;
    }
    #layout-menu,
    #pane-menu,
    #session-menu {
        height: auto;
        max-height: 16;
        border: none;
        padding: 0;
        background: $panel;
    }
    #grid-preview {
        width: auto;
        height: auto;
    }
    #create-panel-error {
        color: $error;
        text-align: center;
        padding: 1 0 0 0;
        display: none;
    }
    #create-panel-error.-shown {
        display: block;
    }
    #create-panel-hint {
        text-align: center;
        color: $text-muted;
        padding: 1 0 0 0;
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

        self._editing = editing
        self._panel_name = (panel_name or "").strip()
        if self._editing and not self._panel_name:
            raise ValueError("Editing a panel requires a panel name")
        if self._editing and not initial_layout_key:
            raise ValueError("Editing a panel requires a layout key")
        self._step = 3 if editing else 1
        self._layout_key = initial_layout_key or DEFAULT_PANEL_LAYOUT_KEY
        self._session_entries = list_all_gd_sessions()
        live = {entry["session_name"] for entry in self._session_entries}
        self._assignments: dict[int, str | None] = {}
        for pane_index, session_name in (initial_panes or {}).items():
            # A session that has since closed cannot be shown: its pane starts empty.
            self._assignments[pane_index] = session_name if session_name in live else None
        #: The pane whose session is being chosen (None: the pane list is shown).
        self._picking: int | None = None
        self._error: str | None = None

    # -- composition --------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="create-panel-container"):
            yield Static(self._title_markup(), id="create-panel-title")
            yield Static(id="create-panel-steps")
            with Horizontal(id="create-panel-body"):
                with Vertical(id="create-panel-left"):
                    yield Static(id="left-label", classes="section-label")
                    yield Input(
                        value=self._panel_name,
                        placeholder="e.g. agents, review, frontend",
                        id="panel-name-input",
                    )
                    yield Static(id="panel-name-help")
                    yield OptionList(*self._layout_options(), id="layout-menu")
                    yield OptionList(id="pane-menu")
                    yield OptionList(id="session-menu")
                with Vertical(id="create-panel-right"):
                    yield Static("Preview", classes="section-label")
                    yield Static(id="grid-preview")
            yield Static(id="create-panel-error")
            yield Static(id="create-panel-hint")

    def on_mount(self) -> None:
        layouts = get_create_panel_layouts()
        keys = [layout.key for layout in layouts]
        menu = self.query_one("#layout-menu", OptionList)
        menu.highlighted = keys.index(self._layout_key) if self._layout_key in keys else 0
        self._render_pane_menu(highlight=0)
        self._show_step(self._step)

    # -- rendering ----------------------------------------------------------

    def _title_markup(self) -> str:
        if self._editing:
            return f"[bold $text]Edit panel[/]  [bold $text-primary]{escape(self._panel_name)}[/]"
        return "[bold $text]New panel[/]"

    def _steps_text(self) -> Text:
        palette = _palette(self.app)
        text = Text()
        for index, label in enumerate(_STEPS, start=1):
            if index > 1:
                text.append("   ›   ", style=palette.muted)
            if index < self._step or (self._editing and index == 1):
                text.append(f"✓ {label}", style=palette.success)
            elif index == self._step:
                text.append(f"{index} {label}", style=f"bold {palette.primary}")
            else:
                text.append(f"{index} {label}", style=palette.muted)
        return text

    @property
    def _layout(self):
        return resolve_panel_layout(self._layout_key)

    def _pane_indexes(self) -> list[int]:
        return [placement.pane_index for placement in self._layout.placements]

    def _label(self, session_name: str | None) -> tuple[str, str]:
        if not session_name:
            return "", ""
        for entry in self._session_entries:
            if entry["session_name"] == session_name:
                sequence = session_name.rsplit("/", 1)[-1]
                return f"{entry['purpose']}/{sequence}", entry["repo"]
        return session_name, ""

    def _preview(self) -> Text:
        palette = _palette(self.app)
        layout = self._layout
        cell_width = max(7, min(16, 44 // layout.cols - 1))
        focused_pane = self._focused_pane() if self._step == 3 else None
        highlight = self.app.get_css_variables().get("primary-muted", "")
        labels: dict[int, str] = {}
        styles: dict[int, str] = {}
        fills: dict[int, str] = {}
        for pane in self._pane_indexes():
            session_name = self._assignments.get(pane) if self._step == 3 else None
            _purpose, repo = self._label(session_name)
            labels[pane] = f"{pane} {repo}" if repo else str(pane)
            if pane == focused_pane:
                # The pane being filled stands out; the rest step back.
                styles[pane] = f"bold {palette.primary}"
                fills[pane] = f"on {highlight}" if highlight else "reverse"
            elif focused_pane is None and self._step != 3:
                styles[pane] = "bold"
            else:
                styles[pane] = palette.muted
        return render_panel_layout_text(
            layout,
            labels,
            styles,
            cell_width=cell_width,
            cell_height=1,
            border_style=palette.muted,
            fills=fills,
        )

    def _pane_prompt(self, pane: int, repo_width: int) -> Text:
        palette = _palette(self.app)
        text = Text(no_wrap=True, overflow="ellipsis")
        session_name = self._assignments.get(pane)
        text.append(f" {pane}  ", style=f"bold {palette.primary}")
        if not session_name:
            text.append("empty", style=f"italic {palette.muted}")
            return text
        purpose, repo = self._label(session_name)
        text.append(_fit(repo, repo_width), style=palette.yellow)
        text.append(f"  {purpose}", style="bold")
        text.truncate(self._row_width(), overflow="ellipsis")
        return text

    def _row_width(self) -> int:
        width = self.query_one("#create-panel-left").size.width
        # Less the list's scrollbar.
        return max(width - 1, 20) if width else 45

    def _pane_options(self) -> list[Option]:
        panes = self._pane_indexes()
        width = min(
            _REPO_WIDTH,
            max((len(self._label(self._assignments.get(pane))[1]) for pane in panes), default=0),
        )
        options = [Option(self._pane_prompt(pane, width), id=f"pane:{pane}") for pane in panes]
        verb = "Save and open" if self._editing else "Create and open"
        options.append(Option("", disabled=True))
        options.append(Option(f"[bold $text-success] ✓ {verb}[/]", id=_CREATE_ROW_ID))
        return options

    def _session_options(self, pane: int) -> list[Option]:
        palette = _palette(self.app)
        current = self._assignments.get(pane)
        used = {
            session_name: other
            for other, session_name in self._assignments.items()
            if session_name and other != pane and other in self._pane_indexes()
        }
        options = [Option(f"[italic {palette.muted}]   leave empty[/]", id=_EMPTY_ID)]
        width = min(_REPO_WIDTH, max((len(e["repo"]) for e in self._session_entries), default=0))
        for entry in self._session_entries:
            session_name = entry["session_name"]
            purpose, repo = self._label(session_name)
            text = Text(no_wrap=True, overflow="ellipsis")
            text.append(" ● " if session_name == current else "   ", style=palette.primary)
            text.append(_fit(repo, width), style=palette.yellow)
            text.append(f"  {purpose}", style="bold")
            if session_name in used:
                text.append(f"  pane {used[session_name]}", style=f"italic {palette.muted}")
            # OptionList wraps long prompts; one line per session reads better.
            text.truncate(self._row_width(), overflow="ellipsis")
            options.append(Option(text, id=session_name))
        if not self._session_entries:
            options.append(
                Option(f"[{palette.muted}]   no sessions are running yet[/]", disabled=True)
            )
        return options

    @staticmethod
    def _layout_options() -> list[Option]:
        return [
            Option(f" {layout.menu_display_label}", id=f"layout:{layout.key}")
            for layout in get_create_panel_layouts()
        ]

    def _hint(self) -> str:
        if self._step == 1:
            return "type a name    \\[enter] next    \\[esc] cancel"
        if self._step == 2:
            back = "cancel" if self._editing else "back"
            return f"↑↓ choose    \\[enter] next    \\[esc] {back}"
        if self._picking is not None:
            return f"↑↓ choose    \\[enter] put in pane {self._picking}    \\[esc] keep as is"
        finish = "save" if self._editing else "create"
        return f"↑↓ pane   \\[enter] choose   a fill empty   x clear   ^o {finish}   \\[esc] back"

    def _refresh_view(self) -> None:
        self.query_one("#create-panel-steps", Static).update(self._steps_text())
        self.query_one("#grid-preview", Static).update(self._preview())
        self.query_one("#create-panel-hint", Static).update(self._hint())
        error = self.query_one("#create-panel-error", Static)
        error.update(escape(self._error or ""))
        error.set_class(bool(self._error), "-shown")

    def _show_step(self, step: int) -> None:
        self._step = step
        name_input = self.query_one("#panel-name-input", Input)
        layout_menu = self.query_one("#layout-menu", OptionList)
        pane_menu = self.query_one("#pane-menu", OptionList)
        session_menu = self.query_one("#session-menu", OptionList)
        help_text = self.query_one("#panel-name-help", Static)
        label = self.query_one("#left-label", Static)
        name_input.display = step == 1
        help_text.display = step == 1
        # The layout step shows the preview; while naming it would only distract.
        self.query_one("#create-panel-right").display = step != 1
        layout_menu.display = step == 2
        pane_menu.display = step == 3 and self._picking is None
        session_menu.display = step == 3 and self._picking is not None
        if step == 1:
            label.update("Name")
            help_text.update(self._name_help())
            name_input.focus()
        elif step == 2:
            label.update("Layout")
            layout_menu.focus()
        elif self._picking is not None:
            palette = _palette(self.app)
            label.update(
                Text.assemble(
                    "Choose the session for ",
                    (f" pane {self._picking} ", f"bold {palette.primary} reverse"),
                )
            )
            session_menu.focus()
        else:
            label.update("Panes")
            pane_menu.focus()
        self._refresh_view()

    def _name_help(self) -> str:
        from ....integrations.tmux.core import make_panel_session_name

        name = self.query_one("#panel-name-input", Input).value.strip()
        if not name:
            return "Name it after what it is for."
        return f"Opens as [bold]{escape(make_panel_session_name(name))}[/bold]"

    def _render_pane_menu(self, highlight: int | None = None) -> None:
        menu = self.query_one("#pane-menu", OptionList)
        previous = menu.highlighted
        menu.clear_options()
        menu.add_options(self._pane_options())
        if highlight is not None:
            menu.highlighted = highlight
        elif previous is not None and previous < menu.option_count:
            menu.highlighted = previous
        else:
            menu.highlighted = 0

    def _focused_pane(self) -> int | None:
        if self._picking is not None:
            return self._picking
        menu = self.query_one("#pane-menu", OptionList)
        option = menu.highlighted_option
        if option is None or not (option.id or "").startswith("pane:"):
            return None
        return int(option.id.split(":", 1)[1])

    # -- validation ---------------------------------------------------------

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

    def _current_panel_name(self) -> str:
        if self._editing:
            return self._panel_name
        return self.query_one("#panel-name-input", Input).value.strip()

    def _name_problem(self) -> str | None:
        name = self._current_panel_name()
        if not name:
            return "Give the panel a name first"
        if self._editing:
            return None
        panel_store = getattr(self.app, "_panel_store", None)
        if panel_store is None:
            return None
        return self.validate_new_panel_name(panel_store, name)

    # -- events ---------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "panel-name-input":
            self._error = None
            self.query_one("#panel-name-help", Static).update(self._name_help())
            self._refresh_view()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "panel-name-input":
            self._advance_from_name()

    def _advance_from_name(self) -> None:
        self._error = self._name_problem()
        if self._error:
            self._refresh_view()
            return
        self._show_step(2)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "layout-menu" and event.option.id:
            self._set_layout(event.option.id.split(":", 1)[1])
        elif event.option_list.id in ("pane-menu", "session-menu"):
            self.query_one("#grid-preview", Static).update(self._preview())

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id or ""
        if event.option_list.id == "layout-menu":
            self._set_layout(option_id.split(":", 1)[1])
            self._render_pane_menu(highlight=0)
            self._show_step(3)
        elif event.option_list.id == "pane-menu":
            if option_id == _CREATE_ROW_ID:
                self.action_submit()
            elif option_id.startswith("pane:"):
                self._start_picking(int(option_id.split(":", 1)[1]))
        elif event.option_list.id == "session-menu":
            self._finish_picking(None if option_id == _EMPTY_ID else option_id)

    def _set_layout(self, layout_key: str) -> None:
        if layout_key == self._layout_key:
            return
        # Every assignment is kept: browsing through a smaller layout must not
        # empty the panes it lacks. Only the chosen layout's panes are used.
        self._layout_key = layout_key
        self._refresh_view()

    def _start_picking(self, pane: int) -> None:
        self._picking = pane
        menu = self.query_one("#session-menu", OptionList)
        menu.clear_options()
        menu.add_options(self._session_options(pane))
        ids = [option.id for option in menu.options]
        current = self._assignments.get(pane)
        menu.highlighted = ids.index(current) if current in ids else (1 if len(ids) > 1 else 0)
        self._show_step(3)

    def _finish_picking(self, session_name: str | None) -> None:
        pane = self._picking
        self._picking = None
        if pane is None:
            return
        if session_name:
            # A session shows in one pane: choosing it again moves it here.
            for other, assigned in self._assignments.items():
                if assigned == session_name and other != pane:
                    self._assignments[other] = None
        self._assignments[pane] = session_name
        panes = self._pane_indexes()
        # On to the next pane, or to "create" after the last one.
        position = panes.index(pane) + 1 if pane in panes else 0
        self._render_pane_menu(highlight=position if position < len(panes) else len(panes) + 1)
        self._show_step(3)

    # -- actions ------------------------------------------------------------

    def action_fill_empty(self) -> None:
        if self._step != 3 or self._picking is not None:
            return
        panes = self._pane_indexes()
        used = {self._assignments.get(pane) for pane in panes}
        free = [e["session_name"] for e in self._session_entries if e["session_name"] not in used]
        for pane in panes:
            if not self._assignments.get(pane) and free:
                self._assignments[pane] = free.pop(0)
        self._render_pane_menu()
        self._refresh_view()

    def action_clear_pane(self) -> None:
        if self._step != 3 or self._picking is not None:
            return
        pane = self._focused_pane()
        if pane is not None:
            self._assignments[pane] = None
            self._render_pane_menu()
            self._refresh_view()

    def action_submit(self) -> None:
        if self._step == 1:
            self._advance_from_name()
            return
        if self._step == 2:
            self._render_pane_menu(highlight=0)
            self._show_step(3)
            return
        if self._picking is not None:
            menu = self.query_one("#session-menu", OptionList)
            option = menu.highlighted_option
            if option is not None and not option.disabled:
                self._finish_picking(None if option.id == _EMPTY_ID else option.id)
        self._error = self._name_problem()
        if self._error:
            self._show_step(1)
            return
        panes = {pane: self._assignments.get(pane) for pane in self._pane_indexes()}
        if not any(panes.values()):
            self._error = "Put a session in at least one pane"
            self._refresh_view()
            return
        self.dismiss((self._current_panel_name(), self._layout_key, panes))

    def action_cancel(self) -> None:
        self._error = None
        if self._picking is not None:
            self._picking = None
            self._show_step(3)
        elif self._step == 3:
            self._show_step(2)
        elif self._step == 2 and not self._editing:
            self._show_step(1)
        else:
            self.dismiss(None)

    def _active_menu(self) -> OptionList | None:
        focused = self.focused
        return focused if isinstance(focused, OptionList) else None

    def action_cursor_down(self) -> None:
        menu = self._active_menu()
        if menu is not None:
            menu.action_cursor_down()

    def action_cursor_up(self) -> None:
        menu = self._active_menu()
        if menu is not None:
            menu.action_cursor_up()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Typing a name must not trigger single-letter keys.
        if action in ("fill_empty", "clear_pane", "cursor_down", "cursor_up") and isinstance(
            self.focused, Input
        ):
            return False
        return super().check_action(action, parameters)
