"""tmux integration via subprocess."""

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ..config import Config
from ..ui_theme import DEFAULT_THEME_NAME, resolve_panel_theme


def _sanitize_repo_name(name: str) -> str:
    """Sanitize a repository name for use in tmux session names.

    Keeps lowercase alphanumeric characters and hyphens. Replaces everything
    else with ``-``, collapses consecutive hyphens, and strips leading/trailing
    hyphens.
    """
    name = name.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


def _list_sessions() -> list[str]:
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [s for s in result.stdout.strip().split("\n") if s]


def _next_n(prefix: str, sessions: list[str] | None = None) -> int:
    if sessions is None:
        sessions = _list_sessions()
    max_n = 0
    for s in sessions:
        if s.startswith(prefix):
            tail = s[len(prefix) :]
            if tail.isdigit():
                max_n = max(max_n, int(tail))
    return max_n + 1


def _make_session_name(repo_name: str, purpose: str = "shell") -> str:
    """Generate the next sequential session name: gd/{repo}/{purpose}/{N}."""
    clean = _sanitize_repo_name(repo_name)
    prefix = f"gd/{clean}/{purpose}/"
    n = _next_n(prefix)
    return f"{prefix}{n}"


def _session_exists(session_name: str) -> bool:
    """Check if a tmux session with the given name exists."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", f"={session_name}"],
        capture_output=True,
    )
    return result.returncode == 0


def _protect_session(session_name: str) -> None:
    """Ensure a gd session survives detach regardless of global tmux config."""
    subprocess.run(
        ["tmux", "set-option", "-t", f"={session_name}:", "destroy-unattached", "off"],
        capture_output=True,
    )


def _active_pane_target(session_name: str) -> str:
    """Return the exact-match tmux target for the session's active pane."""
    return f"={session_name}:"


def _session_option_target(session_name: str) -> str:
    """Return the exact-match tmux target for session-scoped options and queries."""
    return f"={session_name}:"


def list_repo_sessions(repo_name: str) -> list[str]:
    """List all tmux sessions for a given repository."""
    clean = _sanitize_repo_name(repo_name)
    prefix = f"gd/{clean}/"
    sessions = _list_sessions()
    return sorted([s for s in sessions if s.startswith(prefix) and not _is_temp_panel_session(s)])


def list_all_gd_sessions() -> list[dict[str, str]]:
    """List all GitDirector tmux sessions (gd/ prefix).

    Returns a list of dicts with keys: session_name, repo, purpose.
    """
    sessions = _list_sessions()
    entries = []
    for s in sorted(sessions):
        parsed = _parse_gd_session_name(s)
        if parsed is None:
            continue
        repo, purpose, _ = parsed
        entries.append({"session_name": s, "repo": repo, "purpose": purpose})
    return entries


def create_tmux_session(repo_name: str, path: Path, purpose: str = "shell") -> str:
    """Create a new detached tmux session with a unique name and return it."""
    for _ in range(10):
        session_name = _make_session_name(repo_name, purpose)
        if not _session_exists(session_name):
            break
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, "-c", str(path)],
        check=True,
    )
    _protect_session(session_name)
    sync_panel_tmux_config()
    return session_name


def kill_tmux_session(session_name: str) -> bool:
    """Kill a tmux session. Returns True on success."""
    result = subprocess.run(
        ["tmux", "kill-session", "-t", f"={session_name}"],
        capture_output=True,
    )
    return result.returncode == 0


def attach_tmux_session(session_name: str) -> None:
    """Attach to an existing tmux session, blocking until detach/exit."""
    target_session = session_name
    if session_name.startswith("gd/") and not _is_temp_panel_session(session_name):
        sync_panel_tmux_config()
    if _should_open_in_temp_panel(session_name):
        target_session = rebuild_temp_panel_tmux_session(session_name)
    elif _is_persistent_panel_session(target_session):
        _ensure_panel_resize_tracking(target_session)
        reflow_panel_tmux_session(target_session)
    if os.environ.get("TMUX"):
        subprocess.run(["tmux", "switch-client", "-t", f"={target_session}"])
    else:
        subprocess.run(["tmux", "attach-session", "-t", f"={target_session}"])


def open_in_tmux(repo_name: str, path: Path) -> None:
    """Create and attach to a new tmux session rooted at *path*."""
    session_name = create_tmux_session(repo_name, path)
    attach_tmux_session(session_name)


def _sanitize_panel_name(name: str) -> str:
    clean = _sanitize_repo_name(name)
    if clean:
        return clean
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"panel-{digest}"


def _is_temp_panel_session(session_name: str) -> bool:
    parts = session_name.split("/")
    return len(parts) > 4 and parts[:3] == ["gd", "temp", "panel"]


def _is_persistent_panel_session(session_name: str) -> bool:
    parts = session_name.split("/")
    return len(parts) == 3 and parts[:2] == ["gd", "panel"]


def _should_open_in_temp_panel(session_name: str) -> bool:
    return (
        session_name.startswith("gd/")
        and not _is_persistent_panel_session(session_name)
        and not _is_temp_panel_session(session_name)
    )


def make_temp_panel_session_name(session_name: str) -> str:
    suffix = session_name[3:] if session_name.startswith("gd/") else session_name
    return f"gd/temp/panel/{suffix}"


def _temp_panel_display_name(session_name: str) -> str:
    return _panel_session_label(session_name) or _session_slug(session_name) or session_name


def make_panel_session_name(panel_name: str) -> str:
    return f"gd/panel/{_sanitize_panel_name(panel_name)}"


def _panel_proxy_session_name(panel_name: str, pane_index: int) -> str:
    return f"gd-proxy/panel/{_sanitize_panel_name(panel_name)}/{pane_index}"


def _panel_proxy_session_prefix(panel_name: str) -> str:
    return f"gd-proxy/panel/{_sanitize_panel_name(panel_name)}/"


_PANEL_CLIENT_COUNT_OPTION = "@gitdirector_panel_clients"
_PANEL_STATUS_RESTORE_OPTION = "@gitdirector_panel_prev_status"
_PANEL_BORDER_RESTORE_OPTION = "@gitdirector_panel_prev_pane_border_status"
_PANEL_WINDOW_RESTORE_OPTION = "@gitdirector_panel_prev_window_target"
_PANEL_RESIZE_BUSY_OPTION = "@gitdirector_panel_resize_busy"
_PANEL_RESIZE_PENDING_OPTION = "@gitdirector_panel_resize_pending"


def _session_slug(session_name: str | None) -> str | None:
    if not session_name:
        return None
    if session_name.startswith("gd/"):
        return session_name[3:]
    return session_name


def _parse_gd_session_name(session_name: str | None) -> tuple[str, str, str] | None:
    if not session_name:
        return None
    parts = session_name.split("/")
    if len(parts) != 4 or parts[0] != "gd":
        return None
    _, repo, purpose, sequence = parts
    if not repo or not purpose or not sequence:
        return None
    return repo, purpose, sequence


def _panel_session_label(session_name: str | None) -> str | None:
    parsed = _parse_gd_session_name(session_name)
    if parsed:
        repo, purpose, sequence = parsed
        return f"{purpose} {repo}/{sequence}"
    return _session_slug(session_name)


def _panel_pane_title(pane_index: int, session_name: str | None) -> str:
    label = _panel_session_label(session_name)
    if label:
        return label
    return "empty"


def _resolved_panel_theme_name(theme_name: str | None = None) -> str:
    if theme_name:
        return theme_name
    configured_theme = Config().theme
    if configured_theme:
        return configured_theme
    return DEFAULT_THEME_NAME


def _panel_border_format(theme_name: str | None = None, *, show_pane_number: bool = True) -> str:
    theme = resolve_panel_theme(_resolved_panel_theme_name(theme_name))
    badge = ""
    if show_pane_number:
        badge = (
            "#{?pane_active,"
            f"#[bold fg={theme.badge_active_fg} bg={theme.badge_active_bg}],"
            f"#[bold fg={theme.badge_inactive_fg} bg={theme.badge_inactive_bg}]"
            "} #{pane_index} #[default]"
        )
    title = (
        "#{?pane_active,"
        f"#[fg={theme.label_active_fg} bg={theme.label_active_bg}],"
        f"#[fg={theme.label_inactive_fg} bg={theme.label_inactive_bg}]"
        "} #{pane_title} #[default]"
    )
    return f"{badge}{title}"


def _panel_window_status_format() -> str:
    return " #{pane_index}:#{pane_title} "


def _gd_tmux_config_path() -> Path:
    return Path.home() / ".gitdirector" / "gd-tmux.conf"


def _session_badge_text(session_name: str) -> str:
    parts = session_name.split("/")
    if len(parts) >= 4 and parts[0] == "gd" and parts[1] != "panel":
        return parts[2].upper()
    return "SESSION"


def _current_window_target(session_name: str) -> str:
    result = subprocess.run(
        [
            "tmux",
            "display-message",
            "-p",
            "-t",
            _session_option_target(session_name),
            "#{session_name}:#{window_index}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        target = result.stdout.strip()
        if target:
            return target
    return f"{session_name}:0"


def _tmux_theme_config(
    badge_text: str,
    label_text: str,
    session_name: str,
    theme_name: str | None = None,
    *,
    window_target: str | None = None,
    pane_border_status: str | None = None,
    pane_border_format: str | None = None,
    pane_border_lines: str | None = None,
    window_status_format: str = " #I:#W ",
    window_status_current_format: str = " #I:#W ",
    show_status: bool = True,
) -> str:
    theme = resolve_panel_theme(_resolved_panel_theme_name(theme_name))
    window_target = window_target or f"{session_name}:0"
    quoted_session = shlex.quote(_session_option_target(session_name))
    quoted_window = shlex.quote(f"={window_target}")
    status_left = (
        f"#[bold fg={theme.badge_active_fg},bg={theme.badge_active_bg}] {badge_text} #[default]"
        f"#[fg={theme.label_active_fg},bg={theme.label_active_bg}] {label_text} #[default]"
    )
    status_right = (
        f"#[fg={theme.label_inactive_fg},bg={theme.label_inactive_bg}] %H:%M %d %b #[default]"
    )
    lines = []
    if show_status:
        lines.extend(
            [
                f"set-option -t {quoted_session} status-position bottom",
                f'set-option -t {quoted_session} status-style "fg={theme.foreground},bg={theme.panel}"',
                f"set-option -t {quoted_session} status-left-length 40",
                f"set-option -t {quoted_session} status-right-length 24",
                f"set-option -t {quoted_session} status-left {shlex.quote(status_left)}",
                f"set-option -t {quoted_session} status-right {shlex.quote(status_right)}",
            ]
        )
    else:
        lines.append(f"set-option -t {quoted_session} status off")

    lines.extend(
        [
            f'set-option -t {quoted_session} message-style "fg={theme.badge_active_fg},bg={theme.badge_active_bg}"',
            f'set-option -t {quoted_session} message-command-style "fg={theme.label_active_fg},bg={theme.label_active_bg}"',
            f'set-window-option -t {quoted_window} window-status-style "fg={theme.label_inactive_fg},bg={theme.label_inactive_bg}"',
            f'set-window-option -t {quoted_window} window-status-current-style "fg={theme.badge_active_fg},bg={theme.badge_active_bg},bold"',
            f"set-window-option -t {quoted_window} window-status-format {shlex.quote(window_status_format)}",
            f"set-window-option -t {quoted_window} window-status-current-format {shlex.quote(window_status_current_format)}",
            f"set-window-option -t {quoted_window} window-status-separator {shlex.quote('')}",
            f'set-window-option -t {quoted_window} pane-border-style "fg={theme.border_inactive}"',
            f'set-window-option -t {quoted_window} pane-active-border-style "fg={theme.border_active}"',
        ]
    )
    if pane_border_status:
        lines.append(
            f"set-window-option -t {quoted_window} pane-border-status {shlex.quote(pane_border_status)}"
        )
    if pane_border_lines:
        lines.append(
            f"set-window-option -t {quoted_window} pane-border-lines {shlex.quote(pane_border_lines)}"
        )
    if pane_border_format:
        lines.append(
            f"set-window-option -t {quoted_window} pane-border-format {shlex.quote(pane_border_format)}"
        )
    lines.append("")
    return "\n".join(lines)


def _panel_tmux_config(
    panel_name: str,
    session_name: str,
    theme_name: str | None = None,
) -> str:
    return _tmux_theme_config(
        "PANEL",
        panel_name,
        session_name,
        theme_name,
        window_target=f"{session_name}:0",
        pane_border_status="top",
        pane_border_lines="heavy",
        pane_border_format=_panel_border_format(theme_name),
        window_status_format=_panel_window_status_format(),
        window_status_current_format=_panel_window_status_format(),
        show_status=True,
    )


def _session_tmux_config(session_name: str, theme_name: str | None = None) -> str:
    return _tmux_theme_config(
        _session_badge_text(session_name),
        _session_slug(session_name) or session_name,
        session_name,
        theme_name,
        window_target=_current_window_target(session_name),
    )


def _load_panel_tmux_config(
    panel_name: str,
    session_name: str,
    theme_name: str | None = None,
) -> Path:
    config_path = _gd_tmux_config_path()
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(_panel_tmux_config(panel_name, session_name, theme_name))
    subprocess.run(["tmux", "source-file", str(config_path)], check=True)
    return config_path


def _live_panel_sessions() -> list[tuple[str, str]]:
    from ..commands.tui.panels import PanelStore

    sessions: list[tuple[str, str]] = []
    for panel in PanelStore().panels:
        session_name = make_panel_session_name(panel.name)
        if _session_exists(session_name):
            sessions.append((panel.name, session_name))
    return sessions


def _panel_for_session(session_name: str):
    from ..commands.tui.panels import PanelStore

    for panel in PanelStore().panels:
        if make_panel_session_name(panel.name) == session_name:
            return panel
    return None


def _panel_resize_hook_shell(session_name: str) -> str:
    session_target = shlex.quote(_session_option_target(session_name))
    python_code = (
        "from gitdirector.integrations.tmux import reflow_panel_tmux_session; "
        f"reflow_panel_tmux_session({session_name!r})"
    )
    python_command = f"{shlex.quote(sys.executable)} -c {shlex.quote(python_code)}"
    return (
        f"panel_target={session_target}; "
        f'tmux set-option -q -t "$panel_target" {_PANEL_RESIZE_PENDING_OPTION} 1'
        " >/dev/null 2>&1 || true; "
        f'panel_busy=$(tmux show-options -q -v -t "$panel_target"'
        f" {_PANEL_RESIZE_BUSY_OPTION} 2>/dev/null || printf '0'); "
        'if [ "$panel_busy" = "1" ]; then exit 0; fi; '
        f'tmux set-option -q -t "$panel_target" {_PANEL_RESIZE_BUSY_OPTION} 1'
        " >/dev/null 2>&1 || true; "
        "while :; do "
        f'tmux set-option -q -t "$panel_target" {_PANEL_RESIZE_PENDING_OPTION} 0'
        " >/dev/null 2>&1 || true; "
        f"{python_command} >/dev/null 2>&1 || true; "
        f'panel_pending=$(tmux show-options -q -v -t "$panel_target"'
        f" {_PANEL_RESIZE_PENDING_OPTION} 2>/dev/null || printf '0'); "
        'if [ "$panel_pending" != "1" ]; then break; fi; '
        "done; "
        f'tmux set-option -q -u -t "$panel_target" {_PANEL_RESIZE_BUSY_OPTION}'
        " >/dev/null 2>&1 || true; "
        f'tmux set-option -q -u -t "$panel_target" {_PANEL_RESIZE_PENDING_OPTION}'
        " >/dev/null 2>&1 || true"
    )


def _ensure_panel_resize_tracking(session_name: str) -> None:
    if not _is_persistent_panel_session(session_name) or not _session_exists(session_name):
        return

    window_target = f"={session_name}:0"
    session_target = _session_option_target(session_name)
    hook_command = f"run-shell -b {shlex.quote(_panel_resize_hook_shell(session_name))}"

    subprocess.run(
        ["tmux", "set-window-option", "-q", "-t", window_target, "aggressive-resize", "on"],
        check=False,
    )
    subprocess.run(
        ["tmux", "set-hook", "-t", session_target, "client-resized", hook_command],
        check=False,
    )
    subprocess.run(
        ["tmux", "set-hook", "-w", "-t", window_target, "window-resized", hook_command],
        check=False,
    )


def reflow_panel_tmux_session(session_name: str) -> bool:
    if not _is_persistent_panel_session(session_name) or not _session_exists(session_name):
        return False

    panel = _panel_for_session(session_name)
    if panel is None:
        return False

    pane_ids = _list_window_panes_row_major(session_name)
    total_panes = panel.layout.total_panes
    if len(pane_ids) < total_panes:
        return False

    try:
        _equalize_panel_layout(session_name, pane_ids[:total_panes], panel.layout)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return False
    return True


def _live_repo_tmux_sessions() -> list[str]:
    try:
        entries = list_all_gd_sessions()
    except Exception:
        return []

    sessions: list[str] = []
    for entry in entries:
        session_name = entry["session_name"]
        if _session_exists(session_name):
            sessions.append(session_name)
    return sessions


def sync_panel_tmux_config(theme_name: str | None = None) -> Path:
    resolved_theme = _resolved_panel_theme_name(theme_name)
    config_path = _gd_tmux_config_path()
    config_path.parent.mkdir(exist_ok=True)
    live_panel_sessions = _live_panel_sessions()
    live_repo_sessions = _live_repo_tmux_sessions()

    lines = [
        "# Generated by GitDirector",
        f"# theme: {resolved_theme}",
        "",
    ]
    for panel_name, session_name in live_panel_sessions:
        lines.append(_panel_tmux_config(panel_name, session_name, resolved_theme))
    for session_name in live_repo_sessions:
        lines.append(_session_tmux_config(session_name, resolved_theme))

    config_path.write_text("\n".join(lines))

    if live_panel_sessions or live_repo_sessions:
        try:
            subprocess.run(["tmux", "source-file", str(config_path)], check=True)
        except (OSError, subprocess.CalledProcessError):
            return config_path

    return config_path


def kill_panel_tmux_session(panel_name: str) -> bool:
    killed = kill_tmux_session(make_panel_session_name(panel_name))
    proxy_prefix = _panel_proxy_session_prefix(panel_name)
    for session_name in _list_sessions():
        if session_name.startswith(proxy_prefix):
            subprocess.run(["tmux", "kill-session", "-t", f"={session_name}"], check=False)
    return killed


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
    from ..commands.tui.panels import resolve_panel_layout

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


def _panel_proxy_attach_fragment(panel_name: str, pane_index: int, session_name: str) -> str:
    proxy_session = _panel_proxy_session_name(panel_name, pane_index)
    quoted_proxy_target = shlex.quote(f"={proxy_session}")
    return (
        f"tmux kill-session -t {quoted_proxy_target} >/dev/null 2>&1 || true; "
        f"{_panel_attach_fragment(session_name)}"
    )


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
    missing_message = _printf_lines_command([f"Missing session: {session_name}"])
    attach_fragment = (
        _panel_proxy_attach_fragment(panel_name, pane_index, session_name)
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
            f"{_panel_proxy_attach_fragment(panel_name, pane_index, session_name)}"
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
    from ..commands.tui.panels import resolve_panel_layout

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


def _make_agent_ready_marker() -> Path:
    """Create a unique marker path used to signal agent startup."""
    fd, raw_path = tempfile.mkstemp(prefix="gitdirector-agent-", suffix=".ready")
    os.close(fd)
    marker_path = Path(raw_path)
    try:
        marker_path.unlink()
    except FileNotFoundError:
        pass
    return marker_path


def launch_agent_in_tmux_session(session_name: str, agent_cmd: str) -> Path:
    """Launch an agent in *session_name* and return a startup marker path."""
    normalized_agent_cmd = shlex.join(shlex.split(agent_cmd))
    ready_marker = _make_agent_ready_marker()
    ready_marker_quoted = shlex.quote(str(ready_marker))
    pane_target = _active_pane_target(session_name)
    quoted_session_target = shlex.quote(f"={session_name}")
    cleanup_script = (
        f"touch {ready_marker_quoted} >/dev/null 2>&1 || true; "
        "clear; "
        f"{normalized_agent_cmd}; "
        "status=$?; "
        f"rm -f {ready_marker_quoted} >/dev/null 2>&1 || true; "
        f"tmux detach-client -s {quoted_session_target} >/dev/null 2>&1 || true; "
        f"tmux kill-session -t {quoted_session_target} >/dev/null 2>&1 || true; "
        "exit $status"
    )
    subprocess.run(
        [
            "tmux",
            "send-keys",
            "-t",
            pane_target,
            f"sh -lc {shlex.quote(cleanup_script)}",  # agents need login env
            "Enter",
        ],
        check=False,
    )
    return ready_marker


_SHELL_COMMANDS = frozenset(
    {
        "zsh",
        "bash",
        "fish",
        "sh",
        "dash",
        "tcsh",
        "csh",
        "ksh",
    }
)

_AGENT_PURPOSES = frozenset({"opencode", "claude", "copilot", "codex"})

_SILENCE_THRESHOLD_SECS = 8
_BELL_GRACE_SECS = 1.0
_CONTENT_POLL_SECS = 2


def _normalize_process_command(raw_args: str) -> str:
    token = raw_args.strip().split(" ", 1)[0]
    if not token:
        return ""
    return Path(token).name


def _get_process_snapshot() -> tuple[
    dict[int, list[int]],
    dict[int, str],
    dict[int, int],
    dict[int, int],
]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,tpgid=,args="],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}, {}, {}, {}

    children_by_parent: dict[int, list[int]] = {}
    commands_by_pid: dict[int, str] = {}
    pgid_by_pid: dict[int, int] = {}
    tpgid_by_pid: dict[int, int] = {}
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(-?\d+)\s+(-?\d+)\s+(.*)", line)
        if match is None:
            continue
        pid = int(match.group(1))
        ppid = int(match.group(2))
        pgid_by_pid[pid] = int(match.group(3))
        tpgid_by_pid[pid] = int(match.group(4))
        commands_by_pid[pid] = _normalize_process_command(match.group(5))
        children_by_parent.setdefault(ppid, []).append(pid)

    return children_by_parent, commands_by_pid, pgid_by_pid, tpgid_by_pid


def _resolve_pane_command(
    pane_pid: int,
    purpose: str,
    fallback_command: str,
    children_by_parent: dict[int, list[int]],
    commands_by_pid: dict[int, str],
    pgid_by_pid: dict[int, int],
    tpgid_by_pid: dict[int, int],
) -> str:
    descendants: list[tuple[int, int, str]] = []
    stack: list[tuple[int, int]] = [(pane_pid, 0)]
    seen: set[int] = set()
    while stack:
        pid, depth = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for child in children_by_parent.get(pid, []):
            child_depth = depth + 1
            stack.append((child, child_depth))
            command = commands_by_pid.get(child, "")
            if command:
                descendants.append((child_depth, child, command))

    if not descendants:
        return fallback_command

    non_shell_descendants = [
        descendant for descendant in descendants if descendant[2].lstrip("-") not in _SHELL_COMMANDS
    ]
    if not non_shell_descendants:
        return max(descendants, key=lambda descendant: (descendant[0], descendant[1]))[2]

    if purpose in _AGENT_PURPOSES:
        matching_agent_descendants = [
            descendant
            for descendant in non_shell_descendants
            if descendant[2].lstrip("-") == purpose
        ]
        if matching_agent_descendants:
            return min(
                matching_agent_descendants, key=lambda descendant: (descendant[0], descendant[1])
            )[2]

    pane_tpgid = tpgid_by_pid.get(pane_pid, 0)
    if pane_tpgid > 0:
        foreground_descendants = [
            descendant
            for descendant in non_shell_descendants
            if pgid_by_pid.get(descendant[1]) == pane_tpgid
        ]
        if foreground_descendants:
            return min(
                foreground_descendants, key=lambda descendant: (descendant[0], descendant[1])
            )[2]

    return max(non_shell_descendants, key=lambda descendant: (descendant[0], descendant[1]))[2]


def get_all_session_statuses() -> dict[str, dict[str, object]]:
    """Query tmux for bell, foreground command, and dead status of all gd/ panes.

    Returns a dict keyed by session_name with:
      - bell: bool
      - command: str (foreground process name)
      - dead: bool
    """
    result = subprocess.run(
        [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{session_name}|#{pane_current_command}|#{pane_dead}|#{pane_pid}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}

    pane_rows: list[tuple[str, str, bool, int]] = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        session_name = parts[0]
        if _parse_gd_session_name(session_name) is None:
            continue
        try:
            pane_pid = int(parts[3])
        except (ValueError, IndexError):
            pane_pid = 0
        pane_rows.append(
            (
                session_name,
                parts[1],
                parts[2] == "1",
                pane_pid,
            )
        )

    if not pane_rows:
        return {}

    children_by_parent, commands_by_pid, pgid_by_pid, tpgid_by_pid = _get_process_snapshot()
    statuses: dict[str, dict[str, object]] = {}
    for session_name, command, dead, pane_pid in pane_rows:
        effective_command = command
        if pane_pid > 0:
            parts = session_name.split("/")
            purpose = parts[2] if len(parts) >= 4 else ""
            effective_command = _resolve_pane_command(
                pane_pid,
                purpose,
                command,
                children_by_parent,
                commands_by_pid,
                pgid_by_pid,
                tpgid_by_pid,
            )
        statuses[session_name] = {
            "command": effective_command,
            "dead": dead,
        }
    return statuses


def resolve_pane_status(
    purpose: str,
    command: str,
    dead: bool,
    *,
    bell: bool = False,
    last_output_time: float = 0.0,
) -> str:
    """Determine pane status from tmux and monitor info.

    Returns "waiting", "running", or "idle".
    """
    if bell:
        return "waiting"
    if dead:
        return "idle"
    clean_cmd = command.lstrip("-")
    is_shell = clean_cmd in _SHELL_COMMANDS
    if is_shell:
        return "idle"
    if purpose in _AGENT_PURPOSES and clean_cmd in _AGENT_PURPOSES and last_output_time > 0:
        elapsed = time.time() - last_output_time
        if elapsed >= _SILENCE_THRESHOLD_SECS:
            return "idle"
    return "running"


def _capture_pane_text(session_name: str) -> str | None:
    result = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", _active_pane_target(session_name)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _hash_content(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


class _ControlModeReader:
    def __init__(self, session_name: str, callback):
        self._session_name = session_name
        self._callback = callback
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        proc = self._process
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def is_alive(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def _run(self):
        try:
            self._process = subprocess.Popen(
                ["tmux", "-C", "attach-session", "-t", f"={self._session_name}", "-r"],
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for line in self._process.stdout:
                if not self._running:
                    break
                self._parse_line(line.rstrip("\n"))
        except Exception:
            pass
        finally:
            self._running = False
            proc = self._process
            self._process = None
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def _parse_line(self, line: str):
        if line.startswith("%bell"):
            self._callback(self._session_name, "bell")
        elif line.startswith("%output"):
            self._callback(self._session_name, "output")
        elif line.startswith("%exit"):
            self._running = False


class TmuxMonitor:
    """Monitors gd/ sessions via tmux control mode for real-time bell and output events."""

    def __init__(self):
        self._lock = threading.Lock()
        self._readers: dict[str, _ControlModeReader] = {}
        self._bell_active: dict[str, bool] = {}
        self._bell_time: dict[str, float] = {}
        self._last_output_time: dict[str, float] = {}
        self._content_hashes: dict[str, str] = {}
        self._last_content_change_time: dict[str, float] = {}
        self._running = False
        self._sync_thread: threading.Thread | None = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._sync_thread = threading.Thread(target=self._sync_sessions, daemon=True)
        self._sync_thread.start()

    def stop(self):
        self._running = False
        readers = list(self._readers.values())
        self._readers.clear()
        for reader in readers:
            reader.stop()
        sync_thread = self._sync_thread
        self._sync_thread = None
        if (
            sync_thread is not None
            and sync_thread is not threading.current_thread()
            and sync_thread.is_alive()
        ):
            sync_thread.join(timeout=3)

    def get_bell_state(self, session_name: str) -> bool:
        with self._lock:
            return self._bell_active.get(session_name, False)

    def get_last_output_time(self, session_name: str) -> float:
        with self._lock:
            return self._last_output_time.get(session_name, 0.0)

    def get_last_content_change_time(self, session_name: str) -> float:
        with self._lock:
            return self._last_content_change_time.get(session_name, 0.0)

    def clear_bell(self, session_name: str):
        with self._lock:
            self._bell_active[session_name] = False

    def _on_event(self, session_name: str, event_type: str):
        with self._lock:
            if event_type == "bell":
                self._bell_active[session_name] = True
                self._bell_time[session_name] = time.time()
            elif event_type == "output":
                now = time.time()
                self._last_output_time[session_name] = now
                if self._bell_active.get(session_name):
                    bell_time = self._bell_time.get(session_name, 0.0)
                    if now - bell_time >= _BELL_GRACE_SECS:
                        self._bell_active[session_name] = False

    def _sync_sessions(self):
        while self._running:
            try:
                sessions = _list_sessions()
                gd_sessions = {s for s in sessions if _parse_gd_session_name(s) is not None}
                current = set(self._readers.keys())

                for s in gd_sessions - current:
                    self._add_reader(s)

                for s in current - gd_sessions:
                    self._remove_reader(s)

                for s in gd_sessions & current:
                    reader = self._readers.get(s)
                    if reader and not reader.is_alive():
                        self._remove_reader(s)
                        self._add_reader(s)

                self._poll_content_changes(gd_sessions)
            except Exception:
                pass

            for _ in range(20):
                if not self._running:
                    return
                time.sleep(0.1)

    def _add_reader(self, session_name: str):
        reader = _ControlModeReader(session_name, self._on_event)
        self._readers[session_name] = reader
        reader.start()

    def _poll_content_changes(self, sessions: set[str]):
        for session_name in sessions:
            text = _capture_pane_text(session_name)
            if text is None:
                continue
            h = _hash_content(text)
            with self._lock:
                prev = self._content_hashes.get(session_name)
                if prev != h:
                    self._content_hashes[session_name] = h
                    self._last_content_change_time[session_name] = time.time()

    def _remove_reader(self, session_name: str):
        reader = self._readers.pop(session_name, None)
        if reader:
            reader.stop()
        with self._lock:
            self._bell_active.pop(session_name, None)
            self._bell_time.pop(session_name, None)
            self._last_output_time.pop(session_name, None)
            self._content_hashes.pop(session_name, None)
            self._last_content_change_time.pop(session_name, None)
