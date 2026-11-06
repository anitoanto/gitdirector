from rich.console import Console
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.strip import Strip

import gitdirector.commands.tui  # noqa: F401  (installs the output filter)
from gitdirector.commands import fit_left, print_rows
from gitdirector.terminal_text import printable

HOSTILE = "a\x1b[2Jb\x1b]52;c;Zm9v\x07c\x9b31md\x7fe‮f⁦g\r\nh\ti"


def test_printable_drops_controls_and_bidi_overrides():
    assert printable(HOSTILE) == "a[2Jb]52;c;Zm9vc31mdefghi"
    assert printable("നിങ്ങൾ 日本 👍") == "നിങ്ങൾ 日本 👍"


def test_textual_writes_no_control_characters_from_text():
    strip = Strip([Segment(HOSTILE, Style(bold=True)), Segment("\x1bplain\x07")])
    output = strip.render(Console(color_system="truecolor"))
    assert "\x1b[2J" not in output and "\x07" not in output and "\x9b" not in output
    assert "a[2Jb" in output and "plain" in output
    assert "\x1b[1m" in output


def test_cli_tables_print_no_control_characters(capsys):
    styled = Text("ok\x1b]52;c;x\x07", style="bold")
    styled.append("\x1b[31mred", style="red")
    print_rows(["NAME", "DESCRIPTION"], [[HOSTILE, styled]])
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out
    assert "a[2Jb" in out and "ok]52;c;x" in out and "[31mred" in out


def test_fit_left_counts_cells():
    path = "/home/u/プロジェクト/日本語のリポジトリ/src"
    for width in range(1, 40):
        fitted = fit_left(path, width)
        assert Text(fitted).cell_len <= width
        assert fitted.endswith("src") or width < 4
