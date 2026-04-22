"""tmux integration via subprocess."""

import hashlib
import logging
import os
import re
import shlex
import subprocess
import sys
from base64 import b32encode
from pathlib import Path

from ...config import Config
from ...storage import atomic_write_text, normalize_repository_path
from ...ui_theme import DEFAULT_THEME_NAME, resolve_panel_theme

logger = logging.getLogger(__name__)

_REPO_ID_LENGTH = 5


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


def _repo_id_suffix(repo_path: Path) -> str:
    normalized_path = normalize_repository_path(repo_path)
    digest = hashlib.sha1(str(normalized_path).encode("utf-8")).digest()
    return b32encode(digest).decode("ascii").lower().rstrip("=")[:_REPO_ID_LENGTH]


def _repo_session_name_segment(repo_path: Path) -> str:
    clean = _sanitize_repo_name(repo_path.name) or "repo"
    return f"{clean}_{_repo_id_suffix(repo_path)}"


def _repo_label_from_segment(repo_segment: str) -> str:
    base, separator, suffix = repo_segment.rpartition("_")
    if separator and len(suffix) == _REPO_ID_LENGTH and re.fullmatch(r"[a-z2-7]+", suffix):
        return base or "repo"
    return repo_segment


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


def _make_session_name(
    repo_name: str | Path,
    purpose: str = "shell",
    *,
    repo_path: Path | None = None,
) -> str:
    """Generate the next sequential session name: gd/{repo}/{purpose}/{N}."""
    if repo_path is None and isinstance(repo_name, Path):
        repo_path = repo_name
    clean = (
        _repo_session_name_segment(repo_path)
        if repo_path is not None
        else _sanitize_repo_name(str(repo_name))
    )
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


def list_repo_sessions(repo_name: str | Path) -> list[str]:
    """List all tmux sessions for a given repository."""
    if isinstance(repo_name, Path):
        clean = _sanitize_repo_name(repo_name.name)
        prefixes = [f"gd/{_repo_session_name_segment(repo_name)}/", f"gd/{clean}/"]
    else:
        clean = _sanitize_repo_name(repo_name)
        prefixes = [f"gd/{clean}/", f"gd/{clean}_"]
    sessions = _list_sessions()
    return sorted(
        [
            session_name
            for session_name in sessions
            if any(session_name.startswith(prefix) for prefix in prefixes)
            and not _is_temp_panel_session(session_name)
        ]
    )


def list_all_gd_sessions() -> list[dict[str, str]]:
    """List all GitDirector tmux sessions (gd/ prefix).

    Returns a list of dicts with keys: session_name, repo, repo_slug, purpose.
    """
    sessions = _list_sessions()
    entries = []
    for s in sorted(sessions):
        parsed = _parse_gd_session_name(s)
        if parsed is None:
            continue
        repo_slug, purpose, _ = parsed
        entries.append(
            {
                "session_name": s,
                "repo": _repo_label_from_segment(repo_slug),
                "repo_slug": repo_slug,
                "purpose": purpose,
            }
        )
    return entries


def create_tmux_session(repo_name: str, path: Path, purpose: str = "shell") -> str:
    """Create a new detached tmux session with a unique name and return it."""
    for _ in range(10):
        session_name = _make_session_name(repo_name, purpose, repo_path=path)
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
    from .panels import (
        _ensure_panel_prefix_bindings,
        rebuild_temp_panel_tmux_session,
    )

    target_session = session_name
    if session_name.startswith("gd/") and not _is_temp_panel_session(session_name):
        sync_panel_tmux_config()
    if _should_open_in_temp_panel(session_name):
        target_session = rebuild_temp_panel_tmux_session(session_name)
    elif _is_persistent_panel_session(target_session):
        _ensure_panel_prefix_bindings()
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


_PANEL_CLIENT_COUNT_OPTION = "@gitdirector_panel_clients"
_PANEL_STATUS_RESTORE_OPTION = "@gitdirector_panel_prev_status"
_PANEL_BORDER_RESTORE_OPTION = "@gitdirector_panel_prev_pane_border_status"
_PANEL_WINDOW_RESTORE_OPTION = "@gitdirector_panel_prev_window_target"
_PANEL_RESIZE_BUSY_OPTION = "@gitdirector_panel_resize_busy"
_PANEL_RESIZE_PENDING_OPTION = "@gitdirector_panel_resize_pending"


def _session_slug(session_name: str | None) -> str | None:
    if not session_name:
        return None
    parsed = _parse_gd_session_name(session_name)
    if parsed:
        repo_slug, purpose, sequence = parsed
        return f"{_repo_label_from_segment(repo_slug)}/{purpose}/{sequence}"
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
        repo_slug, purpose, sequence = parsed
        return f"{purpose} {_repo_label_from_segment(repo_slug)}/{sequence}"
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
    atomic_write_text(config_path, _panel_tmux_config(panel_name, session_name, theme_name))
    subprocess.run(["tmux", "source-file", str(config_path)], check=True)
    return config_path


def _live_panel_sessions() -> list[tuple[str, str]]:
    from ...commands.tui.panels import PanelStore

    sessions: list[tuple[str, str]] = []
    for panel in PanelStore().panels:
        session_name = make_panel_session_name(panel.name)
        if _session_exists(session_name):
            sessions.append((panel.name, session_name))
    return sessions


def _panel_for_session(session_name: str):
    from ...commands.tui.panels import PanelStore

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
    from .panels import _equalize_panel_layout, _list_window_panes_row_major

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
        logger.debug("Failed to list GitDirector tmux sessions", exc_info=True)
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

    atomic_write_text(config_path, "\n".join(lines))

    if live_panel_sessions or live_repo_sessions:
        try:
            subprocess.run(["tmux", "source-file", str(config_path)], check=True)
        except (OSError, subprocess.CalledProcessError):
            return config_path

    return config_path


__all__ = [name for name in globals() if not name.startswith("__")]
