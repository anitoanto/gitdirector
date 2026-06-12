"""Full-screen ``Review Diff`` modal.

Layout: a left-hand file list (custom ``FileTileList``) and a right-hand
diff panel (``VerticalScroll`` containing a ``Static`` whose renderable is
produced by ``diff_renderer``). All diff/git work happens in a background
worker so the TUI never blocks on large diffs.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import ListItem, LoadingIndicator, Static

from ..diff_renderer import (
    ChangedFile,
    DiffBundle,
    build_diff_bundle,
    render_empty_state,
    render_error,
    render_file_diff,
)
from .commit import (
    CommitLoadingScreen,
    CommitMessageScreen,
    CommitResultScreen,
    StageFilesConfirmScreen,
)
from .diff_files import FileTile, FileTileList

logger = logging.getLogger(__name__)

_FOCUS_FILES = "files"
_FOCUS_DIFF = "diff"


class _DiffContentScroll(Vertical, can_focus=True):
    """Bidirectional scroll container for the diff panel.

    ``VerticalScroll`` only scrolls vertically, so long diff lines
    (``word_wrap=False``) get clipped on the right. This container
    scrolls in both directions so the user can pan to see overflow.
    """


class DiffReviewScreen(ModalScreen[None]):
    """Two-pane modal showing the uncommitted diff for a single repository."""

    BINDINGS = [
        Binding("escape", "close", "Esc close", show=True),
        Binding("q", "close", "q close", show=False),
        Binding("tab", "switch_focus", "Tab switch panel", show=True),
        Binding("shift+tab", "switch_focus_back", "Shift+Tab switch panel", show=False),
        Binding("j", "cursor_down", "↓", show=False),
        Binding("k", "cursor_up", "↑", show=False),
        Binding("J", "cursor_page_down", "Shift+↓ page", show=False),
        Binding("K", "cursor_page_up", "Shift+↑ page", show=False),
        Binding("down", "cursor_down", "↓", show=False),
        Binding("up", "cursor_up", "↑", show=False),
        Binding("shift+down", "cursor_page_down", "Shift+↓ page", show=False),
        Binding("shift+up", "cursor_page_up", "Shift+↑ page", show=False),
        Binding("page_down", "cursor_page_down", "PgDn", show=False),
        Binding("page_up", "cursor_page_up", "PgUp", show=False),
        Binding("n", "next_file", "next file", show=False),
        Binding("p", "prev_file", "prev file", show=False),
        Binding("right", "next_file", "next", show=False),
        Binding("left", "prev_file", "prev", show=False),
        Binding("]", "next_file", "]", show=False),
        Binding("[", "prev_file", "[", show=False),
        Binding("h", "scroll_left", "←", show=False),
        Binding("l", "scroll_right", "→", show=False),
        Binding("g", "commit", "g commit", show=True),
        Binding("r", "refresh", "r refresh", show=True),
    ]

    DEFAULT_CSS = """
    DiffReviewScreen {
        align: center middle;
        background: $panel 80%;
    }
    #diff-container {
        width: 98%;
        height: 96%;
        border: round $primary;
        background: $panel;
        padding: 0 0;
    }
    #diff-header {
        height: 2;
        padding: 0 1;
        background: $boost;
        border-bottom: solid $primary;
    }
    #diff-title {
        text-align: center;
        color: $text;
        text-style: bold;
    }
    #diff-summary {
        text-align: center;
        color: $text-muted;
        height: 1;
    }
    #diff-body {
        height: 1fr;
        padding: 0 0;
    }
    #diff-files-pane {
        width: 52;
        height: 1fr;
        border-right: solid $primary;
        padding: 0 0;
        background: $surface;
    }
    #diff-files-pane.--files-focused {
        border-right: solid $accent;
    }
    #diff-files-pane.--diff-focused {
        border-right: solid $secondary;
    }
    #diff-content-pane.--files-focused {
        border-left: solid $accent;
    }
    #diff-content-pane.--diff-focused {
        border-left: solid $secondary;
    }
    #diff-files-list {
        width: 1fr;
        height: 1fr;
        padding: 0 0;
        background: $surface;
    }
    #diff-files-list > .option-list--option-highlighted {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    #diff-files-list > .option-list--option {
        padding: 0 1;
    }
    #diff-content-pane {
        width: 1fr;
        height: 1fr;
        padding: 0 0;
    }
    #diff-content-pane.--files-focused {
        border-left: solid $accent;
    }
    #diff-content-pane.--diff-focused {
        border-left: solid $secondary;
    }
    #diff-content-scroll {
        width: 1fr;
        height: 1fr;
        border: none;
        background: #0d1117;
        padding: 0 0;
        overflow-x: auto;
        overflow-y: auto;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
    }
    #diff-content {
        width: auto;
        height: auto;
        color: #c9d1d9;
        background: #0d1117;
        padding: 0 0;
    }
    #diff-content-scroll.--added,
    #diff-content.--added {
        background: #0a1f12;
    }
    #diff-content-scroll.--deleted,
    #diff-content.--deleted {
        background: #1f0a0d;
    }
    #diff-loading {
        height: 1fr;
        align: center middle;
        background: $surface;
    }
    #diff-loading LoadingIndicator {
        height: 3;
        color: $primary;
    }
    #diff-loading-text {
        text-align: center;
        color: $text-muted;
        padding: 1 0;
    }
    #diff-empty {
        height: 1fr;
        align: center middle;
        content-align: center middle;
        padding: 0 2;
        color: $text;
    }
    #diff-hint {
        dock: bottom;
        height: 1;
        background: $boost;
        color: $text-muted;
        padding: 0 1;
        text-align: center;
    }
    """

    def __init__(self, repo_name: str, repo_path: Path, branch: str | None = None) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path
        self.branch = branch
        self._bundle: DiffBundle | None = None
        self._files: list[ChangedFile] = []
        self._focus_target: str = _FOCUS_FILES
        self._loading = True
        self._load_failed: str | None = None
        self._worker = None

    def compose(self) -> ComposeResult:
        with Vertical(id="diff-container"):
            with Vertical(id="diff-header"):
                yield Static(
                    f"[bold white]{escape(self.repo_name)}[/bold white]"
                    f"  [dim]\u2014  Review Diff[/dim]",
                    id="diff-title",
                )
                yield Static("", id="diff-summary")
            with Horizontal(id="diff-body"):
                with Vertical(id="diff-files-pane"):
                    yield FileTileList(id="diff-files-list")
                with Vertical(id="diff-content-pane"):
                    with _DiffContentScroll(id="diff-content-scroll"):
                        yield Static(id="diff-content")
                    with Vertical(id="diff-loading"):
                        yield LoadingIndicator()
                        yield Static("Loading diff\u2026", id="diff-loading-text")
                    yield Static("", id="diff-empty")
            yield Static(
                "[bold]tab[/bold] switch panel    "
                "[bold]j/k[/bold] scroll line    "
                "[bold]J/K[/bold] scroll page    "
                "[bold]h/l[/bold] scroll right/left    "
                "[bold]n/p[/bold] next/prev file    "
                "[bold]g[/bold] commit    "
                "[bold]r[/bold] refresh    "
                "[bold]esc[/bold] close",
                id="diff-hint",
            )

    def on_mount(self) -> None:
        self._show_loading()
        self._worker = self._load_diff()

    def _show_loading(self) -> None:
        self._loading = True
        try:
            self.query_one("#diff-loading").display = True
        except Exception:
            pass
        try:
            self.query_one("#diff-content-scroll").display = False
        except Exception:
            pass
        try:
            self.query_one("#diff-empty").display = False
        except Exception:
            pass

    def _show_content(self) -> None:
        self._loading = False
        try:
            self.query_one("#diff-loading").display = False
        except Exception:
            pass
        try:
            self.query_one("#diff-content-scroll").display = True
        except Exception:
            pass

    @work(thread=True, exclusive=True)
    def _load_diff(self) -> None:
        from ....repo import Repository as _Repo

        def _shutdown() -> bool:
            app = getattr(self, "app", None)
            if app is None:
                return True
            return getattr(app, "_shutdown_requested", False)

        def _post(callback, *args) -> None:
            if _shutdown():
                return
            try:
                self.app.call_from_thread(callback, *args)
            except Exception:
                logger.debug("call_from_thread failed", exc_info=True)

        try:
            repo = _Repo(self.repo_path)
        except Exception as exc:
            _post(self._apply_error, str(exc))
            return

        try:
            ok, diff_text, untracked = repo.get_diff_against_head()
        except Exception as exc:
            _post(self._apply_error, str(exc))
            return

        if not ok:
            _post(self._apply_error, diff_text or "git diff failed")
            return

        def _lookup(rel_path: str) -> str | None:
            try:
                return repo.read_file_text(rel_path)
            except Exception:
                return None

        try:
            bundle = build_diff_bundle(diff_text, untracked, _lookup)
        except Exception as exc:
            _post(self._apply_error, str(exc))
            return

        _post(self._apply_bundle, bundle)

    def _apply_error(self, message: str) -> None:
        self._load_failed = message
        self._show_loading()
        try:
            self.query_one("#diff-loading").display = False
        except Exception:
            pass
        try:
            self.query_one("#diff-content-scroll").display = False
        except Exception:
            pass
        try:
            empty = self.query_one("#diff-empty", Static)
            empty.update(render_error(message))
            empty.display = True
        except Exception:
            pass
        self._update_summary()

    def _apply_bundle(self, bundle: DiffBundle) -> None:
        self._bundle = bundle
        self._files = list(bundle.files)
        self._loading = False
        self._load_failed = None

        files_list = self.query_one("#diff-files-list", FileTileList)
        if not self._files:
            files_list.set_files([])
            try:
                self.query_one("#diff-loading").display = False
            except Exception:
                pass
            try:
                self.query_one("#diff-content-scroll").display = False
            except Exception:
                pass
            empty = self.query_one("#diff-empty", Static)
            empty.update(render_empty_state(self.repo_name, self.branch))
            empty.display = True
            self._update_summary()
            return

        files_list.set_files(self._files, repo_dir=str(self.repo_path))
        self._show_content()
        self._update_summary()
        self._apply_focus()

    def _update_summary(self) -> None:
        summary = self.query_one("#diff-summary", Static)
        if self._load_failed:
            summary.update(
                f"[red]diff failed[/red]  [dim]\u2014  {escape(self._load_failed)}[/dim]"
            )
            return
        if self._loading:
            summary.update("[dim]loading uncommitted changes\u2026[/dim]")
            return
        if not self._files:
            branch_part = (
                f"  [dim]branch:[/dim] [cyan]{escape(self.branch)}[/cyan]" if self.branch else ""
            )
            summary.update(f"[green]working tree clean[/green]{branch_part}")
            return
        total_add = sum(f.additions for f in self._files)
        total_del = sum(f.deletions for f in self._files)
        count = len(self._files)
        noun = "file" if count == 1 else "files"
        branch_part = (
            f"  [dim]branch:[/dim] [cyan]{escape(self.branch)}[/cyan]" if self.branch else ""
        )
        summary.update(
            f"[bold white]{count} {noun}[/bold white]"
            f"  [green]+{total_add}[/green]  [red]-{total_del}[/red]"
            f"{branch_part}"
        )

    def _render_selected_file(self) -> None:
        if not self._files:
            return
        index = self._current_file_index()
        if index is None:
            # No selection yet (e.g. a caller invoked us before the
            # list's deferred initial selection has run). Fall back to
            # the first file so the panel is never blank when we have
            # content to show.
            index = 0
        file = self._files[index]
        try:
            content = self.query_one("#diff-content", Static)
            content.update(render_file_diff(file, width=self._content_width()))
            # ``render_file_diff`` returns a Rich ``Group`` renderable,
            # which does not report a natural width to Textual's
            # measurement. Without an explicit width here, the Static
            # collapses to the scroll container's width and long lines
            # (``word_wrap=False``) get clipped instead of becoming
            # horizontally scrollable. Set the width to the renderable's
            # actual width so the container can scroll the overflow.
            content.styles.width = self._content_width() + 6
            self._apply_content_tone(file)
        except Exception:
            logger.debug("Failed to render diff content", exc_info=True)

    def _apply_content_tone(self, file: ChangedFile) -> None:
        """Tint the right-side content area the same family as the
        file's status. Without this, new/deleted files show their
        actual diff lines in green/red but the rest of the panel stays
        the base dark grey, which makes the whole right side read as
        a near-grey strip with floating coloured chips.
        """
        try:
            scroll = self.query_one("#diff-content-scroll")
            content = self.query_one("#diff-content")
        except Exception:
            return
        # Reset the previous-file classes first so the new file's
        # status is the only thing that drives the colour.
        scroll.set_class(False, "--added")
        scroll.set_class(False, "--deleted")
        content.set_class(False, "--added")
        content.set_class(False, "--deleted")
        if file.status in ("A", "?"):
            scroll.set_class(True, "--added")
            content.set_class(True, "--added")
        elif file.status == "D":
            scroll.set_class(True, "--deleted")
            content.set_class(True, "--deleted")

    def _content_width(self) -> int:
        try:
            size = self.app.size
            width = max(40, size.width - 42)
            return min(160, width)
        except Exception:
            return 100

    def _current_file_index(self) -> int | None:
        try:
            files_list = self.query_one("#diff-files-list", FileTileList)
            index = files_list.index
        except Exception:
            return None
        if index is None or not self._files:
            return None
        return max(0, min(index, len(self._files) - 1))

    def _apply_focus(self) -> None:
        target = self._focus_target
        if not self._files:
            target = _FOCUS_FILES
        try:
            # Toggle the focus-class on the two panes so the
            # divider border can shift colour to signal which side
            # has focus. We touch *both* panes on every focus
            # change so the stale classes from the previous focus
            # don't linger.
            files_pane = self.query_one("#diff-files-pane")
            content_pane = self.query_one("#diff-content-pane")
            files_pane.set_class(False, "--files-focused")
            files_pane.set_class(False, "--diff-focused")
            content_pane.set_class(False, "--files-focused")
            content_pane.set_class(False, "--diff-focused")
            if target == _FOCUS_DIFF:
                content_pane.set_class(True, "--diff-focused")
                files_pane.set_class(True, "--diff-focused")
                self.query_one("#diff-content-scroll").focus()
            else:
                files_pane.set_class(True, "--files-focused")
                content_pane.set_class(True, "--files-focused")
                self.query_one("#diff-files-list").focus()
        except Exception:
            logger.debug("Failed to apply focus", exc_info=True)

    def on_list_view_highlighted(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.control is not self.query_one("#diff-files-list"):
            return
        self._render_selected_file()

    def on_file_tile_list_file_selected(self, event: FileTileList.FileSelected) -> None:
        self._render_selected_file()

    def on_list_view_selected(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.control is not self.query_one("#diff-files-list"):
            return
        self._focus_target = _FOCUS_DIFF
        self._apply_focus()

    def on_file_tile_clicked(self, event) -> None:  # type: ignore[no-untyped-def]
        # Clicking a tile moves focus to the file list so the keyboard
        # navigation continues to work from the clicked row.
        try:
            files_list = self.query_one("#diff-files-list", FileTileList)
        except Exception:
            return
        for i, child in enumerate(files_list.children):
            if isinstance(child, ListItem) and child.query_one(FileTile) is event.tile:
                files_list.index = i
                break
        self._focus_target = _FOCUS_FILES
        self._apply_focus()

    def action_switch_focus(self) -> None:
        if not self._files:
            return
        self._focus_target = _FOCUS_DIFF if self._focus_target == _FOCUS_FILES else _FOCUS_FILES
        self._apply_focus()

    def action_switch_focus_back(self) -> None:
        if not self._files:
            return
        self._focus_target = _FOCUS_FILES if self._focus_target == _FOCUS_DIFF else _FOCUS_DIFF
        self._apply_focus()

    def _move_file(self, delta: int) -> None:
        if not self._files:
            return
        files_list = self.query_one("#diff-files-list", FileTileList)
        if files_list.index is None:
            files_list.index = 0
            return
        new_index = max(0, min(len(self._files) - 1, files_list.index + delta))
        if new_index == files_list.index:
            return
        files_list.index = new_index
        if self._focus_target != _FOCUS_FILES:
            self._focus_target = _FOCUS_FILES
            self._apply_focus()

    def action_cursor_down(self) -> None:
        if self._focus_target == _FOCUS_FILES or not self._files:
            self._move_file(1)
            return
        try:
            self.query_one("#diff-content-scroll").action_scroll_down()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        if self._focus_target == _FOCUS_FILES or not self._files:
            self._move_file(-1)
            return
        try:
            self.query_one("#diff-content-scroll").action_scroll_up()
        except Exception:
            pass

    def action_cursor_page_down(self) -> None:
        if self._focus_target == _FOCUS_FILES or not self._files:
            self._move_file(1)
            return
        try:
            self.query_one("#diff-content-scroll").scroll_page_down(animate=False)
        except Exception:
            pass

    def action_cursor_page_up(self) -> None:
        if self._focus_target == _FOCUS_FILES or not self._files:
            self._move_file(-1)
            return
        try:
            self.query_one("#diff-content-scroll").scroll_page_up(animate=False)
        except Exception:
            pass

    def action_next_file(self) -> None:
        self._move_file(1)

    def action_prev_file(self) -> None:
        self._move_file(-1)

    def action_scroll_left(self) -> None:
        try:
            scroll = self.query_one("#diff-content-scroll")
        except Exception:
            return
        step = max(1, self.app.size.width // 8)
        scroll.scroll_to(scroll.scroll_x - step, None, animate=False)

    def action_scroll_right(self) -> None:
        try:
            scroll = self.query_one("#diff-content-scroll")
        except Exception:
            return
        step = max(1, self.app.size.width // 8)
        scroll.scroll_to(scroll.scroll_x + step, None, animate=False)

    def action_commit(self) -> None:
        """Stage all changes, then ask for a commit message + action.

        The flow is:
            g -> StageFilesConfirmScreen (add -A?) ->
                  CommitMessageScreen (message + commit|commit&push) ->
                  CommitLoadingScreen (worker) ->
                  CommitResultScreen (success/failure).
        """
        if not self._files or self._load_failed:
            return
        additions = sum(f.additions for f in self._files)
        deletions = sum(f.deletions for f in self._files)
        self.app.push_screen(
            StageFilesConfirmScreen(
                self.repo_name,
                additions=additions,
                deletions=deletions,
                file_count=len(self._files),
            ),
            callback=self._on_stage_confirm,
        )

    def _on_stage_confirm(self, stage: bool | None) -> None:
        if not stage:
            return
        additions = sum(f.additions for f in self._files)
        deletions = sum(f.deletions for f in self._files)
        self.app.push_screen(
            CommitMessageScreen(
                self.repo_name,
                additions=additions,
                deletions=deletions,
                file_count=len(self._files),
            ),
            callback=self._on_commit_message,
        )

    def _on_commit_message(self, payload: tuple[str, bool] | None) -> None:
        if payload is None:
            return
        message, push_after = payload
        loading = CommitLoadingScreen(self.repo_name, push_after=push_after)
        self.app.push_screen(loading)
        self._commit_worker(self.repo_path, message, push_after, loading)

    @work(thread=True, exclusive=True, group="commit")
    def _commit_worker(
        self,
        repo_path: Path,
        message: str,
        push_after: bool,
        loading: CommitLoadingScreen,
    ) -> None:
        from ....repo import Repository as _Repo

        def _shutdown() -> bool:
            app = getattr(self, "app", None)
            if app is None:
                return True
            return getattr(app, "_shutdown_requested", False)

        def _post(callback, *args) -> None:
            if _shutdown():
                return
            try:
                self.app.call_from_thread(callback, *args)
            except Exception:
                logger.debug("call_from_thread failed", exc_info=True)

        commit_ok = False
        commit_message = message
        push_ok: bool | None = None
        output_lines: list[str] = []

        try:
            repo = _Repo(repo_path)
        except Exception as exc:
            _post(self._show_commit_result, False, commit_message, None, str(exc))
            return

        try:
            _post(loading.set_status, "Staging changes\u2026")
            ok, out = repo.add()
            if not ok:
                _post(
                    self._show_commit_result, False, commit_message, None, f"git add failed: {out}"
                )
                return
            if out:
                output_lines.append(out)

            _post(loading.set_status, "Creating commit\u2026")
            commit_ok, commit_out = repo.commit(message)
            if not commit_ok:
                _post(
                    self._show_commit_result,
                    False,
                    commit_message,
                    None,
                    f"git commit failed: {commit_out}",
                )
                return
            if commit_out:
                output_lines.append(commit_out)

            if push_after:
                _post(loading.set_status, "Pushing to origin\u2026")
                # First push on a fresh clone usually needs
                # ``-u origin <branch>`` to set the upstream. Try
                # a plain push first; if it fails because there's
                # no upstream, fall back to the set-upstream form.
                push_ok, push_out = repo.push()
                push_err_lower = (push_out or "").lower()
                no_upstream = (
                    "no upstream" in push_err_lower
                    or "set up a tracking branch" in push_err_lower
                    or "has no upstream" in push_err_lower
                )
                if not push_ok and no_upstream:
                    push_ok, push_out = repo.push(set_upstream=True)
                if not push_ok:
                    _post(
                        self._show_commit_result,
                        True,
                        commit_message,
                        False,
                        f"commit succeeded, but push failed: {push_out}",
                    )
                    return
                if push_out:
                    output_lines.append(push_out)
                push_ok = True
        except Exception as exc:
            logger.exception("commit worker crashed")
            _post(self._show_commit_result, commit_ok, commit_message, push_ok, str(exc))
            return

        _post(
            self._show_commit_result,
            True,
            commit_message,
            push_ok,
            "\n".join(line for line in output_lines if line).strip() or "Done.",
        )

    def _show_commit_result(
        self,
        commit_ok: bool,
        commit_message: str,
        push_ok: bool | None,
        output: str,
    ) -> None:
        try:
            active = self.app.screen
        except Exception:
            active = None
        # Dismiss the loading screen (whichever instance is on top)
        # before pushing the result modal. ``_commit_worker`` is
        # ``exclusive=True`` so there is only ever one in flight.
        try:
            if isinstance(active, CommitLoadingScreen):
                active.dismiss(None)
        except Exception:
            logger.debug("Failed to dismiss loading screen", exc_info=True)

        self.app.push_screen(
            CommitResultScreen(
                self.repo_name,
                commit_ok,
                commit_message,
                push_ok,
                output,
            )
        )
        # After a successful commit (with or without push) the
        # diff is no longer representative — refresh it so the
        # next view shows the new state of the working tree.
        if commit_ok:
            self._refresh_after_commit()

    def _refresh_after_commit(self) -> None:
        if self._worker is not None:
            try:
                self._worker.cancel()
            except Exception:
                pass
        self._show_loading()
        self._update_summary()
        self._worker = self._load_diff()

    def action_refresh(self) -> None:
        if self._worker is not None:
            try:
                self._worker.cancel()
            except Exception:
                pass
        self._show_loading()
        self._update_summary()
        self._worker = self._load_diff()

    def action_close(self) -> None:
        if self._worker is not None:
            try:
                self._worker.cancel()
            except Exception:
                pass
        self.dismiss(None)


__all__ = ["DiffReviewScreen"]
