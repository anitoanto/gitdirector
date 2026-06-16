"""Session list and tmux status helpers for the TUI."""

from __future__ import annotations

import logging

from rich.markup import escape
from textual import work
from textual.css.query import NoMatches
from textual.widgets import DataTable, Static

from .constants import (
    _DEFAULT_SESSIONS_SORT_COLUMN,
    _SESSION_STATUS_LABEL,
    _SESSION_STATUS_ORDER,
    _SESSIONS_COL_DESCRIPTION,
    _SESSIONS_SORT_COLUMN_NAMES,
)
from .table_text import resolve_wrapped_column_width, wrap_table_cell_text

logger = logging.getLogger(__name__)

_MIN_SESSIONS_DESCRIPTION_WIDTH = 12
_MAX_SESSIONS_DESCRIPTION_WIDTH = 48
_SESSIONS_DESCRIPTION_WIDTH_DIVISOR = 3


def _resolve_sessions_description_width(screen_width: int) -> int:
    """Return the max width (in cells) for the description column.

    The width scales with the available screen size: roughly
    ``screen_width / 3`` with a sensible floor and ceiling so the column
    never collapses or eats the whole table.
    """
    if screen_width <= 0:
        return _MIN_SESSIONS_DESCRIPTION_WIDTH
    return resolve_wrapped_column_width(
        screen_width,
        min_width=_MIN_SESSIONS_DESCRIPTION_WIDTH,
        max_width=_MAX_SESSIONS_DESCRIPTION_WIDTH,
        divisor=_SESSIONS_DESCRIPTION_WIDTH_DIVISOR,
    )


def _wrap_session_description(text: str, max_width: int) -> str:
    """Wrap *text* to *max_width* cells, preserving any existing newlines.

    Empty/placeholder values pass through untouched so callers don't
    have to special-case "-".
    """
    return wrap_table_cell_text(text, max_width)


class ConsoleSessionsMixin:
    @work(thread=True)
    def _load_sessions(self) -> None:
        from ...integrations.tmux import get_all_session_statuses, list_all_gd_sessions

        self.call_from_thread(self._update_status, "Loading sessions…")
        entries = list_all_gd_sessions()
        statuses = get_all_session_statuses()
        self._session_statuses = statuses
        self.call_from_thread(self._populate_sessions_table, entries)

    def _populate_sessions_table(self, entries: list[dict[str, str]]) -> None:
        self._sessions_entries = entries
        self._apply_sessions_filter_and_sort()

    def _apply_sessions_description_column_width(self) -> None:
        try:
            table = self.query_one("#sessions-table", DataTable)
        except NoMatches:
            return
        col_keys = getattr(self, "_sess_col_keys", None)
        if not col_keys or len(col_keys) <= _SESSIONS_COL_DESCRIPTION:
            return
        col_key = col_keys[_SESSIONS_COL_DESCRIPTION]
        width = _resolve_sessions_description_width(self.size.width)
        try:
            column = table.columns[col_key]
        except (KeyError, IndexError):
            return
        column.auto_width = False
        column.width = width

    def _apply_sessions_filter_and_sort(self) -> None:
        try:
            table = self.query_one("#sessions-table", DataTable)
        except NoMatches:
            return
        self._apply_sessions_description_column_width()
        description_width = _resolve_sessions_description_width(self.size.width)
        preserved_row_key = None
        preserved_row_index = None
        restore_focus = False
        if self._resume_selection_tab != "sessions":
            preserved_row_key, preserved_row_index, restore_focus = self._capture_table_selection(
                table
            )
        table.clear()
        no_msg = self.query_one("#no-sessions-message", Static)

        entries = list(self._sessions_entries)
        total = len(entries)

        if self._search_query:
            query = self._search_query.lower()
            entries = [
                entry
                for entry in entries
                if query in entry["session_name"].lower()
                or query in entry["repo"].lower()
                or query in entry["purpose"].lower()
                or query in entry.get("description", "").lower()
            ]

        for entry in entries:
            entry["status"] = self._resolve_session_status(entry)

        sort_keys = {
            0: lambda entry: _SESSION_STATUS_ORDER.get(entry.get("status", "running"), 99),
            1: lambda entry: entry["purpose"].lower(),
            2: lambda entry: entry["repo"].lower(),
            3: lambda entry: entry["session_name"].lower(),
            4: lambda entry: entry.get("description", "").lower(),
        }
        key_func = sort_keys.get(
            self._sessions_sort_column,
            sort_keys[_DEFAULT_SESSIONS_SORT_COLUMN],
        )
        entries.sort(key=key_func, reverse=self._sessions_sort_reverse)

        if not entries and total == 0 and not self._search_query:
            table.display = False
            no_msg.display = True
        else:
            table.display = True
            no_msg.display = False
            for entry in entries:
                status = entry.get("status", "running")
                description = _wrap_session_description(
                    entry.get("description", "-"), description_width
                )
                table.add_row(
                    _SESSION_STATUS_LABEL.get(status, "● running"),
                    entry["purpose"],
                    entry["repo"],
                    entry["session_name"],
                    description,
                    key=entry["session_name"],
                )

        if self._resume_selection_tab == "sessions":
            self._restore_resume_selection("sessions")
        else:
            self._restore_table_selection(
                table,
                preserved_row_key,
                preserved_row_index,
                restore_focus=restore_focus,
            )
        self._update_status(self._build_sessions_loaded_status(len(entries), total))

    def _build_sessions_loaded_status(self, shown: int, total: int) -> str:
        if total == 0 and not self._search_query:
            return "No active sessions"

        if self._search_query:
            count_str = f"{shown} of {total}"
        else:
            count_str = str(total)

        label_count = shown if self._search_query else total
        label = "session" if label_count == 1 else "sessions"
        msg = f"{count_str} active {label}"

        indicators: list[str] = []
        if self._search_query:
            indicators.append(f"filter: '{escape(self._search_query)}'")
        if (
            self._sessions_sort_column != _DEFAULT_SESSIONS_SORT_COLUMN
            or self._sessions_sort_reverse
        ):
            direction = "▼" if self._sessions_sort_reverse else "▲"
            indicators.append(
                f"sort: {_SESSIONS_SORT_COLUMN_NAMES[self._sessions_sort_column]} {direction}"
            )
        if indicators:
            msg += f"  ({', '.join(indicators)})"

        msg += "   ↑↓/jk navigate  [enter] attach  1 repos  2 sessions  r refresh  q quit"
        if self._search_query:
            msg += "  [esc] clear search"
        return msg

    def _should_run_session_status_tracking(self) -> bool:
        return self._active_tab == "sessions" and not self._session_status_tracking_paused

    def _set_session_status_tracking_running(self, running: bool, *, wait: bool = True) -> None:
        poll_timer = getattr(self, "_poll_timer", None)

        if running:
            if self._session_status_tracking_running:
                return
            self._monitor.start()
            if poll_timer is not None:
                poll_timer.resume()
            self._session_status_tracking_running = True
            return

        if poll_timer is not None:
            poll_timer.pause()
        if self._session_status_tracking_running:
            self._monitor.stop(wait=wait)
        self._session_status_tracking_running = False

    def _sync_session_status_tracking(self) -> None:
        self._set_session_status_tracking_running(self._should_run_session_status_tracking())

    def _pause_session_status_tracking(self, *, wait: bool = True) -> None:
        if self._session_status_tracking_paused:
            return
        self._session_status_tracking_paused = True
        self._set_session_status_tracking_running(False, wait=wait)

    def _resume_session_status_tracking(self) -> None:
        if not self._session_status_tracking_paused:
            return
        self._session_status_tracking_paused = False
        self._sync_session_status_tracking()

    def _trigger_status_poll(self) -> None:
        if not self._should_run_session_status_tracking():
            return
        self._poll_session_statuses()

    @work(thread=True, exclusive=True, group="status_poll")
    def _poll_session_statuses(self) -> None:
        from ...integrations.tmux import get_all_session_statuses, list_all_gd_sessions

        if not self._should_run_session_status_tracking():
            return

        entries = list_all_gd_sessions()
        statuses = get_all_session_statuses()
        self._session_statuses = statuses
        self._sessions_entries = entries
        for entry in entries:
            entry["status"] = self._resolve_session_status(entry)
        self.call_from_thread(self._on_statuses_updated)

    def _on_statuses_updated(self) -> None:
        waiting = 0
        for entry in self._sessions_entries:
            new_status = self._resolve_session_status(entry)
            entry["status"] = new_status
            if new_status == "waiting":
                waiting += 1
        count_changed = waiting != self._waiting_count
        self._waiting_count = waiting

        if self._active_tab == "sessions" and self._sessions_entries:
            self._update_session_status_cells()

        if self._active_tab == "panels":
            live_session_names = {entry["session_name"] for entry in self._sessions_entries}
            if live_session_names != self._panels_live_sessions:
                self._apply_panels_filter_and_sort(live_session_names)

        if self._active_tab == "repos" and count_changed:
            total = len(self._results)
            try:
                shown = self.query_one("#repo-table", DataTable).row_count
            except NoMatches:
                return
            self._update_status(self._build_loaded_status(shown, total))

    def _resolve_session_status(self, entry: dict[str, str]) -> str:
        from ...integrations.tmux import resolve_pane_status

        session_name = entry["session_name"]
        bell = self._monitor.get_bell_state(session_name)
        tmux_info = self._session_statuses.get(session_name)
        if tmux_info is None:
            return "waiting" if bell else "running"
        last_content_change = self._monitor.get_last_content_change_time(session_name)
        return resolve_pane_status(
            entry["purpose"],
            str(tmux_info["command"]),
            bool(tmux_info["dead"]),
            bell=bell,
            last_output_time=last_content_change,
        )

    def _update_session_status_cells(self) -> None:
        try:
            table = self.query_one("#sessions-table", DataTable)
        except NoMatches:
            return
        for entry in self._sessions_entries:
            status = self._resolve_session_status(entry)
            entry["status"] = status
            try:
                table.update_cell(
                    entry["session_name"],
                    self._sess_col_keys[0],
                    _SESSION_STATUS_LABEL.get(status, "● running"),
                )
            except Exception:
                logger.debug(
                    "Failed to update session status cell %s",
                    entry["session_name"],
                    exc_info=True,
                )
