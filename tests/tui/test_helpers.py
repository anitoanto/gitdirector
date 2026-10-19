"""Tests for TUI helper functions, constants, and the table palette."""

import re
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App
from textual.color import Color
from textual.theme import BUILTIN_THEMES

from gitdirector.commands.tui import (
    _SORT_COLUMN_NAMES,
    GitDirectorConsole,
    TablePalette,
    resolve_table_palette,
)
from gitdirector.ui_theme import (
    DEFAULT_THEME_NAME,
    MAX_HIGHLIGHT_TINT,
    MIN_CONTRAST,
    MIN_HIGHLIGHT_TINT,
    SupportedThemes,
    _relative_luminance,
    contrast_ratio,
    highlight_backgrounds,
    highlight_tint,
    readable_on,
    resolve_panel_theme,
    supported_theme_name,
    theme_is_supported,
)

ANSI_THEMES = {"ansi-dark", "ansi-light"}
TEXT_VARIABLES = (
    "text",
    "text-muted",
    "text-primary",
    "text-secondary",
    "text-accent",
    "text-success",
    "text-warning",
    "text-error",
)


class _ThemedApp(SupportedThemes, App):
    pass


def _luminance(value: str) -> float:
    return _relative_luminance(Color.parse(value))


PALETTE = TablePalette(success="#00aa00", yellow="#ffaa00", muted="#888888", primary="#6699ff")


class TestTablePaletteLabels:
    def test_session_status_styles(self):
        assert PALETTE.session_status("waiting") == ("● waiting", "bold #ffaa00")
        assert PALETTE.session_status("running") == ("● running", "#00aa00")
        assert PALETTE.session_status("idle") == ("○ idle", "#888888")
        assert PALETTE.session_status("pending") == ("◐ pending", PALETTE.pending)
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


class TestThemeSupport:
    def test_ansi_themes_are_not_supported_and_every_other_builtin_is(self):
        for name, theme in BUILTIN_THEMES.items():
            assert theme_is_supported(theme) is (name not in ANSI_THEMES), name

    def test_supported_theme_name_falls_back(self):
        assert supported_theme_name("nord") == "nord"
        assert supported_theme_name("ansi-light") == DEFAULT_THEME_NAME
        assert supported_theme_name("no-such-theme") == DEFAULT_THEME_NAME
        assert supported_theme_name(None) == DEFAULT_THEME_NAME
        assert supported_theme_name("ansi-dark", fallback="monokai") == "monokai"

    def test_console_offers_only_supported_themes(self):
        app = GitDirectorConsole()
        assert set(app.available_themes) == set(BUILTIN_THEMES) - ANSI_THEMES

    def test_console_keeps_a_supported_configured_theme(self):
        manager = MagicMock()
        manager.config.theme = "nord"
        with patch("gitdirector.commands.tui.app.RepositoryManager", return_value=manager):
            app = GitDirectorConsole()
        assert app.theme == "nord"
        manager.config.save.assert_not_called()

    @pytest.mark.parametrize("configured", ["ansi-light", "ansi-dark", "no-such-theme"])
    def test_console_falls_back_from_a_theme_it_cannot_offer(self, configured):
        manager = MagicMock()
        manager.config.theme = configured
        with patch("gitdirector.commands.tui.app.RepositoryManager", return_value=manager):
            app = GitDirectorConsole()
        assert app.theme == DEFAULT_THEME_NAME
        assert manager.config.theme == DEFAULT_THEME_NAME
        manager.config.save.assert_called_once_with()

    def test_every_offered_theme_reads_on_every_surface(self):
        app = _ThemedApp()
        for name in app.available_themes:
            app.theme = name
            variables = app.get_css_variables()
            backgrounds = highlight_backgrounds(variables)
            for variable in TEXT_VARIABLES:
                colour = Color.parse(variables[variable])
                for background in backgrounds:
                    ratio = contrast_ratio(colour, background)
                    assert ratio >= MIN_CONTRAST, (name, variable, background.hex6, ratio)

    def test_every_offered_theme_has_a_readable_footer(self):
        app = _ThemedApp()
        for name in app.available_themes:
            app.theme = name
            variables = app.get_css_variables()
            footer = Color.parse(variables["footer-background"])
            for variable in ("footer-foreground", "footer-key-foreground"):
                ratio = contrast_ratio(Color.parse(variables[variable]), footer)
                assert ratio >= MIN_CONTRAST, (name, variable, ratio)
            description = Color.parse(variables["footer-description-foreground"])
            assert contrast_ratio(description, footer) >= MIN_CONTRAST, name

    def test_highlight_variables_are_opaque_and_visible(self):
        app = _ThemedApp()
        for name in app.available_themes:
            app.theme = name
            variables = app.get_css_variables()
            surface = Color.parse(variables["surface"])
            panel = Color.parse(variables["panel"])
            primary = Color.parse(variables["primary"])
            for variable in (
                "highlight",
                "highlight-hover",
                "highlight-panel",
                "highlight-panel-hover",
            ):
                assert re.fullmatch(r"#[0-9A-F]{6}", variables[variable]), (name, variable)
            for base, tint in (
                (surface, highlight_tint(surface, primary)),
                (panel, highlight_tint(panel, primary)),
            ):
                assert MIN_HIGHLIGHT_TINT <= tint <= MAX_HIGHLIGHT_TINT, (name, tint)
            assert variables["highlight"] != surface.hex6, name
            assert variables["highlight-panel"] != panel.hex6, name

    def test_light_themes_stay_light_and_dark_themes_dark(self):
        app = _ThemedApp()
        for name, theme in app.available_themes.items():
            app.theme = name
            variables = app.get_css_variables()
            surface, highlight, text = (
                _luminance(variables[key]) for key in ("surface", "highlight", "text")
            )
            if theme.dark:
                assert surface < 0.5 and text > surface and text > highlight, name
            else:
                assert surface > 0.5 and text < surface and text < highlight, name


class TestResolvePanelTheme:
    @pytest.mark.parametrize("name", ["ansi-dark", "ansi-light", "no-such-theme", None])
    def test_unsupported_names_use_the_default_theme(self, name):
        assert resolve_panel_theme(name) == resolve_panel_theme(DEFAULT_THEME_NAME)

    def test_text_colours_read_on_every_pane_background(self):
        for name, theme in BUILTIN_THEMES.items():
            if name in ANSI_THEMES:
                continue
            panel_theme = resolve_panel_theme(name)
            backgrounds = [
                Color.parse(value)
                for value in (
                    panel_theme.background,
                    panel_theme.surface,
                    panel_theme.panel,
                    panel_theme.empty_bg,
                )
            ]
            for colour in (panel_theme.text_muted, panel_theme.text_accent):
                for background in backgrounds:
                    ratio = contrast_ratio(Color.parse(colour), background)
                    assert ratio >= MIN_CONTRAST, (name, colour, background.hex6, ratio)
            for fg, bg in (
                (panel_theme.badge_active_fg, panel_theme.badge_active_bg),
                (panel_theme.badge_inactive_fg, panel_theme.badge_inactive_bg),
                (panel_theme.label_active_fg, panel_theme.label_active_bg),
                (panel_theme.label_inactive_fg, panel_theme.label_inactive_bg),
                (panel_theme.empty_fg, panel_theme.empty_bg),
                (panel_theme.foreground, panel_theme.panel),
            ):
                ratio = contrast_ratio(Color.parse(fg), Color.parse(bg))
                assert ratio >= MIN_CONTRAST, (name, fg, bg, ratio)


class TestResolveTablePalette:
    def test_every_offered_theme_reads_on_surface_panel_and_highlights(self):
        app = _ThemedApp()
        for theme in app.available_themes:
            app.theme = theme
            variables = app.get_css_variables()
            palette = resolve_table_palette(variables)
            for name in ("success", "yellow", "muted", "primary", "danger"):
                colour = Color.parse(getattr(palette, name))
                for background in highlight_backgrounds(variables):
                    ratio = contrast_ratio(colour, background)
                    assert ratio >= MIN_CONTRAST, (theme, name, background.hex6, ratio)

    def test_attention_colour_stays_yellow_in_every_theme(self):
        app = _ThemedApp()
        for theme in app.available_themes:
            app.theme = theme
            colour = Color.parse(resolve_table_palette(app.get_css_variables()).yellow)
            hue = colour.hsl.h * 360
            assert 35 <= hue <= 65, (theme, colour.hex)

    def test_live_colour_stays_green_in_every_theme(self):
        app = _ThemedApp()
        for theme in app.available_themes:
            app.theme = theme
            colour = Color.parse(resolve_table_palette(app.get_css_variables()).success)
            hue = colour.hsl.h * 360
            assert 95 <= hue <= 130, (theme, colour.hex)

    def test_missing_variables_fall_back(self):
        palette = resolve_table_palette({})
        assert palette.success.startswith("#")
        assert palette.muted.startswith("#")


class TestSortConstants:
    def test_sort_column_names(self):
        assert list(_SORT_COLUMN_NAMES.values()) == [
            "Repository",
            "Needs attention",
            "Branch",
            "Last commit",
            "Sessions",
        ]


def test_pending_shares_the_running_green():
    from gitdirector.commands.tui.constants import _SESSION_STATUS_ORDER, resolve_table_palette

    palette = resolve_table_palette(
        {"surface": "#1e1e2e", "primary": "#89b4fa", "foreground": "#cdd6f4"}
    )
    assert palette.pending == palette.success
    assert PALETTE.pending == PALETTE.success
    # Busy first: waiting, running, pending, idle.
    assert sorted(_SESSION_STATUS_ORDER, key=_SESSION_STATUS_ORDER.get) == [
        "waiting",
        "running",
        "pending",
        "idle",
    ]
