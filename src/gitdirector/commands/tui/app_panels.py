"""Panel list helpers for the TUI."""

from __future__ import annotations

from rich.markup import escape
from textual.css.query import NoMatches
from textual.widgets import DataTable, Static

from .constants import (
    _DEFAULT_PANELS_SORT_COLUMN,
    _PANEL_STATUS_LABEL,
    _PANELS_SORT_COLUMN_NAMES,
)
from .panels import Panel, render_panel_layout_preview
from .screens import ConfirmScreen, CreatePanelScreen, PanelActionMenuScreen, RenamePanelScreen

_PANEL_PREVIEW_FILLED = "■"
_PANEL_PREVIEW_OPEN = "□"


def _panel_preview_marker(
    panel: Panel,
    pane_index: int,
    live_sessions: set[str] | None = None,
) -> str:
    session_name = panel.panes.get(pane_index)
    is_filled = bool(session_name) if live_sessions is None else session_name in live_sessions
    return _PANEL_PREVIEW_FILLED if is_filled else _PANEL_PREVIEW_OPEN


def _render_panel_preview(panel: Panel, live_sessions: set[str] | None = None) -> str:
    labels = {
        placement.pane_index: _panel_preview_marker(
            panel,
            placement.pane_index,
            live_sessions,
        )
        for placement in panel.pane_placements
    }
    return render_panel_layout_preview(panel.layout, labels=labels, cell_width=1, cell_height=1)


def _panel_row_height(panel: Panel, live_sessions: set[str] | None = None) -> int:
    return len(_render_panel_preview(panel, live_sessions).splitlines()) + 2


def _panel_row_cell(value: str) -> str:
    return f"\n{value}"


class ConsolePanelsMixin:
    def _live_panel_pane_count(self, panel: Panel, live_sessions: set[str] | None = None) -> int:
        sessions = self._panels_live_sessions if live_sessions is None else live_sessions
        return sum(1 for session_name in panel.panes.values() if session_name in sessions)

    def _panel_status_state(self, panel: Panel, live_sessions: set[str] | None = None) -> str:
        sessions = self._panels_live_sessions if live_sessions is None else live_sessions
        has_live = any(session_name in sessions for session_name in panel.panes.values())
        return "active" if has_live else "empty"

    def _panel_matches_search(
        self, panel: Panel, query: str, live_sessions: set[str] | None = None
    ) -> bool:
        from ...integrations.tmux import make_panel_session_name

        normalized_query = query.replace("×", "x")
        live_panes = self._live_panel_pane_count(panel, live_sessions)
        panes_label = f"{live_panes}/{panel.total_panes}"
        status_label = self._panel_status_state(panel, live_sessions)
        haystacks = [
            panel.name.lower(),
            make_panel_session_name(panel.name).lower(),
            panel.layout_label.lower(),
            panes_label.lower(),
            status_label,
        ]
        haystacks.extend(session.lower() for session in panel.panes.values() if session)
        return any(normalized_query in haystack.replace("×", "x") for haystack in haystacks)

    def _panel_sort_key_func(self):
        from ...integrations.tmux import make_panel_session_name

        col = self._panels_sort_column
        if col == 1:
            return lambda panel: (make_panel_session_name(panel.name).lower(), panel.name.lower())
        if col == 2:
            return lambda panel: (
                panel.layout.sort_rank,
                panel.layout_label.lower(),
                panel.name.lower(),
            )
        if col == 3:
            live = self._panels_live_sessions
            return lambda panel: (
                self._live_panel_pane_count(panel, live),
                panel.total_panes,
                panel.name.lower(),
            )
        if col == 4:
            live = self._panels_live_sessions
            return lambda panel: (
                0 if self._panel_status_state(panel, live) == "active" else 1,
                panel.name.lower(),
            )
        return lambda panel: panel.name.lower()

    def _apply_panels_filter_and_sort(self, live_sessions: set[str] | None = None) -> None:
        from ...integrations.tmux import _list_sessions, make_panel_session_name

        try:
            table = self.query_one("#panels-table", DataTable)
        except NoMatches:
            return
        preserved_row_key = None
        preserved_row_index = None
        restore_focus = False
        if self._resume_selection_tab != "panels":
            preserved_row_key, preserved_row_index, restore_focus = self._capture_table_selection(
                table
            )
        table.clear()
        no_msg = self.query_one("#no-panels-message", Static)

        panels = list(self._panels_entries)
        total = len(panels)

        if live_sessions is None:
            live_sessions = set(_list_sessions())
        else:
            live_sessions = set(live_sessions)
        self._panels_live_sessions = live_sessions

        if self._search_query:
            query = self._search_query.lower()
            panels = [
                panel for panel in panels if self._panel_matches_search(panel, query, live_sessions)
            ]

        panels.sort(key=self._panel_sort_key_func(), reverse=self._panels_sort_reverse)

        if not panels and total == 0 and not self._search_query:
            table.display = False
            no_msg.display = True
        else:
            table.display = True
            no_msg.display = False
            for panel in panels:
                filled = self._live_panel_pane_count(panel, live_sessions)
                total_panes = panel.total_panes
                panes_label = f"{filled}/{total_panes}" if filled else f"0/{total_panes}"
                status_state = self._panel_status_state(panel, live_sessions)
                status_label = _PANEL_STATUS_LABEL[status_state]
                preview = _render_panel_preview(panel, live_sessions)
                table.add_row(
                    _panel_row_cell(preview),
                    _panel_row_cell(panel.name),
                    _panel_row_cell(make_panel_session_name(panel.name)),
                    _panel_row_cell(panel.layout_display_label),
                    _panel_row_cell(panes_label),
                    _panel_row_cell(status_label),
                    height=_panel_row_height(panel, live_sessions),
                    key=panel.name,
                )

        if self._resume_selection_tab == "panels":
            self._restore_resume_selection("panels")
        else:
            self._restore_table_selection(
                table,
                preserved_row_key,
                preserved_row_index,
                restore_focus=restore_focus,
            )
        self._update_status(self._build_panels_loaded_status(len(panels), total))

    def _build_panels_loaded_status(self, shown: int, total: int) -> str:
        if total == 0 and not self._search_query:
            return "No panels   n create   1 repos  2 sessions  3 panels  q quit"

        if self._search_query:
            count_str = f"{shown} of {total}"
        else:
            count_str = str(total)

        label_count = shown if self._search_query else total
        label = "panel" if label_count == 1 else "panels"
        msg = f"{count_str} {label}"

        indicators: list[str] = []
        if self._search_query:
            indicators.append(f"filter: '{escape(self._search_query)}'")
        if self._panels_sort_column != _DEFAULT_PANELS_SORT_COLUMN or self._panels_sort_reverse:
            direction = "▼" if self._panels_sort_reverse else "▲"
            indicators.append(
                f"sort: {_PANELS_SORT_COLUMN_NAMES[self._panels_sort_column]} {direction}"
            )
        if indicators:
            msg += f"  ({', '.join(indicators)})"

        msg += "   ↑↓/jk navigate  [enter] actions  / search  s sort  n create"
        msg += "  1 repos  2 sessions  3 panels  q quit"
        if self._search_query:
            msg += "  [esc] clear search"
        return msg

    def _load_panels(self) -> None:
        self._panel_store.reload()
        try:
            self.query_one("#panels-table", DataTable)
        except NoMatches:
            return
        self._panels_entries = self._panel_store.panels
        self._apply_panels_filter_and_sort()

    def _selected_panel_name(self) -> str | None:
        table = self.query_one("#panels-table", DataTable)
        return self._get_selected_row_key(table)

    def _open_selected_panel_menu(self) -> None:
        panel_name = self._selected_panel_name()
        if not panel_name:
            return
        panel = self._panel_store.get(panel_name)
        if not panel:
            return
        self.push_screen(
            PanelActionMenuScreen(panel),
            callback=lambda action: self._handle_panel_action(action, panel_name),
        )

    def _open_selected_panel(self) -> None:
        panel_name = self._selected_panel_name()
        if not panel_name:
            return
        panel = self._panel_store.get(panel_name)
        if not panel:
            return
        self._open_panel(panel_name)

    def _handle_panel_action(self, action: str | None, panel_name: str) -> None:
        if action is None:
            return
        if action == "open":
            self._open_panel(panel_name)
        elif action == "reconfigure":
            panel = self._panel_store.get(panel_name)
            if not panel:
                return
            self.push_screen(
                CreatePanelScreen(
                    panel_name=panel.name,
                    initial_layout_key=panel.layout.key,
                    initial_panes=panel.panes,
                    editing=True,
                ),
                callback=lambda result: self._handle_reconfigure_panel(panel_name, result),
            )
        elif action == "rename":
            self.push_screen(
                RenamePanelScreen(panel_name),
                callback=lambda new_name: self._do_rename_panel(panel_name, new_name),
            )
        elif action == "delete":
            self.push_screen(
                ConfirmScreen(f"Delete panel '{panel_name}'?"),
                callback=lambda confirmed: self._do_delete_panel(confirmed, panel_name),
            )

    def _do_rename_panel(self, old_name: str, new_name: str | None) -> None:
        if new_name is None or new_name == old_name:
            return
        existing = self._panel_store.get(new_name)
        if existing:
            self._update_status(f"Panel '{new_name}' already exists")
            return
        self._panel_store.rename(old_name, new_name)
        self._load_panels()

    def _open_panel(self, panel_name: str) -> None:
        from ...integrations.tmux import (
            _protect_session,
            _session_exists,
            make_panel_session_name,
            rebuild_panel_tmux_session,
        )

        panel = self._panel_store.get(panel_name)
        if not panel:
            return

        session_name = make_panel_session_name(panel_name)
        if _session_exists(session_name):
            _protect_session(session_name)
        else:
            session_name = rebuild_panel_tmux_session(
                panel.name,
                panel.rows,
                panel.cols,
                panel.panes,
                closed_panes=panel.closed_panes,
                layout_key=panel.layout.key,
            )
        self._suspend_and_attach(session_name, row_key=panel.name)

    def action_new_panel(self) -> None:
        if self._active_tab != "panels":
            return
        if self._consume_resume_new_panel_guard():
            return
        self.push_screen(
            CreatePanelScreen(),
            callback=self._handle_create_panel,
        )

    def _handle_create_panel(
        self,
        result: tuple[str, str, dict[int, str | None]] | None,
    ) -> None:
        if result is None:
            return
        name, layout_key, panes = result
        validation_message = CreatePanelScreen.validate_new_panel_name(self._panel_store, name)
        if validation_message:
            self._update_status(validation_message)
            return

        created_panel = self._panel_store.create(name, panes=panes, layout_key=layout_key)
        self._load_panels()
        if created_panel is None:
            self._update_status(f"Panel '{name}' was not created because all panes are empty")
            return
        self._open_panel(name)

    def _handle_reconfigure_panel(
        self,
        panel_name: str,
        result: tuple[str, str, dict[int, str | None]] | None,
    ) -> None:
        if result is None:
            return

        _, layout_key, panes = result
        if not self._panel_store.reconfigure(panel_name, panes=panes, layout_key=layout_key):
            self._update_status(f"Panel '{panel_name}' could not be reconfigured")
            return

        self._load_panels()
        self._open_panel(panel_name)

    def action_delete_panel(self) -> None:
        if self._active_tab != "panels":
            return
        panel_name = self._selected_panel_name()
        if not panel_name:
            return
        self.push_screen(
            ConfirmScreen(f"Delete panel '{panel_name}'?"),
            callback=lambda confirmed: self._do_delete_panel(confirmed, panel_name),
        )

    def _do_delete_panel(self, confirmed: bool, panel_name: str) -> None:
        if confirmed:
            self._panel_store.delete(panel_name)
            self._load_panels()
