"""Shared modal screen helpers for session-oriented action menus."""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ..constants import _MODAL_BINDINGS, _MODAL_CSS
from ..terminal_caps import strip_unsupported_css as _safe_css


def session_action_menu_css(screen_name: str) -> str:
    return _safe_css(
        f"{screen_name} {{"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )


class SessionActionMenuScreen(ModalScreen[str]):
    """Base menu for creating, attaching, and removing tmux sessions."""

    BINDINGS = _MODAL_BINDINGS

    def __init__(self, title: str, path: Path) -> None:
        super().__init__()
        self.title = title
        self.path = path

    def _subtitle(self) -> str:
        return ""

    def _primary_options(self) -> list[Option]:
        return [Option("[white]+[/white] [bold]TMUX Session[/bold]", id="new_session")]

    def _session_options(self, sessions: list[str]) -> list[Option]:
        if not sessions:
            return []

        count = len(sessions)
        label = "session" if count == 1 else "sessions"
        items = [
            Option("", disabled=True),
            Option(f"[dim]{count} active {label}[/dim]", disabled=True),
        ]
        for session_name in sessions:
            parts = session_name.split("/")
            session_label = f"{parts[2]}/{parts[3]}" if len(parts) >= 4 else session_name
            items.append(
                Option(
                    f"[white]●[/white] [bold]{session_label}[/bold] [dim]{session_name}[/dim]",
                    id=f"attach:{session_name}",
                )
            )
        return items

    def _agent_options(self) -> list[Option]:
        return [
            Option("", disabled=True),
            Option("[dim]Launch AI Agent[/dim]", disabled=True),
            Option("[white]◆[/white] [bold]Pi[/bold]", id="agent:pi"),
            Option("[white]◆[/white] [bold]OpenCode[/bold]", id="agent:opencode"),
            Option("[white]◆[/white] [bold]Claude Code[/bold]", id="agent:claude"),
            Option("[white]◆[/white] [bold]GitHub Copilot[/bold]", id="agent:copilot"),
            Option("[white]◆[/white] [bold]Codex[/bold]", id="agent:codex"),
        ]

    def _remove_options(self, sessions: list[str]) -> list[Option]:
        if not sessions:
            return []
        return [
            Option("", disabled=True),
            Option("[white]✕[/white] [dim]Remove Session...[/dim]", id="remove_session"),
        ]

    def compose(self) -> ComposeResult:
        from ....integrations.tmux import list_repo_sessions

        sessions = list_repo_sessions(self.path)
        items = self._primary_options()
        items.extend(self._session_options(sessions))
        items.extend(self._agent_options())
        items.extend(self._remove_options(sessions))

        with Vertical(id="menu-container"):
            yield Static(f"[bold white]{escape(self.title)}[/bold white]", id="menu-title")
            yield Static(self._subtitle(), id="menu-branch")
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
