"""Modal screens related to repository actions (action menu, git commands, info, pull)."""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import LoadingIndicator, OptionList, Static
from textual.widgets.option_list import Option

from ....info import RepoInfoResult
from ....repo import RepositoryInfo, RepoStatus
from ..constants import _MODAL_BINDINGS
from ..repo_rows import status_text
from ._shared import _render_ansi_output
from .card import (
    LoadingCard,
    ShortcutKeys,
    card_css,
    card_palette,
    card_subtitle,
    heading,
    key_hints,
    menu_row,
    spacer,
)
from .session_actions import SessionActionMenuScreen, session_action_menu_css


class ActionMenuScreen(SessionActionMenuScreen):
    """The launcher for one repository, headed by its branch and status."""

    CSS = session_action_menu_css("ActionMenuScreen")

    def __init__(
        self,
        repo_name: str,
        repo_path: Path,
        branch: str | None = None,
        status: Text | None = None,
    ) -> None:
        super().__init__(repo_name, repo_path)
        self.branch = branch
        self.status = status

    def _meta(self) -> Text:
        return Text(self.branch or "detached", style="bold")

    def _subtitle(self) -> Text:
        return card_subtitle(self.path, self.status)


_GIT_ACTIONS = (
    ("Inspect", "status", "Status", "git status", "s"),
    ("Inspect", "timeline", "Timeline", "git log --graph --all", "t"),
    ("Inspect", "branches", "Branches", "git branch -a", "b"),
    ("Inspect", "remotes", "Remotes", "git remote -v", "r"),
    ("Sync", "pull", "Pull", "git pull --ff-only", "p"),
    ("Sync", "push", "Push", "git push", "P"),
    ("Review", "review_diff", "Review changes", "diff and commit", "d"),
)


def _commits(count: int) -> str:
    return f"{count} commit{'' if count == 1 else 's'}"


class GitOperationsMenuScreen(ShortcutKeys, ModalScreen[str]):
    """Git for one repository: what to inspect, sync and review, with what is pending."""

    BINDINGS = _MODAL_BINDINGS

    CSS = card_css("GitOperationsMenuScreen")

    def __init__(
        self,
        repo_name: str,
        branch: str | None = None,
        info: RepositoryInfo | None = None,
        path: Path | None = None,
    ) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.branch = branch
        self.info = info
        self.path = path
        self._shortcuts = {key: action for _, action, _, _, key in _GIT_ACTIONS}

    def _detail(self, action: str) -> Text:
        """What an action would do right now, when the last status says."""
        info = self.info
        if info is None:
            return Text()
        pending = f"bold {card_palette(self.app).yellow}"
        if action == "pull" and info.behind:
            return Text(f"↓{_commits(info.behind)} to pull", style=pending)
        if action == "push" and info.ahead:
            return Text(f"↑{_commits(info.ahead)} to push", style=pending)
        if action in ("pull", "push") and info.status is RepoStatus.UP_TO_DATE:
            return Text("up to date", style="dim")
        if action == "review_diff":
            staged = len(info.staged_files or ())
            changed = len(info.unstaged_files or ())
            parts = [f"{staged} staged"] if staged else []
            parts += [f"{changed} changed"] if changed else []
            return Text(" · ".join(parts) or "nothing to review", style="dim")
        return Text()

    def _options(self) -> list[Option]:
        items: list[Option] = []
        section = None
        for group, action, label, command, key in _GIT_ACTIONS:
            if group != section:
                if section is not None:
                    items.append(spacer())
                items.append(heading(group))
                section = group
            detail = self._detail(action)
            if not detail.plain:
                detail = Text(command, style="dim")
            items.append(Option(menu_row(Text(label, style="bold"), detail, key), id=action))
        return items

    def compose(self) -> ComposeResult:
        status = status_text(self.info, card_palette(self.app)) if self.info else None
        with Vertical(id="menu-container"):
            with Horizontal(id="menu-header"):
                yield Static(escape(self.repo_name), id="menu-title")
                yield Static(Text(self.branch or "detached", style="bold"), id="menu-meta")
            yield Static(card_subtitle(self.path, status), id="menu-branch")
            yield OptionList(*self._options(), id="action-menu")
            yield Static(
                key_hints(("↑↓", "select"), ("⏎", "run"), ("key", "jump"), ("esc", "close")),
                id="menu-hint",
            )

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


_OUTPUT_HINT = key_hints(("↑↓", "scroll"), ("⏎", "close"), ("esc", "back"))
# Output is for reading: the card takes most of the screen.
_OUTPUT_CSS = """
    .-output-card #menu-container {
        height: 85%;
    }
    #result-output-scroll {
        height: 1fr;
        margin: 1 2 0 2;
        padding: 0 1;
        background: $surface;
    }
    /* A wrapped line would break the graph's columns. */
    #result-output.-graph {
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
"""
_GRAPH_GLYPHS = str.maketrans({"*": "●", "|": "│", "/": "╱", "\\": "╲", "_": "─"})
_GRAPH_CHARS = frozenset("*|/\\_ -.")


def pretty_graph(text: Text) -> Text:
    """``git log --graph``'s ASCII lines drawn with box characters, colours kept.

    Only each line's graph prefix changes; one character stays one
    character, so git's colour spans still line up.
    """
    lines = []
    for line in text.plain.split("\n"):
        width = len(line) - len(line.lstrip("".join(_GRAPH_CHARS)))
        lines.append(line[:width].translate(_GRAPH_GLYPHS) + line[width:])
    pretty = Text("\n".join(lines), style=text.style, no_wrap=True)
    pretty.spans = list(text.spans)
    return pretty


def result_badge(ok: bool, label: str, app) -> Text:
    palette = card_palette(app)
    if ok:
        return Text(f"✓ {label}", style=f"bold {palette.success}")
    return Text(f"✕ {label}", style=f"bold {palette.danger}")


class OutputCard(ModalScreen[str | None]):
    """What a git command printed: the repository, how it went, and the output."""

    BINDINGS = [
        Binding("escape", "back", "Esc back", show=False),
        Binding("enter", "cancel", "Enter close", show=False),
        Binding("down", "scroll_down", "↓", show=False),
        Binding("j", "scroll_down", "↓", show=False),
        Binding("up", "scroll_up", "↑", show=False),
        Binding("k", "scroll_up", "↑", show=False),
    ]

    DEFAULT_CLASSES = "-output-card"
    # Textual scopes a screen's CSS under the concrete class, which would keep
    # these rules off every subclass; the class selector is specific enough.
    SCOPED_CSS = False

    CSS = card_css(".-output-card", width="90%", extra=_OUTPUT_CSS)

    def __init__(
        self,
        repo_name: str,
        command: str | None,
        ok: bool,
        output: str,
        status: str,
        *,
        graph: bool = False,
    ):
        super().__init__()
        self.repo_name = repo_name
        self.command = command
        self.ok = ok
        self.output = output
        self.status = status
        self.graph = graph

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            with Horizontal(id="menu-header"):
                yield Static(escape(self.repo_name), id="menu-title")
                yield Static(result_badge(self.ok, self.status, self.app), id="result-status")
            yield Static(
                Text(f"$ {self.command}" if self.command else "", style="dim"),
                id="menu-branch",
            )
            output = _render_ansi_output(self.output)
            with VerticalScroll(id="result-output-scroll"):
                yield Static(
                    pretty_graph(output) if self.graph else output,
                    id="result-output",
                    classes="-graph" if self.graph else "",
                )
            yield Static(_OUTPUT_HINT, id="menu-hint")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_back(self) -> None:
        self.dismiss("back")

    def action_scroll_down(self) -> None:
        self.query_one("#result-output-scroll", VerticalScroll).action_scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#result-output-scroll", VerticalScroll).action_scroll_up()


class GitCommandResultScreen(OutputCard):
    """The output of a read-only git command (status, log, branches, remotes)."""

    def __init__(
        self,
        repo_name: str,
        command: str | None,
        ok: bool,
        output: str,
        *,
        success_text: str = "Command completed",
        failure_text: str = "Command failed",
        graph: bool = False,
    ) -> None:
        output = output.strip() or ("No output." if not ok else "Command completed.")
        status = success_text if ok else failure_text
        super().__init__(repo_name, command, ok, output, status, graph=graph and ok)


class PullResultScreen(OutputCard):
    """How a pull or push went."""

    def __init__(
        self,
        repo_name: str,
        command: str | None,
        ok: bool,
        output: str,
        *,
        operation: str = "Pull",
        empty_success: str = "Already up to date.",
    ) -> None:
        output = output.strip() or (empty_success if ok else "No output.")
        status = f"{operation} completed" if ok else f"{operation} failed"
        super().__init__(repo_name, command, ok, output, status)
        self.operation = operation


class PullLoadingScreen(LoadingCard):
    """Shown while a pull or push runs."""

    def __init__(self, repo_name: str, command: str, *, verb: str = "Pulling") -> None:
        super().__init__(
            f"{escape(verb)} [bold]{escape(repo_name)}[/bold]",
            f"[dim]$ {escape(command)}[/dim]",
        )
        self.repo_name = repo_name
        self.command = command
        self.verb = verb


_BAR_WIDTH = 18
_MAX_FILE_TYPES = 8


def _facts_grid(facts: list[tuple[str, Text]]) -> Table:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column(overflow="fold")
    for label, value in facts:
        grid.add_row(label, value)
    return grid


def _language_grid(result: RepoInfoResult, accent: str) -> Table:
    """One line per file type, with a bar for its share of the lines (or files)."""
    by_lines = result.total_lines > 0
    total = result.total_lines if by_lines else result.total_files
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True)
    for column in range(3):
        grid.add_column(justify="right", no_wrap=True)
    shown = result.file_types[:_MAX_FILE_TYPES]
    for ft in shown:
        amount = (ft.line_count or 0) if by_lines else ft.count
        filled = round(_BAR_WIDTH * amount / total) if total else 0
        bar = Text("█" * filled, style=accent)
        bar.append("░" * (_BAR_WIDTH - filled), style="dim")
        grid.add_row(
            Text(ft.extension, style="bold"),
            bar,
            Text(f"{ft.count:,} files", style="dim"),
            Text(f"{ft.line_count:,} lines" if ft.line_count is not None else "", style="dim"),
            Text(f"{ft.token_count:,} tok" if ft.token_count is not None else "", style="dim"),
        )
    rest = len(result.file_types) - len(shown)
    if rest > 0:
        grid.add_row(Text(f"+{rest} more", style="dim"), "", "", "", "")
    return grid


class RepoInfoScreen(ModalScreen[None]):
    """A repository (or group) at a glance: its git state, then its code by language."""

    BINDINGS = [_MODAL_BINDINGS[0]]

    CSS = card_css(
        "RepoInfoScreen",
        width=84,
        extra="""
    RepoInfoScreen .info-section {
        height: auto;
        padding: 1 2 0 2;
    }
    RepoInfoScreen .info-heading {
        height: 1;
        padding: 1 2 0 2;
        color: $text-muted;
        text-style: bold;
    }
    RepoInfoScreen #info-loading {
        height: 3;
        padding: 1 0;
    }
    RepoInfoScreen #menu-hint {
        margin: 1 0 1 0;
    }
    """,
    )

    def __init__(
        self,
        repo_name: str,
        repo_path: Path,
        facts: list[tuple[str, Text]] | None = None,
        meta: Text | None = None,
    ) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path
        self.facts = facts or []
        self.meta = meta or Text()

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-container"):
            with Horizontal(id="menu-header"):
                yield Static(escape(self.repo_name), id="menu-title")
                yield Static(self.meta, id="menu-meta")
            yield Static(card_subtitle(self.repo_path), id="menu-branch")
            if self.facts:
                yield Static(_facts_grid(self.facts), id="info-facts", classes="info-section")
            yield Static("CODE", classes="info-heading")
            yield LoadingIndicator(id="info-loading")
            yield Static(key_hints(("esc", "close")), id="menu-hint")

    def populate(self, result: RepoInfoResult) -> None:
        # The worker finishes whenever it finishes; the user may have
        # closed the screen by then.
        if not self.is_attached:
            return
        self.query_one("#info-loading", LoadingIndicator).remove()
        r = result
        stats = Text()
        for index, (value, label) in enumerate(
            (
                (r.total_files, "files"),
                (r.total_lines, "lines"),
                (r.total_tokens, "tokens"),
                (r.max_depth, "levels deep"),
            )
        ):
            if index:
                stats.append("   ")
            stats.append(f"{value:,}", style="bold")
            stats.append(f" {label}", style="dim")
        hint = self.query_one("#menu-hint", Static)
        hint.mount(Static(stats, id="info-stats", classes="info-section"), before=hint)
        if r.file_types:
            accent = card_palette(self.app).primary
            hint.mount(
                Static(_language_grid(r, accent), id="info-table", classes="info-section"),
                before=hint,
            )

    def show_error(self, message: str) -> None:
        if not self.is_attached:
            return
        loading = self.query("#info-loading")
        if loading:
            loading.first().remove()
        hint = self.query_one("#menu-hint", Static)
        hint.mount(
            Static(f"[$text-error]{escape(message)}[/]", id="info-error", classes="info-section"),
            before=hint,
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = [
    "ActionMenuScreen",
    "LoadingCard",
    "OutputCard",
    "GitCommandResultScreen",
    "GitOperationsMenuScreen",
    "PullLoadingScreen",
    "PullResultScreen",
    "RepoInfoScreen",
]
