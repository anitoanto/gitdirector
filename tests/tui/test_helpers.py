"""Tests for TUI helper functions, constants, and the table palette."""

from textual.app import App
from textual.color import Color
from textual.theme import BUILTIN_THEMES

from gitdirector.commands.tui import (
    _SORT_COLUMN_NAMES,
    _STATUS_ORDER,
    TablePalette,
    resolve_table_palette,
)
from gitdirector.commands.tui.constants import _CURSOR_TINT, _changes_sort_key
from gitdirector.repo import RepoStatus
from gitdirector.ui_theme import contrast_ratio, readable_on

from .conftest import _make_info

PALETTE = TablePalette(success="#00aa00", yellow="#ffaa00", muted="#888888", primary="#6699ff")


class TestTablePaletteLabels:
    def test_changes_label_uses_warning_colour(self):
        assert PALETTE.changes_label(_make_info(staged=True, unstaged=True)) == (
            "[bold #ffaa00]staged+unstaged[/]"
        )
        assert PALETTE.changes_label(_make_info(staged=True, unstaged=False)) == (
            "[bold #ffaa00]staged[/]"
        )
        assert PALETTE.changes_label(_make_info(staged=False, unstaged=True)) == (
            "[bold #ffaa00]unstaged[/]"
        )
        assert PALETTE.changes_label(_make_info(staged=False, unstaged=False)) == "—"

    def test_changes_sort_key(self):
        assert _changes_sort_key(_make_info(staged=True, unstaged=True)) == "staged+unstaged"
        assert _changes_sort_key(_make_info(staged=False, unstaged=False)) == "—"

    def test_sync_label_covers_every_status(self):
        assert PALETTE.sync_label(RepoStatus.UP_TO_DATE) == "up to date"
        for status in (
            RepoStatus.BEHIND,
            RepoStatus.AHEAD,
            RepoStatus.DIVERGED,
            RepoStatus.UNKNOWN,
        ):
            assert PALETTE.sync_label(status) == f"[bold #ffaa00]{status.value}[/]"

    def test_session_status_styles(self):
        assert PALETTE.session_status("waiting") == ("● waiting", "bold #ffaa00")
        assert PALETTE.session_status("running") == ("● running", "#00aa00")
        assert PALETTE.session_status("idle") == ("○ idle", "#888888")
        assert PALETTE.session_status("anything-else") == ("● running", "#00aa00")

    def test_panel_status_labels(self):
        assert PALETTE.panel_status_label("active") == "[#00aa00]● active[/]"
        assert PALETTE.panel_status_label("empty") == "[#888888]○ empty[/]"

    def test_group_label(self):
        assert PALETTE.group_label("▾ work") == "[bold #6699ff]▾ work[/]"


class TestReadableOn:
    def test_keeps_colour_that_already_reads(self):
        white, black = Color(255, 255, 255), Color(0, 0, 0)
        assert readable_on(white, black) == white

    def test_lifts_dark_colour_on_dark_background(self):
        dark_green = Color(0, 60, 0)
        result = readable_on(dark_green, Color(20, 20, 20))
        assert contrast_ratio(result, Color(20, 20, 20)) >= 4.5
        assert result.g > result.r and result.g > result.b

    def test_darkens_light_colour_on_light_background(self):
        yellow = Color(255, 230, 100)
        result = readable_on(yellow, Color(240, 240, 240))
        assert contrast_ratio(result, Color(240, 240, 240)) >= 4.5

    def test_ansi_colours_are_left_alone(self):
        ansi = Color.parse("ansi_green")
        assert readable_on(ansi, Color(0, 0, 0)) is ansi

    def test_satisfies_a_light_surface_and_a_mid_tone_highlight_together(self):
        surface = Color(216, 216, 216)
        highlight = surface.blend(Color(0, 69, 120), 0.3)
        result = readable_on(Color(78, 190, 99), surface, highlight)
        assert contrast_ratio(result, surface) >= 4.5
        assert contrast_ratio(result, highlight) >= 4.5


class TestResolveTablePalette:
    def test_every_builtin_theme_reads_on_surface_and_highlight(self):
        app = App()
        for theme in BUILTIN_THEMES:
            if theme.startswith("ansi-"):
                continue
            app.theme = theme
            variables = app.get_css_variables()
            palette = resolve_table_palette(variables)
            surface = Color.parse(variables["surface"]).blend(
                Color.parse(variables["foreground"]), 0.05
            )
            tint = surface.blend(Color.parse(variables["primary"]), _CURSOR_TINT)
            for name in ("success", "yellow", "primary"):
                colour = Color.parse(getattr(palette, name))
                for background in (surface, tint):
                    assert contrast_ratio(colour, background) >= 4.5, (theme, name)
            muted = Color.parse(palette.muted)
            for background in (surface, tint):
                assert contrast_ratio(muted, background) >= 3.5, (theme, "muted")

    def test_ansi_theme_keeps_terminal_palette_names(self):
        app = App()
        app.theme = "ansi-dark"
        palette = resolve_table_palette(app.get_css_variables())
        assert palette.success == "green"
        assert palette.yellow == "yellow"
        assert palette.muted == "bright_black"

    def test_attention_colour_stays_yellow_in_every_theme(self):
        app = App()
        for theme in BUILTIN_THEMES:
            if theme.startswith("ansi-"):
                continue
            app.theme = theme
            colour = Color.parse(resolve_table_palette(app.get_css_variables()).yellow)
            hue = colour.hsl.h * 360
            assert 35 <= hue <= 65, (theme, colour.hex)

    def test_missing_variables_fall_back(self):
        palette = resolve_table_palette({})
        assert palette.success.startswith("#")
        assert palette.muted.startswith("#")


class TestSortConstants:
    def test_sort_column_names_count(self):
        assert len(_SORT_COLUMN_NAMES) == 6

    def test_status_order_covers_all(self):
        for s in RepoStatus:
            assert s in _STATUS_ORDER
