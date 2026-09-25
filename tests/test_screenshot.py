"""``gd-screenshot``: drawing a captured tmux screen into a PNG."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from PIL import Image

from gitdirector.cli import cli
from gitdirector.integrations.tmux.core import ScreenCapture
from gitdirector.screenshot import FONT_SIZE, PADDING, _Fonts, _parse, render_png, terminal_theme

THEME = terminal_theme("#101010", "#e0e0e0")


def _screen(*lines: str, width: int = 12, height: int = 3, cursor=None) -> ScreenCapture:
    padded = list(lines) + [""] * (height - len(lines))
    return ScreenCapture(lines=padded, width=width, height=height, cursor=cursor)


def _cell_box() -> tuple[int, int]:
    fonts = _Fonts(FONT_SIZE)
    ascent, descent = fonts.regular.getmetrics()
    return max(1, round(fonts.regular.getlength("M"))), ascent + descent + 2


def _cell_centre(x: int, y: int) -> tuple[int, int]:
    cell_w, cell_h = _cell_box()
    return PADDING + x * cell_w + cell_w // 2, PADDING + y * cell_h + 1


class TestParse:
    def test_colours_and_attributes_land_on_their_cells(self):
        grid = _parse(_screen("a\x1b[1;31mB\x1b[0m\x1b[44m c\x1b[0m"))

        assert [cell.char for cell in grid[0][:4]] == ["a", "B", " ", "c"]
        assert grid[0][1].style.bold and grid[0][1].style.color.number == 1
        assert grid[0][2].style.bgcolor.number == 4
        assert grid[0][0].style.color is None

    def test_wide_characters_take_two_cells(self):
        grid = _parse(_screen("你x"))

        assert (grid[0][0].char, grid[0][0].width) == ("你", 2)
        assert grid[0][1].width == 0
        assert grid[0][2].char == "x"

    def test_lines_longer_than_the_pane_are_cut(self):
        grid = _parse(_screen("x" * 20, width=5))

        assert len(grid[0]) == 5


class TestRender:
    def test_image_is_the_pane_size_in_cells(self, tmp_path):
        cell_w, cell_h = _cell_box()

        size = render_png(_screen(width=40, height=10), tmp_path / "s.png", THEME)

        assert size == (PADDING * 2 + 40 * cell_w, PADDING * 2 + 10 * cell_h)
        with Image.open(tmp_path / "s.png") as image:
            assert image.format == "PNG" and image.size == size
            assert image.getpixel((1, 1)) == (16, 16, 16)

    def test_backgrounds_and_cursor_are_painted(self, tmp_path):
        render_png(
            _screen("\x1b[41m  \x1b[0m", "\x1b[48;2;1;2;3m \x1b[0m", cursor=(5, 2)),
            tmp_path / "s.png",
            THEME,
        )

        with Image.open(tmp_path / "s.png") as image:
            assert image.getpixel(_cell_centre(1, 0)) == (205, 49, 49)
            assert image.getpixel(_cell_centre(0, 1)) == (1, 2, 3)
            # The cursor is drawn as an inverted cell: foreground on background.
            assert image.getpixel(_cell_centre(5, 2)) == (224, 224, 224)
            assert image.getpixel(_cell_centre(6, 2)) == (16, 16, 16)

    def test_reverse_video_swaps_colours(self, tmp_path):
        render_png(_screen("\x1b[7m \x1b[0m"), tmp_path / "s.png", THEME)

        with Image.open(tmp_path / "s.png") as image:
            assert image.getpixel(_cell_centre(0, 0)) == (224, 224, 224)


class TestCommand:
    @pytest.fixture
    def capture(self, monkeypatch):
        fake = MagicMock(return_value=_screen("hello", cursor=(5, 0)))
        monkeypatch.setattr("gitdirector.integrations.tmux.capture_screen", fake)
        return fake

    def test_saves_the_png_and_prints_its_path(self, capture, tmp_path):
        target = tmp_path / "shot.png"

        result = CliRunner().invoke(cli, ["gd-screenshot", "gd/web_aaaaa/shell/1", str(target)])

        assert result.exit_code == 0, result.output
        assert result.stdout == f"{target}\n"
        capture.assert_called_once_with("gd/web_aaaaa/shell/1")
        with Image.open(target) as image:
            assert image.format == "PNG"

    def test_path_must_be_a_png(self, capture, tmp_path):
        result = CliRunner().invoke(
            cli, ["gd-screenshot", "gd/web_aaaaa/shell/1", str(tmp_path / "shot.jpg")]
        )

        assert result.exit_code == 2
        assert "PATH must end in .png" in result.output
        capture.assert_not_called()

    def test_path_is_required(self, capture):
        result = CliRunner().invoke(cli, ["gd-screenshot", "gd/web_aaaaa/shell/1"])

        assert result.exit_code == 2
        assert "Missing argument 'PATH'" in result.output

    def test_directory_must_exist(self, capture, tmp_path):
        result = CliRunner().invoke(
            cli, ["gd-screenshot", "gd/web_aaaaa/shell/1", str(tmp_path / "no" / "s.png")]
        )

        assert result.exit_code == 1
        assert "directory does not exist" in result.stderr
        capture.assert_not_called()

    def test_session_not_running(self, capture, tmp_path):
        capture.return_value = None

        result = CliRunner().invoke(
            cli, ["gd-screenshot", "gd/web_aaaaa/shell/1", str(tmp_path / "s.png")]
        )

        assert result.exit_code == 1
        assert "is not running" in result.stderr
        assert not (tmp_path / "s.png").exists()


class TestFonts:
    def test_every_attribute_and_script_renders(self, tmp_path):
        line = "\x1b[1mB\x1b[2md\x1b[4mu\x1b[9ms\x1b[8mh\x1b[0m ┌─┐ 你好 ✓"

        render_png(_screen(line, width=20), tmp_path / "s.png", THEME)

        assert (tmp_path / "s.png").stat().st_size > 0

    def test_a_missing_glyph_comes_from_a_fallback_font(self, monkeypatch):
        fonts = _Fonts(FONT_SIZE)
        found = object()
        monkeypatch.setattr(fonts, "_lacks", lambda font, _char: font is not found)
        monkeypatch.setattr(fonts, "_find_fallback", lambda _char: found)

        assert fonts.for_char("你", bold=False) is found
        assert fonts.for_char("a", bold=False) is fonts.regular

    def test_without_a_monospace_font_pillows_own_is_used(self, monkeypatch, tmp_path):
        monkeypatch.setattr("gitdirector.screenshot._font_candidates", lambda: iter(()))

        size = render_png(_screen("still a grid", width=12, height=1), tmp_path / "s.png", THEME)

        assert size[0] > 0 and (tmp_path / "s.png").exists()

    def test_fallback_search_skips_fonts_that_cannot_load(self, monkeypatch):
        monkeypatch.setattr("gitdirector.screenshot._fc_match", lambda _pattern: None)
        monkeypatch.setattr("gitdirector.screenshot._FALLBACK_FONTS", ("/nonexistent.ttf",))

        assert _Fonts(FONT_SIZE)._find_fallback("你") is None
