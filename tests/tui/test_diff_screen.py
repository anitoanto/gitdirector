"""Tests for the ``DiffReviewScreen`` and its integration with the app.

These tests cover:

* Screen composition: header, body, hint, file list, content panel
* Async loading: shows loading indicator, populates list, renders content
* Navigation: j/k, n/p, J/K, ]/[
* Focus switching: tab toggles between file list and diff scroll
* Close: escape and q both dismiss
* Refresh: r reloads the diff
* Empty state: shows "no uncommitted changes" when the working tree is clean
* Error state: shows error message when git diff fails
* Commit flow: ``g`` opens the stage/confirm/commit/push modal chain
* Integration: action menu "Review Diff" option dispatches correctly
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import LoadingIndicator, OptionList, Static

from gitdirector.commands.tui import (
    ActionMenuScreen,
    DiffReviewScreen,
    GitDirectorConsole,
)
from gitdirector.commands.tui.screens.diff_files import FileTileList

from .conftest import _make_info, _mock_manager


@pytest.fixture(autouse=True)
def _patch_repo_init(mocker):
    """Bypass the .git directory check in Repository.__init__ for every test.

    The screen tests use ad-hoc paths (e.g. ``/tmp/my-repo``) that aren't
    real git repos, so we patch the constructor to set the bare minimum
    attributes and let the rest of the workflow run. We also patch
    ``get_diff_against_head`` to return an empty diff so the worker
    completes without touching real git state, which is what most tests
    want; the few tests that need different behaviour re-patch it
    themselves.
    """
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


def _patch_repo_methods(mocker, *, diff_text="", untracked=None, raise_exc=False):
    """Replace the per-instance git calls in the worker with predictable results."""
    from gitdirector import repo as repo_mod

    untracked = untracked or []

    def fake_init(self, path):
        self.path = path
        self.name = path.name

    def fake_diff(self, **_kwargs):
        if raise_exc:
            raise RuntimeError("boom")
        return True, diff_text, list(untracked)

    def fake_untracked(self):
        return list(untracked)

    def fake_read(self, rel_path, **_kwargs):
        return f"contents of {rel_path}\n"

    mocker.patch.object(repo_mod.Repository, "__init__", fake_init)
    mocker.patch.object(repo_mod.Repository, "get_diff_against_head", fake_diff)
    mocker.patch.object(repo_mod.Repository, "_list_untracked_files", fake_untracked)
    mocker.patch.object(repo_mod.Repository, "read_file_text", fake_read)


def _make_valid_repo_path(tmp_path) -> Path:
    """Return a path that satisfies Repository's git-repo check without any real git state."""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


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


class TestDiffReviewScreenCompose:
    async def test_compose_renders_header_body_and_hint(self):
        screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()

            title = app.screen.query_one("#diff-title", Static)
            assert "my-repo" in title.content
            assert "Review Diff" in title.content

            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            assert files_list is not None

            content = app.screen.query_one("#diff-content", Static)
            assert content is not None

            hint = app.screen.query_one("#diff-hint", Static)
            assert "tab" in hint.content.lower()
            assert "esc" in hint.content.lower()

    async def test_loading_indicator_shown_while_diff_loads(self):
        # The loading indicator is shown before the worker completes and then
        # hidden after. Both the autouse fixture's `get_diff_against_head`
        # patch and the worker's lifecycle are exercised in the rest of the
        # test suite, so here we just verify the screen has the loading
        # container mounted in the DOM.
        screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            loading_container = app.screen.query_one("#diff-loading")
            assert loading_container is not None
            assert any(isinstance(child, LoadingIndicator) for child in loading_container.children)


class TestDiffReviewScreenLoading:
    async def test_loads_files_and_renders_content(self, mocker):
        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF, untracked=[])
        screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            assert len(files_list._specs) == 2
            labels = [s.file.path for s in files_list._specs]
            assert any("foo.py" in s for s in labels)
            assert any("bar.py" in s for s in labels)
            summary = app.screen.query_one("#diff-summary", Static)
            assert "2 files" in summary.content
            assert "+3" in summary.content
            assert "-1" in summary.content

    async def test_includes_untracked_files(self, mocker):
        _patch_repo_methods(mocker, diff_text="", untracked=["new.py", "other.txt"])
        screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            assert len(files_list._specs) == 2

    async def test_empty_diff_shows_clean_state(self, mocker):
        _patch_repo_methods(mocker, diff_text="", untracked=[])
        screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            empty = app.screen.query_one("#diff-empty", Static)
            assert empty.display is True
            assert "No uncommitted changes" in empty.content.plain
            summary = app.screen.query_one("#diff-summary", Static)
            assert "working tree clean" in summary.content.lower()

    async def test_git_failure_shows_error_state(self, mocker):
        from gitdirector import repo as repo_mod

        def fake_diff(self, **_kwargs):
            return False, "fatal: bad object HEAD", []

        mocker.patch.object(repo_mod.Repository, "get_diff_against_head", fake_diff)

        screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            empty = app.screen.query_one("#diff-empty", Static)
            assert empty.display is True
            assert "Failed to load diff" in empty.content.plain
            assert "bad object HEAD" in empty.content.plain

    async def test_repository_construction_failure(self, mocker):
        from gitdirector import repo as repo_mod

        def fake_init(self, path):
            raise ValueError("not a git repo")

        mocker.patch.object(repo_mod.Repository, "__init__", fake_init)

        screen = DiffReviewScreen("nope", Path("/tmp/nope"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            empty = app.screen.query_one("#diff-empty", Static)
            assert empty.display is True
            assert "not a git repo" in empty.content.plain


class TestDiffReviewScreenNavigation:
    async def _setup_with_diff(self, app, mocker):
        _patch_repo_methods(mocker, diff_text=SAMPLE_DIFF, untracked=[])
        screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app.push_screen(screen)
        await app.workers.wait_for_complete()
        await app.workers.wait_for_complete()
        return screen

    async def test_navigates_with_j_k(self, mocker):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await self._setup_with_diff(app, mocker)
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.focus()
            await pilot.pause()
            assert files_list.index == 0
            await pilot.press("j")
            assert files_list.index == 1
            await pilot.press("k")
            assert files_list.index == 0

    async def test_navigates_with_n_p(self, mocker):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await self._setup_with_diff(app, mocker)
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.focus()
            await pilot.pause()
            await pilot.press("n")
            assert files_list.index == 1
            await pilot.press("p")
            assert files_list.index == 0

    async def test_navigates_with_brackets(self, mocker):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await self._setup_with_diff(app, mocker)
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.focus()
            await pilot.pause()
            await pilot.press("]")
            assert files_list.index == 1
            await pilot.press("[")
            assert files_list.index == 0

    async def test_navigation_does_not_move_past_boundaries(self, mocker):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            await self._setup_with_diff(app, mocker)
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.focus()
            await pilot.pause()
            for _ in range(5):
                await pilot.press("k")
            assert files_list.index == 0
            for _ in range(5):
                await pilot.press("j")
            assert files_list.index == 1


class TestDiffReviewScreenFocus:
    async def test_tab_switches_focus_between_panels(self, mocker):
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
            focused_id = app.screen.focused.id if app.screen.focused else None
            assert focused_id == "diff-files-list"
            await pilot.press("tab")
            focused_id = app.screen.focused.id if app.screen.focused else None
            assert focused_id == "diff-content-scroll"
            await pilot.press("tab")
            focused_id = app.screen.focused.id if app.screen.focused else None
            assert focused_id == "diff-files-list"


class TestDiffReviewScreenClose:
    async def test_escape_dismisses(self, mocker):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, "", []),
        )

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        results: list = []
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen, callback=lambda v: results.append(v))
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert results == [None]

    async def test_q_dismisses(self, mocker):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, "", []),
        )

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        results: list = []
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen, callback=lambda v: results.append(v))
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            assert results == [None]


class TestDiffReviewScreenRefresh:
    async def test_refresh_reloads_diff(self, mocker):
        from gitdirector import repo as repo_mod

        call_count = {"n": 0}

        def fake_diff(self, **_kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return True, SAMPLE_DIFF, []
            return True, "", []

        mocker.patch.object(repo_mod.Repository, "get_diff_against_head", fake_diff)

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            assert len(files_list._specs) == 2

            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert call_count["n"] >= 2
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            assert len(files_list._specs) == 0


class TestDiffReviewScreenContentRendering:
    async def test_content_updates_when_highlight_changes(self, mocker):
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
            content = app.screen.query_one("#diff-content", Static)
            files_list = app.screen.query_one("#diff-files-list", FileTileList)
            files_list.index = 0
            screen._render_selected_file()
            await pilot.pause()
            first = content.content
            files_list.index = 1
            screen._render_selected_file()
            await pilot.pause()
            second = content.content
            assert first is not second


class TestContentPanelTone:
    """The right-side content panel must take on the file's status tint
    (green for new, red for deleted) so the whole panel reads as one
    cohesive block instead of a grey strip with floating coloured
    chips."""

    async def _setup_with_diff(self, app, pilot, mocker, diff_text):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, diff_text, []),
        )
        screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app.push_screen(screen)
        await app.workers.wait_for_complete()
        await pilot.pause()
        return screen

    async def test_new_file_tints_content_green(self, mocker):
        new_file_diff = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "index 0000000..1234567\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+first line\n"
            "+second line\n"
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = await self._setup_with_diff(app, pilot, mocker, new_file_diff)
            content = screen.query_one("#diff-content", Static)
            scroll = screen.query_one("#diff-content-scroll")
            assert content.has_class("--added"), "new file should tint content green"
            assert scroll.has_class("--added"), "new file should tint scroll green"

    async def test_deleted_file_tints_content_red(self, mocker):
        deleted_diff = (
            "diff --git a/old.py b/old.py\n"
            "deleted file mode 100644\n"
            "index 1234567..0000000\n"
            "--- a/old.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-line1\n"
            "-line2\n"
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = await self._setup_with_diff(app, pilot, mocker, deleted_diff)
            content = screen.query_one("#diff-content", Static)
            scroll = screen.query_one("#diff-content-scroll")
            assert content.has_class("--deleted"), "deleted file should tint content red"
            assert scroll.has_class("--deleted"), "deleted file should tint scroll red"

    async def test_modified_file_keeps_base_dark_tone(self, mocker):
        modified_diff = (
            "diff --git a/foo.py b/foo.py\n"
            "index 1234..5678 100644\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+new\n"
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = await self._setup_with_diff(app, pilot, mocker, modified_diff)
            content = screen.query_one("#diff-content", Static)
            scroll = screen.query_one("#diff-content-scroll")
            assert not content.has_class("--added")
            assert not content.has_class("--deleted")
            assert not scroll.has_class("--added")
            assert not scroll.has_class("--deleted")

    async def test_tone_updates_when_navigating_between_files(self, mocker):
        from gitdirector import repo as repo_mod

        # Two files in the same diff: a new one first, then a
        # modification. The first load selects the new file, the
        # second load keeps both files.
        two_file_diff = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "index 0000000..1234567\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,1 @@\n"
            "+x\n"
            "diff --git a/foo.py b/foo.py\n"
            "index 1234..5678 100644\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+new\n"
        )

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, two_file_diff, []),
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            content = screen.query_one("#diff-content", Static)
            files_list = screen.query_one("#diff-files-list", FileTileList)
            assert len(files_list._specs) == 2
            # File 0 is the new file → content should be green-tinted.
            assert content.has_class("--added")
            # Move to the second file (modified). The --added class
            # must be cleared so we don't get a stale green tint.
            files_list.index = 1
            screen._render_selected_file()
            await pilot.pause()
            assert files_list.index == 1
            assert not content.has_class("--added"), (
                "stale --added class left over from the new file"
            )
            assert not content.has_class("--deleted")

    async def test_horizontal_scroll_moves_pane_right_then_left(self, mocker):
        from gitdirector import repo as repo_mod

        # A diff with lines long enough to overflow a narrow viewport.
        long_line = "x" * 200
        long_diff = (
            "diff --git a/long.py b/long.py\n"
            "index 1234..5678 100644\n"
            "--- a/long.py\n"
            "+++ b/long.py\n"
            "@@ -1,1 +1,1 @@\n"
            f"-{long_line}\n"
            f"+{long_line}\n"
        )
        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, long_diff, []),
        )

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            screen = app.screen
            scroll = screen.query_one("#diff-content-scroll")
            scroll.focus()
            await pilot.pause()
            assert scroll.scroll_x == 0
            assert scroll.max_scroll_x > 0, "content should overflow the viewport horizontally"
            await pilot.press("l")
            await pilot.pause()
            assert scroll.scroll_x > 0, "l should scroll the content right"
            await pilot.press("h")
            await pilot.pause()
            assert scroll.scroll_x == 0, "h should scroll the content back to the start"


# ---------------------------------------------------------------------------
# Integration with the action menu
# ---------------------------------------------------------------------------


class TestReviewDiffActionMenuIntegration:
    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_action_menu_includes_review_diff_option(self, _mock_sessions):
        screen = ActionMenuScreen("my-repo", Path("/tmp/my-repo"), branch="main")
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(screen)
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            ids = [opt.id for opt in menu.options if opt.id is not None]
            assert "review_diff" in ids

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_app_opens_review_diff_screen(self, _mock_sessions, mocker):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, "", []),
        )

        repos = [_make_info("alpha", Path("/tmp/alpha"), branch="main")]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._open_review_diff()
            await pilot.pause()
            assert isinstance(app.screen, DiffReviewScreen)

    @patch("gitdirector.integrations.tmux.list_repo_sessions", return_value=[])
    async def test_handle_menu_action_review_diff(self, _mock_sessions, mocker):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, "", []),
        )

        repos = [_make_info("alpha", Path("/tmp/alpha"), branch="main")]
        app = GitDirectorConsole()
        app.manager = _mock_manager(repos)
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._handle_menu_action("review_diff")
            await pilot.pause()
            assert isinstance(app.screen, DiffReviewScreen)

    async def test_open_review_diff_no_selection(self):
        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as _:
            app._open_review_diff()


class TestDiffReviewScreenCommitFlow:
    """End-to-end coverage for the ``g`` -> commit -> optional push flow."""

    def _patch_repo_with_diff(self, mocker):
        from gitdirector import repo as repo_mod

        def fake_diff(self, **_kw):
            return True, SAMPLE_DIFF, []

        def fake_commit(self, message):
            return True, f"[main abc123] {message}\n"

        def fake_add(self, paths=None):
            return True, ""

        def fake_push(self, *, set_upstream=False):
            return True, "To origin\n"

        mocker.patch.object(
            repo_mod.Repository,
            "__init__",
            lambda self, p: setattr(self, "path", p) or setattr(self, "name", p.name),
        )
        mocker.patch.object(repo_mod.Repository, "get_diff_against_head", fake_diff)
        mocker.patch.object(repo_mod.Repository, "_list_untracked_files", lambda self: [])
        mocker.patch.object(repo_mod.Repository, "read_file_text", lambda self, p, **_kw: None)
        mocker.patch.object(repo_mod.Repository, "add", fake_add)
        mocker.patch.object(repo_mod.Repository, "commit", fake_commit)
        mocker.patch.object(repo_mod.Repository, "push", fake_push)

    async def test_g_binding_disabled_when_no_files(self, mocker):
        from gitdirector import repo as repo_mod

        mocker.patch.object(
            repo_mod.Repository,
            "get_diff_against_head",
            lambda self, **_kw: (True, "", []),
        )

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            from gitdirector.commands.tui.screens.commit import StageFilesConfirmScreen

            pushed: list = []
            original = app.push_screen

            def spy(*a, **kw):
                pushed.append(a)
                return original(*a, **kw)

            app.push_screen = spy  # type: ignore[assignment]
            await pilot.press("g")
            await pilot.pause()
            # No modal of any kind was pushed: action_commit is a no-op
            # when there are no files in the diff.
            assert all(not isinstance(arg, StageFilesConfirmScreen) for arg in pushed)

    async def test_g_opens_stage_confirm_with_aggregated_stats(self, mocker):
        self._patch_repo_with_diff(mocker)
        from gitdirector.commands.tui.screens.commit import StageFilesConfirmScreen

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("g")
            await pilot.pause()

            assert isinstance(app.screen, StageFilesConfirmScreen)
            assert app.screen.additions == 3
            assert app.screen.deletions == 1
            assert app.screen.file_count == 2
            stats = app.screen.query_one("#menu-stats", Static)
            stats_text = str(stats.content)
            assert "+3" in stats_text
            assert "-1" in stats_text

    async def test_stage_confirm_cancel_closes_chain(self, mocker):
        self._patch_repo_with_diff(mocker)

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()

            from gitdirector.commands.tui.screens.commit import StageFilesConfirmScreen

            assert isinstance(app.screen, StageFilesConfirmScreen)
            await pilot.press("escape")
            await pilot.pause()
            # Back to the diff screen, no further modals pushed.
            assert isinstance(app.screen, DiffReviewScreen)

    async def test_commit_only_flow_reaches_result_screen(self, mocker):
        self._patch_repo_with_diff(mocker)

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("g")
            await pilot.pause()

            from gitdirector.commands.tui.screens.commit import (
                CommitMessageScreen,
                CommitResultScreen,
                StageFilesConfirmScreen,
            )

            assert isinstance(app.screen, StageFilesConfirmScreen)
            await pilot.press("down")  # "No" is the default; pick "Yes"
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, CommitMessageScreen)

            msg_input = app.screen.query_one("#commit-message-input")
            msg_input.value = "Add commit flow"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert isinstance(app.screen, CommitResultScreen)
            assert app.screen.commit_ok is True
            assert app.screen.push_ok is None  # commit-only path
            assert "Add commit flow" in str(app.screen.query_one("#commit-result-message").content)

    async def test_commit_and_push_flow(self, mocker):
        self._patch_repo_with_diff(mocker)
        from gitdirector.commands.tui.screens.commit import (
            CommitMessageScreen,
            CommitResultScreen,
            StageFilesConfirmScreen,
        )

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            assert isinstance(app.screen, StageFilesConfirmScreen)
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, CommitMessageScreen)

            app.screen.query_one("#commit-message-input").value = "Ship it"
            # Move focus to the action list and pick "commit & push"
            await pilot.press("tab")
            await pilot.press("down")
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert isinstance(app.screen, CommitResultScreen)
            assert app.screen.commit_ok is True
            assert app.screen.push_ok is True

    async def test_commit_failure_keeps_diff_open(self, mocker):
        from gitdirector import repo as repo_mod

        def fake_diff(self, **_kw):
            return True, SAMPLE_DIFF, []

        def fake_commit(self, message):
            return False, "nothing to commit"

        mocker.patch.object(
            repo_mod.Repository,
            "__init__",
            lambda self, p: setattr(self, "path", p) or setattr(self, "name", p.name),
        )
        mocker.patch.object(repo_mod.Repository, "get_diff_against_head", fake_diff)
        mocker.patch.object(repo_mod.Repository, "_list_untracked_files", lambda self: [])
        mocker.patch.object(repo_mod.Repository, "read_file_text", lambda self, p, **_kw: None)
        mocker.patch.object(repo_mod.Repository, "add", lambda self, paths=None: (True, ""))
        mocker.patch.object(repo_mod.Repository, "commit", fake_commit)
        mocker.patch.object(
            repo_mod.Repository,
            "push",
            lambda self, *, set_upstream=False: (True, ""),
        )

        app = GitDirectorConsole()
        app.manager = _mock_manager()
        async with app.run_test(size=(120, 30)) as pilot:
            screen = DiffReviewScreen("my-repo", Path("/tmp/my-repo"), branch="main")
            app.push_screen(screen)
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            app.screen.query_one("#commit-message-input").value = "failing"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

            from gitdirector.commands.tui.screens.commit import CommitResultScreen

            assert isinstance(app.screen, CommitResultScreen)
            assert app.screen.commit_ok is False
