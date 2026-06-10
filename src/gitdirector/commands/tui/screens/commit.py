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

import re
from typing import Optional

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, LoadingIndicator, OptionList, Static
from textual.widgets.option_list import Option

from ..constants import _MODAL_BINDINGS, _MODAL_CSS

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

    CSS = (
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
                f"[bold white]Stage all changes in[/bold white] "
                f"[cyan]{escape(self.repo_name)}[/cyan]"
                f"[bold white]?[/bold white]",
                id="menu-title",
            )
            yield Static(
                f"[green]+{self.additions}[/green]  "
                f"[red]-{self.deletions}[/red]  "
                f"[dim]\u00b7  {self.file_count} {noun}[/dim]",
                id="menu-stats",
            )
            yield OptionList(
                Option("[dim]\u2717 No, keep working[/dim]", id="no"),
                Option("[white]\u2713[/white] [bold]Yes, stage everything[/bold]", id="yes"),
                id="action-menu",
            )
            yield Static(
                "[green]+N[/green] additions  [red]-N[/red] deletions  "
                "\u00b7  esc to cancel",
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


# Validation mirrors what ``git commit`` accepts on the CLI: a single
# non-empty line is the minimum. We allow embedded newlines (subject +
# blank line + body) since git does, but we trim trailing whitespace
# and refuse zero-length messages.
_COMMIT_MESSAGE_RE = re.compile(r"[\s\S]+", re.MULTILINE)


class CommitMessageScreen(ModalScreen[Optional[tuple[str, bool]]]):
    """Collect a commit message and the final action (commit / commit & push).

    Dismisses with a tuple ``(message, push_after)`` on confirm, or
    ``None`` on cancel. ``push_after=True`` means the user picked
    "commit & push" so the caller should run ``git push`` after the
    commit succeeds.

    Two ``OptionList`` entries below the input act as the action
    picker; the first option ("commit") is highlighted by default.
    Pressing ``enter`` from the input field commits with the currently
    highlighted action, matching the project's existing form
    conventions (see ``CreatePanelScreen``).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Esc cancel", show=True),
        Binding("ctrl+enter", "confirm", "Confirm", show=False),
        Binding("tab", "focus_action", "Tab focus action", show=False),
        Binding("shift+tab", "focus_message", "Shift+Tab focus message", show=False),
        Binding("down", "action_cursor_down", "\u2193 action", show=False),
        Binding("up", "action_cursor_up", "\u2191 action", show=False),
    ]

    CSS = (
        "CommitMessageScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS + """
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
            height: 3;
            border: none;
            background: $boost;
            color: $text;
            margin: 0 0 1 0;
            padding: 0 1;
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
                f"[bold white]Commit changes in[/bold white] "
                f"[cyan]{escape(self.repo_name)}[/cyan]",
                id="commit-message-title",
            )
            yield Static(
                f"[green]+{self.additions}[/green]  "
                f"[red]-{self.deletions}[/red]  "
                f"[dim]\u00b7  {self.file_count} {noun}[/dim]",
                id="commit-message-stats",
            )
            yield Input(
                placeholder="Commit message\u2026",
                id="commit-message-input",
            )
            yield OptionList(
                Option("[white]\u2713[/white] [bold]Commit[/bold]", id="commit"),
                Option("[cyan]\u2191[/cyan] [bold]Commit and push[/bold]", id="commit_push"),
                id="commit-message-action-list",
            )
            yield Static(
                "type message    "
                "[\u2191\u2193] pick action    "
                "[enter] confirm    "
                "[esc] cancel",
                id="commit-message-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#commit-message-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._confirm()

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
        self.query_one("#commit-message-input", Input).focus()

    def action_cursor_down(self) -> None:
        self.query_one("#commit-message-action-list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#commit-message-action-list", OptionList).action_cursor_up()

    def action_cursor_down_global(self) -> None:
        # Bound for the ``down`` key while focus is on the Input —
        # moves the action list, not the cursor inside the Input.
        if self._in_message:
            self.action_cursor_down()
        else:
            super().action_cursor_down()  # type: ignore[misc]

    def _message(self) -> str:
        return self.query_one("#commit-message-input", Input).value.strip()

    def _message_valid(self) -> bool:
        msg = self._message()
        return bool(msg) and bool(_COMMIT_MESSAGE_RE.match(msg))

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

    CSS = (
        "CommitLoadingScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS + """
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
        self._status: str = "Staging\u2026" if not push_after else "Staging\u2026"

    def compose(self) -> ComposeResult:
        with Container(id="commit-loading-container"):
            yield Static(
                f"[bold white]{'Commit & push' if self.push_after else 'Commit'}"
                f" \u2014 {escape(self.repo_name)}[/bold white]",
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

    CSS = (
        "CommitResultScreen {"
        " align: center middle; background: $panel 80%; hatch: right $primary 30%;"
        " }" + _MODAL_CSS + """
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
