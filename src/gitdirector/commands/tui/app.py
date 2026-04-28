"""Main GitDirectorConsole Textual application."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import click
from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Footer, Header, Input, Static, TabbedContent, TabPane

from ...manager import RepositoryManager
from ...repo import Repository, RepositoryInfo
from ... import version_check
from .. import get_version
from . import app_panels as _app_panels
from .app_panels import ConsolePanelsMixin
from .app_repos import ConsoleReposMixin
from .app_sessions import ConsoleSessionsMixin
from .app_ui import ConsoleUIHelpersMixin
from .constants import (
    _DEFAULT_PANELS_SORT_COLUMN,
    _DEFAULT_SESSIONS_SORT_COLUMN,
    _DEFAULT_SORT_COLUMN,
    _SESSION_STATUS_POLL_INTERVAL_SECS,
)
from .panels import Panel, PanelStore
from .screens import (
    ActionMenuScreen,
    AgentLoadingScreen,
    ConfirmScreen,
    GitCommandResultScreen,
    GitOperationsMenuScreen,
    PullLoadingScreen,
    PullResultScreen,
    RemoveSessionScreen,
    RepoInfoScreen,
)

_panel_row_height = _app_panels._panel_row_height
_render_panel_preview = _app_panels._render_panel_preview

__all__ = [
    "GitDirectorConsole",
    "_panel_row_height",
    "_render_panel_preview",
    "_run_console",
    "register",
]


class GitDirectorConsole(
    ConsolePanelsMixin,
    ConsoleSessionsMixin,
    ConsoleReposMixin,
    ConsoleUIHelpersMixin,
    App,
):
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
        Binding("g", "show_git_menu", "Git", show=True),
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
        self._resume_new_panel_guard_until: float = 0.0
        self._panel_store = PanelStore()
        self._status_message = "Loading repositories…"
        self._update_notice = version_check.get_cached_update_notice()
        self._session_status_tracking_paused = False
        self._session_status_tracking_running = False

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
        yield Static(self._compose_status_message(self._status_message), id="status-bar")
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
        self._poll_timer = self.set_interval(
            _SESSION_STATUS_POLL_INTERVAL_SECS,
            self._trigger_status_poll,
        )
        self._set_session_status_tracking_running(False)
        self._load_update_notice()
        self._load_repos()

    @work(thread=True)
    def _load_update_notice(self) -> None:
        notice = version_check.format_update_notice(version_check.get_update_status())
        self.call_from_thread(self._set_update_notice, notice)

    def _set_update_notice(self, notice: str | None) -> None:
        self._update_notice = notice
        self._refresh_status_bar()

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

    def action_select_row(self) -> None:
        if self._active_tab == "sessions":
            table = self.query_one("#sessions-table", DataTable)
            session_name = self._get_selected_row_key(table)
            if session_name is None:
                return
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
            self._arm_resume_new_panel_guard(restore_tab)
            self._resume_session_status_tracking()

        self._repos_stale = True
        self._active_tab = restore_tab

    @work(thread=True)
    def _refresh_repo_for_path(self, path: Path) -> None:
        """Re-fetch full repository status and session count for the given path."""
        from ...integrations.tmux import list_repo_sessions

        info = self.manager.get_repository_status(path, fetch=True)
        self._results[str(path)] = info
        sessions_count = len(list_repo_sessions(path))
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

    def action_show_git_menu(self) -> None:
        if self._active_tab != "repos":
            return
        path = self._get_selected_path()
        if path is None:
            return
        self._push_git_menu_for_path(path)

    def _push_git_menu_for_path(self, path: Path) -> None:
        info = self._results.get(str(path))
        branch = info.branch if info else None
        self.push_screen(
            GitOperationsMenuScreen(path.name, branch),
            callback=lambda action: self._handle_git_menu_action(action, path),
        )

    def _handle_git_result_dismissal(self, action: str | None, path: Path) -> None:
        if action == "back":
            self._push_git_menu_for_path(path)

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

        try:
            result = gather_repo_info(path)
        except Exception as exc:
            self.call_from_thread(screen.show_error, str(exc))
            return
        self.call_from_thread(screen.populate, result)

    def _push_info_screen(self, name: str, path: Path, result) -> None:
        self.push_screen(RepoInfoScreen(name, path))
        total = len(self._results)
        shown = self.query_one("#repo-table", DataTable).row_count
        self._update_status(self._build_loaded_status(shown, total))

    def _handle_git_menu_action(self, action: str | None, path: Path) -> None:
        if action is None:
            return
        if action == "pull":
            self._prompt_repo_pull(path)
        elif action == "status":
            self._show_repo_git_status(path)
        elif action == "timeline":
            self._show_repo_git_timeline(path)
        elif action == "branches":
            self._show_repo_git_branches(path)
        elif action == "remotes":
            self._show_repo_git_remotes(path)

    def _show_repo_git_output(
        self,
        path: Path,
        *,
        command: str,
        loader: Callable[[Repository], tuple[bool, str]],
        success_text: str,
        failure_text: str,
        success_status: str,
        failure_status: str,
    ) -> None:
        try:
            repo = Repository(path)
            ok, message = loader(repo)
        except Exception as exc:
            ok = False
            message = str(exc)

        self.push_screen(
            GitCommandResultScreen(
                path.name,
                command,
                ok,
                message,
                success_text=success_text,
                failure_text=failure_text,
            ),
            callback=lambda action: self._handle_git_result_dismissal(action, path),
        )
        self._update_status(f"{path.name}: {success_status if ok else failure_status}")

    def _show_repo_git_status(self, path: Path) -> None:
        self._show_repo_git_output(
            path,
            command="git status",
            loader=lambda repo: repo.status_output(),
            success_text="Status output",
            failure_text="Status failed",
            success_status="status shown",
            failure_status="status failed",
        )

    def _show_repo_git_timeline(self, path: Path) -> None:
        self._show_repo_git_output(
            path,
            command=(
                "git log --max-count=1000 --graph --decorate --all --color=always --date=short "
                "--pretty=format:%C(auto)%h%Creset %C(blue)%ad%Creset %C(auto)%d%Creset %s"
            ),
            loader=lambda repo: repo.timeline_output(),
            success_text="Timeline shown",
            failure_text="Timeline failed",
            success_status="timeline shown",
            failure_status="timeline failed",
        )

    def _show_repo_git_branches(self, path: Path) -> None:
        self._show_repo_git_output(
            path,
            command="git branch -a",
            loader=lambda repo: repo.branches_output(),
            success_text="Branches shown",
            failure_text="Branches failed",
            success_status="branches shown",
            failure_status="branches failed",
        )

    def _show_repo_git_remotes(self, path: Path) -> None:
        self._show_repo_git_output(
            path,
            command="git remote -v",
            loader=lambda repo: repo.remotes_output(),
            success_text="Remotes shown",
            failure_text="Remotes failed",
            success_status="remotes shown",
            failure_status="remotes failed",
        )

    def _prompt_repo_pull(self, path: Path) -> None:
        try:
            repo = Repository(path)
        except Exception as exc:
            message = str(exc)
            self._update_status(f"{path.name}: {message}")
            self.push_screen(
                PullResultScreen(path.name, None, False, message),
                callback=lambda action: self._handle_git_result_dismissal(action, path),
            )
            return

        remote, branch, err = repo.get_pull_target()
        command = None
        if remote is not None and branch is not None:
            command = f"git pull --ff-only {remote} {branch}"

        if err is not None or command is None or remote is None or branch is None:
            message = err or "Could not determine pull target"
            self._update_status(f"{path.name}: {message}")
            self.push_screen(
                PullResultScreen(path.name, command, False, message),
                callback=lambda action: self._handle_git_result_dismissal(action, path),
            )
            return

        target = f"{remote}/{branch}"
        self.push_screen(
            ConfirmScreen(
                f"Pull '{escape(path.name)}' from [cyan]{escape(target)}[/cyan]?\n"
                f"[dim]{escape(command)}[/dim]"
            ),
            callback=lambda confirmed: self._do_pull_repo(confirmed, path, command),
        )

    def _do_pull_repo(self, confirmed: bool, path: Path, command: str) -> None:
        if not confirmed:
            return
        self._update_status(f"Pulling {path.name}: {command}")
        loading_screen = PullLoadingScreen(path.name, command)
        self.push_screen(loading_screen)
        self._pull_repo(path, command, loading_screen)

    @work(thread=True)
    def _pull_repo(self, path: Path, command: str, loading_screen: PullLoadingScreen) -> None:
        from ..pull import pull_repository

        result = pull_repository(path)
        self.call_from_thread(self._show_pull_result, loading_screen, path, command, result)

    def _show_pull_result(
        self,
        loading_screen: PullLoadingScreen,
        path: Path,
        command: str,
        result: tuple[str, bool, str],
    ) -> None:
        repo_name, ok, message = result
        loading_screen.dismiss(None)
        self.push_screen(
            PullResultScreen(repo_name, command, ok, message),
            callback=lambda action: self._handle_git_result_dismissal(action, path),
        )
        self._update_status(f"{repo_name}: {'pull completed' if ok else 'pull failed'}")
        if ok:
            self._refresh_repo_for_path(path)

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
                    RemoveSessionScreen(path.name, path),
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
            from ...integrations.tmux import (
                _parse_gd_session_name,
                _repo_session_name_segment,
                _sanitize_repo_name,
                kill_tmux_session,
            )

            kill_tmux_session(session_name)

            self._sessions_entries = [
                e for e in self._sessions_entries if e["session_name"] != session_name
            ]
            self._apply_sessions_filter_and_sort()

            parsed = _parse_gd_session_name(session_name)
            if parsed is not None:
                repo_slug, _, _ = parsed
                for path_str, info in self._results.items():
                    info_repo_slugs = {
                        _repo_session_name_segment(info.path),
                        _sanitize_repo_name(info.path.name),
                    }
                    if repo_slug in info_repo_slugs:
                        count = sum(
                            1
                            for entry in self._sessions_entries
                            if (entry_parsed := _parse_gd_session_name(entry["session_name"]))
                            and entry_parsed[0] in info_repo_slugs
                        )
                        self._sessions_cache[path_str] = count
                        self._update_row(info, count)
                        break


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
