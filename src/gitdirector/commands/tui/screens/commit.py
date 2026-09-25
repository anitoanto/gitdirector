"""Modals for the diff viewer's commit → push flow.

1. ``StageFilesConfirmScreen``: stage everything (``git add -A``) first?
2. ``CommitMessageScreen``: the message, and commit or commit and push.
3. ``CommitLoadingScreen``: a spinner while the worker thread runs.
4. ``CommitResultScreen``: how the commit (and push) went.

They are cards like every other popup (see ``card``).
"""

from __future__ import annotations

from typing import Optional

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ..constants import _MODAL_BINDINGS
from .card import (
    LoadingCard,
    ShortcutKeys,
    card_css,
    card_palette,
    key_hints,
    menu_row,
)


def _stats(app, additions: int, deletions: int, file_count: int) -> Text:
    palette = card_palette(app)
    noun = "file" if file_count == 1 else "files"
    return Text.assemble(
        (f"+{additions}", f"bold {palette.success}"),
        " ",
        (f"-{deletions}", f"bold {palette.danger}"),
        (f"  ·  {file_count} {noun}", "dim"),
    )


# ---------------------------------------------------------------------------
# Stage-all confirm
# ---------------------------------------------------------------------------


class StageFilesConfirmScreen(ShortcutKeys, ModalScreen[bool]):
    """Ask whether to ``git add -A`` before committing, showing what that stages.

    Dismisses with ``True`` for "Yes, stage everything", ``False`` for
    "No" or any cancel path.
    """

    BINDINGS = _MODAL_BINDINGS

    CSS = card_css("StageFilesConfirmScreen", width=56)

    def __init__(self, repo_name: str, additions: int, deletions: int, file_count: int) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.additions = additions
        self.deletions = deletions
        self.file_count = file_count
        self._shortcuts = {"n": "no", "y": "yes"}

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            with Horizontal(id="menu-header"):
                yield Static("Stage all changes?", id="menu-title")
                yield Static(
                    _stats(self.app, self.additions, self.deletions, self.file_count),
                    id="menu-stats",
                )
            yield Static(Text(self.repo_name, style="dim"), id="menu-branch")
            yield OptionList(
                Option(menu_row(Text("No, keep working", style="bold"), "", "n"), id="no"),
                Option(menu_row(Text("Yes, stage everything", style="bold"), "", "y"), id="yes"),
                id="action-menu",
            )
            yield Static(key_hints(("y", "stage"), ("n", "no"), ("esc", "cancel")), id="menu-hint")

    def on_mount(self) -> None:
        self.query_one("#action-menu", OptionList).focus()

    def _choose(self, option_id: str) -> None:
        self.dismiss(option_id == "yes")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._choose(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_cursor_down(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#action-menu", OptionList).action_cursor_up()


# ---------------------------------------------------------------------------
# Commit message + action choice
# ---------------------------------------------------------------------------


class _CommitActionOptionList(OptionList):
    """The commit/push picker; j/k live here so typing them in the message works."""

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]


class CommitMessageScreen(ModalScreen[Optional[tuple[str, bool]]]):
    """Collect a commit message and the final action (commit / commit & push).

    Dismisses with ``(message, push_after)`` on confirm, or ``None`` on
    cancel. Tab moves between the message and the action picker; Enter on
    the picker, or ctrl+enter anywhere, commits with the highlighted action.
    """

    # Up/down and j/k are handled by the focused widget: the TextArea moves
    # its own cursor and the action picker binds j/k itself, so the screen
    # only owns confirm, cancel, and focus.
    BINDINGS = [
        Binding("escape", "cancel", "Esc cancel", show=True),
        Binding("ctrl+enter", "confirm", "Confirm", show=False),
        Binding("tab", "focus_toggle", "Tab switch focus", show=False),
        Binding("shift+tab", "focus_toggle", "Shift+Tab switch focus", show=False),
    ]

    CSS = card_css(
        "CommitMessageScreen",
        width=72,
        extra="""
    #commit-message-input {
        min-height: 3;
        max-height: 10;
        scrollbar-size-vertical: 0;
        overflow-y: hidden;
    }
    """,
    )

    def __init__(self, repo_name: str, additions: int, deletions: int, file_count: int) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.additions = additions
        self.deletions = deletions
        self.file_count = file_count
        self._in_message = True

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            with Horizontal(id="menu-header"):
                yield Static("Commit", id="menu-title")
                yield Static(
                    _stats(self.app, self.additions, self.deletions, self.file_count),
                    id="menu-meta",
                )
            yield Static(Text(self.repo_name, style="dim"), id="menu-branch")
            yield TextArea(
                "",
                placeholder="What changed and why…",
                id="commit-message-input",
                classes="card-input",
            )
            yield _CommitActionOptionList(
                Option(
                    menu_row(Text.assemble(("↑ ", "dim"), ("Commit and push", "bold"))),
                    id="commit_push",
                ),
                Option(
                    menu_row(Text.assemble(("✓ ", "dim"), ("Commit", "bold"))),
                    id="commit",
                ),
                id="commit-message-action-list",
                classes="card-actions",
            )
            yield Static(
                key_hints(("tab", "switch"), ("↑↓", "action"), ("^⏎", "commit"), ("esc", "cancel")),
                id="menu-hint",
            )

    def on_mount(self) -> None:
        message = self.query_one("#commit-message-input", TextArea)
        message.focus()
        self.call_after_refresh(self._resize_message_input)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "commit-message-input":
            self.call_after_refresh(self._resize_message_input)

    def _resize_message_input(self) -> None:
        message = self.query_one("#commit-message-input", TextArea)
        message.styles.height = min(10, max(3, message.virtual_size.height))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Click / enter on an option also confirms; the current
        # selection in the list is the action to take.
        self._confirm()

    def action_confirm(self) -> None:
        self._confirm()

    def _confirm(self) -> None:
        if not self._message_valid():
            return
        message = self._message()
        push_after = self._selected_action_id() == "commit_push"
        self.dismiss((message, push_after))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_focus_action(self) -> None:
        self._in_message = False
        self.query_one("#commit-message-action-list", OptionList).focus()

    def action_focus_message(self) -> None:
        self._in_message = True
        self.query_one("#commit-message-input", TextArea).focus()

    def action_focus_toggle(self) -> None:
        """Toggle focus between the message input and the action picker."""
        if self._action_list_has_focus():
            self.action_focus_message()
        else:
            self.action_focus_action()

    def _action_list_has_focus(self) -> bool:
        try:
            action_list = self.query_one("#commit-message-action-list", OptionList)
        except Exception:
            return False
        return self.focused is action_list

    def _message(self) -> str:
        return self.query_one("#commit-message-input", TextArea).text.strip()

    def _message_valid(self) -> bool:
        # Mirrors git: any non-empty message (subject, optional body) is accepted.
        return bool(self._message())

    def _selected_action_id(self) -> str:
        try:
            menu = self.query_one("#commit-message-action-list", OptionList)
        except Exception:
            return "commit"
        option = menu.highlighted_option
        if option is None or option.id is None:
            return "commit"
        return str(option.id)


# ---------------------------------------------------------------------------
# Loading spinner
# ---------------------------------------------------------------------------


class CommitLoadingScreen(LoadingCard):
    """Spinner shown while the commit/push worker runs in a thread."""

    BINDINGS = [Binding("escape", "noop", "Esc", show=False)]

    def __init__(self, repo_name: str, push_after: bool) -> None:
        verb = "Committing and pushing" if push_after else "Committing"
        super().__init__(f"{verb} [bold]{escape(repo_name)}[/bold]", "", "Staging…")
        self.repo_name = repo_name
        self.push_after = push_after

    def set_status(self, message: str) -> None:
        """Update the status line from the worker thread."""
        self.hint = message
        try:
            self.query_one("#menu-hint", Static).update(message)
        except Exception:
            pass

    def action_noop(self) -> None:
        # Esc does nothing on the loading screen: the worker must
        # finish or the app must shut down for the modal to go away.
        return None


# ---------------------------------------------------------------------------
# Result screen
# ---------------------------------------------------------------------------


class CommitResultScreen(ModalScreen[None]):
    """How the commit (and optional push) went."""

    BINDINGS = _MODAL_BINDINGS

    CSS = card_css(
        "CommitResultScreen",
        width=84,
        extra="""
    #commit-result-output {
        height: auto;
        max-height: 12;
        margin: 1 2 0 2;
        padding: 0 1;
        background: $surface;
        color: $text;
    }
    """,
    )

    def __init__(
        self,
        repo_name: str,
        commit_ok: bool,
        commit_message: str,
        push_ok: Optional[bool],
        output: str,
    ) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.commit_ok = commit_ok
        self.commit_message = commit_message
        self.push_ok = push_ok
        self.output = output

    def _badge(self) -> Text:
        palette = card_palette(self.app)
        good, bad = f"bold {palette.success}", f"bold {palette.danger}"
        if not self.commit_ok:
            return Text("✕ Commit failed", style=bad)
        if self.push_ok is True:
            return Text("✓ Committed and pushed", style=good)
        if self.push_ok is False:
            return Text.assemble(("✓ Committed", good), ("  ·  ", "dim"), ("✕ push failed", bad))
        return Text("✓ Committed", style=good)

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            with Horizontal(id="menu-header"):
                yield Static(escape(self.repo_name), id="menu-title")
                yield Static(self._badge(), id="result-status")
            yield Static(Text(self.commit_message, style="italic dim"), id="commit-result-message")
            if self.output:
                yield Static(Text(self.output), id="commit-result-output")
            yield Static(key_hints(("⏎", "close"), ("esc", "close")), id="menu-hint")

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key in {"enter", "space"}:
            self.dismiss(None)
            event.stop()

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = [
    "CommitLoadingScreen",
    "CommitMessageScreen",
    "CommitResultScreen",
    "StageFilesConfirmScreen",
]
