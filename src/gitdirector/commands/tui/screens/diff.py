"""Full-screen ``Review Diff`` modal.

Layout: a left-hand file list (custom ``FileTileList``) and a right-hand
diff panel (``VerticalScroll`` containing a ``Static`` whose renderable is
produced by ``diff_renderer``). All diff/git work happens in a background
worker so the TUI never blocks on large diffs.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

from rich.cells import cell_len
from rich.color import Color
from rich.markup import escape
from rich.segment import Segment, Segments
from rich.style import Style
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.geometry import Size
from textual.screen import ModalScreen
from textual.scroll_view import ScrollView
from textual.scrollbar import ScrollBarRender
from textual.strip import Strip
from textual.widgets import LoadingIndicator, OptionList, Static

from ..diff_renderer import (
    ChangedFile,
    DiffBundle,
    DiffDocument,
    build_diff_bundle,
    build_diff_document,
    render_empty_state,
    render_error,
    warm_lexers,
)
from .card import key_hints
from .commit import (
    CommitLoadingScreen,
    CommitMessageScreen,
    CommitResultScreen,
    StageFilesConfirmScreen,
)
from .diff_files import FileTileList

logger = logging.getLogger(__name__)

_FOCUS_FILES = "files"
_FOCUS_DIFF = "diff"


class _ThinHorizontalScrollBarRender(ScrollBarRender):
    """Draws the horizontal bar as a half-height line.

    A cell is about twice as tall as it is wide, so a 1-row bar reads twice
    as thick as the 1-column vertical one; a lower half block evens them out.
    """

    GLYPH = "\u2584"

    @classmethod
    def render_bar(
        cls,
        size: int = 25,
        virtual_size: float = 50,
        window_size: float = 20,
        position: float = 0,
        thickness: int = 1,
        vertical: bool = True,
        back_color: Color = Color.parse("#555555"),
        bar_color: Color = Color.parse("bright_magenta"),
    ) -> Segments:
        if vertical:
            return super().render_bar(
                size=size,
                virtual_size=virtual_size,
                window_size=window_size,
                position=position,
                thickness=thickness,
                vertical=vertical,
                back_color=back_color,
                bar_color=bar_color,
            )
        size = int(size)
        start = thumb = 0
        if window_size and size and virtual_size > window_size and size != virtual_size:
            thumb = min(size, max(1, round(window_size * size / virtual_size)))
            start = round((size - thumb) * position / (virtual_size - window_size))
            start = min(max(0, start), size - thumb)
        # No bgcolor: the upper half shows the pane's own background.
        before = Segment(cls.GLYPH, Style(color=back_color, meta={"@mouse.down": "scroll_up"}))
        handle = Segment(cls.GLYPH, Style(color=bar_color, meta={"@mouse.down": "grab"}))
        after = Segment(cls.GLYPH, Style(color=back_color, meta={"@mouse.down": "scroll_down"}))
        line = [before] * start + [handle] * thumb + [after] * (size - start - thumb)
        return Segments((line + [Segment.line()]) * thickness, new_lines=False)


class DiffContentView(ScrollView, can_focus=True):
    """The diff panel: draws only the lines on screen, in both directions.

    A ``Static`` holding the whole diff rendered every line of the file on
    each switch; here a line is rendered the first time it scrolls into
    view, so a file of any size opens at once.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._document: DiffDocument | None = None
        self._strips: dict[tuple[int, Style], Strip] = {}

    def on_mount(self) -> None:
        self.horizontal_scrollbar.renderer = _ThinHorizontalScrollBarRender

    @property
    def document(self) -> DiffDocument | None:
        return self._document

    def set_document(self, document: DiffDocument | None) -> None:
        self._document = document
        self._strips.clear()
        lines = len(document) if document is not None else 0
        width = document.width if document is not None else 0
        self.virtual_size = Size(width, lines)
        self.scroll_to(0, 0, animate=False, force=True)
        self.refresh()

    def _line_strip(self, index: int, base: Style) -> Strip:
        key = (index, base)
        strip = self._strips.get(key)
        if strip is None:
            document = self._document
            assert document is not None
            text = document.line(index)
            console = self.app.console
            # Text.render leaves a span-free line's own style off: apply it here.
            line_style = base + console.get_style(text.style) if text.style else base
            segments = Segment.apply_style(text.render(console, end=""), line_style)
            strip = Strip(segments).extend_cell_length(document.width, line_style)
            self._strips[key] = strip
        return strip

    def render_line(self, y: int) -> Strip:
        base = self.rich_style
        width = self.scrollable_content_region.width
        scroll_x, scroll_y = self.scroll_offset
        index = y + scroll_y
        document = self._document
        if document is None or index >= len(document):
            return Strip.blank(width, base)
        strip = self._line_strip(index, base)
        fill = strip._segments[-1].style if strip._segments else base
        return strip.crop(scroll_x, scroll_x + width).extend_cell_length(width, fill or base)


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
        Binding("right", "cursor_right", "→", show=False),
        Binding("left", "cursor_left", "←", show=False),
        Binding("]", "next_file", "]", show=False),
        Binding("[", "prev_file", "[", show=False),
        Binding("h", "scroll_left", "←", show=False),
        Binding("l", "scroll_right", "→", show=False),
        Binding("g", "commit", "g commit", show=True),
        Binding("r", "refresh", "r refresh", show=True),
    ]

    # Full-bleed, like the console: the top bar's header, hairlines between
    # the parts, and the same line of key hints at the bottom.
    DEFAULT_CSS = """
    DiffReviewScreen {
        background: $surface;
    }
    #diff-container {
        width: 1fr;
        height: 1fr;
        background: $surface;
    }
    #diff-header {
        height: 3;
        padding: 0 2;
        background: $panel;
    }
    #diff-title {
        width: 1fr;
        height: 100%;
        content-align-vertical: middle;
        color: $text;
    }
    #diff-summary {
        width: auto;
        height: 100%;
        content-align-vertical: middle;
        color: $text-muted;
    }
    #diff-body {
        height: 1fr;
    }
    #diff-files-pane {
        width: 48;
        height: 1fr;
        border-right: solid $foreground 10%;
        background: $surface;
    }
    .diff-pane-label {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        text-style: bold;
    }
    .diff-pane-label.--focused {
        color: $accent;
    }
    #diff-files-list {
        width: 1fr;
        height: 1fr;
        background: $surface;
    }
    #diff-content-pane {
        width: 1fr;
        height: 1fr;
    }
    #diff-content-scroll {
        width: 1fr;
        height: 1fr;
        border: none;
        color: #c9d1d9;
        background: #0d1117;
        overflow-x: auto;
        overflow-y: auto;
    }
    #diff-content-scroll:focus {
        border: none;
        background-tint: $foreground 0%;
    }
    #diff-content-scroll.--added {
        background: #0a1f12;
    }
    #diff-content-scroll.--deleted {
        background: #1f0a0d;
    }
    #diff-loading {
        height: 1fr;
        align: center middle;
        background: $surface;
    }
    #diff-loading LoadingIndicator {
        height: 1;
        color: $primary;
    }
    #diff-loading-text {
        text-align: center;
        color: $text-muted;
        padding: 1 0;
    }
    #diff-empty {
        height: 1fr;
        content-align: center middle;
        padding: 0 2;
        color: $text-muted;
    }
    #diff-hint {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 2;
    }
    """

    def __init__(self, repo_name: str, repo_path: Path, branch: str | None = None) -> None:
        super().__init__()
        self.repo_name = repo_name
        self.repo_path = repo_path
        self.branch = branch
        self._files: list[ChangedFile] = []
        self._truncated = False
        self._focus_target: str = _FOCUS_FILES
        self._loading = True
        self._load_failed: str | None = None
        # Bumped per load so a slower, superseded worker cannot apply its
        # result over a newer one: a thread worker cannot be interrupted.
        self._load_generation = 0
        self._diff_renders: dict[tuple[int, int], DiffDocument] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="diff-container"):
            with Horizontal(id="diff-header"):
                yield Static(
                    f"[bold]{escape(self.repo_name)}[/]   [dim]review changes[/dim]",
                    id="diff-title",
                )
                yield Static("", id="diff-summary")
            with Horizontal(id="diff-body"):
                with Vertical(id="diff-files-pane"):
                    yield Static("FILES", id="diff-files-pane-label", classes="diff-pane-label")
                    yield FileTileList(id="diff-files-list")
                with Vertical(id="diff-content-pane"):
                    yield Static("DIFF", id="diff-content-pane-label", classes="diff-pane-label")
                    yield DiffContentView(id="diff-content-scroll")
                    with Vertical(id="diff-loading"):
                        yield LoadingIndicator()
                        yield Static("Loading diff\u2026", id="diff-loading-text")
                    yield Static("", id="diff-empty")
            yield Static(
                key_hints(
                    ("tab", "switch pane"),
                    ("j/k", "line"),
                    ("J/K", "page"),
                    ("h/l", "scroll"),
                    ("\\[ ]", "file"),
                    ("g", "commit"),
                    ("r", "refresh"),
                    ("esc", "close"),
                ),
                id="diff-hint",
            )

    def on_mount(self) -> None:
        self._start_load()

    def _start_load(self) -> None:
        self._load_generation += 1
        self._show_loading()
        self._update_summary()
        self._load_diff(self._load_generation)

    async def _if_current(self, generation: int, callback, *args) -> None:
        if generation == self._load_generation and self.is_attached:
            result = callback(*args)
            if inspect.isawaitable(result):
                await result

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
    def _load_diff(self, generation: int) -> None:
        from ....repo import Repository as _Repo

        def _post(callback, *args) -> None:
            if getattr(self.app, "_shutdown_requested", False):
                return
            try:
                self.app.call_from_thread(self._if_current, generation, callback, *args)
            except Exception:
                logger.debug("call_from_thread failed", exc_info=True)

        try:
            repo = _Repo(self.repo_path)
            ok, diff_text, untracked = repo.get_diff_against_head()
            if not ok:
                _post(self._apply_error, diff_text or "git diff failed")
                return

            def _lookup(rel_path: str) -> str | None:
                try:
                    return repo.read_file_text(rel_path)
                except Exception:
                    return None

            bundle = build_diff_bundle(diff_text, untracked, _lookup)
            warm_lexers(bundle.files)
        except Exception as exc:
            _post(self._apply_error, str(exc))
            return
        _post(self._apply_bundle, bundle)

    def _apply_error(self, message: str) -> None:
        self._load_failed = message
        self._loading = False
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

    async def _apply_bundle(self, bundle: DiffBundle) -> None:
        self._files = list(bundle.files)
        self._truncated = bundle.truncated
        self._diff_renders.clear()
        self._loading = False
        self._load_failed = None

        files_list = self.query_one("#diff-files-list", FileTileList)
        if not self._files:
            await files_list.set_files([])
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

        await files_list.set_files(self._files, repo_dir=str(self.repo_path))
        self._render_selected_file()
        self._show_content()
        self._update_summary()
        self._apply_focus()

    def _update_summary(self) -> None:
        summary = self.query_one("#diff-summary", Static)
        branch = f"   [bold]{escape(self.branch)}[/]" if self.branch else ""
        if self._load_failed:
            summary.update(f"[$text-error]diff failed: {escape(self._load_failed)}[/]")
            return
        if self._loading:
            summary.update(f"loading…{branch}")
            return
        if not self._files:
            summary.update(f"[$text-success]working tree clean[/]{branch}")
            return
        total_add = sum(f.additions for f in self._files)
        total_del = sum(f.deletions for f in self._files)
        count = len(self._files)
        noun = "file" if count == 1 else "files"
        truncated = (
            "   [$text-warning]too large: later files not shown[/]" if self._truncated else ""
        )
        summary.update(
            f"{count} {noun}   [$text-success]+{total_add}[/] [$text-error]-{total_del}[/]"
            f"{branch}{truncated}"
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
            view = self.query_one("#diff-content-scroll", DiffContentView)
            code_width = self._diff_code_width(file)
            key = (index, code_width)
            document = self._diff_renders.get(key)
            if document is None:
                document = build_diff_document(file, code_width=code_width)
                self._diff_renders[key] = document
            view.set_document(document)
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
        except Exception:
            return
        # The file's status alone drives the colour.
        scroll.set_class(file.status in ("A", "?"), "--added")
        scroll.set_class(file.status == "D", "--deleted")

    def _content_width(self) -> int:
        try:
            size = self.app.size
            width = max(40, size.width - 42)
            return min(160, width)
        except Exception:
            return 100

    def _diff_code_width(self, file: ChangedFile) -> int:
        longest_line = 0
        lines = file.diff_text.split("\n") if file.diff_text else [file.display_path]
        for line in lines:
            longest_line = max(longest_line, cell_len(line.expandtabs(4)))
        return max(self._content_width(), longest_line)

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
            self._mark_focus(target)
            if target == _FOCUS_DIFF:
                self.query_one("#diff-content-scroll").focus()
            else:
                self.query_one("#diff-files-list").focus()
        except Exception:
            logger.debug("Failed to apply focus", exc_info=True)

    def _mark_focus(self, target: str) -> None:
        files_pane = self.query_one("#diff-files-pane")
        content_pane = self.query_one("#diff-content-pane")
        for pane in (files_pane, content_pane):
            pane.set_class(target == _FOCUS_FILES, "--files-focused")
            pane.set_class(target == _FOCUS_DIFF, "--diff-focused")
        self.query_one("#diff-files-pane-label").set_class(target == _FOCUS_FILES, "--focused")
        self.query_one("#diff-content-pane-label").set_class(target == _FOCUS_DIFF, "--focused")

    def on_descendant_focus(self, event) -> None:  # type: ignore[no-untyped-def]
        # A mouse click moves focus without going through Tab/Enter.
        widget_id = getattr(event.widget, "id", None)
        if widget_id == "diff-content-scroll":
            target = _FOCUS_DIFF
        elif widget_id == "diff-files-list":
            target = _FOCUS_FILES
        else:
            return
        self._focus_target = target
        try:
            self._mark_focus(target)
        except Exception:
            logger.debug("Failed to mark focus", exc_info=True)

    def on_file_tile_list_file_selected(self, event: FileTileList.FileSelected) -> None:
        self._render_selected_file()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Enter or a click on a file: read its diff.
        if event.control is not self.query_one("#diff-files-list"):
            return
        event.stop()
        self._focus_target = _FOCUS_DIFF
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

    def action_cursor_right(self) -> None:
        if self._focus_target == _FOCUS_DIFF and self._files:
            self.action_scroll_right()
            return
        self.action_next_file()

    def action_cursor_left(self) -> None:
        if self._focus_target == _FOCUS_DIFF and self._files:
            self.action_scroll_left()
            return
        self.action_prev_file()

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
        if self._loading or not self._files or self._load_failed:
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
        # diff is no longer representative, so refresh it so the
        # next view shows the new state of the working tree.
        if commit_ok:
            self._refresh_after_commit()

    def _refresh_after_commit(self) -> None:
        self._start_load()

    def action_refresh(self) -> None:
        self._start_load()

    def action_close(self) -> None:
        self.dismiss(None)


__all__ = ["DiffReviewScreen"]
