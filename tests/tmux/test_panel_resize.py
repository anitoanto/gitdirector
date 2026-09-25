"""Panels keep their proportions through window resizes, inside tmux."""

from __future__ import annotations

import shutil
import time
from types import SimpleNamespace

import pytest

from gitdirector.commands.tui.panels import _PANEL_LAYOUTS, PanePlacement
from gitdirector.integrations.tmux import panels as P
from gitdirector.integrations.tmux.core import _run_tmux

from ._shared import _cleanup_tmux_tmpdir, _make_short_tmux_tmpdir, _tmux_integration_lock


class TestParseTmuxLayout:
    def test_nested_splits(self):
        root = P._parse_tmux_layout(
            "4edd,120x30,0,0{80x30,0,0,0,39x30,81,0[39x15,81,0,1,39x14,81,16,2]}"
        )
        assert root.axis == "x"
        assert root.children[0].pane == 0
        right = root.children[1]
        assert right.axis == "y"
        assert [(leaf.pane, leaf.x, leaf.y) for leaf in right.children] == [(1, 81, 0), (2, 81, 16)]

    def test_single_pane(self):
        assert P._parse_tmux_layout("b25d,80x24,0,0,5").pane == 5

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            P._parse_tmux_layout("xxxx,oops")


def _layout(rows, cols, cells):
    placements = tuple(
        PanePlacement(pane_index=i, row=r, col=c, row_span=rs, col_span=cs)
        for i, (r, c, rs, cs) in enumerate(cells, start=1)
    )
    return SimpleNamespace(rows=rows, cols=cols, placements=placements)


class TestPanelResizeCommands:
    def test_sizes_every_child_but_the_last_top_down(self):
        layout = _layout(2, 3, [(0, 0, 2, 2), (0, 2, 1, 1), (1, 2, 1, 1)])
        commands = P._panel_resize_commands(
            "abcd,120x30,0,0{80x30,0,0,0,39x30,81,0[39x15,81,0,1,39x14,81,16,2]}",
            ["%0", "%1", "%2"],
            layout,
        )
        assert commands == [
            ["resize-pane", "-t", "%0", "-x", "67%"],
            ["resize-pane", "-t", "%1", "-y", "50%"],
        ]

    def test_single_pane_needs_nothing(self):
        assert (
            P._panel_resize_commands("b25d,80x24,0,0,5", ["%5"], _layout(1, 1, [(0, 0, 1, 1)]))
            == []
        )

    def test_unknown_panes_give_no_plan(self):
        assert (
            P._panel_resize_commands("b25d,80x24,0,0,5", ["%9"], _layout(1, 1, [(0, 0, 1, 1)]))
            is None
        )


def _geometry(session: str) -> dict[str, tuple[int, ...]]:
    out = _run_tmux(
        ["list-panes", "-t", f"={session}:^", "-F", "#{pane_id} #{pane_width} #{pane_height}"],
        text=True,
    ).stdout
    return {line.split()[0]: tuple(map(int, line.split()[1:])) for line in out.splitlines()}


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux required")
def test_every_layout_keeps_its_slots_and_proportions(tmp_path, monkeypatch):
    with _tmux_integration_lock():
        tmux_dir = _make_short_tmux_tmpdir()
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("TMUX_TMPDIR", str(tmux_dir))
        monkeypatch.delenv("TMUX", raising=False)
        try:
            for key, layout in _PANEL_LAYOUTS.items():
                session = f"gd/panel/t-{key.replace('_', '-')}"
                _run_tmux(
                    ["new-session", "-d", "-s", session, "-x", "120", "-y", "40", "cat"],
                    check=True,
                )
                _run_tmux(["set-option", "-t", f"={session}:", "window-size", "manual"])
                built = P._build_panel_layout(session, layout.rows, layout.cols, key)
                by_slot = P._equalize_panel_layout(session, built, layout)
                P._configure_panel_window(session, by_slot, {}, "rose-pine")

                for slot, pane_id in enumerate(by_slot, start=1):
                    _run_tmux(
                        [
                            "run-shell",
                            "-t",
                            f"={session}:^",
                            "-C",
                            f"select-pane -t '{P._slot_pane_format(slot)}'",
                        ]
                    )
                    active = _run_tmux(
                        ["display-message", "-p", "-t", f"={session}:^", "#{pane_id}"], text=True
                    ).stdout.strip()
                    assert active == pane_id, f"{key}: prefix+{slot} selected {active}"

                P._install_panel_resize_hook(session, by_slot, layout)
                for width, height in ((200, 60), (90, 30)):
                    _run_tmux(
                        [
                            "resize-window",
                            "-t",
                            f"={session}:^",
                            "-x",
                            str(width),
                            "-y",
                            str(height),
                        ]
                    )
                    P._equalize_panel_layout(session, by_slot, layout)
                    exact = _geometry(session)
                    # Back to a different size and forward again, through the hook alone.
                    _run_tmux(["resize-window", "-t", f"={session}:^", "-x", "150", "-y", "50"])
                    _run_tmux(
                        [
                            "resize-window",
                            "-t",
                            f"={session}:^",
                            "-x",
                            str(width),
                            "-y",
                            str(height),
                        ]
                    )
                    deadline = time.monotonic() + 3
                    while True:
                        hooked = _geometry(session)
                        error = max(
                            abs(a - b) for pane in exact for a, b in zip(exact[pane], hooked[pane])
                        )
                        if error <= 2 or time.monotonic() > deadline:
                            break
                        time.sleep(0.05)
                    assert error <= 2, f"{key} at {width}x{height}: {hooked} != {exact}"
                _run_tmux(["kill-session", "-t", f"={session}"])
        finally:
            _run_tmux(["kill-server"])
            _cleanup_tmux_tmpdir(tmux_dir)


class TestPanesVanishingMidBuild:
    """A pane closed while its panel is laid out ends the build cleanly."""

    def test_a_missing_pane_is_a_tmux_error(self, monkeypatch):
        from gitdirector.commands.tui.panels import resolve_panel_layout
        from gitdirector.integrations.tmux.core import TmuxError

        monkeypatch.setattr(P, "_tmux_output", lambda *args: "80 24")
        layout = resolve_panel_layout("grid_2x2")
        with pytest.raises(TmuxError, match="went away"):
            P._equalize_panel_layout("gd/build/x", ["%1", "%2"], layout)
