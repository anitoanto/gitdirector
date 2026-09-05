"""Session list and tmux status helpers for the TUI."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.css.query import NoMatches
from textual.widgets import DataTable, Static
from textual.worker import Worker

from .constants import (
    _DEFAULT_SESSIONS_SORT_COLUMN,
    _SESSION_STATUS_ORDER,
    _SESSIONS_SORT_COLUMN_NAMES,
    TablePalette,
)
from .table_text import wrap_table_cell_text

logger = logging.getLogger(__name__)

_MIN_SESSIONS_DESCRIPTION_WIDTH = 12
_PREFERRED_SESSIONS_DESCRIPTION_WIDTH = 30
_SESSIONS_COL_GAP = 2
_SESSIONS_STATUS_WIDTH = 9
_SESSIONS_MAX_PURPOSE_WIDTH = 36
# The repository column keeps this width regardless of the names shown, so
# the layout does not shift as sessions come and go; longer names truncate.
_SESSIONS_REPO_WIDTH = 26
_SESSIONS_MIN_PURPOSE_WIDTH = 14
_SESSIONS_MIN_REPO_WIDTH = 12
_SESSIONS_FALLBACK_TOTAL_WIDTH = 80
# The DataTable adds a cell padding of one cell on each side and the widget
# itself has ``padding: 0 1``; reserve those plus a column for the scrollbar.
_SESSIONS_TABLE_CHROME_WIDTH = 6


@dataclass(frozen=True)
class SessionsLayout:
    """Resolved column offsets for the composed sessions rows."""

    status: int
    purpose: int
    repo: int
    description: int

    @property
    def description_offset(self) -> int:
        return self.status + self.purpose + self.repo + _SESSIONS_COL_GAP * 3

    @property
    def total(self) -> int:
        return self.description_offset + self.description


def _resolve_sessions_total_width(screen_width: int) -> int:
    if screen_width <= 0:
        return _SESSIONS_FALLBACK_TOTAL_WIDTH
    return max(40, screen_width - _SESSIONS_TABLE_CHROME_WIDTH)


def _fit(values, header: str, max_width: int) -> int:
    widest = max((len(value) for value in values), default=0)
    return max(len(header), min(max_width, widest))


def _resolve_sessions_layout(entries: list[dict[str, str]], screen_width: int) -> SessionsLayout:
    """Size the row columns, giving the rest to the description.

    The session column fits its data; the repository column has a fixed
    width so it does not resize with the names it happens to contain.
    """
    total = _resolve_sessions_total_width(screen_width)
    purpose = _fit(
        (entry.get("purpose", "") for entry in entries), "Session", _SESSIONS_MAX_PURPOSE_WIDTH
    )
    repo = _SESSIONS_REPO_WIDTH
    fixed = _SESSIONS_STATUS_WIDTH + _SESSIONS_COL_GAP * 3

    # On narrow terminals give the description room to breathe by trimming the
    # widest of the two truncatable columns first; both stay readable because
    # the tmux session name below the row spells the session out in full.
    while total - fixed - purpose - repo < _PREFERRED_SESSIONS_DESCRIPTION_WIDTH:
        if purpose > _SESSIONS_MIN_PURPOSE_WIDTH and purpose >= repo:
            purpose -= 1
        elif repo > _SESSIONS_MIN_REPO_WIDTH:
            repo -= 1
        else:
            break

    description = max(_MIN_SESSIONS_DESCRIPTION_WIDTH, total - fixed - purpose - repo)
    return SessionsLayout(
        status=_SESSIONS_STATUS_WIDTH,
        purpose=purpose,
        repo=repo,
        description=description,
    )


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def _sessions_header(layout: SessionsLayout) -> Text:
    header = (
        "Status".ljust(layout.status)
        + " " * _SESSIONS_COL_GAP
        + "Session".ljust(layout.purpose)
        + " " * _SESSIONS_COL_GAP
        + "Repository".ljust(layout.repo)
        + " " * _SESSIONS_COL_GAP
        + "Description"
    )
    return Text(header.ljust(layout.total), no_wrap=True, overflow="ignore")


def _render_session_row(
    entry: dict[str, str], layout: SessionsLayout, palette: TablePalette
) -> tuple[Text, int]:
    """Render one session as a two-line block plus a trailing blank line.

    The first line holds the aligned columns, the second holds the tmux
    session name spanning the full row width, and the third is left empty so
    consecutive sessions stay visually separated.
    """
    status_label, status_style = palette.session_status(entry.get("status", "running"))
    description = wrap_table_cell_text(entry.get("description", "-") or "-", layout.description)
    description_lines = description.split("\n")

    text = Text(no_wrap=True, overflow="ignore")
    text.append(status_label.ljust(layout.status), style=status_style)
    text.append(" " * _SESSIONS_COL_GAP)
    text.append(_truncate(entry.get("purpose", ""), layout.purpose).ljust(layout.purpose))
    text.append(" " * _SESSIONS_COL_GAP)
    text.append(
        _truncate(entry.get("repo", ""), layout.repo).ljust(layout.repo),
        style=palette.yellow,
    )
    text.append(" " * _SESSIONS_COL_GAP)
    text.append(description_lines[0].ljust(layout.description))

    lines = 1
    for extra in description_lines[1:]:
        text.append("\n")
        text.append(" " * layout.description_offset)
        text.append(extra.ljust(layout.description))
        lines += 1

    text.append("\n")
    text.append("  ")
    text.append(
        _truncate(entry.get("session_name", ""), layout.total - 2).ljust(layout.total - 2),
        style=f"italic {palette.muted}",
    )
    text.append("\n")
    text.append(" " * layout.total)
    return text, lines + 2


class ConsoleSessionsMixin:
    def _next_sessions_snapshot_generation(self) -> int:
        self._sessions_snapshot_generation += 1
        return self._sessions_snapshot_generation

    def _load_sessions(self) -> Worker[None]:
        generation = self._next_sessions_snapshot_generation()
        return self._load_sessions_worker(generation)

    @work(thread=True)
    def _load_sessions_worker(self, generation: int) -> None:
        from ...integrations.tmux import list_all_gd_sessions

        self.call_from_thread(self._show_refresh_indicator)
        try:
            entries = list_all_gd_sessions()
            # A synchronous sample so a freshly opened tab shows real
            # statuses instead of waiting for the monitor's next tick.
            statuses = self._monitor.refresh()
            self.call_from_thread(
                self._apply_sessions_snapshot,
                generation,
                entries,
                statuses,
                True,
            )
        finally:
            self.call_from_thread(self._hide_refresh_indicator)

    def _apply_sessions_snapshot(
        self,
        generation: int,
        entries: list[dict[str, str]],
        statuses: dict[str, str],
        refresh_table: bool,
    ) -> None:
        if generation != self._sessions_snapshot_generation or self._shutdown_requested:
            return
        self._session_statuses = statuses
        membership_changed = {entry["session_name"] for entry in entries} != {
            entry["session_name"] for entry in self._sessions_entries
        }
        if refresh_table or (self._active_tab == "sessions" and membership_changed):
            self._populate_sessions_table(entries)
        else:
            self._sessions_entries = entries
            self._on_statuses_updated()

    def _populate_sessions_table(self, entries: list[dict[str, str]]) -> None:
        self._sessions_entries = entries
        self._apply_sessions_filter_and_sort()

    def _apply_sessions_column_layout(self, layout: SessionsLayout | None = None) -> None:
        """Resize the single sessions column and refresh its composed header."""
        try:
            table = self.query_one("#sessions-table", DataTable)
        except NoMatches:
            return
        col_keys = getattr(self, "_sess_col_keys", None)
        if not col_keys:
            return
        if layout is None:
            layout = _resolve_sessions_layout(self._sessions_entries, self.size.width)
        self._sessions_layout = layout
        try:
            column = table.columns[col_keys[0]]
        except (KeyError, IndexError):
            return
        column.auto_width = False
        column.width = layout.total
        column.label = _sessions_header(layout)
        table.refresh()

    def _apply_sessions_filter_and_sort(self) -> None:
        try:
            table = self.query_one("#sessions-table", DataTable)
        except NoMatches:
            return
        preserved_row_key = None
        preserved_row_index = None
        restore_focus = False
        if self._resume_selection_tab != "sessions":
            preserved_row_key, preserved_row_index, restore_focus = self._capture_table_selection(
                table
            )
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

        layout = _resolve_sessions_layout(entries, self.size.width)
        self._apply_sessions_column_layout(layout)

        is_empty = not entries and total == 0 and not self._search_query
        self._set_table_empty_state(table, no_msg, is_empty=is_empty)
        table.clear()
        if not is_empty:
            for entry in entries:
                row, height = _render_session_row(entry, layout, self._palette)
                table.add_row(row, height=height, key=entry["session_name"])
                entry["_rendered_status"] = entry["status"]

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
        self._next_sessions_snapshot_generation()
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

    def _poll_session_statuses(self) -> Worker[None]:
        generation = self._next_sessions_snapshot_generation()
        return self._poll_session_statuses_worker(generation)

    @work(thread=True, exclusive=True, group="status_poll")
    def _poll_session_statuses_worker(self, generation: int) -> None:
        from ...integrations.tmux import list_all_gd_sessions

        if not self._should_run_session_status_tracking():
            return

        # The monitor samples tmux on its own cadence and already carries
        # every session's metadata, so this poll normally costs no tmux call.
        entries = self._monitor.entries()
        if entries is None:
            entries = list_all_gd_sessions()
        statuses = self._monitor.statuses()
        self.call_from_thread(
            self._apply_sessions_snapshot,
            generation,
            entries,
            statuses,
            False,
        )

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
                self.query_one("#repo-table", DataTable)
            except NoMatches:
                return
            shown = getattr(self, "_visible_repo_count", total)
            self._update_status(self._build_loaded_status(shown, total))

    def _resolve_session_status(self, entry: dict[str, str]) -> str:
        """The monitor's verdict for a session, or a neutral default.

        A session the monitor has not sampled yet (it was just created) is
        shown as running until the next sample, unless a bell already
        arrived for it.
        """
        session_name = entry["session_name"]
        status = self._session_statuses.get(session_name)
        if status is not None:
            return status
        return "waiting" if self._monitor.get_bell_state(session_name) else "running"

    def _update_session_status_cells(self) -> None:
        try:
            table = self.query_one("#sessions-table", DataTable)
        except NoMatches:
            return
        layout = getattr(self, "_sessions_layout", None) or _resolve_sessions_layout(
            self._sessions_entries, self.size.width
        )
        for entry in self._sessions_entries:
            status = self._resolve_session_status(entry)
            if entry.get("status") == status and entry.get("_rendered_status") == status:
                continue
            entry["status"] = status
            try:
                row, _height = _render_session_row(entry, layout, self._palette)
                table.update_cell(entry["session_name"], self._sess_col_keys[0], row)
                entry["_rendered_status"] = status
            except Exception:
                logger.debug(
                    "Failed to update session status cell %s",
                    entry["session_name"],
                    exc_info=True,
                )
