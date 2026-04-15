"""Modal screen classes for the TUI."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    LoadingIndicator,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

from .constants import _MODAL_BINDINGS, _MODAL_CSS, _SORT_COLUMN_NAMES


class ActionMenuScreen(ModalScreen[str]):
    """Modal popup with actions for the selected repository."""

    BINDINGS = _MODAL_BINDINGS

    CSS = (
        "ActionMenuScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(self, repo_name: str, repo_path: Path, branch: str | None = None) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path
        self.branch = branch

    def compose(self) -> ComposeResult:
        from ...integrations.tmux import list_repo_sessions

        sessions = list_repo_sessions(self.repo_name)

        with Vertical(id="menu-container"):
            yield Static(f"[bold white]{self.repo_name}[/bold white]", id="menu-title")
            yield Static(
                f"[dim]branch:[/dim] [cyan]{self.branch or '—'}[/cyan]",
                id="menu-branch",
            )
            items: list[Option] = [
                Option("[white]+[/white] [bold]TMUX Session[/bold]", id="new_session"),
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
                    parts = s.split("/")
                    label = f"{parts[2]}/{parts[3]}" if len(parts) >= 4 else s
                    items.append(
                        Option(
                            f"[white]●[/white] [bold]{label}[/bold] [dim]{s}[/dim]",
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

    CSS = (
        "RemoveSessionScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(self, repo_name: str) -> None:
        super().__init__()
        self.repo_name = repo_name

    def compose(self) -> ComposeResult:
        from ...integrations.tmux import list_repo_sessions

        sessions = list_repo_sessions(self.repo_name)

        with Vertical(id="menu-container"):
            yield Static("[bold white]Select session to remove[/bold white]", id="menu-title")
            if sessions:
                options = [
                    Option(
                        f"[red]●[/red] [bold]"
                        f"{'/'.join(s.split('/')[2:]) if '/' in s else s}"
                        f"[/bold] [dim]{s}[/dim]",
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

    CSS = (
        "ConfirmScreen { align: center middle; background: $panel 80%; hatch: right $primary 30%; }"
        + _MODAL_CSS
    )

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

    CSS = (
        "SortMenuScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(
        self, current_column: int, current_reverse: bool, column_names: dict[int, str] | None = None
    ) -> None:
        super().__init__()
        self._current_column = current_column
        self._current_reverse = current_reverse
        self._column_names = column_names or _SORT_COLUMN_NAMES

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static("[bold white]Sort by[/bold white]", id="menu-title")
            items: list[Option] = []
            for idx, name in self._column_names.items():
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


class AgentLoadingScreen(ModalScreen[None]):
    """Full-screen loading overlay shown while an agent initialises."""

    DEFAULT_CSS = """
    AgentLoadingScreen {
        align: center middle;
        background: $panel 90%;
    }
    #loading-container {
        width: 50;
        height: auto;
        background: $panel;
        padding: 2 4;
        border: panel $primary;
    }
    #loading-container LoadingIndicator {
        height: 3;
        color: $primary;
    }
    #loading-text {
        text-align: center;
        color: white;
        padding: 1 0 0 0;
    }
    """

    def __init__(self, agent_cmd: str, session_name: str) -> None:
        super().__init__()
        self._agent_cmd = agent_cmd
        self._session_name = session_name

    def compose(self) -> ComposeResult:
        with Vertical(id="loading-container"):
            yield LoadingIndicator()
            yield Static(
                f"Waiting for [bold]{self._agent_cmd}[/bold] to initialize",
                id="loading-text",
            )

    def on_mount(self) -> None:
        self.set_timer(4, self._do_dismiss)

    def _do_dismiss(self) -> None:
        import subprocess
        import sys

        from ...integrations.tmux import attach_tmux_session

        session_name = self._session_name

        with self.app.suspend():
            sys.stdout.write("\033[?1049h\033[H\033[2J\033[?25l")
            sys.stdout.flush()
            subprocess.run(["tmux", "send-keys", "-t", session_name, "C-l", ""], check=False)
            subprocess.run(["tmux", "clear-history", "-t", session_name], check=False)
            attach_tmux_session(session_name)
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()

        self.dismiss(None)
