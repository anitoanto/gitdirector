import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from ...ui_theme import resolve_panel_theme
from .core import (
    _PANEL_BORDER_RESTORE_OPTION,
    _PANEL_CLIENT_COUNT_OPTION,
    _PANEL_STATUS_RESTORE_OPTION,
    _PANEL_WINDOW_RESTORE_OPTION,
    _current_window_target,
    _ensure_panel_resize_tracking,
    _is_temp_panel_session,
    _load_panel_tmux_config,
    _panel_border_format,
    _panel_pane_title,
    _protect_session,
    _resolved_panel_theme_name,
    _session_exists,
    _session_option_target,
    _session_tmux_config,
    _temp_panel_display_name,
    kill_tmux_session,
    make_panel_session_name,
    make_temp_panel_session_name,
    sync_panel_tmux_config,
)

logger = logging.getLogger(__name__)

_TEMP_PANEL_ATTACH_SETTLE_SECONDS = 0.15
_TMUX_FORK_RETRY_ATTEMPTS = 5


def _run_tmux_with_fork_retry(args: list[str]) -> subprocess.CompletedProcess[str]:
    command = ["tmux", *args]
    last_result: subprocess.CompletedProcess[str] | None = None
    for attempt in range(_TMUX_FORK_RETRY_ATTEMPTS):
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        last_result = result
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        if result.returncode == 0 or "fork failed" not in stderr.lower():
            break
        time.sleep(0.05 * (attempt + 1))

    if last_result is None:
        raise RuntimeError("tmux command was not run")
    return last_result


def _raise_for_tmux_result(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode == 0:
        return
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def _panel_build_session_name(panel_name: str) -> str:
    digest = hashlib.sha1(panel_name.encode("utf-8")).hexdigest()[:12]
    return f"gd/temp/panel/build-{digest}-{os.getpid()}"


def _tmux_session_actual_name(intended_name: str) -> str:
    """Return the session name tmux will actually use for *intended_name*.

    tmux silently replaces ``.`` with ``_`` in session names, so any name we
    construct with a dot in it is stored under a different name on the
    server. Use this helper when looking up or killing a session that was
    created from a dotted name so the lookup matches what tmux recorded.
    """
    return intended_name.replace(".", "_")


def kill_panel_tmux_session(panel_name: str) -> bool:
    if not isinstance(panel_name, str) or not panel_name:
        raise ValueError("kill_panel_tmux_session requires a non-empty panel name")
    return kill_tmux_session(make_panel_session_name(panel_name))


def _kill_tmux_session_by_name(intended_name: str) -> bool:
    """Kill a tmux session by the name we requested, tolerating ``.`` -> ``_``.

    tmux rewrites ``.`` to ``_`` in session names. When we issue a
    ``rename-session`` or ``new-session`` with a dotted name, the actual
    stored name has ``_`` instead. Forward the kill through this helper so
    both forms are tried and the call still succeeds.
    """
    if kill_tmux_session(intended_name):
        return True
    actual = _tmux_session_actual_name(intended_name)
    if actual != intended_name:
        return kill_tmux_session(actual)
    return False


def _tmux_output(*args: str) -> str:
    result = _run_tmux_with_fork_retry(list(args))
    _raise_for_tmux_result(result)
    return result.stdout.strip()


def _tmux_option_value(target: str, option: str, *, window: bool = False) -> str | None:
    command = "show-window-options" if window else "show-options"
    result = subprocess.run(
        ["tmux", command, "-q", "-v", "-t", target, option],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _respawn_pane(pane_id: str, command: str, *, check: bool = True) -> subprocess.CompletedProcess:
    args = ["tmux", "respawn-pane", "-k", "-t", pane_id, command]
    result = _run_tmux_with_fork_retry(args[1:])
    if check:
        _raise_for_tmux_result(result)
    return result


def _list_window_panes_row_major(session_name: str) -> list[str]:
    output = _tmux_output(
        "list-panes",
        "-t",
        f"={session_name}:0",
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
    root_target = f"={session_name}:0.0"
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
) -> None:
    window_target = f"={session_name}:0"
    dims = _tmux_output(
        "display-message", "-t", window_target, "-p", "#{window_width} #{window_height}"
    )
    window_w, window_h = (int(v) for v in dims.split())

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

    subprocess.run(
        ["tmux", "select-layout", "-t", window_target, layout_string],
        check=True,
    )


def _printf_lines_command(lines: list[str]) -> str:
    if not lines:
        return "true"
    quoted_lines = " ".join(shlex.quote(line) for line in lines)
    return f"printf '%s\\n' {quoted_lines}"


def _ensure_panel_prefix_bindings() -> None:
    subprocess.run(
        [
            "tmux",
            "bind-key",
            "-T",
            "prefix",
            "b",
            "if-shell",
            "-F",
            "#{m:gd/panel/*,#{session_name}}",
            "display-panes",
        ],
        check=True,
    )
    for pane_number in range(1, 10):
        subprocess.run(
            [
                "tmux",
                "bind-key",
                "-T",
                "prefix",
                str(pane_number),
                "if-shell",
                "-F",
                "#{m:gd/panel/*,#{session_name}}",
                f"select-pane -t:.{pane_number}",
                f"select-window -t :={pane_number}",
            ],
            check=True,
        )


def _configure_panel_window(
    session_name: str,
    pane_ids: list[str],
    panes: dict[int, str | None],
    theme_name: str | None = None,
    *,
    show_pane_number: bool = True,
) -> None:
    window_target = f"={session_name}:0"
    theme = resolve_panel_theme(_resolved_panel_theme_name(theme_name))
    subprocess.run(
        ["tmux", "set-window-option", "-t", window_target, "pane-base-index", "1"],
        check=True,
    )
    subprocess.run(
        ["tmux", "set-window-option", "-t", window_target, "pane-border-status", "top"],
        check=True,
    )
    subprocess.run(
        ["tmux", "set-window-option", "-t", window_target, "pane-border-lines", "heavy"],
        check=True,
    )
    subprocess.run(
        ["tmux", "set-window-option", "-t", window_target, "remain-on-exit", "on"],
        check=True,
    )
    subprocess.run(
        [
            "tmux",
            "set-window-option",
            "-t",
            window_target,
            "pane-border-style",
            f"fg={theme.border_inactive}",
        ],
        check=True,
    )
    subprocess.run(
        [
            "tmux",
            "set-window-option",
            "-t",
            window_target,
            "pane-active-border-style",
            f"fg={theme.border_active}",
        ],
        check=True,
    )
    subprocess.run(
        [
            "tmux",
            "set-window-option",
            "-t",
            window_target,
            "pane-border-format",
            _panel_border_format(theme_name, show_pane_number=show_pane_number),
        ],
        check=True,
    )

    for pane_number, pane_id in enumerate(pane_ids, start=1):
        subprocess.run(
            [
                "tmux",
                "select-pane",
                "-t",
                pane_id,
                "-T",
                _panel_pane_title(pane_number, panes.get(pane_number)),
            ],
            check=True,
        )


def _panel_attach_fragment(session_name: str) -> str:
    quoted_session = shlex.quote(_session_option_target(session_name))
    quoted_attach_target = shlex.quote(f"={session_name}")
    default_window_target = shlex.quote(f"={session_name}:0")
    cleanup_fragment = (
        f"panel_clients=$(tmux show-options -q -v -t {quoted_session} {_PANEL_CLIENT_COUNT_OPTION} 2>/dev/null || printf '1'); "
        'case "$panel_clients" in ""|*[!0-9]*) panel_clients=1 ;; esac; '
        "panel_clients=$((panel_clients - 1)); "
        'if [ "$panel_clients" -le 0 ]; then '
        f"panel_prev_status=$(tmux show-options -q -v -t {quoted_session} {_PANEL_STATUS_RESTORE_OPTION} 2>/dev/null || printf 'on'); "
        f"panel_prev_border_status=$(tmux show-options -q -v -t {quoted_session} {_PANEL_BORDER_RESTORE_OPTION} 2>/dev/null || printf 'off'); "
        f"panel_restore_window=$(tmux show-options -q -v -t {quoted_session} {_PANEL_WINDOW_RESTORE_OPTION} 2>/dev/null || printf %s {default_window_target}); "
        f'tmux set-option -q -t {quoted_session} status "$panel_prev_status" >/dev/null 2>&1 || true; '
        f"tmux set-option -q -u -t {quoted_session} {_PANEL_CLIENT_COUNT_OPTION} >/dev/null 2>&1 || true; "
        'tmux set-window-option -q -t "$panel_restore_window" pane-border-status "$panel_prev_border_status" >/dev/null 2>&1 || true; '
        f"tmux set-option -q -u -t {quoted_session} {_PANEL_STATUS_RESTORE_OPTION} >/dev/null 2>&1 || true; "
        f"tmux set-option -q -u -t {quoted_session} {_PANEL_BORDER_RESTORE_OPTION} >/dev/null 2>&1 || true; "
        f"tmux set-option -q -u -t {quoted_session} {_PANEL_WINDOW_RESTORE_OPTION} >/dev/null 2>&1 || true; "
        "else "
        f'tmux set-option -q -t {quoted_session} {_PANEL_CLIENT_COUNT_OPTION} "$panel_clients" >/dev/null 2>&1 || true; '
        "fi; "
    )
    return (
        "panel_cleanup_done=0; "
        "panel_cleanup() { "
        'if [ "$panel_cleanup_done" = "1" ]; then return; fi; '
        "panel_cleanup_done=1; "
        f"{cleanup_fragment}"
        "}; "
        "trap panel_cleanup EXIT HUP INT TERM; "
        f"panel_window=$(tmux display-message -p -t {quoted_session} '=#{{session_name}}:#{{window_index}}' 2>/dev/null || printf %s {default_window_target}); "
        f"panel_clients=$(tmux show-options -q -v -t {quoted_session} {_PANEL_CLIENT_COUNT_OPTION} 2>/dev/null || printf '0'); "
        'case "$panel_clients" in ""|*[!0-9]*) panel_clients=0 ;; esac; '
        'if [ "$panel_clients" -eq 0 ]; then '
        f"panel_prev_status=$(tmux show-options -q -v -t {quoted_session} status 2>/dev/null || printf 'on'); "
        "panel_prev_border_status=$(tmux show-window-options -q -v -t \"$panel_window\" pane-border-status 2>/dev/null || printf 'off'); "
        f'tmux set-option -q -t {quoted_session} {_PANEL_STATUS_RESTORE_OPTION} "$panel_prev_status" >/dev/null 2>&1 || true; '
        f'tmux set-option -q -t {quoted_session} {_PANEL_BORDER_RESTORE_OPTION} "$panel_prev_border_status" >/dev/null 2>&1 || true; '
        f'tmux set-option -q -t {quoted_session} {_PANEL_WINDOW_RESTORE_OPTION} "$panel_window" >/dev/null 2>&1 || true; '
        "fi; "
        "panel_clients=$((panel_clients + 1)); "
        f'tmux set-option -q -t {quoted_session} {_PANEL_CLIENT_COUNT_OPTION} "$panel_clients" >/dev/null 2>&1 || true; '
        f"tmux set-option -q -t {quoted_session} destroy-unattached off >/dev/null 2>&1 || true; "
        f"tmux set-option -q -t {quoted_session} status off >/dev/null 2>&1 || true; "
        'tmux set-window-option -q -t "$panel_window" pane-border-status off >/dev/null 2>&1 || true; '
        f"env -u TMUX tmux attach-session -t {quoted_attach_target}; "
        "panel_cleanup; trap - EXIT HUP INT TERM; "
    )


def cleanup_panel_attached_session(session_name: str, theme_name: str | None = None) -> None:
    """Python-side fallback for restoring a session's panel UI options.

    This mirrors the in-shell ``panel_cleanup`` trap installed by
    :func:`_panel_attach_fragment`. The trap is the primary mechanism and
    fires on every normal attach/detach. This Python path covers cases
    where the trap cannot run — most importantly when the panel's outer
    tmux session is killed programmatically (panel deletion or
    reconfiguration) while inner sessions still carry the
    ``@gitdirector_panel_*`` restore options. It is also called from
    ``PanelStore.delete`` / ``PanelStore.reconfigure`` to leave no
    dangling restore options on inner sessions that survive the panel
    teardown.

    No-op when *session_name* no longer exists. Safe to call from Python
    even when the in-shell trap has already fired, since the trap
    unsets the restore options and the second pass becomes a no-op.
    """
    if not _session_exists(session_name):
        return

    session_target = _session_option_target(session_name)
    raw_client_count = _tmux_option_value(session_target, _PANEL_CLIENT_COUNT_OPTION)
    client_count = int(raw_client_count) if raw_client_count and raw_client_count.isdigit() else 0

    if client_count > 1:
        subprocess.run(
            [
                "tmux",
                "set-option",
                "-q",
                "-t",
                session_target,
                _PANEL_CLIENT_COUNT_OPTION,
                str(client_count - 1),
            ],
            check=False,
        )
        return

    restore_status = _tmux_option_value(session_target, _PANEL_STATUS_RESTORE_OPTION)
    if restore_status is None:
        restore_status = "on"
    restore_border = _tmux_option_value(session_target, _PANEL_BORDER_RESTORE_OPTION)
    if restore_border is None:
        restore_border = "off"
    restore_window = _tmux_option_value(session_target, _PANEL_WINDOW_RESTORE_OPTION)
    if restore_window is None:
        restore_window = _current_window_target(session_name)
    exact_restore_window = (
        restore_window if restore_window.startswith("=") else f"={restore_window}"
    )

    subprocess.run(
        ["tmux", "set-option", "-q", "-t", session_target, "status", restore_status],
        check=False,
    )
    subprocess.run(
        [
            "tmux",
            "set-window-option",
            "-q",
            "-t",
            exact_restore_window,
            "pane-border-status",
            restore_border,
        ],
        check=False,
    )
    for option in (
        _PANEL_CLIENT_COUNT_OPTION,
        _PANEL_STATUS_RESTORE_OPTION,
        _PANEL_BORDER_RESTORE_OPTION,
        _PANEL_WINDOW_RESTORE_OPTION,
    ):
        subprocess.run(
            ["tmux", "set-option", "-q", "-u", "-t", session_target, option],
            check=False,
        )

    if session_name.startswith("gd/"):
        sync_panel_tmux_config(theme_name)


def _standalone_attach_fragment(session_name: str) -> str:
    quoted_session = shlex.quote(_session_option_target(session_name))
    quoted_attach_target = shlex.quote(f"={session_name}")
    config_lines = [
        line.strip() for line in _session_tmux_config(session_name).splitlines() if line.strip()
    ]
    config_fragment = "".join(f"tmux {line} >/dev/null 2>&1 || true; " for line in config_lines)
    return (
        f"tmux set-option -q -t {quoted_session} destroy-unattached off >/dev/null 2>&1 || true; "
        f"{config_fragment}env -u TMUX tmux attach-session -t {quoted_attach_target}; "
    )


def _temp_panel_pane_command(
    temp_panel_session_name: str,
    session_name: str,
    *,
    attach_delay_seconds: float = 0.0,
) -> str:
    quoted_session_target = shlex.quote(f"={session_name}")
    quoted_temp_panel_target = shlex.quote(f"={temp_panel_session_name}")
    missing_message = _printf_lines_command([f"Missing session: {session_name}"])
    delay_fragment = f"sleep {attach_delay_seconds}; " if attach_delay_seconds > 0 else ""
    script = (
        "clear; "
        f"if tmux has-session -t {quoted_session_target} >/dev/null 2>&1; then "
        f"{delay_fragment}"
        f"{_panel_attach_fragment(session_name)}"
        "else "
        f"{missing_message}; "
        "fi; "
        f"tmux kill-session -t {quoted_temp_panel_target} >/dev/null 2>&1 || true; "
        "exit 0"
    )
    return f"sh -c {shlex.quote(script)}"


def cleanup_temp_panel_tmux_session(temp_panel_session_name: str) -> bool:
    if not _is_temp_panel_session(temp_panel_session_name):
        raise ValueError(f"not a temp panel session: {temp_panel_session_name!r}")
    return kill_tmux_session(temp_panel_session_name)


def _embedded_tmux_attach_command(
    session_name: str,
    panel_name: str | None = None,
    pane_index: int | None = None,
) -> str:
    quoted_session_target = shlex.quote(f"={session_name}")
    missing_message = _printf_lines_command(["", "MISSING SESSION", session_name])
    attach_fragment = (
        _panel_attach_fragment(session_name)
        if panel_name is not None and pane_index is not None
        else _standalone_attach_fragment(session_name)
    )
    script = (
        "clear; "
        f"if tmux has-session -t {quoted_session_target} >/dev/null 2>&1; then "
        f"{attach_fragment}"
        "else "
        f"{missing_message}; "
        "fi"
    )
    return f"sh -c {shlex.quote(script)}"


def _panel_pane_command(
    panel_name: str,
    pane_index: int,
    session_name: str | None,
    *,
    closed: bool = False,
) -> str:
    closed_message = _printf_lines_command(
        [
            "",
            "\033[2mSESSION CLOSED\033[0m",
        ]
    )
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
            f"{_panel_attach_fragment(session_name)}"
            f"clear; {closed_message}; "
            "else "
            f"{missing_message}; "
            "fi; "
            "exit 0"
        )
    elif closed:
        script = f"clear; {closed_message}; exit 0"
    else:
        script = "clear; exit 0"
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
    from ...commands.tui.panels import resolve_panel_layout

    session_name = make_panel_session_name(panel_name)
    theme_name = _resolved_panel_theme_name(theme_name)
    layout = resolve_panel_layout(layout_key, rows, cols)
    closed_panes = closed_panes or set()
    build_session_name = _panel_build_session_name(panel_name)

    for session in panes.values():
        if session and _session_exists(session):
            _protect_session(session)

    kill_tmux_session(build_session_name)

    old_panel_exists = _session_exists(session_name)
    orphan_session_name = (
        f"{session_name}_orphaned-{os.getpid()}-{int(time.time() * 1000)}"
        if old_panel_exists
        else None
    )
    actual_orphan_session_name = (
        _tmux_session_actual_name(orphan_session_name) if orphan_session_name else None
    )
    renamed_to_final = False

    try:
        term_cols, term_lines = shutil.get_terminal_size()
        subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",
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
        subprocess.run(
            [
                "tmux",
                "set-window-option",
                "-t",
                f"={build_session_name}:0",
                "pane-border-status",
                "top",
            ],
            check=True,
        )

        pane_ids = _build_panel_layout(build_session_name, layout.rows, layout.cols, layout.key)
        _equalize_panel_layout(build_session_name, pane_ids, layout)
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
            _respawn_pane(
                pane_id,
                _panel_pane_command(
                    panel_name,
                    pane_index,
                    pane_session,
                    closed=pane_index in closed_panes,
                ),
            )

        if old_panel_exists:
            subprocess.run(
                ["tmux", "rename-session", "-t", f"={session_name}", orphan_session_name],
                check=True,
            )
        subprocess.run(
            ["tmux", "rename-session", "-t", f"={build_session_name}", session_name],
            check=True,
        )
        renamed_to_final = True
        _protect_session(session_name)
        _ensure_panel_resize_tracking(session_name)
        sync_panel_tmux_config(theme_name)
        _ensure_panel_prefix_bindings()

        if old_panel_exists and actual_orphan_session_name is not None:
            for inner_session in panes.values():
                if inner_session and _session_exists(inner_session):
                    cleanup_panel_attached_session(inner_session, theme_name)
            _kill_tmux_session_by_name(actual_orphan_session_name)
    except Exception:
        if renamed_to_final:
            kill_tmux_session(session_name)
            if actual_orphan_session_name is not None and _session_exists(
                actual_orphan_session_name
            ):
                subprocess.run(
                    [
                        "tmux",
                        "rename-session",
                        "-t",
                        f"={actual_orphan_session_name}",
                        session_name,
                    ],
                    check=False,
                )
        else:
            kill_tmux_session(build_session_name)
            if actual_orphan_session_name is not None and _session_exists(
                actual_orphan_session_name
            ):
                subprocess.run(
                    [
                        "tmux",
                        "rename-session",
                        "-t",
                        f"={actual_orphan_session_name}",
                        session_name,
                    ],
                    check=False,
                )
        raise

    return session_name


def ensure_temp_panel_tmux_session(
    session_name: str,
    theme_name: str | None = None,
    *,
    attach_delay_seconds: float = 0.0,
) -> str:
    """Return a just-in-time temp panel session name for *session_name*.

    The temp session is a 1:1 wrapper around an existing inner session
    and is named deterministically from it (see
    :func:`make_temp_panel_session_name`). A wrapper normally kills
    itself as soon as the inner attach exits. If a previous wrapper is
    still present, only clearly inactive wrappers are removed here; a
    wrapper with a live pane or attached client may be serving another
    attach and is returned as-is.
    """
    temp_panel_session_name = make_temp_panel_session_name(session_name)
    if _session_exists(temp_panel_session_name):
        if _temp_panel_session_is_inactive(temp_panel_session_name):
            if _kill_temp_panel_session_and_wait(temp_panel_session_name):
                return _create_temp_panel_tmux_session(
                    session_name,
                    theme_name,
                    attach_delay_seconds=attach_delay_seconds,
                )
            _respawn_temp_panel_pane(
                temp_panel_session_name,
                session_name,
                attach_delay_seconds=attach_delay_seconds,
            )
            _settle_temp_panel_attach()
        return temp_panel_session_name
    return _create_temp_panel_tmux_session(
        session_name,
        theme_name,
        attach_delay_seconds=attach_delay_seconds,
    )


def _kill_temp_panel_session_and_wait(temp_panel_session_name: str) -> bool:
    cleanup_temp_panel_tmux_session(temp_panel_session_name)
    for _attempt in range(5):
        if not _session_exists(temp_panel_session_name):
            return True
        time.sleep(0.05)
    return not _session_exists(temp_panel_session_name)


def _temp_panel_session_is_inactive(temp_panel_session_name: str) -> bool:
    result = subprocess.run(
        [
            "tmux",
            "list-panes",
            "-t",
            f"={temp_panel_session_name}:0",
            "-F",
            "#{session_attached}|#{pane_dead}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return True

    rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not rows:
        return True

    for row in rows:
        attached, _, dead = row.partition("|")
        if attached.isdigit() and int(attached) > 0:
            return False
        if dead != "1":
            return False
    return True


def _settle_temp_panel_attach() -> None:
    time.sleep(_TEMP_PANEL_ATTACH_SETTLE_SECONDS)


def _respawn_temp_panel_pane(
    temp_panel_session_name: str,
    session_name: str,
    *,
    attach_delay_seconds: float = 0.0,
) -> None:
    """Re-run the attach command in the first pane of the temp session.

    Falls back to a full rebuild if the session is missing its window
    or pane (e.g. the user closed the only window externally) so the
    caller never ends up attached to a session with no usable pane.
    """
    pane_id = _first_pane_id(temp_panel_session_name)
    if pane_id is None:
        _create_temp_panel_tmux_session(
            session_name,
            _resolved_panel_theme_name(None),
            attach_delay_seconds=attach_delay_seconds,
        )
        return
    result = _respawn_pane(
        pane_id,
        _temp_panel_pane_command(
            temp_panel_session_name,
            session_name,
            attach_delay_seconds=attach_delay_seconds,
        ),
        check=False,
    )
    if result.returncode != 0:
        kill_tmux_session(temp_panel_session_name)
        _create_temp_panel_tmux_session(
            session_name,
            _resolved_panel_theme_name(None),
            attach_delay_seconds=attach_delay_seconds,
        )


def _first_pane_id(temp_panel_session_name: str) -> str | None:
    result = subprocess.run(
        [
            "tmux",
            "list-panes",
            "-t",
            f"={temp_panel_session_name}:0",
            "-F",
            "#{pane_id}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.splitlines()[0].strip()


def _create_temp_panel_tmux_session(
    session_name: str,
    theme_name: str | None = None,
    *,
    attach_delay_seconds: float = 0.0,
) -> str:
    temp_panel_session_name = make_temp_panel_session_name(session_name)
    temp_panel_name = _temp_panel_display_name(session_name)
    theme_name = _resolved_panel_theme_name(theme_name)

    try:
        term_cols, term_lines = shutil.get_terminal_size()
        # `-P -F #{pane_id}` returns the new pane's ID on stdout so we can
        # use it directly for the respawn below (no follow-up `list-panes`
        # query that could race with the server's session initialization).
        new_session = subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                temp_panel_session_name,
                "-n",
                temp_panel_name,
                "-x",
                str(term_cols),
                "-y",
                str(term_lines),
                "-c",
                str(Path.home()),
                "-P",
                "-F",
                "#{pane_id}",
                "cat",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        pane_id = new_session.stdout.strip()

        _protect_session(temp_panel_session_name)
        _configure_panel_window(
            temp_panel_session_name,
            [pane_id],
            {1: session_name},
            theme_name,
            show_pane_number=False,
        )
        _load_panel_tmux_config(temp_panel_name, temp_panel_session_name, theme_name)
        _respawn_pane(
            pane_id,
            _temp_panel_pane_command(
                temp_panel_session_name,
                session_name,
                attach_delay_seconds=attach_delay_seconds,
            ),
        )
        _settle_temp_panel_attach()
    except Exception:
        kill_tmux_session(temp_panel_session_name)
        raise
    return temp_panel_session_name


__all__ = [name for name in globals() if not name.startswith("__")]
