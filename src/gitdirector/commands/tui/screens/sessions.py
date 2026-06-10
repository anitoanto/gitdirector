"""Modal screens related to tmux session selection and management."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..constants import _MODAL_BINDINGS, _MODAL_CSS


class RemoveSessionScreen(ModalScreen[str | None]):
    """Modal listing sessions available for removal."""

    BINDINGS = _MODAL_BINDINGS

    CSS = (
        "RemoveSessionScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(self, repo_name: str, repo_path: Path) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path

    def compose(self) -> ComposeResult:
        from ....integrations.tmux import list_repo_sessions

        sessions = list_repo_sessions(self.repo_path)

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


class SelectSessionScreen(ModalScreen[str | None]):
    """Modal for selecting a tmux session to assign to a pane."""

    BINDINGS = _MODAL_BINDINGS

    CSS = (
        "SelectSessionScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(self, pane_index: int, current_session: str | None = None) -> None:
        super().__init__()
        self.pane_index = pane_index
        self.current_session = current_session

    def compose(self) -> ComposeResult:
        from ....integrations.tmux import list_all_gd_sessions

        sessions = list_all_gd_sessions()

        with Vertical(id="menu-container"):
            yield Static(
                f"[bold white]Assign Session to Pane {self.pane_index}[/bold white]",
                id="menu-title",
            )
            items: list[Option] = []
            if self.current_session:
                items.append(
                    Option("[red]✕[/red] [dim]Clear pane[/dim]", id="__clear__"),
                )
                items.append(Option("", disabled=True))
            if sessions:
                for entry in sessions:
                    sn = entry["session_name"]
                    repo = entry["repo"]
                    purpose = entry["purpose"]
                    current_marker = " [cyan]◄ current[/cyan]" if sn == self.current_session else ""
                    items.append(
                        Option(
                            f"[white]●[/white] [bold]{purpose}[/bold]"
                            f" [dim]{repo}[/dim]  {sn}{current_marker}",
                            id=sn,
                        )
                    )
            else:
                items.append(Option("[dim]No active sessions[/dim]", disabled=True))
            yield OptionList(*items, id="action-menu")
            yield Static("↑↓/jk select    \\[enter] confirm    \\[esc] cancel", id="menu-hint")

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


__all__ = ["RemoveSessionScreen", "SelectSessionScreen"]
