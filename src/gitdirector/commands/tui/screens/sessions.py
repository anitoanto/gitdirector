"""Modal screens related to tmux session selection and management."""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ..constants import _MODAL_BINDINGS
from .card import ShortcutKeys, card_css, card_subtitle, key_hints, menu_row, spacer

_SESSION_KEYS = "123456789"
_PICK_HINT = key_hints(("↑↓", "select"), ("⏎", "choose"), ("1-9", "jump"), ("esc", "cancel"))


def _session_label(session_name: str) -> str:
    """``claude/1`` for ``gd/<repo>/claude/1``."""
    parts = session_name.split("/")
    return "/".join(parts[2:]) if len(parts) > 2 else session_name


class _SessionPicker(ShortcutKeys, ModalScreen[str | None]):
    BINDINGS = _MODAL_BINDINGS

    def _keyed(self, session_names: list[str]) -> list[str]:
        """Give the first nine sessions their number key; returns the keys in order."""
        keys = list(_SESSION_KEYS[: len(session_names)])
        self._shortcuts.update(zip(keys, session_names))
        return keys + [""] * (len(session_names) - len(keys))

    def on_mount(self) -> None:
        menu = self.query("#action-menu")
        if menu:
            menu.first().focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._choose(event.option.id)

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


class RemoveSessionScreen(_SessionPicker):
    """Pick one of a repository's sessions to remove."""

    CSS = card_css("RemoveSessionScreen")

    def __init__(self, repo_name: str, repo_path: Path) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path
        self._shortcuts = {}

    def compose(self) -> ComposeResult:
        from ....integrations.tmux import list_repo_sessions

        sessions = list_repo_sessions(self.repo_path)

        with Vertical(id="menu-container"):
            yield Static("Remove a session", id="menu-title")
            yield Static(card_subtitle(self.repo_path), id="menu-branch")
            if sessions:
                options = [
                    Option(
                        menu_row(
                            Text.assemble(("✕ ", "dim"), (_session_label(name), "bold")),
                            Text(name, style="dim"),
                            key,
                        ),
                        id=name,
                    )
                    for name, key in zip(sessions, self._keyed(sessions))
                ]
                yield OptionList(*options, id="action-menu")
                yield Static(_PICK_HINT, id="menu-hint")
            else:
                yield Static("No running sessions.", classes="card-body")
                yield Static(key_hints(("esc", "close")), id="menu-hint")


class SelectSessionScreen(_SessionPicker):
    """Pick the session a panel pane shows, or clear the pane."""

    CSS = card_css("SelectSessionScreen", width=72)

    def __init__(self, pane_index: int, current_session: str | None = None) -> None:
        super().__init__()
        self.pane_index = pane_index
        self.current_session = current_session
        self._shortcuts = {"x": "__clear__"} if current_session else {}

    def compose(self) -> ComposeResult:
        from ....integrations.tmux import list_all_gd_sessions

        sessions = list_all_gd_sessions()
        names = [entry["session_name"] for entry in sessions]

        with Vertical(id="menu-container"):
            yield Static(f"Pane {self.pane_index}", id="menu-title")
            yield Static("Choose the session this pane shows.", id="menu-branch")
            items: list[Option] = []
            if self.current_session:
                items.append(
                    Option(
                        menu_row(Text.assemble(("✕ ", "dim"), ("Clear pane", "dim")), "", "x"),
                        id="__clear__",
                    )
                )
                items.append(spacer())
            for entry, key in zip(sessions, self._keyed(names)):
                name = entry["session_name"]
                current = name == self.current_session
                label = Text.assemble(
                    ("● " if current else "○ ", "bold" if current else "dim"),
                    (entry["purpose"], "bold"),
                    ("  ", ""),
                    (entry["repo"], "dim"),
                )
                detail = Text("current", style="bold") if current else Text(name, style="dim")
                items.append(Option(menu_row(label, detail, key), id=name))
            if not sessions:
                items.append(Option(Text("No running sessions.", style="dim"), disabled=True))
            yield OptionList(*items, id="action-menu")
            yield Static(_PICK_HINT, id="menu-hint")


__all__ = ["EditSessionDescriptionScreen", "RemoveSessionScreen", "SelectSessionScreen"]


class DescriptionTextArea(TextArea):
    BINDINGS = [*TextArea.BINDINGS, Binding("enter", "submit", show=False, priority=True)]

    def action_submit(self) -> None:
        self.screen.action_submit()


class EditSessionDescriptionScreen(ModalScreen[str | None]):
    """Modal for editing the description stored on a tmux session.

    Dismisses with the entered description (already stripped, may be
    empty) on confirm, or ``None`` on cancel. Callers that want the
    default placeholder ("-") should treat an empty result as "clear the
    description".
    """

    BINDINGS = _MODAL_BINDINGS

    CSS = card_css(
        "EditSessionDescriptionScreen",
        extra="""
    EditSessionDescriptionScreen #description-input {
        min-height: 3;
        max-height: 10;
        scrollbar-size-vertical: 0;
        overflow-y: hidden;
    }
    """,
    )

    def __init__(self, session_name: str, current_description: str) -> None:
        super().__init__()
        self.session_name = session_name
        self.current_description = current_description
        self._initial_value = "" if current_description == "-" else current_description

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            yield Static("Description", id="menu-title")
            yield Static(escape(self.session_name), id="description-session-name")
            yield DescriptionTextArea(
                self._initial_value,
                placeholder="What is this session for? Empty resets it.",
                id="description-input",
                classes="card-input",
            )
            yield Static(key_hints(("⏎", "save"), ("esc", "cancel")), id="menu-hint")

    def on_mount(self) -> None:
        inp = self.query_one("#description-input", TextArea)
        inp.focus()
        if self._initial_value:
            lines = self._initial_value.splitlines() or [""]
            inp.move_cursor((len(lines) - 1, len(lines[-1])))
        self.call_after_refresh(self._resize_description_input)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "description-input":
            self.call_after_refresh(self._resize_description_input)

    def _resize_description_input(self) -> None:
        inp = self.query_one("#description-input", TextArea)
        inp.styles.height = min(10, max(3, inp.virtual_size.height))

    def action_submit(self) -> None:
        inp = self.query_one("#description-input", TextArea)
        self.dismiss(inp.text.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        pass

    def action_cursor_up(self) -> None:
        pass
