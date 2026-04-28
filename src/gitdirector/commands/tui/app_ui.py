"""Shared GitDirectorConsole tab, search, and selection helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from time import monotonic

from rich.markup import escape
from textual.app import App
from textual.css.query import NoMatches
from textual.widgets import DataTable, Input, Static, TabbedContent
from textual.widgets.data_table import RowDoesNotExist

from .constants import (
    _PANELS_SORT_COLUMN_NAMES,
    _SESSIONS_SORT_COLUMN_NAMES,
)
from .screens import SortMenuScreen

_POST_RESUME_NEW_PANEL_GUARD_SECONDS = 0.25

logger = logging.getLogger(__name__)


class ConsoleUIHelpersMixin:
    def _compose_status_message(self, message: str) -> str:
        notice = getattr(self, "_update_notice", None)
        if not notice:
            return message
        if not message:
            return notice
        return f"{message}  |  {notice}"

    def _refresh_status_bar(self) -> None:
        try:
            self.query_one("#status-bar", Static).update(
                self._compose_status_message(self._status_message)
            )
        except NoMatches:
            return

    def action_tab_repos(self) -> None:
        if self._resume_target_tab is not None and self._resume_target_tab != "repos":
            return
        self.query_one("#tabs", TabbedContent).active = "repos"

    def action_tab_sessions(self) -> None:
        if self._resume_target_tab is not None and self._resume_target_tab != "sessions":
            return
        self.query_one("#tabs", TabbedContent).active = "sessions"

    def action_tab_panels(self) -> None:
        if self._resume_target_tab is not None and self._resume_target_tab != "panels":
            return
        self.query_one("#tabs", TabbedContent).active = "panels"

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "new_panel":
            return self._active_tab == "panels"
        if action in {"show_git_menu", "show_info"}:
            return self._active_tab == "repos"
        return super().check_action(action, parameters)

    def _arm_resume_new_panel_guard(self, restore_tab: str) -> None:
        if restore_tab == "panels":
            self._resume_new_panel_guard_until = monotonic() + _POST_RESUME_NEW_PANEL_GUARD_SECONDS
            return
        self._resume_new_panel_guard_until = 0.0

    def _consume_resume_new_panel_guard(self) -> bool:
        if self._resume_new_panel_guard_until <= 0.0:
            return False
        if monotonic() >= self._resume_new_panel_guard_until:
            self._resume_new_panel_guard_until = 0.0
            return False
        return True

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab_id = event.pane.id or ""
        if self._resume_tab_activation_guard == tab_id:
            self._resume_tab_activation_guard = None
            self._active_tab = tab_id
            self.refresh_bindings()
            self._sync_session_status_tracking()
            return
        if self._resume_target_tab is not None:
            if tab_id != self._resume_target_tab:
                self.query_one("#tabs", TabbedContent).active = self._resume_target_tab
                return
            self._active_tab = tab_id
            self.refresh_bindings()
            self._sync_session_status_tracking()
            return
        self._active_tab = tab_id
        self.refresh_bindings()
        self._sync_session_status_tracking()
        if tab_id == "sessions":
            self._load_sessions()
        elif tab_id == "panels":
            self._load_panels()
        elif tab_id == "repos":
            if self._repos_stale:
                self._repos_stale = False
                self._results.clear()
                self._sessions_cache.clear()
                self._load_repos()
            else:
                total = len(self._results)
                try:
                    shown = self.query_one("#repo-table", DataTable).row_count
                except NoMatches:
                    return
                self._update_status(self._build_loaded_status(shown, total))

    def _handle_app_resume(self, _app: App) -> None:
        if self._resume_target_tab is None:
            return

        import sys
        import termios

        try:
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except (AttributeError, OSError):
            pass

        self.call_after_refresh(
            self._restore_after_resume,
            self._resume_target_tab,
            self._resume_refresh_path,
        )

    def _restore_after_resume(self, restore_tab: str, restore_path: Path | None) -> None:
        if self._resume_target_tab != restore_tab:
            return

        tabs = self.query_one("#tabs", TabbedContent)

        if tabs.active != restore_tab:
            self._resume_tab_activation_guard = restore_tab
            tabs.active = restore_tab
        self._active_tab = restore_tab
        self.refresh_bindings()
        self._sync_session_status_tracking()

        if restore_tab == "sessions":
            self._load_sessions()
            self.query_one("#sessions-table", DataTable).focus()
        elif restore_tab == "panels":
            self._load_panels()
            self.query_one("#panels-table", DataTable).focus()
            self._restore_resume_selection("panels")
        else:
            self.query_one("#repo-table", DataTable).focus()
            self._restore_resume_selection("repos")

        self._resume_target_tab = None
        self._resume_refresh_path = None

        tabs.refresh(layout=True)

        if restore_path is not None:
            self._refresh_repo_for_path(restore_path)

    def _update_status(self, message: str) -> None:
        self._status_message = message
        self._refresh_status_bar()

    def _update_search_indicator(self) -> None:
        repo_ind = self.query_one("#repo-search-indicator", Static)
        sess_ind = self.query_one("#sessions-search-indicator", Static)
        panel_ind = self.query_one("#panels-search-indicator", Static)
        if self._search_query:
            escaped_query = escape(self._search_query)
            text = (
                f"Search results for '[bold]{escaped_query}[/bold]'"
                "  —  press [bold]esc[/bold] to clear"
            )
            repo_ind.update(text)
            sess_ind.update(text)
            panel_ind.update(text)
            repo_ind.display = True
            sess_ind.display = True
            panel_ind.display = True
        else:
            repo_ind.display = False
            sess_ind.display = False
            panel_ind.display = False

    def _get_selected_path(self) -> Path | None:
        table = self.query_one("#repo-table", DataTable)
        row_key = self._get_selected_row_key(table)
        if row_key is None:
            return None
        return Path(row_key)

    def _get_selected_row_key(self, table: DataTable) -> str | None:
        if table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            logger.debug("Failed to resolve selected row key", exc_info=True)
            return None
        return str(row_key.value)

    def _table_selector_for_tab(self, tab_id: str) -> str:
        if tab_id == "sessions":
            return "#sessions-table"
        if tab_id == "panels":
            return "#panels-table"
        return "#repo-table"

    def _capture_resume_selection(
        self,
        tab_id: str,
        *,
        session_name: str | None = None,
        path: Path | None = None,
        row_key: str | None = None,
    ) -> None:
        self._resume_selection_tab = tab_id
        if not self.is_running:
            self._resume_selection_row = None
            if tab_id == "sessions":
                self._resume_selection_key = session_name or row_key
            elif tab_id == "panels":
                self._resume_selection_key = row_key
            else:
                self._resume_selection_key = str(path) if path is not None else row_key
            return

        table = self.query_one(self._table_selector_for_tab(tab_id), DataTable)
        self._resume_selection_row = table.cursor_coordinate.row if table.row_count > 0 else None
        if tab_id == "sessions":
            self._resume_selection_key = session_name or self._get_selected_row_key(table)
        elif tab_id == "panels":
            self._resume_selection_key = row_key or self._get_selected_row_key(table)
        else:
            self._resume_selection_key = (
                str(path) if path is not None else row_key or self._get_selected_row_key(table)
            )

    def _clear_resume_selection(self) -> None:
        self._resume_selection_tab = None
        self._resume_selection_key = None
        self._resume_selection_row = None

    def _restore_resume_selection(self, tab_id: str) -> None:
        if self._resume_selection_tab != tab_id:
            return

        table = self.query_one(self._table_selector_for_tab(tab_id), DataTable)
        if table.row_count == 0:
            self._clear_resume_selection()
            return

        restored = False
        if self._resume_selection_key is not None:
            try:
                target_row = table.get_row_index(self._resume_selection_key)
            except RowDoesNotExist:
                pass
            else:
                table.move_cursor(row=target_row)
                restored = True

        if not restored and self._resume_selection_row is not None:
            target_row = min(self._resume_selection_row, table.row_count - 1)
            if target_row >= 0:
                table.move_cursor(row=target_row)
                restored = True

        if restored:
            table.focus()
        self._clear_resume_selection()

    def _capture_table_selection(
        self,
        table: DataTable,
    ) -> tuple[str | None, int | None, bool]:
        if table.row_count == 0:
            return None, None, self.focused is table
        return self._get_selected_row_key(table), table.cursor_coordinate.row, self.focused is table

    def _restore_table_selection(
        self,
        table: DataTable,
        row_key: str | None,
        row_index: int | None,
        *,
        restore_focus: bool,
    ) -> None:
        if table.row_count == 0:
            return

        restored = False
        if row_key is not None:
            try:
                target_row = table.get_row_index(row_key)
            except RowDoesNotExist:
                pass
            else:
                table.move_cursor(row=target_row)
                restored = True

        if not restored and row_index is not None:
            target_row = min(row_index, table.row_count - 1)
            if target_row >= 0:
                table.move_cursor(row=target_row)
                restored = True

        if restored and restore_focus:
            table.focus()

    def _get_active_table(self) -> DataTable:
        if self._active_tab == "sessions":
            return self.query_one("#sessions-table", DataTable)
        if self._active_tab == "panels":
            return self.query_one("#panels-table", DataTable)
        return self.query_one("#repo-table", DataTable)

    def action_cursor_down(self) -> None:
        self._get_active_table().action_cursor_down()

    def action_cursor_up(self) -> None:
        self._get_active_table().action_cursor_up()

    def action_cursor_left(self) -> None:
        self._get_active_table().scroll_left()

    def action_cursor_right(self) -> None:
        self._get_active_table().scroll_right()

    def action_search(self) -> None:
        self.query_one("#search-container").display = True
        self.query_one("#search-bar", Input).focus()

    def _apply_active_filter_and_sort(self) -> None:
        if self._active_tab == "sessions":
            self._apply_sessions_filter_and_sort()
        elif self._active_tab == "panels":
            self._apply_panels_filter_and_sort()
        else:
            self._apply_filter_and_sort()

    def action_close_search(self) -> None:
        container = self.query_one("#search-container")
        if container.display:
            self.query_one("#search-bar", Input).value = ""
            container.display = False
            self._search_query = ""
            self._update_search_indicator()
            self._apply_active_filter_and_sort()
            self._get_active_table().focus()
        elif self._search_query:
            self._search_query = ""
            self._update_search_indicator()
            self._apply_active_filter_and_sort()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-bar":
            self._search_query = event.value
            self._apply_active_filter_and_sort()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-bar":
            self._search_query = event.value
            self._update_search_indicator()
            self._apply_active_filter_and_sort()
            self.query_one("#search-container").display = False
            self._get_active_table().focus()

    def action_sort(self) -> None:
        if self._active_tab == "sessions":
            self.push_screen(
                SortMenuScreen(
                    self._sessions_sort_column,
                    self._sessions_sort_reverse,
                    _SESSIONS_SORT_COLUMN_NAMES,
                ),
                callback=self._handle_sessions_sort_selection,
            )
        elif self._active_tab == "panels":
            self.push_screen(
                SortMenuScreen(
                    self._panels_sort_column,
                    self._panels_sort_reverse,
                    _PANELS_SORT_COLUMN_NAMES,
                ),
                callback=self._handle_panels_sort_selection,
            )
        else:
            self.push_screen(
                SortMenuScreen(self._sort_column, self._sort_reverse),
                callback=self._handle_sort_selection,
            )

    def _handle_sort_selection(self, result: tuple | None) -> None:
        if result is None:
            return
        self._sort_column, self._sort_reverse = result
        self._apply_filter_and_sort()

    def _handle_sessions_sort_selection(self, result: tuple | None) -> None:
        if result is None:
            return
        self._sessions_sort_column, self._sessions_sort_reverse = result
        self._apply_sessions_filter_and_sort()

    def _handle_panels_sort_selection(self, result: tuple | None) -> None:
        if result is None:
            return
        self._panels_sort_column, self._panels_sort_reverse = result
        self._apply_panels_filter_and_sort()
