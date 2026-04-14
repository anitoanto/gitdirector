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

from ...manager import RepositoryManager
from ...repo import RepositoryInfo
from .constants import (
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
    SortMenuScreen,
)


class GitDirectorConsole(App):
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
        Binding("escape", "close_search", show=False),
        Binding("1", "tab_repos", "Repos", show=True),
        Binding("2", "tab_sessions", "Sessions", show=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.manager = RepositoryManager()
        self._repo_paths: list[Path] = []
        self._results: dict[str, RepositoryInfo] = {}
        self._sessions_cache: dict[str, int] = {}
        self._search_query: str = ""
        self._sort_column: int = 0
        self._sort_reverse: bool = False
        self._active_tab: str = "repos"
        self._sessions_entries: list[dict[str, str]] = []
        self._sessions_sort_column: int = 0
        self._sessions_sort_reverse: bool = False
        self._repos_stale: bool = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("Repositories", id="repos"):
                yield DataTable(id="repo-table", cursor_type="row")
                yield Static(
                    "No repositories linked.  Run"
                    " [bold]gitdirector link <path>[/bold] to get started.",
                    id="no-repos-message",
                )
            with TabPane("Sessions", id="sessions"):
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
        self._sess_col_keys = sessions_table.add_columns("Session", "Repository", "Session Name")
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

        with ThreadPoolExecutor(max_workers=self.manager.config.max_workers) as executor:
            futures = {
                executor.submit(self.manager.get_repository_status, path): path
                for path in self._repo_paths
            }
            for future in as_completed(futures):
                from ...integrations.tmux import list_repo_sessions

                info = future.result()
                self._results[str(info.path)] = info
                done += 1
                sessions_count = len(list_repo_sessions(info.path.name))
                self._sessions_cache[str(info.path)] = sessions_count
                self.call_from_thread(self._update_row, info, sessions_count)
                remaining = total - done
                if remaining > 0:
                    self.call_from_thread(
                        self._update_status,
                        f"{done} done, {remaining} remaining…",
                    )

        if self._search_query or self._sort_column != 0 or self._sort_reverse:
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
        self.query_one("#tabs", TabbedContent).active = "repos"

    def action_tab_sessions(self) -> None:
        self.query_one("#tabs", TabbedContent).active = "sessions"

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab_id = event.pane.id or ""
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

    @work(thread=True)
    def _load_sessions(self) -> None:
        from ...integrations.tmux import list_all_gd_sessions

        self.call_from_thread(self._update_status, "Loading sessions…")
        entries = list_all_gd_sessions()
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
                or q in e["slug"].lower()
            ]

        sort_keys = {
            0: lambda e: e["slug"].lower(),
            1: lambda e: e["repo"].lower(),
            2: lambda e: e["session_name"].lower(),
        }
        key_func = sort_keys.get(self._sessions_sort_column, sort_keys[0])
        entries.sort(key=key_func, reverse=self._sessions_sort_reverse)

        if not entries and total == 0 and not self._search_query:
            table.display = False
            no_msg.display = True
        else:
            table.display = True
            no_msg.display = False
            for entry in entries:
                table.add_row(
                    entry["slug"], entry["repo"], entry["session_name"], key=entry["session_name"]
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
            indicators.append(f"filter: '{self._search_query}'")
        if self._sessions_sort_column != 0 or self._sessions_sort_reverse:
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

    def _update_status(self, message: str) -> None:
        self.query_one("#status-bar", Static).update(message)

    def _get_selected_path(self) -> Path | None:
        table = self.query_one("#repo-table", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return Path(str(row_key.value))

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
            if self._active_tab == "sessions":
                self._apply_sessions_filter_and_sort()
            else:
                self._apply_filter_and_sort()
            self._get_active_table().focus()
        elif self._search_query:
            self._search_query = ""
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
        if self._sort_column != 0 or self._sort_reverse:
            direction = "▼" if self._sort_reverse else "▲"
            indicators.append(f"sort: {_SORT_COLUMN_NAMES[self._sort_column]} {direction}")
        if indicators:
            msg += f"  ({', '.join(indicators)})"

        msg += "   ↑↓/jk navigate  [enter] open  / search  s sort  r refresh  q quit"
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

        import subprocess

        from ...integrations.tmux import create_tmux_session

        session_name = create_tmux_session(path.name, path)

        if agent_cmd:
            subprocess.run(
                ["tmux", "send-keys", "-t", session_name, f"clear && {agent_cmd}", "Enter"],
                check=False,
            )
            self.push_screen(
                AgentLoadingScreen(agent_cmd, session_name),
                callback=lambda _: self.set_timer(0.2, lambda: self._refresh_repo_for_path(path)),
            )
        else:
            self._suspend_and_attach(session_name, path)

    def _suspend_and_attach(self, session_name: str, path: Path | None = None) -> None:
        """Suspend the TUI and attach to the tmux session."""
        import sys

        from ...integrations.tmux import attach_tmux_session

        with self.suspend():
            sys.stdout.write("\033[?1049h\033[H\033[2J\033[?25l")
            sys.stdout.flush()
            attach_tmux_session(session_name)
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()

        self._repos_stale = True
        if path is not None:
            self.set_timer(0.2, lambda: self._refresh_repo_for_path(path))
        if self._active_tab == "sessions":
            self.set_timer(0.3, lambda: self._load_sessions())

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
            self._update_status(f"Session '{session_name}' removed")

    def action_refresh(self) -> None:
        self._results.clear()
        self._sessions_cache.clear()
        self._load_repos()
        if self._active_tab == "sessions":
            self._load_sessions()


def _run_console() -> None:
    app = GitDirectorConsole()
    app.run()


def register(cli: click.Group):
    @cli.command(name="console")
    def console_cmd():
        """Interactive TUI for browsing and opening repositories."""
        _run_console()
