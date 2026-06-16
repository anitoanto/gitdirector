"""Modal screens related to repository actions (action menu, git commands, info, pull)."""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import LoadingIndicator, OptionList, Static
from textual.widgets.option_list import Option

from ....info import RepoInfoResult
from ..constants import _MODAL_BINDINGS, _MODAL_CSS
from ..terminal_caps import strip_unsupported_css as _safe_css
from ._shared import _render_ansi_output
from .session_actions import SessionActionMenuScreen, session_action_menu_css


class ActionMenuScreen(SessionActionMenuScreen):
    """Modal popup with actions for the selected repository."""

    CSS = session_action_menu_css("ActionMenuScreen")

    def __init__(self, repo_name: str, repo_path: Path, branch: str | None = None) -> None:
        super().__init__(repo_name, repo_path)
        self.branch = branch

    def _subtitle(self) -> str:
        return f"[dim]branch:[/dim] [cyan]{self.branch or '—'}[/cyan]"

    def _primary_options(self) -> list[Option]:
        return super()._primary_options() + [
            Option("", disabled=True),
            Option(
                "[white]\u00b1[/white] [bold]Review Diff[/bold] [dim]uncommitted changes[/dim]",
                id="review_diff",
            ),
        ]


class GitOperationsMenuScreen(ModalScreen[str]):
    """Modal popup with git operations for the selected repository."""

    BINDINGS = _MODAL_BINDINGS

    CSS = _safe_css(
        "GitOperationsMenuScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

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
                Option("[white]>[/white] [bold]Status[/bold] [dim]git status[/dim]", id="status"),
                Option(
                    "[white]*[/white] [bold]Timeline[/bold] "
                    "[dim]git log --graph --decorate --all[/dim]",
                    id="timeline",
                ),
                Option(
                    "[white]⑂[/white] [bold]Branches[/bold] [dim]git branch -a[/dim]",
                    id="branches",
                ),
                Option(
                    "[white]◎[/white] [bold]Remotes[/bold] [dim]git remote -v[/dim]",
                    id="remotes",
                ),
                Option(
                    "[white]↓[/white] [bold]Pull[/bold] [dim]git pull --ff-only[/dim]",
                    id="pull",
                ),
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


class GitCommandResultScreen(ModalScreen[str | None]):
    """Modal popup showing the output of a git command."""

    BINDINGS = [
        Binding("escape", "back", "Esc back", show=False),
        Binding("enter", "cancel", "Enter close", show=False),
        Binding("down", "scroll_down", "↓", show=False),
        Binding("j", "scroll_down", "↓", show=False),
        Binding("up", "scroll_up", "↑", show=False),
        Binding("k", "scroll_up", "↑", show=False),
    ]

    DEFAULT_CSS = _safe_css("""
    GitCommandResultScreen {
        align: center middle;
        background: $panel 80%;
        hatch: right $primary 30%;
    }
    #git-command-result-container {
        width: 84;
        height: auto;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    #git-command-result-title {
        text-align: center;
        padding: 1 1 0 1;
        color: $text;
    }
    #git-command-result-command {
        text-align: center;
        padding: 0 1 1 1;
        color: $text-muted;
    }
    #git-command-result-status {
        text-align: center;
        padding: 0 1 1 1;
    }
    #git-command-result-output-scroll {
        height: 12;
        border: round $surface;
        padding: 0 1;
        margin: 0 1;
    }
    #git-command-result-output {
        color: $text;
    }
    #git-command-result-hint {
        text-align: center;
        padding: 1 1 1 1;
        color: $text-muted;
    }
    """)

    def __init__(
        self,
        repo_name: str,
        command: str | None,
        ok: bool,
        output: str,
        *,
        success_text: str = "Command completed",
        failure_text: str = "Command failed",
    ) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.command = command
        self.ok = ok
        self.output = output.strip() or ("No output." if not ok else "Command completed.")
        self.success_text = success_text
        self.failure_text = failure_text

    def compose(self) -> ComposeResult:
        status_text = self.success_text if self.ok else self.failure_text
        status_style = "green" if self.ok else "red"

        with Vertical(id="git-command-result-container"):
            yield Static(
                f"[bold white]{escape(self.repo_name)}[/bold white]",
                id="git-command-result-title",
            )
            if self.command:
                yield Static(
                    f"[dim]{escape(self.command)}[/dim]",
                    id="git-command-result-command",
                )
            yield Static(
                f"[{status_style}]{escape(status_text)}[/{status_style}]",
                id="git-command-result-status",
            )
            with VerticalScroll(id="git-command-result-output-scroll"):
                yield Static(_render_ansi_output(self.output), id="git-command-result-output")
            yield Static(
                "↑↓/jk scroll    \\[enter] close    \\[esc] back",
                id="git-command-result-hint",
            )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_back(self) -> None:
        self.dismiss("back")

    def action_scroll_down(self) -> None:
        self.query_one("#git-command-result-output-scroll", VerticalScroll).action_scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#git-command-result-output-scroll", VerticalScroll).action_scroll_up()


class PullResultScreen(ModalScreen[str | None]):
    """Modal popup showing the outcome of a repository pull."""

    BINDINGS = [
        Binding("escape", "back", "Esc back", show=False),
        Binding("enter", "cancel", "Enter close", show=False),
        Binding("down", "scroll_down", "↓", show=False),
        Binding("j", "scroll_down", "↓", show=False),
        Binding("up", "scroll_up", "↑", show=False),
        Binding("k", "scroll_up", "↑", show=False),
    ]

    CSS = _safe_css(
        "PullResultScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }"
        """
    #pull-result-container {
        width: 84;
        height: auto;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    #pull-result-title {
        text-align: center;
        padding: 1 1 0 1;
        color: $text;
    }
    #pull-result-command {
        text-align: center;
        padding: 0 1 1 1;
        color: $text-muted;
    }
    #pull-result-status {
        text-align: center;
        padding: 0 1 1 1;
    }
    #pull-result-output-scroll {
        height: 12;
        border: round $surface;
        padding: 0 1;
        margin: 0 1;
    }
    #pull-result-output {
        color: $text;
    }
    #pull-result-hint {
        text-align: center;
        padding: 1 1 1 1;
        color: $text-muted;
    }
    """
    )

    def __init__(self, repo_name: str, command: str | None, ok: bool, output: str) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.command = command
        self.ok = ok
        self.output = output.strip() or ("Already up to date." if ok else "No output.")

    def compose(self) -> ComposeResult:
        status_text = "Pull completed" if self.ok else "Pull failed"
        status_style = "green" if self.ok else "red"

        with Vertical(id="pull-result-container"):
            yield Static(
                f"[bold white]{escape(self.repo_name)}[/bold white]",
                id="pull-result-title",
            )
            if self.command:
                yield Static(f"[dim]{escape(self.command)}[/dim]", id="pull-result-command")
            yield Static(
                f"[{status_style}]{status_text}[/{status_style}]",
                id="pull-result-status",
            )
            with VerticalScroll(id="pull-result-output-scroll"):
                yield Static(_render_ansi_output(self.output), id="pull-result-output")
            yield Static(
                "↑↓/jk scroll    \\[enter] close    \\[esc] back",
                id="pull-result-hint",
            )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_back(self) -> None:
        self.dismiss("back")

    def action_scroll_down(self) -> None:
        self.query_one("#pull-result-output-scroll", VerticalScroll).action_scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#pull-result-output-scroll", VerticalScroll).action_scroll_up()


class PullLoadingScreen(ModalScreen[None]):
    """Loading overlay shown while a repository pull is in progress."""

    DEFAULT_CSS = _safe_css("""
    PullLoadingScreen {
        align: center middle;
        background: $panel 80%;
        hatch: right $primary 30%;
    }
    #pull-loading-container {
        width: 50%;
        height: auto;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    #pull-loading-container LoadingIndicator {
        height: 3;
        color: $primary;
    }
    #pull-loading-title {
        text-align: center;
        color: white;
        padding: 1 0 0 0;
    }
    #pull-loading-command {
        text-align: center;
        color: $text-muted;
        padding: 0 0 1 0;
    }
    #pull-loading-hint {
        text-align: center;
        padding: 1 1 1 1;
        color: $text-muted;
    }
    """)

    def __init__(self, repo_name: str, command: str) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.command = command

    def compose(self) -> ComposeResult:
        with Vertical(id="pull-loading-container"):
            yield LoadingIndicator()
            yield Static(f"Pulling [bold]{escape(self.repo_name)}[/bold]", id="pull-loading-title")
            yield Static(f"[dim]{escape(self.command)}[/dim]", id="pull-loading-command")
            yield Static("please wait...", id="pull-loading-hint")


class RepoInfoScreen(ModalScreen[None]):
    """Modal popup showing repository file statistics."""

    BINDINGS = [_MODAL_BINDINGS[0]]

    CSS = _safe_css(
        "RepoInfoScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }"
        """
    #info-container {
        width: 80;
        height: auto;
        border: round $primary;
        background: $panel;
        padding: 1 2;
    }
    #info-title {
        text-align: center;
        padding: 1 1 0 1;
        color: $text;
    }
    #info-path {
        text-align: center;
        padding: 0 1 1 1;
        color: $text-muted;
    }
    #info-loading {
        height: 3;
        padding: 1 0;
    }
    #info-stats {
        padding: 0 3;
        color: $text;
        text-align: center;
    }
    #info-table {
        padding: 1 3 0 3;
        color: $text;
        text-align: center;
    }
    #info-hint {
        text-align: center;
        padding: 1 1 1 1;
        color: $text-muted;
    }
    """
    )

    def __init__(self, repo_name: str, repo_path: Path) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path

    def compose(self) -> ComposeResult:
        with Vertical(id="info-container"):
            yield Static(
                f"[bold white]{self.repo_name}[/bold white]",
                id="info-title",
            )
            yield Static(
                f"[dim]{self.repo_path}[/dim]",
                id="info-path",
            )
            yield LoadingIndicator(id="info-loading")
            yield Static("", id="info-hint")

    def populate(self, result: RepoInfoResult) -> None:
        self.query_one("#info-loading", LoadingIndicator).remove()
        r = result
        stats = Static(
            f"[dim]Files[/dim]  [bold white]{r.total_files:,}[/bold white]    "
            f"[dim]Lines[/dim]  [bold white]{r.total_lines:,}[/bold white]\n"
            f"[dim]Tokens[/dim]  [bold white]{r.total_tokens:,}[/bold white]    "
            f"[dim]Max Depth[/dim]  [bold white]{r.max_depth}[/bold white]",
            id="info-stats",
        )
        hint = self.query_one("#info-hint", Static)
        hint.mount(stats, before=hint)
        if r.file_types:
            rows = (
                f"[dim]{'':>2}{'EXTENSION':<12} {'FILES':>6}   {'LINES':>8}"
                f"   {'TOKENS':>10}[/dim]\n"
            )
            for ft in r.file_types:
                lines_str = f"{ft.line_count:,}" if ft.line_count is not None else "-"
                tokens_str = f"{ft.token_count:,}" if ft.token_count is not None else "-"
                rows += (
                    f"[cyan]  {ft.extension:<12}[/cyan]"
                    f" [white]{ft.count:>6}[/white]"
                    f"   [dim]{lines_str:>8}[/dim]"
                    f"   [dim]{tokens_str:>10}[/dim]\n"
                )
            table = Static(rows.rstrip(), id="info-table")
            hint.mount(table, before=hint)
        hint.update("\\[esc] close")

    def show_error(self, message: str) -> None:
        loading = self.query("#info-loading")
        if loading:
            loading.first().remove()
        hint = self.query_one("#info-hint", Static)
        hint.update(f"[red]{escape(message)}[/red]\n\\[esc] close")

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = [
    "ActionMenuScreen",
    "GitCommandResultScreen",
    "GitOperationsMenuScreen",
    "PullLoadingScreen",
    "PullResultScreen",
    "RepoInfoScreen",
]
