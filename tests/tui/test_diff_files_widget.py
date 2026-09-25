"""Tests for the FileTile and FileTileList widgets.

Covers the tile layout (status letter + filename + right-aligned stats, the
folder under it), the selection tint, and the "diff visualization updates on
selection" bug fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Static

from gitdirector.commands.tui import (
    DiffReviewScreen,
    GitDirectorConsole,
)
from gitdirector.commands.tui.diff_renderer import ChangedFile
from gitdirector.commands.tui.screens.diff_files import (
    FileTile,
    FileTileList,
    _FileTileSpec,
)

from .conftest import _mock_manager, _wait_for_deferred_scroll

# ---------------------------------------------------------------------------
# _FileTileSpec
# ---------------------------------------------------------------------------


class TestFileTileSpec:
    def test_filename_just_basename(self):
        f = ChangedFile(path="src/foo/bar.py", status="M")
        spec = _FileTileSpec(f, "/tmp/repo")
        assert spec.filename() == "bar.py"

    def test_filename_root_level(self):
        f = ChangedFile(path="Makefile", status="M")
        spec = _FileTileSpec(f, "/tmp/repo")
        assert spec.filename() == "Makefile"

    def test_filename_rename_keeps_arrow(self):
        f = ChangedFile(path="new.py", status="R", is_rename=True, old_path="old.py")
        spec = _FileTileSpec(f, "/tmp/repo")
        assert spec.filename() == "old.py \u2192 new.py"

    def test_subtitle_is_the_folder_inside_the_repo(self):
        f = ChangedFile(path="src/foo/bar.py", status="M")
        assert _FileTileSpec(f, "/tmp/repo").subtitle() == "src/foo/"

    def test_subtitle_at_the_repo_root(self):
        f = ChangedFile(path="Makefile", status="M")
        assert _FileTileSpec(f, "/tmp/repo").subtitle() == "./"

    def test_icon_letter_known(self):
        for status, letter in [("A", "A"), ("M", "M"), ("D", "D"), ("R", "R"), ("?", "U")]:
            assert _FileTileSpec(ChangedFile(path="x", status=status), "").icon_letter() == letter

    def test_icon_letter_unknown_status(self):
        spec = _FileTileSpec(ChangedFile(path="x", status="Z"), "")
        assert spec.icon_letter() == "Z"


# ---------------------------------------------------------------------------
# FileTile widget (Textual app)
# ---------------------------------------------------------------------------


SAMPLE_DIFF = (
    "diff --git a/src/foo.py b/src/foo.py\n"
    "index 1234..5678 100644\n"
    "--- a/src/foo.py\n"
    "+++ b/src/foo.py\n"
    "@@ -1,3 +1,3 @@\n"
    " def hello():\n"
    '-    return "old"\n'
    '+    return "new"\n'
    "diff --git a/src/bar.py b/src/bar.py\n"
    "new file mode 100644\n"
    "index 0000000..1234567\n"
    "--- /dev/null\n"
    "+++ b/src/bar.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+first line\n"
    "+second line\n"
)


@pytest.fixture(autouse=True)
def _patch_repo_init(mocker):
    from gitdirector import repo as repo_mod

    def fake_init(self, path):
        self.path = path
        self.name = path.name

    def fake_diff(self, **_kwargs):
        return True, "", []

    def fake_untracked(self):
        return []

    def fake_read(self, rel_path, **_kwargs):
        return None

    mocker.patch.object(repo_mod.Repository, "__init__", fake_init)
    mocker.patch.object(repo_mod.Repository, "get_diff_against_head", fake_diff)
    mocker.patch.object(repo_mod.Repository, "_list_untracked_files", fake_untracked)
    mocker.patch.object(repo_mod.Repository, "read_file_text", fake_read)
    yield


def _patch_repo_methods(mocker, *, diff_text="", untracked=None):
    from gitdirector import repo as repo_mod

    untracked = untracked or []

    def fake_init(self, path):
        self.path = path
        self.name = path.name

    def fake_diff(self, **_kwargs):
        return True, diff_text, list(untracked)

    def fake_untracked(self):
        return list(untracked)

    def fake_read(self, rel_path, **_kwargs):
        return f"contents of {rel_path}\n"

    mocker.patch.object(repo_mod.Repository, "__init__", fake_init)
    mocker.patch.object(repo_mod.Repository, "get_diff_against_head", fake_diff)
    mocker.patch.object(repo_mod.Repository, "_list_untracked_files", fake_untracked)
    mocker.patch.object(repo_mod.Repository, "read_file_text", fake_read)


class TestFileTileLayout:
    async def test_tile_has_icon_title_subtitle_stats(self, mocker):
        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            tile = files_list.children[0].query_one(FileTile)
            assert tile.query_one(".tile-icon") is not None
            assert tile.query_one(".tile-title") is not None
            assert tile.query_one(".tile-subtitle") is not None
            assert tile.query_one(".tile-stats") is not None

    async def test_title_shows_filename_only(self, mocker):
        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            tile = files_list.children[0].query_one(FileTile)
            title = tile.query_one(".tile-title", Static)
            # Title is just the basename, not the full path.
            assert "foo.py" in str(title.render())
            assert "src/" not in str(title.render())

    async def test_subtitle_shows_full_path(self, mocker):
        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            tile = files_list.children[0].query_one(FileTile)
            subtitle = tile.query_one(".tile-subtitle", Static)
            assert str(subtitle.render()) == "src/"

    async def test_stats_render_in_title_row(self, mocker):
        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            tile = files_list.children[0].query_one(FileTile)
            stats = tile.query_one(".tile-stats", Static)
            text = str(stats.render())
            assert "+1" in text
            assert "-1" in text

    async def test_icon_is_the_status_letter_in_its_colour(self, mocker):
        from gitdirector.commands.tui.diff_renderer import STATUS_TEXT

        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            icons = [c.query_one(".tile-icon", Static).content for c in files_list.children]
            # SAMPLE_DIFF has one modified file and one new file; no pill backgrounds.
            assert sorted(icon.plain for icon in icons) == ["A", "M"]
            for icon in icons:
                assert STATUS_TEXT[icon.plain] in str(icon.style)
                assert " on " not in str(icon.style)


class TestFileTileSelection:
    async def test_only_the_selected_tile_is_tinted(self, mocker):
        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.focus()
            await pilot.pause()
            first, second = (c.query_one(FileTile) for c in files_list.children)
            assert first.selected and first.has_class("--selected")
            assert not second.selected and not second.has_class("--selected")
            assert first.styles.background != second.styles.background


# ---------------------------------------------------------------------------
# The "right-side diff doesn't update when file is selected" bug.
# ---------------------------------------------------------------------------


class TestDiffUpdatesOnSelection:
    async def test_content_updates_on_j_press(self, mocker):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, SAMPLE_DIFF, []),
        )

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.focus()
            await pilot.pause()
            content = app.screen.query_one("#diff-content", Static)
            screen._render_selected_file()
            await pilot.pause()
            first = content.content
            await pilot.press("j")
            await pilot.pause()
            second = content.content
            assert first is not second

    async def test_content_does_not_update_on_n_press(self, mocker):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, SAMPLE_DIFF, []),
        )

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.focus()
            await pilot.pause()
            content = app.screen.query_one("#diff-content", Static)
            screen._render_selected_file()
            await pilot.pause()
            first = content.content
            await pilot.press("n")
            await pilot.pause()
            second = content.content
            assert first is second

    async def test_content_updates_on_bracket_press(self, mocker):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, SAMPLE_DIFF, []),
        )

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.focus()
            await pilot.pause()
            content = app.screen.query_one("#diff-content", Static)
            screen._render_selected_file()
            await pilot.pause()
            first = content.content
            await pilot.press("]")
            await pilot.pause()
            second = content.content
            assert first is not second

    async def test_content_updates_on_navigation(self, mocker):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, SAMPLE_DIFF, []),
        )

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.focus()
            await pilot.pause()
            content = app.screen.query_one("#diff-content", Static)
            screen._render_selected_file()
            await pilot.pause()
            first = content.content
            await pilot.press("]")  # next file
            await pilot.pause()
            second = content.content
            assert first is not second

    async def test_content_updates_via_clicked_event(self, mocker):
        # A mouse click goes through ListView's own selection, a
        # different path from keyboard navigation.
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, SAMPLE_DIFF, []),
        )

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.focus()
            await pilot.pause()
            content = app.screen.query_one("#diff-content", Static)
            screen._render_selected_file()
            await pilot.pause()
            first = content.content
            await pilot.click(files_list.children[1].query_one(FileTile))
            await pilot.pause()
            second = content.content
            assert first is not second
            assert files_list.index == 1


class TestFileListScrolling:
    """The file list must auto-scroll to keep the highlighted tile in
    view when the list overflows the available height. This used to
    be broken because ``FileTileList.watch_index`` overrode the
    parent ``ListView.watch_index`` without calling ``super()``, so
    the built-in ``scroll_to_widget`` never ran."""

    def _many_files_diff(self, count: int) -> str:
        out = []
        for i in range(count):
            out.append(f"diff --git a/file{i}.py b/file{i}.py\n")
            out.append(f"index {i:04x}..{i + 1:04x} 100644\n")
            out.append(f"--- a/file{i}.py\n")
            out.append(f"+++ b/file{i}.py\n")
            out.append("@@ -1 +1 @@\n")
            out.append("-old\n")
            out.append("+new\n")
        return "".join(out)

    async def test_scrolling_keeps_selected_tile_visible(self, mocker):
        from gitdirector import repo as repo_mod

        big_diff = self._many_files_diff(20)
        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, big_diff, []),
        )
        # Use a small terminal height so the list overflows.
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 12)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            await _wait_for_deferred_scroll(files_list)
            assert len(files_list._specs) == 20
            assert files_list.is_scrollable
            assert files_list.max_scroll_y > 0
            # Jump to the last item; the list must scroll to keep
            # it visible (scroll_offset.y should advance past zero).
            files_list.index = 19
            target_y = files_list.max_scroll_y
            await _wait_for_deferred_scroll(files_list)
            assert files_list.scroll_offset.y > 0
            # And the scroll position should be near the maximum.
            assert files_list.scroll_offset.y == target_y

    async def test_scrolling_back_keeps_selected_tile_visible(self, mocker):
        from gitdirector import repo as repo_mod

        big_diff = self._many_files_diff(20)
        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, big_diff, []),
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 12)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.index = 19
            await _wait_for_deferred_scroll(files_list)
            # Now jump back to the first item; the scroll should
            # come back to zero.
            files_list.index = 0
            await _wait_for_deferred_scroll(files_list)
            assert files_list.scroll_offset.y == 0

    async def test_bracket_nav_scrolls_to_bottom(self, mocker):
        from gitdirector import repo as repo_mod

        big_diff = self._many_files_diff(20)
        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, big_diff, []),
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 12)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.focus()
            await pilot.pause()
            for _ in range(20):
                await pilot.press("]")
            await _wait_for_deferred_scroll(files_list)
            assert files_list.index == 19
            assert files_list.scroll_offset.y > 0


class TestFocusIndicator:
    """The active pane header must signal which side has keyboard focus."""

    async def test_initial_focus_marks_files_side(self, mocker):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, SAMPLE_DIFF, []),
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_pane = screen.query_one("#diff-files-pane")
            content_pane = screen.query_one("#diff-content-pane")
            files_label = screen.query_one("#diff-files-pane-label")
            content_label = screen.query_one("#diff-content-pane-label")
            assert files_pane.has_class("--files-focused")
            assert content_pane.has_class("--files-focused")
            assert not files_pane.has_class("--diff-focused")
            assert not content_pane.has_class("--diff-focused")
            assert files_label.has_class("--focused")
            assert not content_label.has_class("--focused")

    async def test_tab_toggles_focus_classes(self, mocker):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, SAMPLE_DIFF, []),
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_pane = screen.query_one("#diff-files-pane")
            content_pane = screen.query_one("#diff-content-pane")
            files_label = screen.query_one("#diff-files-pane-label")
            content_label = screen.query_one("#diff-content-pane-label")
            # Tab → diff side
            await pilot.press("tab")
            await pilot.pause()
            assert files_pane.has_class("--diff-focused")
            assert content_pane.has_class("--diff-focused")
            assert not files_pane.has_class("--files-focused")
            assert not content_pane.has_class("--files-focused")
            assert not files_label.has_class("--focused")
            assert content_label.has_class("--focused")
            # Tab → files side again
            await pilot.press("tab")
            await pilot.pause()
            assert files_pane.has_class("--files-focused")
            assert content_pane.has_class("--files-focused")
            assert not files_pane.has_class("--diff-focused")
            assert not content_pane.has_class("--diff-focused")
            assert files_label.has_class("--focused")
            assert not content_label.has_class("--focused")


class TestSetFiles:
    async def test_selects_the_first_file_once_mounted(self):
        from textual.app import App, ComposeResult

        received: list[ChangedFile | None] = []

        class _MiniApp(App):
            def compose(self) -> ComposeResult:
                yield FileTileList(id="fl")

            def on_file_tile_list_file_selected(self, event: FileTileList.FileSelected) -> None:
                received.append(event.file)

        files = [
            ChangedFile(path="new.py", status="A"),
            ChangedFile(path="foo.py", status="M"),
        ]
        app = _MiniApp()
        async with app.run_test(size=(60, 20)) as pilot:
            fl = app.query_one("#fl", FileTileList)
            await fl.set_files(files)
            await pilot.pause()
            assert fl.index == 0
            assert received and received[-1] is files[0]

            fl.index = 1
            await pilot.pause()
            tiles = [c.query_one(FileTile) for c in fl.children]
            assert [t.selected for t in tiles] == [False, True]
            assert received[-1] is files[1]

            await fl.set_files([])
            assert fl.index is None
            assert fl.selected_file() is None
