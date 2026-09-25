from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ...ui_theme import resolve_panel_theme
from .core import (
    _FIRST_WINDOW,
    PANEL_SLOT_OPTION,
    TmuxError,
    _chain_tmux_commands,
    _list_sessions,
    _panel_pane_title,
    _protect_session,
    _resolved_panel_theme_name,
    _run_tmux,
    _scrub_session_environment,
    _session_exists,
    _tmux_child_environment_command,
    _tmux_new_session_environment_args,
    _tmux_server_is_gone,
    kill_tmux_session,
    make_panel_session_name,
    respawn_pane,
    sync_panel_tmux_config,
)

logger = logging.getLogger(__name__)

_TMUX_FORK_RETRY_ATTEMPTS = 5


def _run_tmux_with_fork_retry(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Retry a tmux command that failed only because the server could not fork.

    Only for commands that create a pane: tmux discards a pane whose spawn
    failed. Never for ``respawn-pane`` (see :func:`~.core.respawn_pane`).
    """
    for attempt in range(_TMUX_FORK_RETRY_ATTEMPTS):
        result = _run_tmux(args, text=True)
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        if result.returncode == 0 or "fork failed" not in stderr.lower():
            break
        time.sleep(0.05 * (attempt + 1))
    return result


def _raise_for_tmux_result(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode == 0:
        return
    if _tmux_server_is_gone(result.stderr):
        raise TmuxError(
            "the tmux server exited while gitdirector was talking to it; "
            "any sessions it held are gone",
            args_list=list(result.args) if isinstance(result.args, list) else None,
            returncode=result.returncode,
            stderr=result.stderr,
        )
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def _panel_build_session_name(panel_name: str) -> str:
    digest = hashlib.sha1(panel_name.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return f"gd/build/{digest}-{os.getpid()}-{secrets.token_hex(3)}"


_PANEL_HELPER_OWNER = re.compile(
    r"^gd/(?:build/[0-9a-f]+-(\d+)-[0-9a-f]+|panel/.+_orphaned-(\d+)-\d+)$"
)
# One rebuild at a time: each swaps sessions under the panel's final name.
_REBUILD_LOCK = threading.Lock()


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reap_stale_panel_helpers() -> list[str]:
    """Kill panel build and orphaned sessions whose gitdirector process is gone.

    A rebuild removes both when it finishes or fails; they outlive it only
    when the process that started it died in between.
    """
    reaped: list[str] = []
    for session_name in _list_sessions():
        match = _PANEL_HELPER_OWNER.match(session_name)
        if match is None:
            continue
        owner = int(match.group(1) or match.group(2))
        if owner != os.getpid() and not _process_alive(owner) and kill_tmux_session(session_name):
            reaped.append(session_name)
    return reaped


def kill_panel_tmux_session(panel_name: str) -> bool:
    if not isinstance(panel_name, str) or not panel_name:
        raise ValueError("kill_panel_tmux_session requires a non-empty panel name")
    return kill_tmux_session(make_panel_session_name(panel_name))


def panel_tmux_session_exists(panel_name: str) -> bool:
    if not isinstance(panel_name, str) or not panel_name:
        raise ValueError("panel_tmux_session_exists requires a non-empty panel name")
    return _session_exists(make_panel_session_name(panel_name))


def _tmux_output(*args: str) -> str:
    result = _run_tmux_with_fork_retry(list(args))
    _raise_for_tmux_result(result)
    return result.stdout.strip()


def _list_window_panes_row_major(session_name: str) -> list[str]:
    output = _tmux_output(
        "list-panes",
        "-t",
        f"={session_name}:{_FIRST_WINDOW}",
        "-F",
        "#{pane_id}|#{pane_top}|#{pane_left}",
    )
    panes: list[tuple[int, int, str]] = []
    for line in output.splitlines():
        pane_id, pane_top, pane_left = line.split("|", 2)
        panes.append((int(pane_top), int(pane_left), pane_id))
    panes.sort(key=lambda item: (item[0], item[1]))
    return [pane_id for _, _, pane_id in panes]


def _find_panel_region_split(
    rows: int,
    cols: int,
    placements: tuple[tuple[int, int, int, int], ...],
) -> (
    tuple[
        str,
        int,
        tuple[tuple[int, int, int, int], ...],
        tuple[tuple[int, int, int, int], ...],
    ]
    | None
):
    for row_boundary in range(1, rows):
        top: list[tuple[int, int, int, int]] = []
        bottom: list[tuple[int, int, int, int]] = []
        for row, col, row_span, col_span in placements:
            if row + row_span <= row_boundary:
                top.append((row, col, row_span, col_span))
            elif row >= row_boundary:
                bottom.append((row - row_boundary, col, row_span, col_span))
            else:
                break
        else:
            if top and bottom:
                return ("rows", row_boundary, tuple(top), tuple(bottom))

    for col_boundary in range(1, cols):
        left: list[tuple[int, int, int, int]] = []
        right: list[tuple[int, int, int, int]] = []
        for row, col, row_span, col_span in placements:
            if col + col_span <= col_boundary:
                left.append((row, col, row_span, col_span))
            elif col >= col_boundary:
                right.append((row, col - col_boundary, row_span, col_span))
            else:
                break
        else:
            if left and right:
                return ("cols", col_boundary, tuple(left), tuple(right))

    return None


def _split_panel_region(
    target: str,
    rows: int,
    cols: int,
    placements: tuple[tuple[int, int, int, int], ...],
) -> None:
    if len(placements) <= 1:
        return

    split = _find_panel_region_split(rows, cols, placements)
    if split is None:
        raise ValueError(f"Unsupported panel layout region {rows}x{cols}: {placements}")

    axis, boundary, first_region, second_region = split
    if axis == "rows":
        second_size_pct = round(100 * (rows - boundary) / rows)
        second_target = _tmux_output(
            "split-window",
            "-v",
            "-l",
            f"{second_size_pct}%",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            target,
            "cat",
        )
        _split_panel_region(target, boundary, cols, first_region)
        _split_panel_region(second_target, rows - boundary, cols, second_region)
        return

    second_size_pct = round(100 * (cols - boundary) / cols)
    second_target = _tmux_output(
        "split-window",
        "-h",
        "-l",
        f"{second_size_pct}%",
        "-P",
        "-F",
        "#{pane_id}",
        "-t",
        target,
        "cat",
    )
    _split_panel_region(target, rows, boundary, first_region)
    _split_panel_region(second_target, rows, cols - boundary, second_region)


def _build_panel_layout(
    session_name: str,
    rows: int,
    cols: int,
    layout_key: str | None = None,
) -> list[str]:
    from ...commands.tui.panels import resolve_panel_layout

    layout = resolve_panel_layout(layout_key, rows, cols)
    root_target = f"={session_name}:{_FIRST_WINDOW}"
    placements = tuple(
        (placement.row, placement.col, placement.row_span, placement.col_span)
        for placement in layout.placements
    )
    _split_panel_region(root_target, layout.rows, layout.cols, placements)
    return _list_window_panes_row_major(session_name)


def _distribute_equal(total: int, parts: int) -> list[int]:
    base = total // parts
    remainder = total % parts
    return [base + (1 if i < remainder else 0) for i in range(parts)]


def _distribute_proportional(total: int, parts: int, ratios: tuple[int, ...] | None) -> list[int]:
    if not ratios or len(ratios) != parts:
        return _distribute_equal(total, parts)
    total_ratio = sum(ratios)
    if total_ratio == 0:
        return _distribute_equal(total, parts)

    sizes = [(total * r) // total_ratio for r in ratios]
    rem = total - sum(sizes)

    indices = sorted(range(parts), key=lambda i: ratios[i], reverse=True)
    for i in range(rem):
        sizes[indices[i % parts]] += 1
    return sizes


def _span_size(sizes: list[int], start: int, span: int) -> int:
    return sum(sizes[start : start + span]) + (span - 1)


def _layout_checksum(spec: str) -> int:
    csum = 0
    for ch in spec:
        csum = ((csum >> 1) | ((csum & 1) << 15)) & 0xFFFF
        csum = (csum + ord(ch)) & 0xFFFF
    return csum


def _build_layout_spec(
    placements: tuple[tuple[int, int, int, int], ...],
    pane_id_map: dict[tuple[int, int], int],
    row_heights: list[int],
    col_widths: list[int],
    x: int,
    y: int,
) -> str:
    if len(placements) == 1:
        p = placements[0]
        w = _span_size(col_widths, p[1], p[3])
        h = _span_size(row_heights, p[0], p[2])
        return f"{w}x{h},{x},{y},{pane_id_map[(p[0], p[1])]}"

    min_r = min(p[0] for p in placements)
    max_re = max(p[0] + p[2] for p in placements)
    min_c = min(p[1] for p in placements)
    max_ce = max(p[1] + p[3] for p in placements)
    reg_w = _span_size(col_widths, min_c, max_ce - min_c)
    reg_h = _span_size(row_heights, min_r, max_re - min_r)

    for rb in range(min_r + 1, max_re):
        top: list[tuple[int, int, int, int]] = []
        bot: list[tuple[int, int, int, int]] = []
        valid = True
        for p in placements:
            if p[0] + p[2] <= rb:
                top.append(p)
            elif p[0] >= rb:
                bot.append(p)
            else:
                valid = False
                break
        if valid and top and bot:
            top_h = _span_size(row_heights, min_r, rb - min_r)
            ts = _build_layout_spec(tuple(top), pane_id_map, row_heights, col_widths, x, y)
            bs = _build_layout_spec(
                tuple(bot), pane_id_map, row_heights, col_widths, x, y + top_h + 1
            )
            return f"{reg_w}x{reg_h},{x},{y}[{ts},{bs}]"

    for cb in range(min_c + 1, max_ce):
        left: list[tuple[int, int, int, int]] = []
        right: list[tuple[int, int, int, int]] = []
        valid = True
        for p in placements:
            if p[1] + p[3] <= cb:
                left.append(p)
            elif p[1] >= cb:
                right.append(p)
            else:
                valid = False
                break
        if valid and left and right:
            left_w = _span_size(col_widths, min_c, cb - min_c)
            ls = _build_layout_spec(tuple(left), pane_id_map, row_heights, col_widths, x, y)
            rs = _build_layout_spec(
                tuple(right), pane_id_map, row_heights, col_widths, x + left_w + 1, y
            )
            return f"{reg_w}x{reg_h},{x},{y}" + "{" + f"{ls},{rs}" + "}"

    raise ValueError(f"Unsupported panel layout region: {placements}")


def _equalize_panel_layout(
    session_name: str,
    pane_ids: list[str],
    layout: object,
) -> list[str]:
    """Apply *layout* at the window's current size; return pane ids in slot order.

    tmux ignores the pane ids in a custom layout and hands the window's
    panes to its cells in window order, so which pane ends up in which slot
    is read back from where each pane now sits.
    """
    window_target = f"={session_name}:{_FIRST_WINDOW}"
    dims = _tmux_output(
        "display-message", "-t", window_target, "-p", "#{window_width} #{window_height}"
    )
    try:
        window_w, window_h = (int(v) for v in dims.split())
    except ValueError as exc:
        raise TmuxError(f"could not read the size of {window_target}: {dims!r}") from exc

    sorted_placements = sorted(layout.placements, key=lambda p: (p.row, p.col))
    pane_id_map: dict[tuple[int, int], int] = {}
    for i, p in enumerate(sorted_placements):
        pane_id_map[(p.row, p.col)] = int(pane_ids[i].lstrip("%"))

    row_heights = _distribute_proportional(
        window_h - (layout.rows - 1), layout.rows, getattr(layout, "row_ratios", None)
    )
    col_widths = _distribute_proportional(
        window_w - (layout.cols - 1), layout.cols, getattr(layout, "col_ratios", None)
    )

    placements_tuples = tuple((p.row, p.col, p.row_span, p.col_span) for p in sorted_placements)
    spec = _build_layout_spec(placements_tuples, pane_id_map, row_heights, col_widths, 0, 0)
    checksum = _layout_checksum(spec)
    layout_string = f"{checksum:04x},{spec}"

    _run_tmux(["select-layout", "-t", window_target, layout_string], check=True)

    applied = _tmux_output("display-message", "-p", "-t", window_target, "#{window_layout}")
    pane_at = {
        (leaf.x, leaf.y): f"%{leaf.pane}" for leaf in _layout_leaves(_parse_tmux_layout(applied))
    }
    by_slot = []
    for placement in sorted(layout.placements, key=lambda p: p.pane_index):
        left = sum(col_widths[: placement.col]) + placement.col
        top = sum(row_heights[: placement.row]) + placement.row
        by_slot.append(pane_at.get((left, top)))
    if None in by_slot or len(set(by_slot)) != len(by_slot):
        raise ValueError(f"panel layout did not land as planned: {applied!r}")
    return by_slot


@dataclass
class _LayoutCell:
    """One cell of a tmux ``#{window_layout}``: a pane, or a split of cells."""

    x: int
    y: int
    pane: int | None = None
    #: ``"x"`` for a left-right split (``{...}``), ``"y"`` for top-bottom.
    axis: str | None = None
    children: list[_LayoutCell] = field(default_factory=list)


_LAYOUT_CELL_RE = re.compile(r"\d+x\d+,(\d+),(\d+)")


def _layout_leaves(node: _LayoutCell):
    if node.pane is not None:
        yield node
    for child in node.children:
        yield from _layout_leaves(child)


def _parse_tmux_layout(layout: str) -> _LayoutCell:
    """Parse ``csum,WxH,X,Y{...}`` / ``[...]`` / ``,ID`` into a cell tree."""
    text = layout.split(",", 1)[1]

    def cell(pos: int) -> tuple[_LayoutCell, int]:
        match = _LAYOUT_CELL_RE.match(text, pos)
        if match is None:
            raise ValueError(f"bad tmux layout at {pos}: {layout}")
        pos = match.end()
        x, y = int(match.group(1)), int(match.group(2))
        if text[pos] == ",":
            end = pos + 1
            while end < len(text) and text[end].isdigit():
                end += 1
            return _LayoutCell(x, y, pane=int(text[pos + 1 : end])), end
        closing = "}" if text[pos] == "{" else "]"
        node = _LayoutCell(x, y, axis="x" if closing == "}" else "y")
        pos += 1
        while True:
            child, pos = cell(pos)
            node.children.append(child)
            if text[pos] == closing:
                return node, pos + 1
            pos += 1

    root, _ = cell(0)
    return root


def _cumulative_fractions(parts: int, ratios: tuple[int, ...] | None) -> list[float]:
    weights = list(ratios) if ratios and len(ratios) == parts and sum(ratios) else [1] * parts
    total = sum(weights)
    edges = [0.0]
    for weight in weights:
        edges.append(edges[-1] + weight / total)
    return edges


def _panel_resize_commands(
    window_layout: str, pane_ids: list[str], layout: object
) -> list[list[str]] | None:
    """``resize-pane`` commands that restore *layout*'s proportions at any size.

    *pane_ids* are in slot order, as :func:`_equalize_panel_layout` returns them.

    tmux's own resize keeps pane sizes roughly but lets the ratios drift, so
    a panel re-applies them whenever its window resizes. Each split's
    children except the last are sized as a percentage of the window, top
    down, through a pane that is a direct child: ``resize-pane`` resizes the
    nearest split of its axis. Returns ``None`` when some child has no such
    pane (it would resize the wrong split).
    """
    placements = sorted(layout.placements, key=lambda p: p.pane_index)
    by_pane = {int(pid.lstrip("%")): p for pid, p in zip(pane_ids, placements)}
    edges = {
        "x": _cumulative_fractions(layout.cols, getattr(layout, "col_ratios", None)),
        "y": _cumulative_fractions(layout.rows, getattr(layout, "row_ratios", None)),
    }

    def leaves(node: _LayoutCell):
        return (leaf.pane for leaf in _layout_leaves(node))

    def fraction(node: _LayoutCell, axis: str) -> float:
        spans = [by_pane[pane] for pane in leaves(node)]
        if axis == "x":
            start = min(p.col for p in spans)
            end = max(p.col + p.col_span for p in spans)
        else:
            start = min(p.row for p in spans)
            end = max(p.row + p.row_span for p in spans)
        return edges[axis][end] - edges[axis][start]

    commands: list[list[str]] = []

    def visit(node: _LayoutCell) -> bool:
        if node.pane is not None:
            return True
        for child in node.children[:-1]:
            handle = child.pane
            if handle is None:
                handle = next((c.pane for c in child.children if c.pane is not None), None)
            if handle is None:
                return False
            percent = round(fraction(child, node.axis) * 100)
            commands.append(["resize-pane", "-t", f"%{handle}", f"-{node.axis}", f"{percent}%"])
        return all(visit(child) for child in node.children)

    try:
        root = _parse_tmux_layout(window_layout)
        if set(leaves(root)) != set(by_pane):
            return None
        return commands if visit(root) else None
    except (ValueError, IndexError, KeyError):
        return None


def _install_panel_resize_hook(session_name: str, pane_ids: list[str], layout: object) -> None:
    """Keep the panel's proportions through every window resize, inside tmux."""
    window_target = f"={session_name}:{_FIRST_WINDOW}"
    window_layout = _tmux_output("display-message", "-p", "-t", window_target, "#{window_layout}")
    commands = _panel_resize_commands(window_layout, pane_ids, layout)
    setup = [["set-window-option", "-q", "-t", window_target, "aggressive-resize", "on"]]
    if commands:
        hook = " ; ".join(shlex.join(command) for command in commands)
        setup.append(["set-hook", "-w", "-t", window_target, "window-resized", hook])
    elif commands is None:
        logger.warning("panel %s: no resize plan for its layout; tmux resizes it", session_name)
    if not commands:
        setup.append(["set-hook", "-u", "-w", "-t", window_target, "window-resized"])
    _run_tmux(_chain_tmux_commands(setup))


def _printf_lines_command(lines: list[str]) -> str:
    if not lines:
        return "true"
    quoted_lines = " ".join(shlex.quote(line) for line in lines)
    return f"printf '%s\\n' {quoted_lines}"


def _slot_pane_format(slot: int) -> str:
    """Format naming the pane of the current window that holds *slot*."""
    return f"#{{P:#{{?#{{==:#{{{PANEL_SLOT_OPTION}}},{slot}}},#{{pane_id}},}}}}"


def _ensure_panel_prefix_bindings() -> None:
    in_panel = "#{m:gd/panel/*,#{session_name}}"
    commands = [["bind-key", "-T", "prefix", "b", "if-shell", "-F", in_panel, "display-panes"]]
    commands.extend(
        [
            "bind-key",
            "-T",
            "prefix",
            str(slot),
            "if-shell",
            "-F",
            in_panel,
            # Slots, not pane indexes: tmux numbers panes in layout-tree
            # order, which is not slot order for every layout.
            f"run-shell -C \"select-pane -t '{_slot_pane_format(slot)}'\"",
            f"select-window -t :={slot}",
        ]
        for slot in range(1, 10)
    )
    _run_tmux(_chain_tmux_commands(commands), check=True)
    # prefix b was just rebound without its deck meaning.
    from .deck import ensure_deck_bindings

    ensure_deck_bindings()


def _configure_panel_window(
    session_name: str,
    pane_ids: list[str],
    panes: dict[int, str | None],
    theme_name: str | None = None,
) -> None:
    window_target = f"={session_name}:{_FIRST_WINDOW}"
    theme = resolve_panel_theme(_resolved_panel_theme_name(theme_name))
    window_options = (
        ("pane-base-index", "1"),
        # Each pane draws its session's own header (with the slot badge);
        # the panel's separators show which pane has focus.
        ("pane-border-status", "off"),
        ("pane-border-lines", "heavy"),
        ("remain-on-exit", "on"),
        ("pane-border-style", f"fg={theme.border_inactive}"),
        ("pane-active-border-style", f"fg={theme.border_active}"),
    )
    commands = [
        ["set-window-option", "-t", window_target, option, value]
        for option, value in window_options
    ]
    for slot, pane_id in enumerate(pane_ids, start=1):
        commands.append(["select-pane", "-t", pane_id, "-T", _panel_pane_title(panes.get(slot))])
        commands.append(["set-option", "-p", "-t", pane_id, PANEL_SLOT_OPTION, str(slot)])
    _run_tmux(_chain_tmux_commands(commands), check=True)


def _panel_view_command(panel_name: str, slot: int, session_name: str) -> str:
    """Show *session_name* in a panel pane through a view of it.

    The view is a session grouped with the real one: it shows the same
    windows, but its own session options -- its status line turned off and
    its slot, which the session's header shows as a badge -- never touch
    the real session. tmux deletes it when this pane's client goes away.
    """
    view_name = f"gd/view/{_sanitize_view_part(panel_name)}-{slot}-$$"
    return (
        f"env -u TMUX tmux new-session -t {shlex.quote(f'={session_name}')} -s {view_name}"
        f" \\; set-option status off \\; set-option {PANEL_SLOT_OPTION} {slot}"
        " \\; set-option destroy-unattached on"
    )


def _sanitize_view_part(panel_name: str) -> str:
    return make_panel_session_name(panel_name).rsplit("/", 1)[-1]


def _panel_pane_command(
    panel_name: str,
    pane_index: int,
    session_name: str | None,
    *,
    closed: bool = False,
) -> str:
    closed_message = _printf_lines_command(["", "\033[2mSESSION CLOSED\033[0m"])
    if session_name:
        quoted_session_target = shlex.quote(f"={session_name}")
        missing_message = _printf_lines_command(
            [
                f"Panel: {panel_name}",
                f"Pane {pane_index}: missing session",
                session_name,
            ]
        )
        script = (
            "clear; "
            f"if tmux has-session -t {quoted_session_target} >/dev/null 2>&1; then "
            f"{_panel_view_command(panel_name, pane_index, session_name)}; "
            f"clear; {closed_message}; "
            "else "
            f"{missing_message}; "
            "fi; "
            "exit 0"
        )
    elif closed:
        script = f"clear; {closed_message}; exit 0"
    else:
        script = f"clear; {_printf_lines_command(['', f'{pane_index}: empty'])}; exit 0"
    return f"sh -c {shlex.quote(script)}"


def rebuild_panel_tmux_session(
    panel_name: str,
    rows: int,
    cols: int,
    panes: dict[int, str | None],
    closed_panes: set[int] | None = None,
    layout_key: str | None = None,
    theme_name: str | None = None,
) -> str:
    with _REBUILD_LOCK:
        return _rebuild_panel_tmux_session(
            panel_name, rows, cols, panes, closed_panes, layout_key, theme_name
        )


def _rebuild_panel_tmux_session(
    panel_name: str,
    rows: int,
    cols: int,
    panes: dict[int, str | None],
    closed_panes: set[int] | None,
    layout_key: str | None,
    theme_name: str | None,
) -> str:
    from ...commands.tui.panels import resolve_panel_layout

    session_name = make_panel_session_name(panel_name)
    theme_name = _resolved_panel_theme_name(theme_name)
    layout = resolve_panel_layout(layout_key, rows, cols)
    closed_panes = closed_panes or set()
    build_session_name = _panel_build_session_name(panel_name)

    for session in panes.values():
        if session and _session_exists(session):
            _protect_session(session)

    reap_stale_panel_helpers()

    old_panel_exists = _session_exists(session_name)
    orphan_session_name = (
        f"{session_name}_orphaned-{os.getpid()}-{int(time.time() * 1000)}"
        if old_panel_exists
        else None
    )
    renamed_to_final = False

    try:
        term_cols, term_lines = shutil.get_terminal_size()
        _run_tmux(
            [
                "new-session",
                "-d",
                *_tmux_new_session_environment_args(),
                "-s",
                build_session_name,
                "-n",
                panel_name,
                "-x",
                str(term_cols),
                "-y",
                str(term_lines),
                "-c",
                str(Path.home()),
                "cat",
            ],
            check=True,
        )
        _protect_session(build_session_name)
        # Panel panes are respawned below, so scrubbing here is enough to
        # keep gitdirector's launch context out of every one of them.
        _scrub_session_environment(build_session_name)
        pane_ids = _build_panel_layout(build_session_name, layout.rows, layout.cols, layout.key)
        pane_ids = _equalize_panel_layout(build_session_name, pane_ids, layout)
        _configure_panel_window(build_session_name, pane_ids, panes, theme_name)
        total_panes = layout.total_panes
        for pane_index, pane_id in enumerate(pane_ids[:total_panes], start=1):
            pane_session = panes.get(pane_index)
            if pane_session is not None and not _session_exists(pane_session):
                logger.warning(
                    "Panel %s pane %d references missing session %s; skipping attach",
                    panel_name,
                    pane_index,
                    pane_session,
                )
                continue
            respawn_pane(
                pane_id,
                _tmux_child_environment_command(
                    _panel_pane_command(
                        panel_name,
                        pane_index,
                        pane_session,
                        closed=pane_index in closed_panes,
                    )
                ),
            )

        if old_panel_exists:
            _run_tmux(["rename-session", "-t", f"={session_name}", orphan_session_name], check=True)
        _run_tmux(["rename-session", "-t", f"={build_session_name}", session_name], check=True)
        renamed_to_final = True
        _protect_session(session_name)
        _install_panel_resize_hook(session_name, pane_ids[:total_panes], layout)
        sync_panel_tmux_config(theme_name)
        _ensure_panel_prefix_bindings()

        if old_panel_exists and orphan_session_name is not None:
            # Its panes' view sessions go with it; the real sessions they
            # showed were never touched.
            kill_tmux_session(orphan_session_name)
    except Exception:
        # Undo whichever half of the swap happened, then restore the old
        # panel under its original name.
        kill_tmux_session(session_name if renamed_to_final else build_session_name)
        if orphan_session_name is not None and _session_exists(orphan_session_name):
            _run_tmux(["rename-session", "-t", f"={orphan_session_name}", session_name])
        raise

    return session_name


__all__ = [
    "kill_panel_tmux_session",
    "rebuild_panel_tmux_session",
]
