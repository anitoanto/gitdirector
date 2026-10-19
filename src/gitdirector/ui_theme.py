from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.color import Color
    from textual.theme import Theme

DEFAULT_THEME_NAME = "rose-pine"

MIN_CONTRAST = 4.5
# The console's highlight (cursor row, picked option, active tab) is the
# surface tinted toward the primary colour: 30% when the theme can afford
# it, less on a bright panel where that much would leave coloured text no
# room to read (white or black must keep 6:1 on it), never under 10%, with
# hover at 40% of the highlight. CSS gets the results as `$highlight`,
# `$highlight-hover`, `$highlight-panel` and `$highlight-panel-hover`.
MAX_HIGHLIGHT_TINT = 0.30
MIN_HIGHLIGHT_TINT = 0.10
HOVER_SHARE = 0.4
_HIGHLIGHT_HEADROOM = 6.0

# A theme is only usable when each of these resolves to a concrete, opaque
# RGB colour: the contrast checks below need real values.
_CORE_COLOURS = (
    "background",
    "surface",
    "panel",
    "foreground",
    "primary",
    "secondary",
    "accent",
    "success",
    "warning",
    "error",
)
# Theme colours the console shows as text, and the generated variable that
# carries the readable version of each.
_TEXT_VARIANTS = {
    "text-primary": "primary",
    "text-secondary": "secondary",
    "text-accent": "accent",
    "text-success": "success",
    "text-warning": "warning",
    "text-error": "error",
}


@dataclass(frozen=True)
class PanelTheme:
    background: str
    foreground: str
    surface: str
    panel: str
    primary: str
    secondary: str
    accent: str
    text_muted: str
    text_accent: str
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


def theme_is_supported(theme: Theme) -> bool:
    """Whether the console can render *theme* with verified contrast.

    An ANSI theme names slots of the terminal's own palette ("ansi_blue",
    "ansi_default"), so its real colours are unknown: alpha tints collapse to
    solid blocks, nothing can be checked for contrast, and "light" cannot be
    honoured on a dark terminal. Only themes made of concrete RGB values pass.
    """
    from textual.color import Color

    if theme.ansi:
        return False
    variables = theme.to_color_system().generate()
    for name in _CORE_COLOURS:
        try:
            colour = Color.parse(variables.get(name, ""))
        except Exception:
            return False
        if colour.ansi is not None or colour.auto or colour.a < 1:
            return False
    return True


def supported_theme_name(
    theme_name: str | None,
    themes: Mapping[str, Theme] | None = None,
    fallback: str = DEFAULT_THEME_NAME,
) -> str:
    """*theme_name* when it is a supported theme in *themes*, else *fallback*.

    *themes* defaults to Textual's built-in themes.
    """
    if themes is None:
        from textual.theme import BUILTIN_THEMES

        themes = BUILTIN_THEMES
    theme = themes.get(theme_name or "")
    if theme is not None and theme_is_supported(theme):
        return theme_name  # type: ignore[return-value]
    return fallback


class SupportedThemes:
    """Mixin for a Textual ``App``: offer only supported themes, with readable colours.

    Unsupported themes leave the registry (and so the command palette's
    theme picker) as the app is built; ``apply_theme`` resolves a configured
    name to one that is offered; and every text colour the CSS variables
    expose is pushed to :data:`MIN_CONTRAST` on every surface it can sit on.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        for name, theme in self.available_themes.items():  # type: ignore[attr-defined]
            if not theme_is_supported(theme):
                self.unregister_theme(name)  # type: ignore[attr-defined]

    def apply_theme(self, theme_name: str | None) -> str:
        """Switch to *theme_name* when it is offered, else to the default; return the result."""
        themes = self.available_themes  # type: ignore[attr-defined]
        fallback = DEFAULT_THEME_NAME if DEFAULT_THEME_NAME in themes else self.theme  # type: ignore[attr-defined]
        name = supported_theme_name(theme_name, themes, fallback)
        if name != self.theme:  # type: ignore[attr-defined]
            self.theme = name  # type: ignore[attr-defined]
        return name

    def get_css_variables(self) -> dict[str, str]:
        variables = readable_css_variables(super().get_css_variables())  # type: ignore[misc]
        self.theme_variables = variables
        return variables


def _resolve_theme(theme_name: str | None) -> Theme:
    # Imported lazily: this module is reached from plain CLI commands through
    # the tmux integration, and loading Textual there is a visible startup cost.
    from textual.theme import BUILTIN_THEMES

    return BUILTIN_THEMES[supported_theme_name(theme_name)]


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


def readable_on(color: Color, *backgrounds: Color, minimum: float = MIN_CONTRAST) -> Color:
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


def _css_colour(value: str, over: Color) -> Color:
    """The opaque colour a CSS value paints over *over* (``auto 60%``, ``#abc 50%``...)."""
    from textual.color import Color

    parts = value.split()
    alpha = 1.0
    if len(parts) == 2 and parts[1].endswith("%"):
        alpha = float(parts[1][:-1]) / 100
        value = parts[0]
    if value == "auto":
        colour = over.get_contrast_text(alpha)
    else:
        colour = Color.parse(value)
        if alpha < 1:
            colour = colour.with_alpha(alpha)
    return over + colour


def _is_light(color: Color) -> bool:
    return _relative_luminance(color) > 0.5


def contrast_text(background: Color) -> Color:
    """White or black, whichever reads better on *background* (always at least 4.5:1)."""
    from textual.color import Color

    white, black = Color(255, 255, 255), Color(0, 0, 0)
    return (
        white if contrast_ratio(white, background) >= contrast_ratio(black, background) else black
    )


def highlight_tint(base: Color, primary: Color, light: bool | None = None) -> float:
    """How far *base* can move toward *primary* and still leave room for text."""
    from textual.color import Color

    pole = Color(0, 0, 0) if (_is_light(base) if light is None else light) else Color(255, 255, 255)
    tint = MAX_HIGHLIGHT_TINT
    while tint > MIN_HIGHLIGHT_TINT:
        if contrast_ratio(pole, base.blend(primary, tint)) >= _HIGHLIGHT_HEADROOM:
            break
        tint = round(tint - 0.05, 2)
    return tint


def highlight_backgrounds(variables: Mapping[str, str]) -> tuple[Color, Color, Color, Color]:
    """Every background text can sit on: surface, panel, and the highlight on each."""
    from textual.color import Color

    surface = Color.parse(variables["surface"])
    panel = _css_colour(variables["panel"], surface)
    primary = Color.parse(variables["primary"])
    light = _is_light(surface)
    return (
        surface,
        panel,
        surface.blend(primary, highlight_tint(surface, primary, light)),
        panel.blend(primary, highlight_tint(panel, primary, light)),
    )


def readable_css_variables(variables: Mapping[str, str]) -> dict[str, str]:
    """Return *variables* with the console's highlight and readable text colours.

    ``$highlight`` and ``$highlight-panel`` (and their ``-hover`` forms) are
    the primary tint each background can afford. ``$text`` and
    ``$text-muted`` are resolved over the surface and, like the
    ``$text-<colour>`` variants, nudged until they clear :data:`MIN_CONTRAST`
    on the surface, the panel and the highlight of each; the footer's text
    colours are checked against the footer's own background. Other
    backgrounds and borders are left as the theme set them.
    """
    try:
        backgrounds = highlight_backgrounds(variables)
    except Exception:
        return dict(variables)
    surface, panel, highlight, highlight_panel = backgrounds
    readable = dict(variables)
    readable["highlight"] = _hex(highlight)
    readable["highlight-hover"] = _hex(surface.blend(highlight, HOVER_SHARE))
    readable["highlight-panel"] = _hex(highlight_panel)
    readable["highlight-panel-hover"] = _hex(panel.blend(highlight_panel, HOVER_SHARE))

    def harden(name: str, value: str, *on: Color) -> None:
        try:
            colour = _css_colour(value, on[0])
        except Exception:
            return
        if colour.ansi is not None:
            return
        readable[name] = _hex(readable_on(colour, *on))

    for name in ("text", "text-muted"):
        if name in variables:
            harden(name, variables[name], *backgrounds)
    for name, source in _TEXT_VARIANTS.items():
        if source in variables:
            harden(name, variables[source], *backgrounds)

    def over(name: str, base: Color) -> Color:
        try:
            return _css_colour(variables.get(name, "transparent"), base)
        except Exception:
            return base

    footer = over("footer-background", panel)
    hover = over("block-hover-background", footer)
    harden("footer-foreground", variables.get("footer-foreground", "auto"), footer, hover)
    harden(
        "footer-key-foreground",
        variables.get("footer-key-foreground", "auto"),
        over("footer-key-background", footer),
        hover,
    )
    harden(
        "footer-description-foreground",
        variables.get("footer-description-foreground", "auto"),
        over("footer-description-background", footer),
        hover,
    )
    return readable


@lru_cache(maxsize=8)
def textual_surface(theme_name: str | None) -> str | None:
    """The ``$surface`` colour a Textual app paints with this theme; None when transparent."""
    surface = _resolve_theme(theme_name).to_color_system().generate().get("surface", "")
    return surface if surface.startswith("#") else None


@lru_cache(maxsize=8)
def resolve_panel_theme(theme_name: str | None) -> PanelTheme:
    from textual.color import Color

    theme = _resolve_theme(theme_name)
    variables = readable_css_variables(theme.to_color_system().generate())
    parse = Color.parse

    primary = parse(variables["primary"])
    secondary = parse(variables["secondary"])
    accent = parse(variables["accent"])
    foreground = parse(variables["foreground"])
    background = parse(variables["background"])
    surface = parse(variables["surface"])
    panel = parse(variables["panel"])

    badge_active_bg = primary
    badge_inactive_bg = panel.blend(primary, 0.18)
    label_active_bg = secondary.blend(accent, 0.22)
    label_inactive_bg = surface.blend(panel, 0.45)
    empty_bg = panel.blend(background, 0.2 if theme.dark else 0.08)
    border_active = accent.blend(primary, 0.45)
    border_inactive = panel.blend(foreground, 0.18 if theme.dark else 0.3)
    text_backgrounds = (background, surface, panel, empty_bg)
    # Status lines and placeholders write the foreground on the panel.
    foreground = readable_on(foreground, *text_backgrounds)

    return PanelTheme(
        background=_hex(background),
        foreground=_hex(foreground),
        surface=_hex(surface),
        panel=_hex(panel),
        primary=_hex(primary),
        secondary=_hex(secondary),
        accent=_hex(accent),
        text_muted=_hex(readable_on(parse(variables["text-muted"]), *text_backgrounds)),
        text_accent=_hex(readable_on(parse(variables["text-accent"]), *text_backgrounds)),
        badge_active_bg=_hex(badge_active_bg),
        badge_active_fg=_hex(contrast_text(badge_active_bg)),
        badge_inactive_bg=_hex(badge_inactive_bg),
        badge_inactive_fg=_hex(contrast_text(badge_inactive_bg)),
        label_active_bg=_hex(label_active_bg),
        label_active_fg=_hex(contrast_text(label_active_bg)),
        label_inactive_bg=_hex(label_inactive_bg),
        label_inactive_fg=_hex(contrast_text(label_inactive_bg)),
        empty_bg=_hex(empty_bg),
        empty_fg=_hex(contrast_text(empty_bg)),
        border_active=_hex(border_active),
        border_inactive=_hex(border_inactive),
    )
