from __future__ import annotations

from dataclasses import dataclass

from textual.color import Color
from textual.theme import BUILTIN_THEMES, Theme


DEFAULT_THEME_NAME = "rose-pine"


@dataclass(frozen=True)
class PanelTheme:
    background: str
    foreground: str
    surface: str
    panel: str
    primary: str
    secondary: str
    accent: str
    badge_active_bg: str
    badge_active_fg: str
    badge_inactive_bg: str
    badge_inactive_fg: str
    label_active_bg: str
    label_active_fg: str
    label_inactive_bg: str
    label_inactive_fg: str
    empty_bg: str
    empty_fg: str
    border_active: str
    border_inactive: str


def _resolve_theme(theme_name: str | None) -> Theme:
    if theme_name and theme_name in BUILTIN_THEMES:
        return BUILTIN_THEMES[theme_name]
    return BUILTIN_THEMES.get(DEFAULT_THEME_NAME, next(iter(BUILTIN_THEMES.values())))


def _parse_color(value: str | None, fallback: str) -> Color:
    return Color.parse(value or fallback)


def _hex(color: Color) -> str:
    return color.hex6


def resolve_panel_theme(theme_name: str | None) -> PanelTheme:
    theme = _resolve_theme(theme_name)
    fallback_foreground = "#F5F5F5" if theme.dark else "#1A1A1A"
    fallback_background = "#1B1B1B" if theme.dark else "#F5F5F5"

    primary = _parse_color(theme.primary, "#5FD7FF")
    secondary = _parse_color(theme.secondary, theme.primary)
    accent = _parse_color(theme.accent, theme.primary)
    foreground = _parse_color(theme.foreground, fallback_foreground)
    background = _parse_color(theme.background, fallback_background)
    surface = _parse_color(theme.surface, _hex(background.blend(primary, 0.1)))
    panel = _parse_color(theme.panel, _hex(surface.blend(background, 0.35)))

    badge_active_bg = primary
    badge_inactive_bg = panel.blend(primary, 0.18)
    label_active_bg = secondary.blend(accent, 0.22)
    label_inactive_bg = surface.blend(panel, 0.45)
    empty_bg = panel.blend(background, 0.2 if theme.dark else 0.08)
    border_active = accent.blend(primary, 0.45)
    border_inactive = panel.blend(foreground, 0.18 if theme.dark else 0.3)

    return PanelTheme(
        background=_hex(background),
        foreground=_hex(foreground),
        surface=_hex(surface),
        panel=_hex(panel),
        primary=_hex(primary),
        secondary=_hex(secondary),
        accent=_hex(accent),
        badge_active_bg=_hex(badge_active_bg),
        badge_active_fg=_hex(badge_active_bg.get_contrast_text()),
        badge_inactive_bg=_hex(badge_inactive_bg),
        badge_inactive_fg=_hex(badge_inactive_bg.get_contrast_text()),
        label_active_bg=_hex(label_active_bg),
        label_active_fg=_hex(label_active_bg.get_contrast_text()),
        label_inactive_bg=_hex(label_inactive_bg),
        label_inactive_fg=_hex(label_inactive_bg.get_contrast_text()),
        empty_bg=_hex(empty_bg),
        empty_fg=_hex(empty_bg.get_contrast_text()),
        border_active=_hex(border_active),
        border_inactive=_hex(border_inactive),
    )