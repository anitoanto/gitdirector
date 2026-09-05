from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.color import Color
    from textual.theme import Theme

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
    # Imported lazily: this module is reached from plain CLI commands through
    # the tmux integration, and loading Textual there is a visible startup cost.
    from textual.theme import BUILTIN_THEMES

    if theme_name and theme_name in BUILTIN_THEMES:
        return BUILTIN_THEMES[theme_name]
    return BUILTIN_THEMES.get(DEFAULT_THEME_NAME, next(iter(BUILTIN_THEMES.values())))


def _parse_color(value: str | None, fallback: str) -> Color:
    from textual.color import Color

    return Color.parse(value or fallback)


def _hex(color: Color) -> str:
    return color.hex6


def _relative_luminance(color: Color) -> float:
    def channel(value: int) -> float:
        scaled = value / 255
        return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color.r) + 0.7152 * channel(color.g) + 0.0722 * channel(color.b)


def contrast_ratio(foreground: Color, background: Color) -> float:
    """WCAG contrast ratio between two opaque colours (1.0 to 21.0)."""
    lighter = _relative_luminance(foreground)
    darker = _relative_luminance(background)
    if lighter < darker:
        lighter, darker = darker, lighter
    return (lighter + 0.05) / (darker + 0.05)


def readable_on(color: Color, *backgrounds: Color, minimum: float = 4.5) -> Color:
    """Return *color*, pushed toward black or white until it reads on every background.

    The hue is kept; only lightness moves, and only as far as needed, so a
    theme's green stays green on a light and on a dark surface. Both
    directions are tried because a mid-tone highlight can sit between two
    backgrounds; the one that clears *minimum* on all of them wins, and
    otherwise the best achievable candidate is returned. ANSI colours are
    returned untouched because their real value is the terminal's.
    """
    from textual.color import Color

    def worst(candidate: Color) -> float:
        return min(contrast_ratio(candidate, background) for background in backgrounds)

    if color.ansi is not None or not backgrounds or worst(color) >= minimum:
        return color

    average_luminance = sum(_relative_luminance(b) for b in backgrounds) / len(backgrounds)
    poles = [Color(255, 255, 255), Color(0, 0, 0)]
    if average_luminance > 0.5:
        poles.reverse()

    best = color
    best_ratio = worst(color)
    for pole in poles:
        for step in range(1, 21):
            candidate = color.blend(pole, step * 0.05)
            ratio = worst(candidate)
            if ratio >= minimum:
                return candidate
            if ratio > best_ratio:
                best, best_ratio = candidate, ratio
    return best


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
