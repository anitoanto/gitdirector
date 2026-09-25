"""The console's top bar: tab switcher, counts, and compact layout."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Static, TabbedContent

from gitdirector.commands.tui import GitDirectorConsole
from gitdirector.commands.tui.topbar import NavState, TopBar

from .conftest import SAMPLE_SESSIONS, _make_info, _mock_manager, patch_sessions


def _text(app, widget_id: str) -> str:
    return str(app.query_one(f"#{widget_id}", Static).render())


def _console() -> GitDirectorConsole:
    app = GitDirectorConsole()
    app.manager = _mock_manager([_make_info("alpha", Path("/tmp/alpha"))])
    return app


class TestTopBar:
    async def test_replaces_the_header_and_the_tab_strip(self):
        app = _console()
        async with app.run_test(size=(120, 30)):
            bar = app.query_one(TopBar)
            assert bar.region.y == 0 and bar.region.height == 3
            assert not app.query("Header")
            assert not app.query_one("#tabs ContentTabs").display

    async def test_the_palette_icon_sits_in_the_middle(self):
        app = _console()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            palette = app.query_one("#palette")
            row = "".join(segment.text for segment in palette.render_line(1))
            # A glyph the terminal draws narrower than Rich measures would drift left.
            assert row == "  ≡  "

    async def test_a_divider_sits_between_each_pair_of_tabs(self):
        app = _console()
        async with app.run_test(size=(120, 30)):
            ids = [child.id or child.classes for child in app.query_one(TopBar).children]
            tabs = ids.index("nav-repos"), ids.index("nav-sessions"), ids.index("nav-panels")
            assert [ids[i + 1] for i in tabs[:2]] == [frozenset({"nav-divider"})] * 2

    async def test_clicking_a_tab_switches_to_it(self):
        app = _console()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            # Anywhere in the three rows, not just the label's.
            await pilot.click("#nav-sessions", offset=(0, 2))
            await pilot.pause()
            assert app.query_one("#tabs", TabbedContent).active == "sessions"
            assert app.query_one("#nav-sessions").has_class("-active")
            assert not app.query_one("#nav-repos").has_class("-active")

    async def test_number_keys_move_the_active_tab(self):
        app = _console()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("3")
            await pilot.pause()
            assert app.query_one("#nav-panels").has_class("-active")

    # The sample sessions, not whatever tmux happens to run on this machine.
    @patch_sessions()
    async def test_shows_the_console_counts_and_the_waiting_badge(self, _mock_list):
        app = _console()
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            app._waiting_count = 2
            app._refresh_top_bar()
            await pilot.pause()
            assert _text(app, "nav-repos") == "Repositories 1"
            assert _text(app, "nav-sessions") == f"Sessions {len(SAMPLE_SESSIONS)} ●2"
            assert _text(app, "nav-panels") == "Panels"


class TestNavMarkup:
    async def test_hides_zero_counts_and_no_waiting(self):
        bar = TopBar("1.0")
        bar._state = NavState(active="repos", repos=12, sessions=0, waiting=0, panels=1)
        texts = bar._texts(compact=False)
        assert texts["nav-repos"] == "Repositories [$text-muted]12[/]"
        assert texts["nav-sessions"] == "Sessions"
        assert texts["nav-panels"] == "Panels [$text-muted]1[/]"

    async def test_goes_compact_when_narrow(self):
        app = _console()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert _text(app, "brand") == "◆ GitDirector"
            assert "v" in _text(app, "top-meta")
        async with _console().run_test(size=(60, 30)) as pilot:
            await pilot.pause()
            assert _text(pilot.app, "brand") == "◆"
            assert _text(pilot.app, "nav-repos").startswith("Repos")
            assert "v" not in _text(pilot.app, "top-meta")


class TestUpdateNotice:
    async def test_a_newer_release_is_shown_next_to_the_version(self):
        app = _console()
        async with app.run_test(size=(140, 30)) as pilot:
            # The console's own check (finding nothing here) must not land after.
            await app.workers.wait_for_complete()
            app._set_update_notice("Update available: v9.9.9 (current v1.0.0)", "9.9.9")
            await pilot.pause()
            meta = app.query_one("#top-meta", Static)
            assert "→ v9.9.9" in _text(app, "top-meta")
            assert "pip install -U gitdirector" in str(meta.tooltip)

    async def test_nothing_extra_without_an_update(self):
        app = _console()
        async with app.run_test(size=(140, 30)) as pilot:
            # The console's own check (finding nothing here) must not land after.
            await app.workers.wait_for_complete()
            app._set_update_notice(None, None)
            await pilot.pause()
            assert "→" not in _text(app, "top-meta")
            assert app.query_one("#top-meta", Static).tooltip is None

    async def test_narrow_bar_keeps_the_update(self):
        app = _console()
        async with app.run_test(size=(60, 30)) as pilot:
            # The console's own check (finding nothing here) must not land after.
            await app.workers.wait_for_complete()
            app._set_update_notice("Update available", "9.9.9")
            await pilot.pause()
            assert _text(app, "top-meta").startswith("↑ v9.9.9")

    def test_only_a_newer_release_counts(self):
        from gitdirector.commands.tui.app import _newer_version
        from gitdirector.version_check import UpdateStatus

        assert _newer_version(UpdateStatus("1.8.8", "1.9.0")) == "1.9.0"
        assert _newer_version(UpdateStatus("1.8.8", "1.8.8")) is None
        assert _newer_version(UpdateStatus("1.8.8", None)) is None
        assert _newer_version(None) is None
