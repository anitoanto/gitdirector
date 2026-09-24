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

from .constants import TablePalette
from .table_text import wrap_table_cell_text

logger = logging.getLogger(__name__)

_MIN_SESSIONS_ID_WIDTH = 12
_PREFERRED_SESSIONS_ID_WIDTH = 30
_NO_DESCRIPTION = "no description"
_SESSIONS_COL_GAP = 2
_SESSIONS_STATUS_WIDTH = 9
_SESSIONS_MAX_PURPOSE_WIDTH = 36
# The repository column keeps this width regardless of the names shown, so
# the layout does not shift as sessions come and go; longer names truncate.
_SESSIONS_REPO_WIDTH = 26
_SESSIONS_MIN_PURPOSE_WIDTH = 14
_SESSIONS_MIN_REPO_WIDTH = 12
_SESSIONS_FALLBACK_TOTAL_WIDTH = 80
# The table's own cell padding is off; each line carries this padding itself.
_SESSIONS_CELL_PADDING = 1
# The tab's own margin, the widget's ``padding: 0 1``, and the scrollbar,
# less the cell padding each line draws itself.
_SESSIONS_TABLE_CHROME_WIDTH = 6
# A repo's sessions hang off one guide line in front of the repo column.
_GUIDE_WIDTH = 2
_BRACKET_OPEN = "╭"
_BRACKET_SIDE = "│"
_BRACKET_CLOSE = "╰"


@dataclass(frozen=True)
class SessionsLayout:
    """Resolved column widths for the composed sessions rows."""

    repo: int
    status: int
    purpose: int
    session_id: int

    @property
    def status_offset(self) -> int:
        return _GUIDE_WIDTH + self.repo + _SESSIONS_COL_GAP

    @property
    def purpose_offset(self) -> int:
        return self.status_offset + self.status + _SESSIONS_COL_GAP

    @property
    def session_id_offset(self) -> int:
        return self.purpose_offset + self.purpose + _SESSIONS_COL_GAP

    @property
    def total(self) -> int:
        return self.session_id_offset + self.session_id

    @property
    def cell_width(self) -> int:
        return self.total + _SESSIONS_CELL_PADDING * 2


def _resolve_sessions_total_width(screen_width: int) -> int:
    if screen_width <= 0:
        return _SESSIONS_FALLBACK_TOTAL_WIDTH
    return max(40, screen_width - _SESSIONS_TABLE_CHROME_WIDTH)


def _fit(values, header: str, max_width: int) -> int:
    widest = max((len(value) for value in values), default=0)
    return max(len(header), min(max_width, widest))


def _resolve_sessions_layout(entries: list[dict[str, str]], screen_width: int) -> SessionsLayout:
    """Size the row columns, giving the rest to the tmux session name.

    The session column fits its data; the repository column has a fixed
    width so it does not resize with the names it happens to contain.
    """
    total = _resolve_sessions_total_width(screen_width)
    purpose = _fit(
        (entry.get("purpose", "") for entry in entries), "Session", _SESSIONS_MAX_PURPOSE_WIDTH
    )
    repo = _SESSIONS_REPO_WIDTH
    fixed = _GUIDE_WIDTH + _SESSIONS_STATUS_WIDTH + _SESSIONS_COL_GAP * 3

    # On narrow terminals give the session name room by trimming the widest of
    # the two truncatable columns first; the session name spells both out.
    while total - fixed - purpose - repo < _PREFERRED_SESSIONS_ID_WIDTH:
        if purpose > _SESSIONS_MIN_PURPOSE_WIDTH and purpose >= repo:
            purpose -= 1
        elif repo > _SESSIONS_MIN_REPO_WIDTH:
            repo -= 1
        else:
            break

    session_id = max(_MIN_SESSIONS_ID_WIDTH, total - fixed - purpose - repo)
    return SessionsLayout(
        repo=repo,
        status=_SESSIONS_STATUS_WIDTH,
        purpose=purpose,
        session_id=session_id,
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
        " " * (_SESSIONS_CELL_PADDING + _GUIDE_WIDTH)
        + "Repository".ljust(layout.repo)
        + " " * _SESSIONS_COL_GAP
        + "Status".ljust(layout.status)
        + " " * _SESSIONS_COL_GAP
        + "Session".ljust(layout.purpose)
        + " " * _SESSIONS_COL_GAP
        + "Session ID"
    )
    return Text(header.ljust(layout.cell_width), no_wrap=True, overflow="ignore")


def session_matches(query: str, *fields: str) -> bool:
    """Whether a search for *query* finds a session: its name, repo, purpose, description."""
    query = query.strip().lower()
    return not query or any(query in field.lower() for field in fields)


def _session_order(entry: dict[str, str]) -> tuple[str, str, str, int]:
    """A repo's sessions stay together: by repo, then purpose, then number."""
    sequence = entry.get("session_name", "").rsplit("/", 1)[-1]
    return (
        entry.get("repo", "").casefold(),
        entry.get("repo_slug", ""),
        entry.get("purpose", ""),
        int(sequence) if sequence.isdigit() else 0,
    )


def _repo_positions(entries: list[dict[str, str]]) -> dict[str, str]:
    """Each session's place in its repo's run: ``only``, ``first``, ``middle`` or ``last``."""
    positions: dict[str, str] = {}
    runs: list[list[str]] = []
    previous = None
    for entry in entries:
        repo = entry.get("repo_slug") or entry.get("repo", "")
        if repo != previous or not runs:
            runs.append([])
            previous = repo
        runs[-1].append(entry["session_name"])
    for run in runs:
        for index, name in enumerate(run):
            if len(run) == 1:
                positions[name] = "only"
            elif index == 0:
                positions[name] = "first"
            elif index == len(run) - 1:
                positions[name] = "last"
            else:
                positions[name] = "middle"
    return positions


@dataclass(frozen=True)
class RowGuide:
    """The bracket column of one row: its first line, the lines below, a spacer."""

    top: str = " "
    side: str = " "
    #: An extra line after the row, holding this bracket mark (None: no line).
    spacer: str | None = None


def _row_guides(entries: list[dict[str, str]], positions: dict[str, str]) -> dict[str, RowGuide]:
    """How the bracket holding a repo's sessions runs through each row.

    A group of two or more stands apart: it opens with ``╭`` on the line
    above its repo name, runs ``│`` down every line of its sessions, and
    closes with ``╰`` on a line after the last one. The opening line ends
    the row above, so a highlighted row never carries a blank line on top.
    """
    names = [entry["session_name"] for entry in entries]
    guides: dict[str, RowGuide] = {}
    opened = False
    for index, name in enumerate(names):
        position = positions.get(name, "only")
        following = positions.get(names[index + 1]) if index + 1 < len(names) else None
        if position == "only":
            spacer = _BRACKET_OPEN if following == "first" else None
            guides[name] = RowGuide(spacer=spacer)
        elif position == "last":
            guides[name] = RowGuide(_BRACKET_SIDE, _BRACKET_SIDE, _BRACKET_CLOSE)
        else:
            top = _BRACKET_SIDE if position == "middle" or opened else _BRACKET_OPEN
            guides[name] = RowGuide(top, _BRACKET_SIDE)
        opened = guides[name].spacer == _BRACKET_OPEN
    return guides


def _wrap_session_name(name: str, width: int) -> list[str]:
    """*name* split into lines of *width*, breaking after a ``/`` where possible."""
    lines: list[str] = []
    while len(name) > width:
        cut = name.rfind("/", 0, width) + 1 or width
        lines.append(name[:cut])
        name = name[cut:]
    return [*lines, name]


def _render_session_row(
    entry: dict[str, str],
    layout: SessionsLayout,
    palette: TablePalette,
    *,
    position: str = "only",
    guide: RowGuide | None = None,
) -> tuple[Text, int]:
    """Render one session: its columns, its description, and a blank line.

    The sessions of one repo sit inside a bracket (see :func:`_row_guides`)
    and only the first names the repo, so a group reads at a glance. The
    description sits under the status and wraps instead of truncating.
    """
    guide = guide or RowGuide()
    status_label, status_style = palette.session_status(entry.get("status", "running"))
    session_lines = _wrap_session_name(entry.get("session_name", ""), layout.session_id)
    pad = " " * _SESSIONS_CELL_PADDING
    text = Text(no_wrap=True, overflow="ignore")

    def lower_line(offset: int, mark: str = guide.side) -> None:
        text.append("\n")
        text.append(pad)
        text.append(f"{mark} ", style=palette.muted)
        text.append(" " * (offset - _GUIDE_WIDTH))

    text.append(pad)
    text.append(f"{guide.top} ", style=palette.muted)
    repo = entry.get("repo", "") if position in ("only", "first") else ""
    text.append(_truncate(repo, layout.repo).ljust(layout.repo), style=f"bold {palette.yellow}")
    text.append(" " * _SESSIONS_COL_GAP)
    text.append(status_label.ljust(layout.status), style=status_style)
    text.append(" " * _SESSIONS_COL_GAP)
    text.append(_truncate(entry.get("purpose", ""), layout.purpose).ljust(layout.purpose))
    text.append(" " * _SESSIONS_COL_GAP)
    text.append(session_lines[0].ljust(layout.session_id), style=palette.muted)
    text.append(pad)
    for extra in session_lines[1:]:
        lower_line(layout.session_id_offset)
        text.append(extra.ljust(layout.session_id), style=palette.muted)
        text.append(pad)

    description = (entry.get("description") or "").strip()
    width = layout.total - layout.status_offset
    if description and description != "-":
        description_lines = wrap_table_cell_text(description, width).split("\n")
        style = ""
    else:
        description_lines = [_NO_DESCRIPTION]
        style = f"italic {palette.muted}"
    for line in description_lines:
        lower_line(layout.status_offset)
        text.append(line.ljust(width), style=style)
        text.append(pad)

    lower_line(layout.cell_width - _SESSIONS_CELL_PADDING)
    if guide.spacer is not None:
        lower_line(layout.cell_width - _SESSIONS_CELL_PADDING, guide.spacer)
    blank_lines = 2 if guide.spacer is not None else 1
    return text, len(session_lines) + len(description_lines) + blank_lines


_ROW_STATE_KEYS = frozenset({"status"})


def _session_rows(entries: list[dict[str, str]]) -> list[tuple]:
    """What a sessions table built from *entries* shows, minus live status."""
    return [
        tuple(sorted((k, v) for k, v in entry.items() if k not in _ROW_STATE_KEYS))
        for entry in entries
    ]


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
            try:
                entries = list_all_gd_sessions()
                # A synchronous sample so a freshly opened tab shows real
                # statuses instead of waiting for the monitor's next tick.
                statuses = self._monitor.refresh()
            except Exception:
                # This load runs from startup and after every launch, so a
                # missing tmux or a hung server must log, not take the
                # console down with a worker error. The cache stays as is.
                logger.warning("Loading tmux sessions failed", exc_info=True)
                return
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
        self._sessions_loaded = True
        self._session_statuses = statuses
        rows_changed = _session_rows(entries) != _session_rows(self._sessions_entries)
        # Off the Sessions tab only the cache is updated: the table is
        # repainted from it on activation, and touching it here would
        # also overwrite the active tab's status bar.
        if self._active_tab == "sessions" and (refresh_table or rows_changed):
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
        column.width = layout.cell_width
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
            entries = [
                entry
                for entry in entries
                if session_matches(
                    self._search_query,
                    entry["session_name"],
                    entry["repo"],
                    entry["purpose"],
                    entry.get("description", ""),
                )
            ]

        for entry in entries:
            entry["status"] = self._resolve_session_status(entry)

        entries.sort(key=_session_order)

        layout = _resolve_sessions_layout(entries, self.size.width)
        self._apply_sessions_column_layout(layout)

        is_empty = not entries and total == 0 and not self._search_query
        self._set_table_empty_state(table, no_msg, is_empty=is_empty)
        table.clear()
        self._rendered_session_status = {}
        if not is_empty:
            positions = _repo_positions(entries)
            guides = _row_guides(entries, positions)
            self._session_positions = positions
            self._session_guides = guides
            for entry in entries:
                name = entry["session_name"]
                row, height = _render_session_row(
                    entry,
                    layout,
                    self._palette,
                    position=positions[name],
                    guide=guides[name],
                )
                table.add_row(row, height=height, key=entry["session_name"])
                self._rendered_session_status[entry["session_name"]] = entry["status"]

        if self._resume_selection_tab == "sessions":
            self._restore_resume_selection("sessions")
        else:
            self._restore_table_selection(
                table,
                preserved_row_key,
                preserved_row_index,
                restore_focus=restore_focus,
            )
        # Repainting a hidden tab's table (a theme change, a removed
        # session) must not take over the visible tab's status bar.
        if self._active_tab == "sessions":
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
        if indicators:
            msg += f"  ({', '.join(indicators)})"

        msg += "   ↑↓/jk navigate  [enter] attach  1 repos  2 sessions  r refresh  q quit"
        if self._search_query:
            msg += "  [esc] clear search"
        return msg

    def _should_run_session_status_tracking(self) -> bool:
        # Tracking runs on every tab, not just Sessions, so the session
        # list and statuses are already current when the user switches
        # over; the tab then repaints from the cache instead of loading.
        # It only stops while the TUI is suspended (attach) or quitting.
        return not self._session_status_tracking_paused

    def _show_sessions_tab(self) -> None:
        """Bring the Sessions tab up to date on activation.

        The background tracking keeps ``_sessions_entries`` fresh on every
        tab, so a tab that has already been loaded once repaints
        synchronously from the cache: no worker, no refresh indicator, no
        stale rows. The first activation before any snapshot arrived (or
        after a failed one) still loads from tmux.
        """
        if self._sessions_loaded:
            self._apply_sessions_filter_and_sort()
        else:
            self._load_sessions()

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

    def _poll_session_statuses(self) -> None:
        """Pick up the monitor's latest sample.

        The monitor samples tmux on its own thread and keeps the result in
        memory, so this costs no tmux call and runs on the UI thread. Before
        its first sample there is nothing newer than the initial load.
        """
        entries = self._monitor.entries()
        if entries is None:
            return
        self._apply_sessions_snapshot(
            self._next_sessions_snapshot_generation(),
            entries,
            self._monitor.statuses(),
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
        rendered = self._rendered_session_status
        # The guides of the rows on screen, which a search may have thinned out.
        positions = getattr(self, "_session_positions", {})
        guides = getattr(self, "_session_guides", {})
        for entry in self._sessions_entries:
            session_name = entry["session_name"]
            status = self._resolve_session_status(entry)
            entry["status"] = status
            # Rows filtered out by a search are not in the table at all.
            if session_name not in rendered or rendered[session_name] == status:
                continue
            try:
                row, _height = _render_session_row(
                    entry,
                    layout,
                    self._palette,
                    position=positions.get(session_name, "only"),
                    guide=guides.get(session_name),
                )
                table.update_cell(session_name, self._sess_col_keys[0], row)
                rendered[session_name] = status
            except Exception:
                logger.debug(
                    "Failed to update session status cell %s",
                    entry["session_name"],
                    exc_info=True,
                )
