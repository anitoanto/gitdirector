"""Main GitDirectorConsole Textual application."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
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
    _DEFAULT_SESSIONS_SORT_COLUMN,
    _DEFAULT_SORT_COLUMN,
    _SESSION_STATUS_LABEL,
    _SESSION_STATUS_ORDER,
    _SESSIONS_SORT_COLUMN_NAMES,
    _SORT_COLUMN_NAMES,
    _STATUS_LABEL,
    _STATUS_ORDER,
    _changes_label,
)
from .screens import (
    ActionMenuScreen,
    AgentLoadingScreen,
    ConfirmScreen,
    RemoveSessionScreen,
    RepoInfoScreen,
    SortMenuScreen,
)


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
        Binding("1", "tab_repos", "Repos", show=True),
        Binding("2", "tab_sessions", "Sessions", show=True),
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
        self._repos_stale: bool = False
        self._monitor = TmuxMonitor()
        self._session_statuses: dict[str, dict[str, object]] = {}
        self._waiting_count: int = 0
        self._resume_target_tab: str | None = None
        self._resume_refresh_path: Path | None = None
        self._resume_selection_tab: str | None = None
        self._resume_selection_key: str | None = None
        self._resume_selection_row: int | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("Repositories", id="repos"):
                yield Static("", id="repo-search-indicator", classes="search-indicator")
                yield DataTable(id="repo-table", cursor_type="row")
                yield Static(
                    "No repositories linked.  Run"
                    " [bold]gitdirector link <path>[/bold] to get started.",
                    id="no-repos-message",
                )
            with TabPane("Sessions", id="sessions"):
                yield Static("", id="sessions-search-indicator", classes="search-indicator")
                yield DataTable(id="sessions-table", cursor_type="row")
                yield Static(
                    "No active sessions.  Open a repository and start a tmux session"
                    " to see it here.",
                    id="no-sessions-message",
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
        self.app_resume_signal.subscribe(self, self._handle_app_resume)
        self._poll_timer = self.set_interval(3, self._trigger_status_poll)
        self._monitor.start()
        self._load_repos()

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

        from ...integrations.tmux import _sanitize_repo_name, list_all_gd_sessions

        all_sessions = list_all_gd_sessions()
        sessions_by_repo: dict[str, int] = {}
        for entry in all_sessions:
            repo = entry["repo"]
            sessions_by_repo[repo] = sessions_by_repo.get(repo, 0) + 1

        with ThreadPoolExecutor(max_workers=self.manager.config.max_workers) as executor:
            futures = {
                executor.submit(self.manager.get_repository_status, path): path
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

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab_id = event.pane.id or ""
        if self._resume_target_tab is not None:
            if tab_id != self._resume_target_tab:
                self.query_one("#tabs", TabbedContent).active = self._resume_target_tab
                return
            self._active_tab = tab_id
            return
        self._active_tab = tab_id
        if tab_id == "sessions":
            self._load_sessions()
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
            tabs.active = restore_tab
        self._active_tab = restore_tab

        if restore_tab == "sessions":
            self._load_sessions()
            self.query_one("#sessions-table", DataTable).focus()
        else:
            self.query_one("#repo-table", DataTable).focus()
            self._restore_resume_selection("repos")

        if restore_path is not None:
            self._refresh_repo_for_path(restore_path)

        self.call_after_refresh(self._clear_resume_restore, restore_tab)

    def _clear_resume_restore(self, restore_tab: str) -> None:
        tabs = self.query_one("#tabs", TabbedContent)
        if self._resume_target_tab == restore_tab and tabs.active == restore_tab:
            self._resume_target_tab = None
            self._resume_refresh_path = None

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
        table = self.query_one("#sessions-table", DataTable)
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

    # -- Session status polling -----------------------------------------------

    def _trigger_status_poll(self) -> None:
        self._poll_session_statuses()

    @work(thread=True, exclusive=True, group="status_poll")
    def _poll_session_statuses(self) -> None:
        from ...integrations.tmux import get_all_session_statuses

        statuses = get_all_session_statuses()
        self._session_statuses = statuses
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
        elif count_changed:
            total = len(self._results)
            shown = self.query_one("#repo-table", DataTable).row_count
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
        table = self.query_one("#sessions-table", DataTable)
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
        if self._search_query:
            text = (
                f"Search results for '[bold]{self._search_query}[/bold]'"
                "  —  press [bold]esc[/bold] to clear"
            )
            repo_ind.update(text)
            sess_ind.update(text)
            repo_ind.display = True
            sess_ind.display = True
        else:
            repo_ind.display = False
            sess_ind.display = False

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

    def _capture_resume_selection(
        self,
        tab_id: str,
        *,
        session_name: str | None = None,
        path: Path | None = None,
    ) -> None:
        self._resume_selection_tab = tab_id
        if not self.is_running:
            self._resume_selection_row = None
            if tab_id == "sessions":
                self._resume_selection_key = session_name
            else:
                self._resume_selection_key = str(path) if path is not None else None
            return

        table = self.query_one(
            "#sessions-table" if tab_id == "sessions" else "#repo-table",
            DataTable,
        )
        self._resume_selection_row = table.cursor_coordinate.row if table.row_count > 0 else None
        if tab_id == "sessions":
            self._resume_selection_key = session_name or self._get_selected_row_key(table)
        else:
            self._resume_selection_key = (
                str(path) if path is not None else self._get_selected_row_key(table)
            )

    def _clear_resume_selection(self) -> None:
        self._resume_selection_tab = None
        self._resume_selection_key = None
        self._resume_selection_row = None

    def _restore_resume_selection(self, tab_id: str) -> None:
        if self._resume_selection_tab != tab_id:
            return

        table = self.query_one(
            "#sessions-table" if tab_id == "sessions" else "#repo-table",
            DataTable,
        )
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

    def _get_active_table(self) -> DataTable:
        if self._active_tab == "sessions":
            return self.query_one("#sessions-table", DataTable)
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

    def action_close_search(self) -> None:
        container = self.query_one("#search-container")
        if container.display:
            self.query_one("#search-bar", Input).value = ""
            container.display = False
            self._search_query = ""
            self._update_search_indicator()
            if self._active_tab == "sessions":
                self._apply_sessions_filter_and_sort()
            else:
                self._apply_filter_and_sort()
            self._get_active_table().focus()
        elif self._search_query:
            self._search_query = ""
            self._update_search_indicator()
            if self._active_tab == "sessions":
                self._apply_sessions_filter_and_sort()
            else:
                self._apply_filter_and_sort()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-bar":
            self._search_query = event.value
            if self._active_tab == "sessions":
                self._apply_sessions_filter_and_sort()
            else:
                self._apply_filter_and_sort()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-bar":
            self._search_query = event.value
            self._update_search_indicator()
            if self._active_tab == "sessions":
                self._apply_sessions_filter_and_sort()
            else:
                self._apply_filter_and_sort()
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

    def action_select_row(self) -> None:
        if self._active_tab == "sessions":
            table = self.query_one("#sessions-table", DataTable)
            if table.row_count == 0:
                return
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            session_name = str(row_key.value)
            self._suspend_and_attach(session_name)
        else:
            self.action_show_menu()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "sessions-table":
            session_name = str(event.row_key.value)
            self._suspend_and_attach(session_name)
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

    def _suspend_and_attach(self, session_name: str, path: Path | None = None) -> None:
        """Suspend the TUI and attach to the tmux session."""
        import sys
        import termios

        from ...integrations.tmux import attach_tmux_session

        self._monitor.clear_bell(session_name)
        restore_tab = self._active_tab
        self._resume_target_tab = restore_tab
        self._resume_refresh_path = path
        self._capture_resume_selection(restore_tab, session_name=session_name, path=path)

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

        self._repos_stale = True
        self._active_tab = restore_tab

    @work(thread=True)
    def _refresh_repo_for_path(self, path: Path) -> None:
        """Re-fetch full repository status and session count for the given path."""
        from ...integrations.tmux import list_repo_sessions

        info = self.manager.get_repository_status(path)
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
