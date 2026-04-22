import shlex
import shutil
import subprocess
from pathlib import Path

from ...ui_theme import resolve_panel_theme
from .core import (
    _PANEL_BORDER_RESTORE_OPTION,
    _PANEL_CLIENT_COUNT_OPTION,
    _PANEL_STATUS_RESTORE_OPTION,
    _PANEL_WINDOW_RESTORE_OPTION,
    _current_window_target,
    _ensure_panel_resize_tracking,
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


def kill_panel_tmux_session(panel_name: str) -> bool:
    return kill_tmux_session(make_panel_session_name(panel_name))


def _tmux_output(*args: str) -> str:
    result = subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        check=True,
    )
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


def _split_panel_row(start_target: str, cols: int) -> None:
    current_target = start_target
    for step in range(1, cols):
        size_pct = round(100 * (cols - step) / (cols - step + 1))
        current_target = _tmux_output(
            "split-window",
            "-h",
            "-l",
            f"{size_pct}%",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            current_target,
            "cat",
        )


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


def _build_panel_grid(session_name: str, rows: int, cols: int) -> list[str]:
    root_target = f"={session_name}:0.0"
    _split_panel_row(root_target, cols)

    for row in range(2, rows + 1):
        size_pct = round(100 / row)
        row_target = _tmux_output(
            "split-window",
            "-v",
            "-f",
            "-l",
            f"{size_pct}%",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            root_target,
            "cat",
        )
        _split_panel_row(row_target, cols)

    return _list_window_panes_row_major(session_name)


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
    return (
        f"panel_window=$(tmux display-message -p -t {quoted_session} '#{{session_name}}:#{{window_index}}' 2>/dev/null || printf %s {default_window_target}); "
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


def cleanup_panel_attached_session(session_name: str, theme_name: str | None = None) -> None:
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

    restore_status = _tmux_option_value(session_target, _PANEL_STATUS_RESTORE_OPTION) or "on"
    restore_border = _tmux_option_value(session_target, _PANEL_BORDER_RESTORE_OPTION) or "off"
    restore_window = _tmux_option_value(
        session_target, _PANEL_WINDOW_RESTORE_OPTION
    ) or _current_window_target(session_name)
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


def _temp_panel_pane_command(temp_panel_session_name: str, session_name: str) -> str:
    quoted_session_target = shlex.quote(f"={session_name}")
    quoted_temp_panel_target = shlex.quote(f"={temp_panel_session_name}")
    missing_message = _printf_lines_command([f"Missing session: {session_name}"])
    script = (
        "clear; "
        f"if tmux has-session -t {quoted_session_target} >/dev/null 2>&1; then "
        f"{_panel_attach_fragment(session_name)}"
        f"tmux kill-session -t {quoted_temp_panel_target} >/dev/null 2>&1 || true; "
        "else "
        f"{missing_message}; "
        "exec tail -f /dev/null; "
        "fi"
    )
    return f"sh -c {shlex.quote(script)}"


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
            "exec tail -f /dev/null"
        )
    elif closed:
        script = f"clear; {closed_message}; exec tail -f /dev/null"
    else:
        script = f"clear; {_printf_lines_command(['', 'UNASSIGNED'])}; exec tail -f /dev/null"
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

    for session in panes.values():
        if session and _session_exists(session):
            _protect_session(session)

    kill_panel_tmux_session(panel_name)

    term_cols, term_lines = shutil.get_terminal_size()
    subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session_name,
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
    _protect_session(session_name)
    subprocess.run(
        ["tmux", "set-window-option", "-t", f"={session_name}:0", "pane-border-status", "top"],
        check=True,
    )

    pane_ids = _build_panel_layout(session_name, layout.rows, layout.cols, layout.key)
    _equalize_panel_layout(session_name, pane_ids, layout)
    _configure_panel_window(session_name, pane_ids, panes, theme_name)
    _load_panel_tmux_config(panel_name, session_name, theme_name)
    _ensure_panel_resize_tracking(session_name)
    sync_panel_tmux_config(theme_name)
    _ensure_panel_prefix_bindings()
    total_panes = layout.total_panes
    for pane_index, pane_id in enumerate(pane_ids[:total_panes], start=1):
        subprocess.run(
            [
                "tmux",
                "respawn-pane",
                "-k",
                "-t",
                pane_id,
                _panel_pane_command(
                    panel_name,
                    pane_index,
                    panes.get(pane_index),
                    closed=pane_index in closed_panes,
                ),
            ],
            check=True,
        )

    return session_name


def rebuild_temp_panel_tmux_session(
    session_name: str,
    theme_name: str | None = None,
) -> str:
    temp_panel_session_name = make_temp_panel_session_name(session_name)
    temp_panel_name = _temp_panel_display_name(session_name)
    theme_name = _resolved_panel_theme_name(theme_name)

    if _session_exists(temp_panel_session_name):
        subprocess.run(
            ["tmux", "kill-session", "-t", f"={temp_panel_session_name}"],
            check=False,
        )

    term_cols, term_lines = shutil.get_terminal_size()
    subprocess.run(
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
            "cat",
        ],
        check=True,
    )

    _protect_session(temp_panel_session_name)
    pane_ids = _build_panel_layout(temp_panel_session_name, 1, 1, "grid_1x1")
    _configure_panel_window(
        temp_panel_session_name,
        pane_ids,
        {1: session_name},
        theme_name,
        show_pane_number=False,
    )
    _load_panel_tmux_config(temp_panel_name, temp_panel_session_name, theme_name)
    subprocess.run(
        [
            "tmux",
            "respawn-pane",
            "-k",
            "-t",
            pane_ids[0],
            _temp_panel_pane_command(temp_panel_session_name, session_name),
        ],
        check=True,
    )
    return temp_panel_session_name


__all__ = [name for name in globals() if not name.startswith("__")]
