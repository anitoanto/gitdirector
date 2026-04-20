"""Main GitDirectorConsole Textual application."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.data_table import RowDoesNotExist

from ...manager import RepositoryManager
from ...repo import RepositoryInfo
from .. import get_version
from .constants import (
    _DEFAULT_PANELS_SORT_COLUMN,
    _DEFAULT_SESSIONS_SORT_COLUMN,
    _DEFAULT_SORT_COLUMN,
    _PANEL_STATUS_LABEL,
    _PANELS_SORT_COLUMN_NAMES,
    _SESSION_STATUS_LABEL,
    _SESSION_STATUS_ORDER,
    _SESSIONS_SORT_COLUMN_NAMES,
    _SORT_COLUMN_NAMES,
    _STATUS_LABEL,
    _STATUS_ORDER,
    _changes_label,
)
from .panels import Panel, PanelStore, render_panel_layout_preview
from .screens import (
    ActionMenuScreen,
    AgentLoadingScreen,
    ConfirmScreen,
    CreatePanelScreen,
    PanelActionMenuScreen,
    RemoveSessionScreen,
    RenamePanelScreen,
    RepoInfoScreen,
    SortMenuScreen,
)

_PANEL_PREVIEW_FILLED = "■"
_PANEL_PREVIEW_OPEN = "□"


def _panel_preview_marker(panel: Panel, pane_index: int) -> str:
    return _PANEL_PREVIEW_FILLED if panel.panes.get(pane_index) else _PANEL_PREVIEW_OPEN


def _render_grid_panel_preview(panel: Panel) -> str:
    rows: list[str] = []
    for row_index in range(panel.rows):
        row_cells = " ".join(
            _panel_preview_marker(panel, (row_index * panel.cols) + col_index + 1)
            for col_index in range(panel.cols)
        )
        if row_index == 0:
            rows.append(f"┌{row_cells}┐")
        elif row_index == panel.rows - 1:
            rows.append(f"└{row_cells}┘")
        else:
            rows.append(f"│{row_cells}│")

    return "\n".join(rows)


def _render_asymmetric_panel_preview(panel: Panel) -> str:
    marker = lambda pane_index: _panel_preview_marker(panel, pane_index)
    if panel.layout.key == "tall_left":
        return "\n".join(
            [
                f"┌─┬{marker(2)}┐",
                f"│{marker(1)}├─┤",
                f"└─┴{marker(3)}┘",
            ]
        )
    if panel.layout.key == "tall_right":
        return "\n".join(
            [
                f"┌{marker(1)}┬─┐",
                f"├─┤{marker(2)}│",
                f"└{marker(3)}┴─┘",
            ]
        )
    if panel.layout.key == "wide_top":
        return "\n".join(
            [
                f"┌─{marker(1)}─┐",
                "├─┬─┤",
                f"└{marker(2)}┴{marker(3)}┘",
            ]
        )
    if panel.layout.key == "wide_bottom":
        return "\n".join(
            [
                f"┌{marker(1)}┬{marker(2)}┐",
                "├─┴─┤",
                f"└─{marker(3)}─┘",
            ]
        )
    labels = {
        placement.pane_index: marker(placement.pane_index) for placement in panel.pane_placements
    }
    return render_panel_layout_preview(panel.layout, labels=labels, cell_width=1, cell_height=1)


def _render_panel_preview(panel: Panel) -> str:
    if panel.layout.key.startswith("grid_"):
        return _render_grid_panel_preview(panel)
    return _render_asymmetric_panel_preview(panel)


def _panel_row_height(panel: Panel) -> int:
    return len(_render_panel_preview(panel).splitlines()) + 2


def _panel_row_cell(value: str) -> str:
    return f"\n{value}"


class GitDirectorConsole(App):
    TITLE = f"GitDirector [v{get_version()}]"
    CSS = """
    Screen {
        background: $surface;
        overflow: hidden;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: white;
        padding: 0 2;
    }
    #search-container {
        dock: bottom;
        height: 3;
        display: none;
        background: $boost;
        padding: 0 1;
        align: left middle;
    }
    #search-label {
        width: auto;
        color: $accent;
        padding: 0 1 0 0;
    }
    #search-bar {
        width: 1fr;
        height: 3;
        border: none;
        background: $boost;
        color: $text;
    }
    DataTable {
        height: 1fr;
        overflow-x: auto;
        overflow-y: auto;
        padding: 0 1;
    }
    #no-repos-message {
        height: 1fr;
        display: none;
        align: center middle;
        color: $text-muted;
        padding: 2 4;
        content-align: center middle;
    }
    #no-sessions-message {
        height: 1fr;
        display: none;
        align: center middle;
        color: $text-muted;
        padding: 2 4;
        content-align: center middle;
    }
    #no-panels-message {
        height: 1fr;
        display: none;
        align: center middle;
        color: $text-muted;
        padding: 2 4;
        content-align: center middle;
    }
    .search-indicator {
        dock: top;
        height: 1;
        display: none;
        background: $accent 30%;
        color: $text;
        padding: 0 2;
    }
    TabbedContent {
        height: 1fr;
    }
    #tabs Tabs {
        height: 3;
    }
    #tabs Tab {
        height: 3;
        content-align: center middle;
    }
    #tabs Tab.-active {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    #tabs Tabs:focus Tab.-active {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    TabPane {
        padding: 0;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("enter", "select_row", "Open", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("h", "cursor_left", "Left", show=False),
        Binding("l", "cursor_right", "Right", show=False),
        Binding("slash", "search", "Search", show=True),
        Binding("s", "sort", "Sort", show=True),
        Binding("i", "show_info", "Info", show=True),
        Binding("escape", "close_search", show=False),
        Binding("1", "tab_repos", "Repos", show=False),
        Binding("2", "tab_sessions", "Sessions", show=False),
        Binding("3", "tab_panels", "Panels", show=False),
        Binding("n", "new_panel", "New Panel", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        from ...integrations.tmux import TmuxMonitor

        self.manager = RepositoryManager()
        self.theme = self.manager.config.theme
        self._repo_paths: list[Path] = []
        self._results: dict[str, RepositoryInfo] = {}
        self._sessions_cache: dict[str, int] = {}
        self._search_query: str = ""
        self._sort_column: int = _DEFAULT_SORT_COLUMN
        self._sort_reverse: bool = False
        self._active_tab: str = "repos"
        self._sessions_entries: list[dict[str, str]] = []
        self._sessions_sort_column: int = _DEFAULT_SESSIONS_SORT_COLUMN
        self._sessions_sort_reverse: bool = False
        self._panels_entries: list[Panel] = []
        self._panels_sort_column: int = _DEFAULT_PANELS_SORT_COLUMN
        self._panels_sort_reverse: bool = False
        self._panels_live_sessions: set[str] = set()
        self._repos_stale: bool = False
        self._monitor = TmuxMonitor()
        self._session_statuses: dict[str, dict[str, object]] = {}
        self._waiting_count: int = 0
        self._resume_target_tab: str | None = None
        self._resume_refresh_path: Path | None = None
        self._resume_tab_activation_guard: str | None = None
        self._resume_selection_tab: str | None = None
        self._resume_selection_key: str | None = None
        self._resume_selection_row: int | None = None
        self._panel_store = PanelStore()
        self._session_status_tracking_paused = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("[1] Repositories", id="repos"):
                yield Static("", id="repo-search-indicator", classes="search-indicator")
                yield DataTable(id="repo-table", cursor_type="row")
                yield Static(
                    "No repositories linked.  Run"
                    " [bold]gitdirector link <path>[/bold] to get started.",
                    id="no-repos-message",
                )
            with TabPane("[2] Sessions", id="sessions"):
                yield Static("", id="sessions-search-indicator", classes="search-indicator")
                yield DataTable(id="sessions-table", cursor_type="row")
                yield Static(
                    "No active sessions.  Open a repository and start a tmux session"
                    " to see it here.",
                    id="no-sessions-message",
                )
            with TabPane("[3] Panels", id="panels"):
                yield Static("", id="panels-search-indicator", classes="search-indicator")
                yield DataTable(id="panels-table", cursor_type="row")
                yield Static(
                    "No panels created.  Press [bold]n[/bold] to create a new panel.",
                    id="no-panels-message",
                )
        with Horizontal(id="search-container"):
            yield Static("/ search:", id="search-label")
            yield Input(placeholder="type to filter…", id="search-bar")
        yield Static("Loading repositories…", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        self._col_keys = table.add_columns(
            "Repository", "Sync", "Branch", "Changes", "Last Commit", "Sessions", "Path"
        )
        sessions_table = self.query_one("#sessions-table", DataTable)
        self._sess_col_keys = sessions_table.add_columns(
            "Status", "Session", "Repository", "Session Name"
        )
        panels_table = self.query_one("#panels-table", DataTable)
        self._panels_col_keys = panels_table.add_columns(
            "Map", "Name", "TMUX", "Layout", "Panes", "Status"
        )
        self.app_resume_signal.subscribe(self, self._handle_app_resume)
        self._sync_tmux_theme_config(self.theme)
        self._poll_timer = self.set_interval(3, self._trigger_status_poll)
        self._monitor.start()
        self._load_repos()
        self._trigger_status_poll()

    def _sync_tmux_theme_config(self, theme_name: str | None = None) -> None:
        from ...integrations.tmux import sync_panel_tmux_config

        sync_panel_tmux_config(theme_name or self.theme)

    def _watch_theme(self, theme_name: str) -> None:
        super()._watch_theme(theme_name)

        manager = getattr(self, "manager", None)
        if manager is None:
            return

        config = manager.config
        if config.theme != theme_name:
            config.theme = theme_name
            config.save()

        self._sync_tmux_theme_config(theme_name)

    @work(thread=True)
    def _load_repos(self) -> None:
        self._repo_paths = sorted(self.manager.config.repositories, key=lambda p: p.name.lower())

        if not self._repo_paths:
            self.call_from_thread(self._show_no_repos)
            return

        self.call_from_thread(self._populate_initial_rows)

        total = len(self._repo_paths)
        done = 0
        self.call_from_thread(self._update_status, f"Checking {total} repositories…")

        from ...integrations.tmux import (
            _sanitize_repo_name,
            list_all_gd_sessions,
        )

        all_sessions = list_all_gd_sessions()
        sessions_by_repo: dict[str, int] = {}
        for entry in all_sessions:
            repo = entry["repo"]
            sessions_by_repo[repo] = sessions_by_repo.get(repo, 0) + 1

        with ThreadPoolExecutor(max_workers=self.manager.config.max_workers) as executor:
            futures = {
                executor.submit(self.manager.get_repository_status, path, fetch=True): path
                for path in self._repo_paths
            }
            for future in as_completed(futures):
                info = future.result()
                self._results[str(info.path)] = info
                done += 1
                clean_name = _sanitize_repo_name(info.path.name)
                sessions_count = sessions_by_repo.get(clean_name, 0)
                self._sessions_cache[str(info.path)] = sessions_count
                self.call_from_thread(self._update_row, info, sessions_count)
                remaining = total - done
                if remaining > 0:
                    self.call_from_thread(
                        self._update_status,
                        f"{done} done, {remaining} remaining…",
                    )

        if self._search_query or self._sort_column != _DEFAULT_SORT_COLUMN or self._sort_reverse:
            self.call_from_thread(self._apply_filter_and_sort)
        else:
            self.call_from_thread(
                self._update_status,
                self._build_loaded_status(total, total),
            )

    def _populate_initial_rows(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        table.clear()
        for path in self._repo_paths:
            table.add_row(
                path.name,
                "... ... ... ...",
                "... ... ... ...",
                "... ... ... ...",
                "... ... ... ... ... ...",
                "...",
                str(path),
                key=str(path),
            )
            self._restore_resume_selection("repos")

    def _update_row(self, info: RepositoryInfo, sessions: int = 0) -> None:
        self._sessions_cache[str(info.path)] = sessions
        table = self.query_one("#repo-table", DataTable)
        row_key = str(info.path)
        ck = self._col_keys
        try:
            table.update_cell(row_key, ck[1], _STATUS_LABEL.get(info.status, "unknown"))
            table.update_cell(row_key, ck[2], info.branch or "—")
            table.update_cell(row_key, ck[3], _changes_label(info))
            table.update_cell(row_key, ck[4], info.last_updated or "—")
            table.update_cell(row_key, ck[5], str(sessions) if sessions > 0 else "—")
        except Exception:
            pass

    def _show_no_repos(self) -> None:
        self.query_one("#repo-table", DataTable).display = False
        self.query_one("#no-repos-message", Static).display = True
        self._update_status("No repositories linked")

    # -- Tab switching --------------------------------------------------------

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
        return super().check_action(action, parameters)

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab_id = event.pane.id or ""
        if self._resume_tab_activation_guard == tab_id:
            self._resume_tab_activation_guard = None
            self._active_tab = tab_id
            self.refresh_bindings()
            return
        if self._resume_target_tab is not None:
            if tab_id != self._resume_target_tab:
                self.query_one("#tabs", TabbedContent).active = self._resume_target_tab
                return
            self._active_tab = tab_id
            self.refresh_bindings()
            return
        self._active_tab = tab_id
        self.refresh_bindings()
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
                shown = self.query_one("#repo-table", DataTable).row_count
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

    def _apply_sessions_filter_and_sort(self) -> None:
        try:
            table = self.query_one("#sessions-table", DataTable)
        except NoMatches:
            return
        table.clear()
        no_msg = self.query_one("#no-sessions-message", Static)

        entries = list(self._sessions_entries)
        total = len(entries)

        if self._search_query:
            q = self._search_query.lower()
            entries = [
                e
                for e in entries
                if q in e["session_name"].lower()
                or q in e["repo"].lower()
                or q in e["purpose"].lower()
            ]

        for entry in entries:
            entry["status"] = self._resolve_session_status(entry)

        sort_keys = {
            0: lambda e: _SESSION_STATUS_ORDER.get(e.get("status", "running"), 99),
            1: lambda e: e["purpose"].lower(),
            2: lambda e: e["repo"].lower(),
            3: lambda e: e["session_name"].lower(),
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
                table.add_row(
                    _SESSION_STATUS_LABEL.get(status, "● running"),
                    entry["purpose"],
                    entry["repo"],
                    entry["session_name"],
                    key=entry["session_name"],
                )

        self._restore_resume_selection("sessions")
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
            indicators.append(f"filter: '{self._search_query}'")
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

    def _pause_session_status_tracking(self) -> None:
        if self._session_status_tracking_paused:
            return
        self._session_status_tracking_paused = True
        poll_timer = getattr(self, "_poll_timer", None)
        if poll_timer is not None:
            poll_timer.pause()
        self._monitor.stop()

    def _resume_session_status_tracking(self) -> None:
        if not self._session_status_tracking_paused:
            return
        self._session_status_tracking_paused = False
        self._monitor.start()
        poll_timer = getattr(self, "_poll_timer", None)
        if poll_timer is not None:
            poll_timer.resume()

    # -- Session status polling -----------------------------------------------

    def _trigger_status_poll(self) -> None:
        self._poll_session_statuses()

    @work(thread=True, exclusive=True, group="status_poll")
    def _poll_session_statuses(self) -> None:
        from ...integrations.tmux import get_all_session_statuses, list_all_gd_sessions

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
            self._apply_panels_filter_and_sort(
                {entry["session_name"] for entry in self._sessions_entries}
            )

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
                pass

    def _update_status(self, message: str) -> None:
        self.query_one("#status-bar", Static).update(message)

    def _update_search_indicator(self) -> None:
        repo_ind = self.query_one("#repo-search-indicator", Static)
        sess_ind = self.query_one("#sessions-search-indicator", Static)
        panel_ind = self.query_one("#panels-search-indicator", Static)
        if self._search_query:
            text = (
                f"Search results for '[bold]{self._search_query}[/bold]'"
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
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
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

    # -- Search ---------------------------------------------------------------

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

    # -- Sort -----------------------------------------------------------------

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

    # -- Filter / sort helpers ------------------------------------------------

    def _sort_key_func(self):
        col = self._sort_column
        if col == 1:
            return lambda i: _STATUS_ORDER.get(i.status, 99)
        if col == 2:
            return lambda i: (i.branch or "").lower()
        if col == 3:
            return lambda i: _changes_label(i)
        if col == 4:
            return lambda i: i.last_commit_timestamp or 0
        if col == 5:
            return lambda i: self._sessions_cache.get(str(i.path), 0)
        if col == 6:
            return lambda i: str(i.path).lower()
        return lambda i: i.name.lower()

    def _apply_filter_and_sort(self) -> None:
        """Rebuild table rows based on current search query and sort state."""
        table = self.query_one("#repo-table", DataTable)
        table.clear()

        infos = list(self._results.values())
        total = len(infos)

        if self._search_query:
            q = self._search_query.lower()
            infos = [
                i
                for i in infos
                if q in i.name.lower() or q in (i.branch or "").lower() or q in str(i.path).lower()
            ]

        infos.sort(key=self._sort_key_func(), reverse=self._sort_reverse)

        for info in infos:
            sessions = self._sessions_cache.get(str(info.path), 0)
            table.add_row(
                info.name,
                _STATUS_LABEL.get(info.status, "unknown"),
                info.branch or "—",
                _changes_label(info),
                info.last_updated or "—",
                str(sessions) if sessions > 0 else "—",
                str(info.path),
                key=str(info.path),
            )

        self._restore_resume_selection("repos")
        self._update_status(self._build_loaded_status(len(infos), total))

    def _build_loaded_status(self, shown: int, total: int) -> str:
        if total == 0 and not self._search_query:
            return "No repositories tracked"

        if self._search_query:
            count_str = f"{shown} of {total}"
        else:
            count_str = str(total)

        label = "repository" if shown == 1 else "repositories"
        msg = f"{count_str} {label} loaded"

        indicators: list[str] = []
        if self._search_query:
            indicators.append(f"filter: '{self._search_query}'")
        if self._sort_column != _DEFAULT_SORT_COLUMN or self._sort_reverse:
            direction = "▼" if self._sort_reverse else "▲"
            indicators.append(f"sort: {_SORT_COLUMN_NAMES[self._sort_column]} {direction}")
        if indicators:
            msg += f"  ({', '.join(indicators)})"

        msg += "   ↑↓/jk navigate  [enter] open  / search  s sort  r refresh  q quit"
        if self._search_query:
            msg += "  [esc] clear search"
        if self._waiting_count > 0:
            w = self._waiting_count
            label = "session" if w == 1 else "sessions"
            msg += f"  ⟐ {w} {label} waiting"
        return msg

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
                table.add_row(
                    _panel_row_cell(_render_panel_preview(panel)),
                    _panel_row_cell(panel.name),
                    _panel_row_cell(make_panel_session_name(panel.name)),
                    _panel_row_cell(panel.layout_display_label),
                    _panel_row_cell(panes_label),
                    _panel_row_cell(status_label),
                    height=_panel_row_height(panel),
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
            indicators.append(f"filter: '{self._search_query}'")
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

    def action_select_row(self) -> None:
        if self._active_tab == "sessions":
            table = self.query_one("#sessions-table", DataTable)
            if table.row_count == 0:
                return
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            session_name = str(row_key.value)
            self._suspend_and_attach(session_name)
        elif self._active_tab == "panels":
            self._open_selected_panel_menu()
        else:
            self.action_show_menu()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "sessions-table":
            session_name = str(event.row_key.value)
            self._suspend_and_attach(session_name)
        elif event.data_table.id == "panels-table":
            self._open_selected_panel_menu()
        else:
            self.action_show_menu()

    def action_open_tmux(self, agent_cmd: str | None = None) -> None:
        """Create a new tmux session and optionally launch an AI agent in it."""
        path = self._get_selected_path()
        if path is None:
            return

        from ...integrations.tmux import create_tmux_session, launch_agent_in_tmux_session

        purpose = agent_cmd if agent_cmd else "shell"
        session_name = create_tmux_session(path.name, path, purpose=purpose)

        if agent_cmd:
            ready_marker = launch_agent_in_tmux_session(session_name, agent_cmd)
            self.push_screen(
                AgentLoadingScreen(agent_cmd, session_name, ready_marker),
                callback=lambda _: self.set_timer(0.2, lambda: self._refresh_repo_for_path(path)),
            )
        else:
            self._suspend_and_attach(session_name, path)

    def _suspend_and_attach(
        self,
        session_name: str,
        path: Path | None = None,
        row_key: str | None = None,
    ) -> None:
        """Suspend the TUI and attach to the tmux session."""
        import sys
        import termios

        from ...integrations.tmux import attach_tmux_session

        self._monitor.clear_bell(session_name)
        restore_tab = self._active_tab
        self._resume_target_tab = restore_tab
        self._resume_refresh_path = path
        row_key = row_key or (
            self._get_selected_row_key(self._get_active_table())
            if restore_tab == "panels"
            else None
        )
        self._capture_resume_selection(
            restore_tab,
            session_name=session_name,
            path=path,
            row_key=row_key,
        )

        self._pause_session_status_tracking()
        try:
            with self.suspend():
                sys.stdout.write("\033[?1049h\033[H\033[2J\033[?25l")
                sys.stdout.flush()
                attach_tmux_session(session_name)
                sys.stdout.write("\033[?25h")
                sys.stdout.flush()
                try:
                    termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
                except (AttributeError, OSError):
                    pass
        finally:
            self._resume_session_status_tracking()

        self._repos_stale = True
        self._active_tab = restore_tab

    @work(thread=True)
    def _refresh_repo_for_path(self, path: Path) -> None:
        """Re-fetch full repository status and session count for the given path."""
        from ...integrations.tmux import list_repo_sessions

        info = self.manager.get_repository_status(path, fetch=True)
        self._results[str(path)] = info
        sessions_count = len(list_repo_sessions(path.name))
        self._sessions_cache[str(path)] = sessions_count
        self.call_from_thread(self._update_row, info, sessions_count)

    def _attach_to_session(self, session_name: str, path: Path | None = None) -> None:
        """Attach to an existing tmux session."""
        self._suspend_and_attach(session_name, path)

    def action_show_menu(self) -> None:
        path = self._get_selected_path()
        if path is None:
            return
        info = self._results.get(str(path))
        branch = info.branch if info else None
        self.push_screen(
            ActionMenuScreen(path.name, path, branch),
            callback=self._handle_menu_action,
        )

    def action_show_info(self) -> None:
        if self._active_tab != "repos":
            return
        path = self._get_selected_path()
        if path is None:
            return
        screen = RepoInfoScreen(path.name, path)
        self.push_screen(screen)
        self._gather_and_show_info(path, screen)

    @work(thread=True)
    def _gather_and_show_info(self, path: Path, screen: RepoInfoScreen) -> None:
        from ...info import gather_repo_info

        result = gather_repo_info(path)
        self.call_from_thread(screen.populate, result)

    def _push_info_screen(self, name: str, path: Path, result) -> None:
        self.push_screen(RepoInfoScreen(name, path))
        total = len(self._results)
        shown = self.query_one("#repo-table", DataTable).row_count
        self._update_status(self._build_loaded_status(shown, total))

    _AGENT_COMMANDS = {
        "agent:opencode": "opencode",
        "agent:claude": "claude",
        "agent:copilot": "copilot",
        "agent:codex": "codex",
    }

    def _handle_menu_action(self, action: str | None) -> None:
        if action is None:
            return
        if action == "new_session":
            self.action_open_tmux()
        elif action in self._AGENT_COMMANDS:
            self.action_open_tmux(agent_cmd=self._AGENT_COMMANDS[action])
        elif action.startswith("attach:"):
            session_name = action[len("attach:") :]
            path = self._get_selected_path()
            self._attach_to_session(session_name, path)
        elif action == "remove_session":
            path = self._get_selected_path()
            if path:
                self.push_screen(
                    RemoveSessionScreen(path.name),
                    callback=self._handle_remove_selection,
                )

    def _handle_remove_selection(self, session_name: str | None) -> None:
        if session_name is None:
            return
        self.push_screen(
            ConfirmScreen(f"Remove session '{session_name}'?"),
            callback=lambda confirmed: self._do_remove(confirmed, session_name),
        )

    def _do_remove(self, confirmed: bool, session_name: str) -> None:
        if confirmed:
            from ...integrations.tmux import kill_tmux_session

            kill_tmux_session(session_name)

            self._sessions_entries = [
                e for e in self._sessions_entries if e["session_name"] != session_name
            ]
            self._apply_sessions_filter_and_sort()

            parts = session_name.split("/")
            if len(parts) >= 2:
                repo_name = parts[1]
                for path_str, info in self._results.items():
                    from ...integrations.tmux import _sanitize_repo_name

                    if _sanitize_repo_name(info.path.name) == repo_name:
                        count = max(self._sessions_cache.get(path_str, 1) - 1, 0)
                        self._sessions_cache[path_str] = count
                        self._update_row(info, count)
                        break

    def action_refresh(self) -> None:
        self._results.clear()
        self._sessions_cache.clear()
        self._load_repos()
        if self._active_tab == "sessions":
            self._load_sessions()
        elif self._active_tab == "panels":
            self._load_panels()

    # -- Panels ---------------------------------------------------------------

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
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return str(row_key.value)

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
        from ...integrations.tmux import make_panel_session_name

        name, layout_key, panes = result
        existing = self._panel_store.get(name)
        if existing:
            self._update_status(f"Panel '{name}' already exists")
            return

        session_name = make_panel_session_name(name)
        if any(
            make_panel_session_name(panel.name) == session_name
            for panel in self._panel_store.panels
        ):
            self._update_status(f"Panel '{name}' conflicts with an existing panel session name")
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


def _run_console() -> None:
    app = GitDirectorConsole()
    try:
        app.run()
    finally:
        app._monitor.stop()


def register(cli: click.Group):
    @cli.command(name="console")
    def console_cmd():
        """Interactive TUI for browsing and opening repositories."""
        _run_console()
