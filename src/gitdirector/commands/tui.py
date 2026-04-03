"""Interactive TUI console for GitDirector using Textual."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, OptionList, Static
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


class ActionMenuScreen(ModalScreen[str]):
    """Modal popup with actions for the selected repository."""

    BINDINGS = [
        Binding("escape", "cancel", "Esc close", show=True),
        Binding("j", "cursor_down", "↓", show=False),
        Binding("k", "cursor_up", "↑", show=False),
    ]

    CSS = """
    ActionMenuScreen {
        align: center middle;
    }
    #menu-container {
        width: 50%;
        height: auto;
        border: panel $panel;
        background: $panel;
        padding: 0 2;
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
        padding: 0;
        margin: 1 0;
    }
    #menu-hint {
        text-align: center;
        padding: 0 1 1 1;
        color: $text-muted;
    }
    """

    def __init__(self, repo_name: str, branch: str | None = None) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.branch = branch

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static(f"[bold white]{self.repo_name}[/bold white]", id="menu-title")
            yield Static(
                f"[dim]branch:[/dim] [cyan]{self.branch or '—'}[/cyan]",
                id="menu-branch",
            )
            yield OptionList(
                Option("TMUX Session", id="open_tmux"),
                id="action-menu",
            )
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
        padding: 0 1;
    }
    DataTable {
        height: 1fr;
        overflow-x: auto;
        overflow-y: auto;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("h", "cursor_left", "Left", show=False),
        Binding("l", "cursor_right", "Right", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.manager = RepositoryManager()
        self._repo_paths: list[Path] = []
        self._results: dict[str, RepositoryInfo] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield DataTable(id="repo-table", cursor_type="row")
        yield Static("Loading repositories…", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        self._col_keys = table.add_columns(
            "Repository", "Sync", "Branch", "Changes", "Last Commit", "Path"
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
                info = future.result()
                self._results[str(info.path)] = info
                done += 1
                self.call_from_thread(self._update_row, info)
                remaining = total - done
                if remaining > 0:
                    self.call_from_thread(
                        self._update_status,
                        f"{done} done, {remaining} remaining…",
                    )

        self.call_from_thread(
            self._update_status,
            f"{total} {'repository' if total == 1 else 'repositories'} loaded   "
            "↑↓/jk navigate  [enter] open tmux  r refresh  q quit",
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
                str(path),
                key=str(path),
            )

    def _update_row(self, info: RepositoryInfo) -> None:
        table = self.query_one("#repo-table", DataTable)
        row_key = str(info.path)
        ck = self._col_keys
        table.update_cell(row_key, ck[1], _STATUS_LABEL.get(info.status, "unknown"))
        table.update_cell(row_key, ck[2], info.branch or "—")
        table.update_cell(row_key, ck[3], _changes_label(info))
        table.update_cell(row_key, ck[4], info.last_updated or "—")

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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_show_menu()

    def action_open_tmux(self) -> None:
        path = self._get_selected_path()
        if path is None:
            return
        repo_name = path.name
        # Exit the TUI before handing over to tmux
        self._tmux_target = (repo_name, path)
        self.exit(result=("tmux", repo_name, path))

    def action_show_menu(self) -> None:
        path = self._get_selected_path()
        if path is None:
            return
        info = self._results.get(str(path))
        branch = info.branch if info else None
        self.push_screen(ActionMenuScreen(path.name, branch), callback=self._handle_menu_action)

    def _handle_menu_action(self, action: str | None) -> None:
        if action is None:
            return
        if action == "open_tmux":
            self.action_open_tmux()

    def action_refresh(self) -> None:
        self._results.clear()
        self._load_repos()


def _run_console() -> None:
    app = GitDirectorConsole()
    result = app.run()
    if result is not None:
        action, repo_name, path = result
        if action == "tmux":
            from ..integrations.tmux import open_in_tmux

            open_in_tmux(repo_name, path)


def register(cli: click.Group):
    @cli.command(name="console")
    def console_cmd():
        """Interactive TUI for browsing and opening repositories."""
        _run_console()
