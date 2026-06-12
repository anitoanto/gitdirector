"""Tests for the FileTile and FileTileList widgets.

Covers the redesigned tile layout (icon + filename title + right-aligned
stats + full-path subtitle), the selection palette generator, and the
"diff visualization updates on selection" bug fix.
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
    selection_colors,
)

from .conftest import _mock_manager

# ---------------------------------------------------------------------------
# Pure-Python: selection_colors
# ---------------------------------------------------------------------------


class TestSelectionColors:
    """The palette generator must always return legible combinations."""

    @pytest.mark.parametrize(
        "status,icon_bg",
        [
            ("A", "#238636"),
            ("M", "#9e6a03"),
            ("D", "#da3633"),
            ("R", "#1f6feb"),
            ("?", "#8957e5"),
        ],
    )
    def test_known_status_palettes_are_legible(self, status, icon_bg):
        tile_bg, border, title_fg, subtitle_fg = selection_colors(icon_bg)
        assert tile_bg.startswith("#") and len(tile_bg) == 7
        assert border.startswith("#") and len(border) == 7
        assert title_fg.startswith("#") and len(title_fg) == 7
        assert subtitle_fg.startswith("#") and len(subtitle_fg) == 7
        # The title foreground should always be near-white; anything
        # darker would be illegible on a dark selection background.
        assert title_fg.lower() in {"#ffffff", "#f0f6fc"}

    def test_modified_pull_is_warm_tinted(self):
        # The amber "M" icon should give us a selection that's
        # noticeably warmer than a flat dark grey, so the row reads
        # as part of the same family.
        tile_bg, _border, _t, _s = selection_colors("#9e6a03")
        r = int(tile_bg[1:3], 16)
        g = int(tile_bg[3:5], 16)
        b = int(tile_bg[5:7], 16)
        # Red channel should be the largest by a clear margin.
        assert r > g and r > b

    def test_added_pull_is_greenish(self):
        tile_bg, *_ = selection_colors("#238636")
        g = int(tile_bg[3:5], 16)
        r = int(tile_bg[1:3], 16)
        b = int(tile_bg[5:7], 16)
        assert g >= r and g >= b

    def test_deleted_pull_is_reddish(self):
        tile_bg, *_ = selection_colors("#da3633")
        r = int(tile_bg[1:3], 16)
        g = int(tile_bg[3:5], 16)
        b = int(tile_bg[5:7], 16)
        assert r > g and r > b

    def test_grey_icon_falls_back_to_neutral(self):
        # An achromatic "?" shouldn't get a muddy brown tint; we keep
        # it a clean dark grey.
        tile_bg, border, _, _ = selection_colors("#6e7681")
        r, g, b = (int(tile_bg[i : i + 2], 16) for i in (1, 3, 5))
        # All channels close to each other
        assert max(r, g, b) - min(r, g, b) < 20

    def test_unknown_color_falls_back(self):
        # CSS named colours or theme tokens should produce the safe
        # default without raising.
        tile_bg, border, title_fg, subtitle_fg = selection_colors("$surface")
        assert tile_bg == "#1f2937"
        assert border == "#1f6feb"
        assert title_fg == "#f0f6fc"

    def test_is_deterministic(self):
        assert selection_colors("#9e6a03") == selection_colors("#9e6a03")

    def test_different_icons_produce_different_palettes(self):
        amber = selection_colors("#9e6a03")
        green = selection_colors("#238636")
        assert amber != green


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

    def test_subtitle_is_absolute_path(self):
        f = ChangedFile(path="src/foo.py", status="M")
        spec = _FileTileSpec(f, "/tmp/repo")
        assert spec.subtitle() == "/tmp/repo/src/foo.py"

    def test_subtitle_strips_trailing_slash(self):
        f = ChangedFile(path="src/foo.py", status="M")
        spec = _FileTileSpec(f, "/tmp/repo/")
        assert spec.subtitle() == "/tmp/repo/src/foo.py"

    def test_icon_letter_known(self):
        for status, letter in [("A", "A"), ("M", "M"), ("D", "D"), ("R", "R"), ("?", "U")]:
            assert _FileTileSpec(ChangedFile(path="x", status=status), "").icon_letter() == letter

    def test_icon_letter_unknown_status(self):
        spec = _FileTileSpec(ChangedFile(path="x", status="Z"), "")
        assert spec.icon_letter() == "Z"

    def test_icon_bg_known(self):
        from gitdirector.commands.tui.diff_renderer import STATUS_PILL_BG

        spec = _FileTileSpec(ChangedFile(path="x", status="M"), "")
        assert spec.icon_bg() == STATUS_PILL_BG["M"]


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
            assert "/tmp/my-repo/src/foo.py" in str(subtitle.render())

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

    async def test_icon_has_status_letter(self, mocker):
        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            # The SAMPLE_DIFF has one modified file (M) and one new file (A)
            # The icon is rendered as a Text styled with the icon's bg/fg,
            # padded to 3 cells so the colour block is visible end-to-end.
            letters = {str(c.query_one(".tile-icon", Static).render()) for c in files_list.children}
            assert any("M" in s for s in letters)
            assert any("A" in s for s in letters)
            # And every icon must be 3 cells wide.
            for s in letters:
                assert len(s) == 3

    async def test_icon_background_is_painted_via_text_style(self, mocker):
        # The icon's coloured background is part of the rich.Text style
        # (so it actually renders end-to-end in the terminal) rather than
        # a CSS background on the Static (which used to get covered by
        # the FileTile's tall-border characters).
        from rich.text import Text as RichText

        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            for child in files_list.children:
                tile = child.query_one(FileTile)
                text = tile._icon_text()
                assert isinstance(text, RichText)
                # The text must be padded to exactly 3 cells so the bg
                # colour spans the full icon width.
                assert len(text.plain) == 3
                # The text's style string must include a "on COLOR"
                # background so the colour block renders as part of the
                # text (not as a CSS background that can be hidden by
                # other widgets' borders).
                assert "on " in str(text.style)

    async def test_icon_text_includes_letter_for_every_status(self, mocker):
        # Round-trip every known status through the Static renderable and
        # confirm the letter shows up regardless of how the bg/fg are
        # combined.
        from rich.text import Text as RichText

        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            seen_letters = set()
            for child in files_list.children:
                tile = child.query_one(FileTile)
                rendered = tile._icon_text()
                assert isinstance(rendered, RichText)
                assert len(rendered.plain) == 3
                # The middle cell is the letter.
                letter = rendered.plain.strip()
                seen_letters.add(letter)
            # SAMPLE_DIFF has one M and one A.
            assert "M" in seen_letters
            assert "A" in seen_letters


class TestFileTileSelection:
    async def test_selected_tile_uses_legible_palette(self, mocker):
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
            tile = files_list.children[0].query_one(FileTile)
            assert tile.selected is True
            # The palette must be a 4-tuple of hex strings
            tile_bg, border, title_fg, subtitle_fg = tile.selection_palette
            for color in (tile_bg, border, title_fg, subtitle_fg):
                assert color.startswith("#")
                assert len(color) == 7

    async def test_deselected_tile_has_no_bg(self, mocker):
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
            # The second tile should NOT be selected
            tile = files_list.children[1].query_one(FileTile)
            assert tile.selected is False

    async def test_palette_differs_per_status(self, mocker):
        # The selection palette should depend on the icon's status so
        # that the row feels like part of the same family as the icon.
        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF)
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            tiles = [c.query_one(FileTile) for c in files_list.children]
            assert len(tiles) == 2
            assert tiles[0].selection_palette != tiles[1].selection_palette


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

    async def test_content_updates_on_n_press(self, mocker):
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
            assert first is not second

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
        # The screen's on_file_tile_clicked handler is the path used
        # by mouse clicks; ensure the diff still updates when a tile
        # is clicked (which is a slightly different code path from
        # keyboard nav).
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
            # Post the click event for the second tile.
            second_tile = files_list.children[1].query_one(FileTile)
            second_tile.post_message(FileTile.Clicked(second_tile))
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
            assert len(files_list._specs) == 20
            assert files_list.is_scrollable
            # Jump to the last item; the list must scroll to keep
            # it visible (scroll_offset.y should advance past zero).
            files_list.index = 19
            await pilot.pause()
            assert files_list.scroll_offset.y > 0
            # And the scroll position should be near the maximum.
            assert files_list.scroll_offset.y == files_list.max_scroll_y

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
            await pilot.pause()
            # Now jump back to the first item; the scroll should
            # come back to zero.
            files_list.index = 0
            await pilot.pause()
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
            await pilot.pause()
            assert files_list.index == 19
            assert files_list.scroll_offset.y > 0


class TestFocusBorderTint:
    """The divider between the file list and the diff content must
    shift colour to signal which side has keyboard focus."""

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
            assert files_pane.has_class("--files-focused")
            assert content_pane.has_class("--files-focused")
            assert not files_pane.has_class("--diff-focused")
            assert not content_pane.has_class("--diff-focused")

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
            # Tab → diff side
            await pilot.press("tab")
            await pilot.pause()
            assert files_pane.has_class("--diff-focused")
            assert content_pane.has_class("--diff-focused")
            assert not files_pane.has_class("--files-focused")
            assert not content_pane.has_class("--files-focused")
            # Tab → files side again
            await pilot.press("tab")
            await pilot.pause()
            assert files_pane.has_class("--files-focused")
            assert content_pane.has_class("--files-focused")
            assert not files_pane.has_class("--diff-focused")
            assert not content_pane.has_class("--diff-focused")


class TestSetFilesRegression:
    """Regression tests for the empty-list and unbounded-retry fix.

    Previously, ``set_files([])`` left ``_suppress_watch=True`` forever
    so the user could never change selection, and
    ``_apply_initial_selection`` re-queued itself unboundedly.
    """

    def test_empty_list_clears_suppress_watch(self):
        from textual.app import App

        class _MiniApp(App):
            pass

        app = _MiniApp()
        with app._context():  # required for widget construction
            fl = FileTileList()
            fl.set_files([])
            assert fl._suppress_watch is False
            assert fl._pending_index is None

    def test_apply_initial_selection_bounded_by_retry_limit(self):
        from textual.app import App

        class _MiniApp(App):
            pass

        app = _MiniApp()
        with app._context():
            fl = FileTileList()
            # Set a pending index that will never be satisfied because
            # the list has 0 nodes.
            fl._pending_index = 0
            fl._pending_retry_count = 0
            fl._suppress_watch = True
            # Call repeatedly: should bail out at the retry limit
            # rather than re-queue forever.
            for _ in range(fl._PENDING_RETRY_LIMIT + 5):
                fl._apply_initial_selection()
            assert fl._pending_index is None
            assert fl._suppress_watch is False
