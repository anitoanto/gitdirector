"""Main GitDirectorConsole Textual application."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Input, Static, TabbedContent, TabPane
from textual.widgets._footer import FooterKey, FooterLabel
from textual.worker import NoActiveWorker, Worker, get_current_worker

from ... import version_check
from ...agents import AGENTS_BY_KEY
from ...manager import RepositoryManager
from ...repo import Repository, RepositoryInfo
from ...storage import normalize_repository_path
from .. import get_version
from . import app_panels as _app_panels
from .app_groups import ConsoleGroupsMixin
from .app_panels import ConsolePanelsMixin
from .app_repos import ConsoleReposMixin
from .app_sessions import ConsoleSessionsMixin
from .app_ui import ConsoleUIHelpersMixin
from .constants import (
    _DEFAULT_PANELS_SORT_COLUMN,
    _DEFAULT_SESSIONS_SORT_COLUMN,
    _DEFAULT_SORT_COLUMN,
    _SESSION_STATUS_POLL_INTERVAL_SECS,
    TablePalette,
    resolve_table_palette,
)
from .panels import Panel, PanelStore
from .screens.diff import DiffReviewScreen
from .screens.groups import GroupActionMenuScreen
from .screens.panels import AgentLoadingScreen, ConfirmScreen
from .screens.repos import (
    ActionMenuScreen,
    GitCommandResultScreen,
    GitOperationsMenuScreen,
    PullLoadingScreen,
    PullResultScreen,
    RepoInfoScreen,
)
from .screens.sessions import EditSessionDescriptionScreen, RemoveSessionScreen
from .terminal_caps import host_color_system, no_color_requested

_panel_row_height = _app_panels._panel_row_height
_render_panel_preview = _app_panels._render_panel_preview

__all__ = [
    "GitDirectorConsole",
    "_panel_row_height",
    "_render_panel_preview",
    "_run_console",
]


logger = logging.getLogger(__name__)


_NO_UPSTREAM_PUSH_MARKERS = (
    "no upstream",
    "set up a tracking branch",
    "has no upstream",
)


class RefreshFooter(Footer):
    refreshing = reactive(False, repaint=False)
    refresh_text = reactive("", repaint=False)

    def watch_refreshing(self) -> None:
        self.call_after_refresh(self.recompose)

    def watch_refresh_text(self) -> None:
        self.call_after_refresh(self.recompose)

    def compose(self) -> ComposeResult:
        for child in super().compose():
            if (
                self.refreshing
                and isinstance(child, FooterKey)
                and child.has_class("-command-palette")
            ):
                yield FooterLabel(self.refresh_text, classes="-refresh-indicator")
            yield child


def _is_no_upstream_push_error(message: str) -> bool:
    message_lower = message.lower()
    return any(marker in message_lower for marker in _NO_UPSTREAM_PUSH_MARKERS)


class GitDirectorConsole(
    ConsolePanelsMixin,
    ConsoleSessionsMixin,
    ConsoleGroupsMixin,
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
    HeaderTitle {
        padding: 0 10 0 8;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 2;
    }
    FooterLabel.-refresh-indicator {
        dock: right;
        height: 1;
        margin: 0;
        padding: 0 2 0 1;
        background: $footer-background;
        color: $text-muted;
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
        scrollbar-size-horizontal: 0;
    }
    /* Highlight rows and options with a translucent tint of the theme's
       primary colour instead of Textual's solid block cursor. A solid
       primary block forces a contrasting text colour over the cell, which
       erases status colours and turns dimmed text unreadable; a tint keeps
       the row's own colours legible in both dark and light themes. */
    DataTable > .datatable--cursor,
    DataTable:focus > .datatable--cursor {
        background: $primary 30%;
        color: $text;
        text-style: bold;
    }
    DataTable > .datatable--hover {
        background: $primary 12%;
    }
    DataTable > .datatable--header-hover {
        background: $primary 12%;
    }
    OptionList > .option-list--option-highlighted,
    OptionList:focus > .option-list--option-highlighted {
        background: $primary 30%;
        color: $text;
        text-style: bold;
    }
    OptionList > .option-list--option-hover {
        background: $primary 12%;
    }
    #repo-table,
    #sessions-table,
    #panels-table,
    #no-repos-message,
    #no-sessions-message,
    #no-panels-message {
        display: none;
    }
    #no-repos-message,
    #no-sessions-message,
    #no-panels-message {
        height: 1fr;
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
        Binding("down", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("h", "cursor_left", "Left", show=False),
        Binding("left", "cursor_left", "Left", show=False),
        Binding("l", "cursor_right", "Right", show=False),
        Binding("right", "cursor_right", "Right", show=False),
        Binding("_", "reset_horizontal_scroll", show=False),
        Binding("slash", "search", "Search", show=True),
        Binding("s", "sort", "Sort", show=True),
        Binding("g", "show_git_menu", "Git", show=True),
        Binding("i", "show_info", "Info", show=True),
        Binding("d", "edit_session_description", "Description", show=True),
        Binding("escape", "close_search", show=False),
        Binding("1", "tab_repos", "Repos", show=False),
        Binding("2", "tab_sessions", "Sessions", show=False),
        Binding("3", "tab_panels", "Panels", show=False),
        Binding("space", "toggle_group", "Toggle", show=True),
        # Reaches us only from terminals that speak the kitty keyboard
        # protocol; legacy terminals send a plain space for shift+space.
        Binding("shift+space", "toggle_all_groups", "Toggle all", show=False),
        Binding("n", "new_panel", "New Panel", show=True),
    ]

    def __init__(self) -> None:
        # Ensure Textual's ``App.console`` auto-detects ``"truecolor"`` for
        # its render Console. ``Strip.render()`` uses ``console._color_system``
        # to pick the SGR format; if it resolves to ``"256"``, every
        # truecolor segment produced by child agents gets quantised to
        # the 256-colour palette — visible banding in gradients.
        #
        # Rich's auto-detection runs inside ``App.__init__`` using a
        # snapshot of ``os.environ``, so the variable must be set
        # *before* ``super().__init__()`` is called. We only force
        # ``COLORTERM=truecolor`` when the host actually advertises
        # truecolor — never downgrade a host that can't render it.
        if not no_color_requested() and host_color_system() == "truecolor":
            os.environ["COLORTERM"] = "truecolor"
        super().__init__()
        from ...integrations.tmux import TmuxMonitor

        self.manager = RepositoryManager()
        self.theme = self.manager.config.theme
        self._repo_paths: list[Path] = []
        self._results: dict[str, RepositoryInfo] = {}
        self._search_query: str = ""
        self._sort_column: int = _DEFAULT_SORT_COLUMN
        self._sort_reverse: bool = False
        self._active_tab: str = "repos"
        self._sessions_entries: list[dict[str, str]] = []
        self._sessions_sort_column: int = _DEFAULT_SESSIONS_SORT_COLUMN
        self._sessions_sort_reverse: bool = False
        self._sessions_layout = None
        self._panels_entries: list[Panel] = []
        self._panels_sort_column: int = _DEFAULT_PANELS_SORT_COLUMN
        self._panels_sort_reverse: bool = False
        self._panels_live_sessions: set[str] = set()
        self._groups_entries = []
        self._collapsed_groups: set[str] = set()
        self._visible_repo_count: int = 0
        self._visible_group_count: int = 0
        self._repos_cache_updated_at: float | None = None
        self._repos_cache_saved_at: float | None = None
        self._repos_refreshing = False
        self._monitor = TmuxMonitor()
        self._session_statuses: dict[str, str] = {}
        self._sessions_snapshot_generation = 0
        # Set once the first sessions snapshot has been applied; until then
        # the Sessions tab has nothing cached and must load on activation.
        self._sessions_loaded = False
        self._waiting_count: int = 0
        self._resume_target_tab: str | None = None
        self._resume_refresh_path: Path | None = None
        self._resume_tab_activation_guard: str | None = None
        self._resume_selection_tab: str | None = None
        self._resume_selection_key: str | None = None
        self._resume_selection_row: int | None = None
        self._resume_new_panel_guard_until: float = 0.0
        self._panel_store = PanelStore()
        self._status_message = ""
        self._update_notice = version_check.get_cached_update_notice()
        self._session_status_tracking_paused = False
        self._session_status_tracking_running = False
        self._shutdown_requested = False
        self._repo_status_executor: ThreadPoolExecutor | None = None
        self._refresh_operations = 0
        self._refresh_frame = 0
        self._palette_cache: tuple[str, TablePalette] | None = None

    @property
    def _palette(self) -> TablePalette:
        """Table colours for the current theme, resolved once per theme."""
        theme = self.theme
        if self._palette_cache is None or self._palette_cache[0] != theme:
            self._palette_cache = (theme, resolve_table_palette(self.get_css_variables()))
        return self._palette_cache[1]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, icon="☰")
        with TabbedContent(id="tabs"):
            with TabPane("[1] Repositories", id="repos"):
                yield Static("", id="repo-search-indicator", classes="search-indicator")
                yield DataTable(
                    id="repo-table", cursor_type="row", cursor_foreground_priority="renderable"
                )
                yield Static(
                    "No repositories linked.  Run"
                    " [bold]gitdirector link <path>[/bold] to get started.",
                    id="no-repos-message",
                )
            with TabPane("[2] Sessions", id="sessions"):
                yield Static("", id="sessions-search-indicator", classes="search-indicator")
                yield DataTable(
                    id="sessions-table", cursor_type="row", cursor_foreground_priority="renderable"
                )
                yield Static(
                    "No active sessions.  Open a repository and start a tmux session"
                    " to see it here.",
                    id="no-sessions-message",
                )
            with TabPane("[3] Panels", id="panels"):
                yield Static("", id="panels-search-indicator", classes="search-indicator")
                yield DataTable(
                    id="panels-table", cursor_type="row", cursor_foreground_priority="renderable"
                )
                yield Static(
                    "No panels created.  Press [bold]n[/bold] to create a new panel.",
                    id="no-panels-message",
                )
        with Horizontal(id="search-container"):
            yield Static("/ search:", id="search-label")
            yield Input(placeholder="type to filter…", id="search-bar")
        yield Static(
            self._compose_status_message(self._status_message),
            id="status-bar",
            markup=False,
        )
        yield RefreshFooter()

    def on_mount(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        self._col_keys = table.add_columns(
            "Repository", "Sync", "Branch", "Changes", "Last Commit", "Path"
        )
        sessions_table = self.query_one("#sessions-table", DataTable)
        # The sessions tab renders each row as one composed full-width block
        # (columns on the first line, tmux session name on the second), so it
        # uses a single column whose header carries the column titles.
        self._sess_col_keys = sessions_table.add_columns("Sessions")
        self._apply_sessions_column_layout()
        panels_table = self.query_one("#panels-table", DataTable)
        self._panels_col_keys = panels_table.add_columns(
            "Map", "Name", "TMUX", "Layout", "Panes", "Status"
        )
        self._disable_tabs_widget_arrow_keybindings()
        self.app_resume_signal.subscribe(self, self._handle_app_resume)
        self._sync_tmux_theme_config(self.theme)
        self._poll_timer = self.set_interval(
            _SESSION_STATUS_POLL_INTERVAL_SECS,
            self._trigger_status_poll,
        )
        self._sync_session_status_tracking()
        self.set_interval(0.25, self._advance_refresh_indicator)
        self._load_update_notice()
        self._load_repos_from_cache()
        self._refresh_repos()
        # Load the session list up front so the Sessions tab is populated
        # the first time it is opened; the tracking keeps it current after.
        self._load_sessions()

    def _show_refresh_indicator(self) -> None:
        self._refresh_operations += 1
        try:
            footer = self.query_one(RefreshFooter)
        except NoMatches:
            return
        footer.refreshing = True
        self._advance_refresh_indicator()

    def _hide_refresh_indicator(self) -> None:
        self._refresh_operations = max(0, self._refresh_operations - 1)
        if self._refresh_operations == 0:
            self.set_timer(0.35, self._hide_refresh_indicator_when_idle)

    def _hide_refresh_indicator_when_idle(self) -> None:
        if self._refresh_operations == 0:
            try:
                self.query_one(RefreshFooter).refreshing = False
            except NoMatches:
                pass

    def _advance_refresh_indicator(self) -> None:
        if self._refresh_operations == 0:
            return
        frame = "◐◓◑◒"[self._refresh_frame % 4]
        self._refresh_frame += 1
        try:
            self.query_one(RefreshFooter).refresh_text = f"{frame} refreshing"
        except NoMatches:
            pass

    def _disable_tabs_widget_arrow_keybindings(self) -> None:
        """Remove the ``left``/``right`` bindings from every Tabs widget.

        The ``Tabs`` widget binds ``left``/``right`` to ``previous_tab``/``next_tab``
        so it can switch tabs when focused. We want those keys to scroll the
        active table horizontally instead, so strip the bindings on the
        underlying ContentTabs instances. Users can still switch tabs with the
        ``1``/``2``/``3`` number keys (and ``tab``/``shift+tab``).
        """
        from textual.widgets._tabs import Tabs

        for tabs_widget in self.query(Tabs):
            tabs_widget._bindings.key_to_bindings.pop("left", None)
            tabs_widget._bindings.key_to_bindings.pop("right", None)

    def _background_shutdown_requested(self, worker: Worker | None = None) -> bool:
        return self._shutdown_requested or (
            worker is not None and (worker.is_cancelled or not self.is_running)
        )

    def _current_worker_or_none(self) -> Worker | None:
        try:
            return get_current_worker()
        except NoActiveWorker:
            return None

    def _shutdown_background_work(self) -> None:
        if self._shutdown_requested:
            return

        self._shutdown_requested = True
        self._pause_session_status_tracking(wait=False)
        self._monitor.stop(wait=True)

        executor = self._repo_status_executor
        self._repo_status_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

        self.workers.cancel_all()
        Repository.kill_running_git_commands()

    def action_quit(self) -> None:
        self._shutdown_background_work()
        self.exit()

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
        if self.is_running:
            self._rerender_tables()

    def _rerender_tables(self) -> None:
        """Repaint every table so cells pick up the new theme's palette."""
        for repaint in (
            self._rerender_repo_rows,
            self._apply_sessions_filter_and_sort,
            self._apply_panels_filter_and_sort,
        ):
            try:
                repaint()
            except (NoMatches, AttributeError):
                logger.debug("table not ready for theme repaint", exc_info=True)

    def action_select_row(self) -> None:
        if self._active_tab == "sessions":
            table = self.query_one("#sessions-table", DataTable)
            session_name = self._get_selected_row_key(table)
            if session_name is None:
                return
            self._suspend_and_attach(
                session_name,
                attach_delay_seconds=AgentLoadingScreen._MIN_WAIT,
            )
        elif self._active_tab == "panels":
            self._open_selected_panel_menu()
        else:
            self.action_show_menu()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "sessions-table":
            session_name = str(event.row_key.value)
            self._suspend_and_attach(
                session_name,
                attach_delay_seconds=AgentLoadingScreen._MIN_WAIT,
            )
        elif event.data_table.id == "panels-table":
            self._open_selected_panel_menu()
        else:
            self.action_show_menu()

    def action_open_tmux(
        self,
        agent_cmd: str | None = None,
        *,
        description: str | None = None,
        purpose: str | None = None,
    ) -> None:
        """Create a new tmux session and optionally launch an AI agent in it.

        *purpose* overrides the session-name label, which otherwise defaults to
        *agent_cmd* (or ``shell``). Agents launched with flags need it so the
        session name stays readable.
        """
        path = self._get_selected_path()
        if path is None:
            return

        from ...integrations.tmux import (
            create_tmux_session,
            kill_tmux_session,
            launch_command_in_tmux_session,
        )

        launch_tab = self._active_tab
        purpose = purpose or agent_cmd or "shell"
        session_kwargs = {"purpose": purpose, "description": description}
        if launch_tab == "repos" and self._selected_repo_row_is_group():
            repo_label = self._get_selected_group_session_repo_label()
            if repo_label:
                session_kwargs["repo_label"] = repo_label
        try:
            session_name = create_tmux_session(path.name, path, **session_kwargs)
        except Exception as exc:
            logger.warning("tmux session creation failed: %s", exc)
            self._update_status(f"tmux session creation failed: {exc}")
            return

        def refresh_after_launch(_value: object) -> None:
            self.set_timer(
                0.2,
                lambda: self._refresh_after_session_launch(path, launch_tab),
            )

        if agent_cmd:
            try:
                ready_marker = launch_command_in_tmux_session(session_name, agent_cmd)
            except Exception as exc:
                logger.warning("tmux agent launch failed: %s", exc)
                kill_tmux_session(session_name)
                self._update_status(f"tmux agent launch failed: {exc}")
                return
            self._show_attach_loading_screen(
                session_name,
                path,
                ready_marker=ready_marker,
                skip_config_sync=True,
                callback=refresh_after_launch,
            )
        else:
            self._show_attach_loading_screen(
                session_name,
                path,
                skip_config_sync=True,
                callback=refresh_after_launch,
            )

    def _show_attach_loading_screen(
        self,
        session_name: str,
        path: Path | None = None,
        row_key: str | None = None,
        ready_marker: Path | None = None,
        *,
        skip_config_sync: bool = False,
        callback: Callable[[object], None] | None = None,
    ) -> None:
        from ...integrations.tmux.core import _parse_gd_session_name

        title = "session"
        loading_hint = "waiting for session to initialize\u2026"
        parsed = _parse_gd_session_name(session_name)
        if parsed is not None:
            _repo, purpose, _sequence = parsed
            title = purpose
            if purpose != "shell":
                loading_hint = "waiting for agent to initialize\u2026"

        def attach() -> None:
            self._suspend_and_attach(
                session_name,
                path,
                row_key=row_key,
                skip_config_sync=skip_config_sync,
            )

        self.push_screen(
            AgentLoadingScreen(
                title,
                session_name,
                ready_marker,
                loading_hint=loading_hint,
                on_attach=attach,
            ),
            callback=callback,
        )

    def _suspend_and_attach(
        self,
        session_name: str,
        path: Path | None = None,
        row_key: str | None = None,
        *,
        skip_config_sync: bool = False,
        attach_delay_seconds: float = 0.0,
    ) -> None:
        """Suspend the TUI and attach to the tmux session."""
        self._monitor.clear_bell(session_name)
        restore_tab = self._active_tab
        self._resume_target_tab = restore_tab
        selected_repo_group = restore_tab == "repos" and self._selected_repo_row_is_group()
        refresh_path = None if selected_repo_group else path
        self._resume_refresh_path = self._resolve_repo_refresh_path(
            session_name,
            refresh_path,
        )
        if row_key is None and restore_tab in {"panels", "repos"}:
            try:
                row_key = self._get_selected_row_key(self._get_active_table())
            except Exception:
                row_key = None
        self._capture_resume_selection(
            restore_tab,
            session_name=session_name,
            path=path,
            row_key=row_key,
        )

        self._pause_session_status_tracking(wait=False)
        attach_error: Exception | None = None
        try:
            try:
                # Everything that can fail runs *inside* the suspend block
                # and never lets an exception escape it. Textual's
                # ``App.suspend`` does not use try/finally: an exception
                # raised in the body skips ``resume_application_mode``, so
                # the driver stays closed (no input thread, no writer) and
                # the app is frozen for good. Writes to ``sys.stdout`` are
                # also only meaningful in here, where Textual has pointed it
                # at the real terminal; outside the block stdout is
                # captured by the app and never reaches the screen.
                with self.suspend():
                    attach_error = self._attach_while_suspended(
                        session_name,
                        skip_config_sync=skip_config_sync,
                        attach_delay_seconds=attach_delay_seconds,
                    )
            except Exception as exc:
                # Suspending or resuming the driver itself failed.
                logger.warning("tmux attach failed: %s", exc)
                attach_error = exc
        finally:
            self._arm_resume_new_panel_guard(restore_tab)
            self._resume_session_status_tracking()

        if attach_error is not None:
            self._update_status(f"tmux attach failed: {attach_error}")

        self._active_tab = restore_tab

    @staticmethod
    def _attach_while_suspended(
        session_name: str,
        *,
        skip_config_sync: bool,
        attach_delay_seconds: float,
    ) -> Exception | None:
        """Run the blocking tmux attach with the TUI suspended.

        Returns the failure instead of raising so the caller's ``suspend``
        context always completes and restarts the driver. Whatever happens,
        the terminal is handed back with the cursor visible and outside any
        alternate screen this method entered, and stale input is dropped.
        """
        import sys
        import termios

        from ...integrations.tmux import attach_tmux_session

        def write_terminal(sequence: str) -> None:
            try:
                sys.stdout.write(sequence)
                sys.stdout.flush()
            except Exception:
                pass

        entered_manual_alt_screen = False
        error: Exception | None = None
        try:
            if not os.environ.get("TMUX"):
                # Enter the alt screen before tmux does so the primary
                # screen never flashes between the TUI and the session.
                write_terminal("\033[?1049h\033[H\033[2J\033[?25l")
                entered_manual_alt_screen = True
            attach_tmux_session(
                session_name,
                skip_config_sync=skip_config_sync,
                attach_delay_seconds=attach_delay_seconds,
            )
        except Exception as exc:
            logger.warning("tmux attach failed: %s", exc)
            error = exc
        finally:
            if entered_manual_alt_screen:
                if error is None:
                    # tmux already left the alt screen on its way out.
                    write_terminal("\033[?25h")
                else:
                    # tmux may never have drawn anything: leave the cleared
                    # alt screen ourselves or the terminal stays black.
                    write_terminal("\033[?25h\033[?1049l")
            try:
                termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
            except (AttributeError, OSError, ValueError):
                pass
        return error

    def _refresh_after_session_launch(self, path: Path, launch_tab: str) -> None:
        if launch_tab == "repos":
            if str(path) in self._results:
                self._refresh_repo_for_path(path)
            elif len(self._results) < len(self._repo_paths):
                self._populate_initial_rows()
            else:
                self._apply_filter_and_sort()
        # Whichever tab the session was launched from, pick it up in the
        # session list right away so a switch to the Sessions tab does not
        # have to wait for the next background poll.
        self._load_sessions()

    def _resolve_repo_refresh_path(
        self, session_name: str, path: Path | None = None
    ) -> Path | None:
        if path is not None:
            return normalize_repository_path(path)

        from ...integrations.tmux.core import (
            _parse_gd_session_name,
            _repo_label_from_segment,
            _repo_session_name_segment,
        )

        parsed = _parse_gd_session_name(session_name)
        if parsed is None:
            return None

        repo_segment, _purpose, _sequence = parsed
        tracked_paths: list[Path] = []
        seen_paths: set[Path] = set()
        for repo_path in [*self.manager.config.repositories, *self._repo_paths]:
            normalized_path = normalize_repository_path(repo_path)
            if normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            tracked_paths.append(normalized_path)

        exact_matches = [
            repo_path
            for repo_path in tracked_paths
            if _repo_session_name_segment(repo_path) == repo_segment
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

        repo_label = _repo_label_from_segment(repo_segment)
        label_matches = [
            repo_path
            for repo_path in tracked_paths
            if _repo_label_from_segment(_repo_session_name_segment(repo_path)) == repo_label
        ]
        if len(label_matches) == 1:
            return label_matches[0]

        return None

    @work(thread=True)
    def _refresh_repo_for_path(self, path: Path) -> None:
        """Re-fetch full repository status for the given path."""
        self.call_from_thread(self._show_refresh_indicator)
        try:
            worker = self._current_worker_or_none()
            if self._background_shutdown_requested(worker):
                return

            info = self.manager.get_repository_status(path, fetch=True)
            if self._background_shutdown_requested(worker):
                return

            self._results[str(path)] = info
            if self._repos_cache_saved_at is not None and len(self._results) == len(
                self._repo_paths
            ):
                self._save_repos_cache(updated_at=self._repos_cache_saved_at)
            self.call_from_thread(self._update_row, info)
        finally:
            self.call_from_thread(self._hide_refresh_indicator)

    def _attach_to_session(self, session_name: str, path: Path | None = None) -> None:
        """Attach to an existing tmux session."""
        self._suspend_and_attach(
            session_name,
            path,
            attach_delay_seconds=AgentLoadingScreen._MIN_WAIT,
        )

    def action_show_menu(self) -> None:
        path = self._get_selected_path()
        if path is None:
            return
        if self._active_tab == "repos":
            group = self._get_selected_group()
            if group is not None:
                self.push_screen(
                    GroupActionMenuScreen(
                        group.name,
                        group.path,
                        group.repo_count,
                        group.repo_names,
                    ),
                    callback=self._handle_menu_action,
                )
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
        if self._selected_repo_row_is_group():
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
        group = self._get_selected_group()
        if group is not None:
            screen = RepoInfoScreen(f"{group.name} ({group.repo_count} repos)", group.path)
            self.push_screen(screen)
            self._gather_and_show_group_info(group, screen)
            return
        path = self._get_selected_path()
        if path is None:
            return
        screen = RepoInfoScreen(path.name, path)
        self.push_screen(screen)
        self._gather_and_show_info(path, screen)

    def action_edit_session_description(self) -> None:
        """Open the description editor for the currently highlighted session row."""
        if self._active_tab != "sessions":
            return
        from ...integrations.tmux.core import _get_session_description

        table = self.query_one("#sessions-table", DataTable)
        session_name = self._get_selected_row_key(table)
        if session_name is None:
            return
        current_description = _get_session_description(session_name)
        self.push_screen(
            EditSessionDescriptionScreen(session_name, current_description),
            callback=lambda value, sn=session_name: self._handle_description_edit(sn, value),
        )

    def _handle_description_edit(self, session_name: str, value: str | None) -> None:
        if value is None:
            return
        from ...integrations.tmux.core import _set_session_description

        _set_session_description(session_name, value)
        for entry in self._sessions_entries:
            if entry["session_name"] == session_name:
                entry["description"] = value if value else "-"
                break
        self._apply_sessions_filter_and_sort()

    def on_resize(self, event) -> None:
        if not hasattr(self, "_sess_col_keys"):
            return
        self._apply_sessions_column_layout()
        if self._active_tab == "sessions" and self._sessions_entries:
            self._apply_sessions_filter_and_sort()

    @work(thread=True)
    def _gather_and_show_info(self, path: Path, screen: RepoInfoScreen) -> None:
        worker = self._current_worker_or_none()
        if self._background_shutdown_requested(worker):
            return

        from ...info import gather_repo_info

        try:
            result = gather_repo_info(path)
        except Exception as exc:
            if self._background_shutdown_requested(worker):
                return
            self.call_from_thread(screen.show_error, str(exc))
            return
        if self._background_shutdown_requested(worker):
            return
        self.call_from_thread(screen.populate, result)

    @work(thread=True)
    def _gather_and_show_group_info(self, group, screen: RepoInfoScreen) -> None:
        worker = self._current_worker_or_none()
        if self._background_shutdown_requested(worker):
            return

        from ...info import FileTypeInfo, RepoInfoResult, gather_repo_info

        try:
            results = [gather_repo_info(path) for path in group.repositories]
        except Exception as exc:
            if self._background_shutdown_requested(worker):
                return
            self.call_from_thread(screen.show_error, str(exc))
            return

        file_types: dict[str, tuple[int, int, int, bool]] = {}
        for result in results:
            for file_type in result.file_types:
                count, lines, tokens, has_text = file_types.get(
                    file_type.extension, (0, 0, 0, False)
                )
                if file_type.line_count is not None:
                    lines += file_type.line_count
                    tokens += file_type.token_count or 0
                    has_text = True
                file_types[file_type.extension] = (count + file_type.count, lines, tokens, has_text)

        aggregate = RepoInfoResult(
            total_files=sum(result.total_files for result in results),
            file_types=sorted(
                [
                    FileTypeInfo(
                        extension, count, lines if has_text else None, tokens if has_text else None
                    )
                    for extension, (count, lines, tokens, has_text) in file_types.items()
                ],
                key=lambda file_type: file_type.line_count or 0,
                reverse=True,
            ),
            total_lines=sum(result.total_lines for result in results),
            total_tokens=sum(result.total_tokens for result in results),
            max_depth=max((result.max_depth for result in results), default=0),
        )
        if self._background_shutdown_requested(worker):
            return
        self.call_from_thread(screen.populate, aggregate)

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
        elif action == "push":
            self._prompt_repo_push(path)
        elif action == "review_diff":
            self._open_review_diff(path)

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

    def _prompt_repo_push(self, path: Path) -> None:
        try:
            Repository(path)
        except Exception as exc:
            message = str(exc)
            self._update_status(f"{path.name}: {message}")
            self.push_screen(
                PullResultScreen(path.name, None, False, message, operation="Push"),
                callback=lambda action: self._handle_git_result_dismissal(action, path),
            )
            return

        command = "git push"
        self.push_screen(
            ConfirmScreen(f"Push '{escape(path.name)}' to remote?\n[dim]{escape(command)}[/dim]"),
            callback=lambda confirmed: self._do_push_repo(confirmed, path, command),
        )

    def _do_push_repo(self, confirmed: bool, path: Path, command: str) -> None:
        if not confirmed:
            return
        self._update_status(f"Pushing {path.name}: {command}")
        loading_screen = PullLoadingScreen(path.name, command, verb="Pushing")
        self.push_screen(loading_screen)
        self._push_repo(path, command, loading_screen)

    def _push_repository(self, path: Path, command: str) -> tuple[str, bool, str, str]:
        repo = Repository(path)
        ok, message = repo.push()
        if not ok and _is_no_upstream_push_error(message):
            branch = repo.get_current_branch()
            command = f"git push -u origin {branch}" if branch else "git push -u origin <branch>"
            ok, message = repo.push(set_upstream=True)
        return path.name, ok, message, command

    @work(thread=True)
    def _push_repo(self, path: Path, command: str, loading_screen: PullLoadingScreen) -> None:
        worker = self._current_worker_or_none()
        if self._background_shutdown_requested(worker):
            return

        try:
            result = self._push_repository(path, command)
        except Exception as exc:
            logger.exception("push worker crashed")
            error_result = (path.name, False, f"Push failed: {exc}", command)
            if self._background_shutdown_requested(worker):
                return
            try:
                self.call_from_thread(self._show_push_result, loading_screen, path, error_result)
            except Exception:
                logger.debug("Failed to post push error to UI", exc_info=True)
            return

        if self._background_shutdown_requested(worker):
            return
        try:
            self.call_from_thread(self._show_push_result, loading_screen, path, result)
        except Exception:
            logger.debug("Failed to post push result to UI", exc_info=True)

    def _show_push_result(
        self,
        loading_screen: PullLoadingScreen,
        path: Path,
        result: tuple[str, bool, str, str],
    ) -> None:
        repo_name, ok, message, command = result
        loading_screen.dismiss(None)
        self.push_screen(
            PullResultScreen(
                repo_name,
                command,
                ok,
                message,
                operation="Push",
                empty_success="Push completed.",
            ),
            callback=lambda action: self._handle_git_result_dismissal(action, path),
        )
        self._update_status(f"{repo_name}: {'push completed' if ok else 'push failed'}")
        if ok:
            self._refresh_repo_for_path(path)

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
                f"Pull '{escape(path.name)}' from [$text-primary]{escape(target)}[/]?\n"
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
        worker = self._current_worker_or_none()
        if self._background_shutdown_requested(worker):
            return

        from ..pull import pull_repository

        try:
            result = pull_repository(path)
        except Exception as exc:
            logger.exception("pull worker crashed")
            error_result = (path.name, False, f"Pull failed: {exc}")
            if self._background_shutdown_requested(worker):
                return
            try:
                self.call_from_thread(
                    self._show_pull_result, loading_screen, path, command, error_result
                )
            except Exception:
                logger.debug("Failed to post pull error to UI", exc_info=True)
            return

        if self._background_shutdown_requested(worker):
            return
        try:
            self.call_from_thread(self._show_pull_result, loading_screen, path, command, result)
        except Exception:
            logger.debug("Failed to post pull result to UI", exc_info=True)

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

    def _handle_menu_action(self, action: str | None) -> None:
        if action is None:
            return
        if action == "new_session":
            self.action_open_tmux()
        elif action.startswith("agent:"):
            agent = AGENTS_BY_KEY.get(action[len("agent:") :])
            if agent is not None:
                self.action_open_tmux(agent_cmd=agent.launch_command, purpose=agent.purpose)
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

    def _open_review_diff(self, path: Path | None = None) -> None:
        if path is None:
            path = self._get_selected_path()
        if path is None:
            return
        info = self._results.get(str(path))
        branch = info.branch if info else None
        self.push_screen(DiffReviewScreen(path.name, path, branch=branch))

    def _handle_remove_selection(self, session_name: str | None) -> None:
        if session_name is None:
            return
        self.push_screen(
            ConfirmScreen(f"Remove session '{session_name}'?"),
            callback=lambda confirmed: self._do_remove(confirmed, session_name),
        )

    def _do_remove(self, confirmed: bool, session_name: str) -> None:
        if confirmed:
            from ...integrations.tmux import kill_tmux_session, sync_panel_tmux_config

            kill_tmux_session(session_name)
            sync_panel_tmux_config()

            self._sessions_entries = [
                e for e in self._sessions_entries if e["session_name"] != session_name
            ]
            self._apply_sessions_filter_and_sort()


def _run_console() -> None:
    app = GitDirectorConsole()
    try:
        app.run()
    finally:
        app._shutdown_background_work()
