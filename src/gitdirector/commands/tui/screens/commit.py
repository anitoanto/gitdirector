"""Modals for the ``Review Diff`` → commit → push flow.

The flow is:

1. ``StageFilesConfirmScreen`` — asks the user to stage every change
   (``git add -A``) before committing. Shows the aggregated ``+N -M``
   stats so the user can sanity-check what they're about to commit.
2. ``CommitMessageScreen`` — collects a commit message and lets the
   user pick "commit" or "commit & push" as the final action. Uses a
   single-line ``Input`` (multi-line commits aren't supported by the
   project's existing UX patterns — see ``RenamePanelScreen``).
3. ``CommitLoadingScreen`` — spinner with a status line while the
   commit/push runs in a worker thread (the TUI must not block).
4. ``CommitResultScreen`` — shows the outcome of the commit and (if
   requested) the push, with a footer hint to close.

The screens follow the same design language as the rest of the TUI
(rounded ``$primary`` border, ``$panel`` background, ``$boost`` docked
hint bar) so they sit naturally on top of the ``DiffReviewScreen``.
"""

from __future__ import annotations

from typing import Optional

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import LoadingIndicator, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ..constants import _MODAL_BINDINGS, _MODAL_CSS
from ..terminal_caps import strip_unsupported_css as _safe_css

# ---------------------------------------------------------------------------
# Stage-all confirm
# ---------------------------------------------------------------------------


class StageFilesConfirmScreen(ModalScreen[bool]):
    """Ask the user whether to ``git add -A`` before committing.

    The prompt includes the aggregated ``+N -M`` stats across every
    file in the current diff so the user sees exactly what would be
    staged if they answer Yes.

    Dismisses with ``True`` for "Yes, stage everything", ``False`` for
    "No, don't stage" or any cancel path.
    """

    BINDINGS = _MODAL_BINDINGS

    CSS = _safe_css(
        "StageFilesConfirmScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS
    )

    def __init__(self, repo_name: str, additions: int, deletions: int, file_count: int) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.additions = additions
        self.deletions = deletions
        self.file_count = file_count

    def compose(self) -> ComposeResult:
        noun = "file" if self.file_count == 1 else "files"
        with Vertical(id="menu-container"):
            yield Static(
                f"[bold $text]Stage all changes in[/] "
                f"[$text-primary]{escape(self.repo_name)}[/]"
                f"[bold $text]?[/]",
                id="menu-title",
            )
            yield Static(
                f"[$text-success]+{self.additions}[/]  "
                f"[$text-error]-{self.deletions}[/]  "
                f"[dim]\u00b7  {self.file_count} {noun}[/dim]",
                id="menu-stats",
            )
            yield OptionList(
                Option("[dim]\u2717 No, keep working[/dim]", id="no"),
                Option("[$text]\u2713[/] [bold]Yes, stage everything[/bold]", id="yes"),
                id="action-menu",
            )
            yield Static(
                "[$text-success]+N[/] additions  [$text-error]-N[/] deletions  \u00b7  esc to cancel",
                id="menu-hint",
            )

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


# ---------------------------------------------------------------------------
# Commit message + action choice
# ---------------------------------------------------------------------------


class _CommitActionOptionList(OptionList):
    """OptionList used as the commit/push picker with vim-style j/k keys.

    The ``j``/``k`` bindings live on the widget itself so they only
    fire when the picker has focus. This keeps typing ``j`` or ``k``
    inside the commit message input working as expected.
    """

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]


class CommitMessageScreen(ModalScreen[Optional[tuple[str, bool]]]):
    """Collect a commit message and the final action (commit / commit & push).

    Dismisses with a tuple ``(message, push_after)`` on confirm, or
    ``None`` on cancel. ``push_after=True`` means the user picked
    "commit & push" so the caller should run ``git push`` after the
    commit succeeds.

    Two picker entries below the input act as the action
    picker; the first option ("commit") is highlighted by default.
    Pressing ``enter`` from the input field commits with the currently
    highlighted action, matching the project's existing form
    conventions (see ``CreatePanelScreen``).

    Focus toggles between the input and the action picker with
    ``tab`` / ``shift+tab``. While the picker is focused, ``j`` /
    ``k`` (and ``up`` / ``down``) move the selection.
    """

    # Up/down and j/k are handled by the focused widget: the TextArea moves
    # its own cursor and the action picker (``_CommitActionOptionList``)
    # binds j/k itself, so the screen only owns confirm, cancel, and focus.
    BINDINGS = [
        Binding("escape", "cancel", "Esc cancel", show=True),
        Binding("ctrl+enter", "confirm", "Confirm", show=False),
        Binding("tab", "focus_toggle", "Tab switch focus", show=False),
        Binding("shift+tab", "focus_toggle", "Shift+Tab switch focus", show=False),
    ]

    CSS = _safe_css(
        "CommitMessageScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }"
        + _MODAL_CSS
        + """
        #commit-message-container {
            width: 60%;
            height: auto;
            border: round $primary;
            background: $panel;
            padding: 1 2;
        }
        #commit-message-title {
            text-align: center;
            padding: 0 1 1 1;
            color: $text;
        }
        #commit-message-stats {
            text-align: center;
            padding: 0 1 1 1;
            color: $text-muted;
        }
        #commit-message-input {
            width: 1fr;
            height: auto;
            min-height: 3;
            max-height: 10;
            border: none;
            background: $boost;
            color: $text;
            margin: 0 0 1 0;
            padding: 0 1;
            scrollbar-size-vertical: 0;
            overflow-y: hidden;
        }
        #commit-message-input:focus {
            background: $boost;
        }
        #commit-message-action-list {
            width: 1fr;
            height: auto;
            border: none;
            padding: 0 1;
            margin: 0 0 1 0;
        }
        #commit-message-hint {
            text-align: center;
            padding: 0 1;
            color: $text-muted;
        }
    """
    )

    def __init__(self, repo_name: str, additions: int, deletions: int, file_count: int) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.additions = additions
        self.deletions = deletions
        self.file_count = file_count
        self._in_message = True

    def compose(self) -> ComposeResult:
        noun = "file" if self.file_count == 1 else "files"
        with Vertical(id="commit-message-container"):
            yield Static(
                f"[bold $text]Commit changes in[/] [$text-primary]{escape(self.repo_name)}[/]",
                id="commit-message-title",
            )
            yield Static(
                f"[$text-success]+{self.additions}[/]  "
                f"[$text-error]-{self.deletions}[/]  "
                f"[dim]\u00b7  {self.file_count} {noun}[/dim]",
                id="commit-message-stats",
            )
            yield TextArea(
                "",
                placeholder="Commit message\u2026",
                id="commit-message-input",
            )
            yield _CommitActionOptionList(
                Option("[$text-primary]\u2191[/] [bold]Commit and push[/bold]", id="commit_push"),
                Option("[$text]\u2713[/] [bold]Commit[/bold]", id="commit"),
                id="commit-message-action-list",
            )
            yield Static(
                "type message    \\[tab] switch    [\u2191\u2193/jk] pick action"
                "    [ctrl+enter] confirm    \\[esc] cancel",
                id="commit-message-hint",
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


class CommitLoadingScreen(ModalScreen[None]):
    """Spinner shown while the commit/push worker runs in a thread."""

    BINDINGS = [Binding("escape", "noop", "Esc", show=False)]

    CSS = _safe_css(
        "CommitLoadingScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }"
        + _MODAL_CSS
        + """
        #commit-loading-container {
            width: 50%;
            height: auto;
            border: round $primary;
            background: $panel;
            padding: 1 2;
        }
        #commit-loading-title {
            text-align: center;
            color: $text;
            padding: 0 1;
        }
        #commit-loading-status {
            text-align: center;
            color: $text-muted;
            padding: 1 1 0 1;
        }
    """
    )

    def __init__(self, repo_name: str, push_after: bool) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.push_after = push_after
        self._status = "Staging\u2026"

    def compose(self) -> ComposeResult:
        with Container(id="commit-loading-container"):
            yield Static(
                f"[bold $text]{'Commit & push' if self.push_after else 'Commit'}"
                f" \u2014 {escape(self.repo_name)}[/]",
                id="commit-loading-title",
            )
            yield LoadingIndicator(id="commit-loading-spinner")
            yield Static(self._status, id="commit-loading-status")

    def set_status(self, message: str) -> None:
        """Update the spinner status line from the worker thread."""
        self._status = message
        try:
            self.query_one("#commit-loading-status", Static).update(message)
        except Exception:
            pass

    def action_noop(self) -> None:
        # Esc does nothing on the loading screen — the worker must
        # finish or the app must shut down for the modal to go away.
        return None


# ---------------------------------------------------------------------------
# Result screen
# ---------------------------------------------------------------------------


class CommitResultScreen(ModalScreen[None]):
    """Show the outcome of a commit (and optional push) to the user."""

    BINDINGS = _MODAL_BINDINGS

    CSS = _safe_css(
        "CommitResultScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }"
        + _MODAL_CSS
        + """
        #commit-result-container {
            width: 60%;
            height: auto;
            border: round $primary;
            background: $panel;
            padding: 1 2;
        }
        #commit-result-title {
            text-align: center;
            color: $text;
            padding: 0 1;
        }
        #commit-result-message {
            text-align: center;
            color: $text-muted;
            padding: 0 1 1 1;
        }
        #commit-result-output {
            width: 1fr;
            height: auto;
            max-height: 12;
            padding: 0 1;
            background: $boost;
            color: $text;
        }
        #commit-result-hint {
            text-align: center;
            padding: 1 1 0 1;
            color: $text-muted;
        }
    """
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

    def compose(self) -> ComposeResult:
        if self.commit_ok:
            title = (
                f"[bold green]\u2713 Pushed[/bold green]  "
                f"[dim]\u2014  {escape(self.repo_name)}[/dim]"
                if self.push_ok is True
                else f"[bold green]\u2713 Committed[/bold green]  "
                f"[dim]\u2014  {escape(self.repo_name)}[/dim]"
            )
        else:
            title = (
                f"[bold red]\u2717 Commit failed[/bold red]  "
                f"[dim]\u2014  {escape(self.repo_name)}[/dim]"
            )

        with Container(id="commit-result-container"):
            yield Static(title, id="commit-result-title")
            yield Static(
                f"[dim]message:[/dim] [italic]{escape(self.commit_message)}[/italic]",
                id="commit-result-message",
            )
            yield Static(escape(self.output) if self.output else "", id="commit-result-output")
            yield Static(
                "[enter]/[esc] close",
                id="commit-result-hint",
            )

    def on_mount(self) -> None:
        # Focus the container so any key press (enter/space) dismisses.
        try:
            self.query_one("#commit-result-container").focus()
        except Exception:
            pass

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
