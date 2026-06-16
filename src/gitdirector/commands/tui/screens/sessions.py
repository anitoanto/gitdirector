"""Modal screens related to tmux session selection and management."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ..constants import _MODAL_BINDINGS, _MODAL_CSS
from ..terminal_caps import strip_unsupported_css as _safe_css


class RemoveSessionScreen(ModalScreen[str | None]):
    """Modal listing sessions available for removal."""

    BINDINGS = _MODAL_BINDINGS

    CSS = _safe_css(
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

    CSS = _safe_css(
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


__all__ = ["EditSessionDescriptionScreen", "RemoveSessionScreen", "SelectSessionScreen"]


class EditSessionDescriptionScreen(ModalScreen[str | None]):
    """Modal for editing the description stored on a tmux session.

    Dismisses with the entered description (already stripped, may be
    empty) on confirm, or ``None`` on cancel. Callers that want the
    default placeholder ("-") should treat an empty result as "clear the
    description".
    """

    BINDINGS = _MODAL_BINDINGS

    CSS = _safe_css(
        "EditSessionDescriptionScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }"
        + _MODAL_CSS
        + """
    EditSessionDescriptionScreen #menu-container {
        width: 64;
    }
    EditSessionDescriptionScreen #description-input {
        width: 1fr;
        height: 3;
        border: none;
        background: $boost;
        color: $text;
        margin: 1 0;
        padding: 0 1;
    }
    #description-session-name {
        text-align: center;
        padding: 0 1 0 1;
        color: $text-muted;
    }
    """
    )

    def __init__(self, session_name: str, current_description: str) -> None:
        super().__init__()
        self.session_name = session_name
        self.current_description = current_description
        self._initial_value = "" if current_description == "-" else current_description

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static("[bold white]Edit Description[/bold white]", id="menu-title")
            yield Static(f"[dim]{self.session_name}[/dim]", id="description-session-name")
            yield Input(
                value=self._initial_value,
                placeholder="description (leave empty to reset to '-')",
                id="description-input",
            )
            yield Static("\\[enter] save    \\[esc] cancel", id="menu-hint")

    def on_mount(self) -> None:
        inp = self.query_one("#description-input", Input)
        inp.focus()
        if self._initial_value:
            inp.action_end()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        new_value = event.value.strip()
        self.dismiss(new_value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        pass

    def action_cursor_up(self) -> None:
        pass
