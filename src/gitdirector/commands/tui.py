"""Interactive TUI console for GitDirector using Textual."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from ..manager import RepositoryManager
from ..repo import RepositoryInfo, RepoStatus

_STATUS_LABEL = {
    RepoStatus.UP_TO_DATE: "up to date",
    RepoStatus.BEHIND: "behind",
    RepoStatus.AHEAD: "ahead",
    RepoStatus.DIVERGED: "diverged",
    RepoStatus.UNKNOWN: "unknown",
}


def _changes_label(info: RepositoryInfo) -> str:
    if info.staged and info.unstaged:
        return "staged+unstaged"
    if info.staged:
        return "staged"
    if info.unstaged:
        return "unstaged"
    return "—"


_SORT_COLUMN_NAMES = {
    0: "Repository",
    1: "Sync",
    2: "Branch",
    3: "Changes",
    4: "Last Commit",
    5: "Sessions",
    6: "Path",
}

_STATUS_ORDER = {
    RepoStatus.UP_TO_DATE: 0,
    RepoStatus.AHEAD: 1,
    RepoStatus.BEHIND: 2,
    RepoStatus.DIVERGED: 3,
    RepoStatus.UNKNOWN: 4,
}


_MODAL_CSS = """
    #menu-container {
        width: 50%;
        height: auto;
        border: panel $panel;
        background: $panel;
        padding: 1 2;
    }
    #menu-title {
        text-align: center;
        padding: 1 1 0 1;
        color: $text;
    }
    #menu-branch {
        text-align: center;
        padding: 0 1 1 1;
        color: $text-muted;
    }
    #action-menu {
        width: 1fr;
        height: auto;
        border: none;
        padding: 1 2;
        margin: 1 0;
    }
    #menu-hint {
        text-align: center;
        padding: 1 1 1 1;
        color: $text-muted;
    }
"""

_MODAL_BINDINGS = [
    Binding("escape", "cancel", "Esc close", show=True),
    Binding("j", "cursor_down", "↓", show=False),
    Binding("k", "cursor_up", "↑", show=False),
]


class ActionMenuScreen(ModalScreen[str]):
    """Modal popup with actions for the selected repository."""

    BINDINGS = _MODAL_BINDINGS

    CSS = "ActionMenuScreen { align: center middle; }" + _MODAL_CSS

    def __init__(self, repo_name: str, repo_path: Path, branch: str | None = None) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path
        self.branch = branch

    def compose(self) -> ComposeResult:
        from ..integrations.tmux import list_repo_sessions

        sessions = list_repo_sessions(self.repo_name)

        with Vertical(id="menu-container"):
            yield Static(f"[bold white]{self.repo_name}[/bold white]", id="menu-title")
            yield Static(
                f"[dim]branch:[/dim] [cyan]{self.branch or '—'}[/cyan]",
                id="menu-branch",
            )
            items: list[Option] = [
                Option("[white]＋[/white] [bold]TMUX Session[/bold]", id="new_session"),
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
                    slug = s.rsplit("-", 1)[-1] if "-" in s else s
                    items.append(
                        Option(
                            f"[white]●[/white] [bold]{slug}[/bold] [dim]{s}[/dim]",
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

    CSS = "RemoveSessionScreen { align: center middle; }" + _MODAL_CSS

    def __init__(self, repo_name: str) -> None:
        super().__init__()
        self.repo_name = repo_name

    def compose(self) -> ComposeResult:
        from ..integrations.tmux import list_repo_sessions

        sessions = list_repo_sessions(self.repo_name)

        with Vertical(id="menu-container"):
            yield Static("[bold white]Select session to remove[/bold white]", id="menu-title")
            if sessions:
                options = [
                    Option(
                        f"[red]●[/red] [bold]{s.rsplit('-', 1)[-1]}[/bold] [dim]{s}[/dim]",
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

    CSS = "ConfirmScreen { align: center middle; }" + _MODAL_CSS

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

    CSS = "SortMenuScreen { align: center middle; }" + _MODAL_CSS

    def __init__(self, current_column: int, current_reverse: bool) -> None:
        super().__init__()
        self._current_column = current_column
        self._current_reverse = current_reverse

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static("[bold white]Sort by[/bold white]", id="menu-title")
            items: list[Option] = []
            for idx, name in _SORT_COLUMN_NAMES.items():
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


class GitDirectorConsole(App):
    CSS = """
    Screen {
        background: $surface;
        overflow: hidden;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        background: $accent;
        color: $text;
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
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("h", "cursor_left", "Left", show=False),
        Binding("l", "cursor_right", "Right", show=False),
        Binding("slash", "search", "Search", show=True),
        Binding("s", "sort", "Sort", show=True),
        Binding("escape", "close_search", show=False),
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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield DataTable(id="repo-table", cursor_type="row")
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
        self._load_repos()

    @work(thread=True)
    def _load_repos(self) -> None:
        self._repo_paths = sorted(self.manager.config.repositories, key=lambda p: p.name.lower())

        if not self._repo_paths:
            self.call_from_thread(self._update_status, "No repositories tracked")
            return

        # First pass: add rows with repo names only
        self.call_from_thread(self._populate_initial_rows)

        # Second pass: load statuses concurrently
        total = len(self._repo_paths)
        done = 0
        self.call_from_thread(self._update_status, f"Checking {total} repositories…")

        with ThreadPoolExecutor(max_workers=self.manager.config.max_workers) as executor:
            futures = {
                executor.submit(self.manager.get_repository_status, path): path
                for path in self._repo_paths
            }
            for future in as_completed(futures):
                from ..integrations.tmux import list_repo_sessions

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
            pass  # Row may have been filtered out by active search

    def _update_status(self, message: str) -> None:
        self.query_one("#status-bar", Static).update(message)

    def _get_selected_path(self) -> Path | None:
        table = self.query_one("#repo-table", DataTable)
        if table.row_count == 0:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return Path(str(row_key.value))

    def action_cursor_down(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        table.action_cursor_up()

    def action_cursor_left(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        table.scroll_left()

    def action_cursor_right(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        table.scroll_right()

    # -- Search ---------------------------------------------------------------

    def action_search(self) -> None:
        self.query_one("#search-container").display = True
        self.query_one("#search-bar", Input).focus()

    def action_close_search(self) -> None:
        container = self.query_one("#search-container")
        if not container.display:
            return
        self.query_one("#search-bar", Input).value = ""
        container.display = False
        self._search_query = ""
        self._apply_filter_and_sort()
        self.query_one("#repo-table", DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-bar":
            self._search_query = event.value
            self._apply_filter_and_sort()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-bar":
            self.query_one("#search-container").display = False
            self.query_one("#repo-table", DataTable).focus()

    # -- Sort -----------------------------------------------------------------

    def action_sort(self) -> None:
        self.push_screen(
            SortMenuScreen(self._sort_column, self._sort_reverse),
            callback=self._handle_sort_selection,
        )

    def _handle_sort_selection(self, result: tuple | None) -> None:
        if result is None:
            return
        self._sort_column, self._sort_reverse = result
        self._apply_filter_and_sort()

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
        return lambda i: i.name.lower()  # col == 0 (default)

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
        return msg

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_show_menu()

    def action_open_tmux(self, agent_cmd: str | None = None) -> None:
        """Create a new tmux session and optionally launch an AI agent in it."""
        path = self._get_selected_path()
        if path is None:
            return

        from ..integrations.tmux import attach_tmux_session, create_tmux_session

        session_name = create_tmux_session(path.name, path)

        if agent_cmd:
            import subprocess

            subprocess.run(
                ["tmux", "send-keys", "-t", session_name, agent_cmd, "Enter"],
                check=False,
            )

        import sys

        with self.suspend():
            sys.stdout.write("\033[?1049h\033[H\033[2J")
            sys.stdout.flush()
            attach_tmux_session(session_name)

    def _attach_to_session(self, session_name: str) -> None:
        """Attach to an existing tmux session."""
        import sys

        from ..integrations.tmux import attach_tmux_session

        with self.suspend():
            sys.stdout.write("\033[?1049h\033[H\033[2J")
            sys.stdout.flush()
            attach_tmux_session(session_name)

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
            self._attach_to_session(session_name)
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
            from ..integrations.tmux import kill_tmux_session

            kill_tmux_session(session_name)
            self._update_status(f"Session '{session_name}' removed")

    def action_refresh(self) -> None:
        self._results.clear()
        self._sessions_cache.clear()
        self._load_repos()


def _run_console() -> None:
    app = GitDirectorConsole()
    app.run()


def register(cli: click.Group):
    @cli.command(name="console")
    def console_cmd():
        """Interactive TUI for browsing and opening repositories."""
        _run_console()
