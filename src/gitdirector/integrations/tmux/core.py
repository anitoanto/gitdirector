"""tmux integration via subprocess."""

import hashlib
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
from base64 import b32encode
from pathlib import Path

from ...config import Config
from ...storage import atomic_write_text, normalize_repository_path
from ...ui_theme import DEFAULT_THEME_NAME, resolve_panel_theme

logger = logging.getLogger(__name__)

_REPO_ID_LENGTH = 5
_SESSION_LIST_SEPARATOR = "\t"
_LAST_SYNC_CONTENT: dict[Path, str] = {}


class TmuxError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        args_list: list[str] | None = None,
        returncode: int | None = None,
        stderr: str | bytes | None = None,
    ) -> None:
        details = message
        if returncode is not None:
            details = f"{details} (exit {returncode})"
        if stderr:
            raw_stderr = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
            raw_stderr = raw_stderr.strip()
            if raw_stderr:
                details = f"{details}: {raw_stderr}"
        super().__init__(details)
        self.args_list = args_list
        self.returncode = returncode
        self.stderr = stderr


def _run_tmux(
    args: list[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    text: bool = False,
) -> subprocess.CompletedProcess:
    command = ["tmux", *args]
    kwargs: dict[str, object] = {}
    if capture_output:
        kwargs["capture_output"] = True
    if text:
        kwargs["text"] = True
    try:
        result = subprocess.run(command, **kwargs)
    except subprocess.CalledProcessError as exc:
        raise TmuxError(
            "tmux command failed",
            args_list=command,
            returncode=exc.returncode,
            stderr=exc.stderr,
        ) from exc
    except OSError as exc:
        raise TmuxError(str(exc), args_list=command) from exc
    if check and isinstance(result.returncode, int) and result.returncode != 0:
        raise TmuxError(
            "tmux command failed",
            args_list=command,
            returncode=result.returncode,
            stderr=result.stderr,
        )
    return result


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
    result = _run_tmux(["list-sessions", "-F", "#{session_name}"], text=True)
    if result.returncode != 0:
        return []
    return [s for s in result.stdout.strip().split("\n") if s]


def _session_name_segments(
    repo_name: str | Path,
    purpose: str,
    *,
    repo_path: Path | None = None,
) -> tuple[str, str]:
    if repo_path is None and isinstance(repo_name, Path):
        repo_path = repo_name
    repo_segment = (
        _repo_session_name_segment(repo_path)
        if repo_path is not None
        else (_sanitize_repo_name(str(repo_name)) or "repo")
    )
    purpose_segment = _sanitize_repo_name(purpose) or "cmd"
    return repo_segment, purpose_segment


def _next_session_sequence(repo_segment: str, purpose_segment: str, sessions: list[str]) -> int:
    max_sequence = 0
    for session_name in sessions:
        parsed = _parse_gd_session_name(session_name)
        if parsed is None:
            continue
        parsed_repo, parsed_purpose, parsed_sequence = parsed
        if parsed_repo == repo_segment and parsed_purpose == purpose_segment:
            max_sequence = max(max_sequence, int(parsed_sequence))
    return max_sequence + 1


def _make_session_name(
    repo_name: str | Path,
    purpose: str = "shell",
    *,
    repo_path: Path | None = None,
    sessions: list[str] | None = None,
) -> str:
    """Generate the next sequential session name: gd/{repo}/{purpose}/{N}.

    The purpose is sanitized to ``[a-z0-9-]`` so the resulting name always
    has exactly four ``/``-separated parts. This is what
    :func:`_parse_gd_session_name` and the TUI Sessions tab rely on. The
    full unsanitized purpose (e.g. a ``gitdirector gd-tmux`` command) is
    still embedded verbatim in the session's working command, so no
    information is lost — only the session-name label is normalized.
    """
    if sessions is None:
        sessions = _list_sessions()
    repo_segment, purpose_segment = _session_name_segments(
        repo_name,
        purpose,
        repo_path=repo_path,
    )
    sequence = _next_session_sequence(repo_segment, purpose_segment, sessions)
    return f"gd/{repo_segment}/{purpose_segment}/{sequence}"


def _session_exists(session_name: str) -> bool:
    """Check if a tmux session with the given name exists."""
    try:
        result = _run_tmux(["has-session", "-t", f"={session_name}"])
        return result.returncode == 0
    except TmuxError:
        return False


def _protect_session(session_name: str) -> None:
    """Ensure a gd session survives detach regardless of global tmux config."""
    _run_tmux(["set-option", "-t", f"={session_name}:", "destroy-unattached", "off"], check=True)


def _active_pane_target(session_name: str) -> str:
    """Return the exact-match tmux target for the session's active pane."""
    return f"={session_name}:"


def _session_option_target(session_name: str) -> str:
    """Return the exact-match tmux target for session-scoped options and queries."""
    return f"={session_name}:"


def _detached_session_size_args() -> list[str]:
    cols, lines = shutil.get_terminal_size()
    return ["-x", str(cols), "-y", str(lines)]


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

    Returns a list of dicts with keys: session_name, repo, repo_slug,
    purpose, description. The description is the user-set value stored in
    the session's ``@gitdirector_description`` tmux option, or ``"-"``
    when the option is unset.
    """
    result = _run_tmux(
        [
            "list-sessions",
            "-F",
            _SESSION_LIST_SEPARATOR.join(
                [
                    "#{session_name}",
                    f"#{{{GD_REPO_LABEL_OPTION}}}",
                    f"#{{{GD_DESCRIPTION_OPTION}}}",
                ]
            ),
        ],
        text=True,
    )
    if result.returncode != 0:
        return []
    entries = []
    rows: list[tuple[str, str, str]] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        session_name, repo_label, description = (line.split(_SESSION_LIST_SEPARATOR, 2) + ["", ""])[
            :3
        ]
        rows.append((session_name, repo_label.strip(), description.strip()))
    for session_name, repo_label, description in sorted(rows, key=lambda row: row[0]):
        parsed = _parse_gd_session_name(session_name)
        if parsed is None:
            continue
        repo_slug, purpose, _ = parsed
        if not repo_label or "\n" in repo_label or "/" in repo_label:
            repo_label = _repo_label_from_segment(repo_slug)
        entries.append(
            {
                "session_name": session_name,
                "repo": repo_label,
                "repo_slug": repo_slug,
                "purpose": purpose,
                "description": description or GD_DEFAULT_DESCRIPTION,
            }
        )
    return entries


def create_tmux_session(
    repo_name: str,
    path: Path,
    purpose: str = "shell",
    *,
    description: str | None = None,
    repo_label: str | None = None,
) -> str:
    """Create a new detached tmux session with a unique name and return it.

    The optional *description* is stored in the session's
    ``@gitdirector_description`` tmux option so it can be displayed in
    the TUI Sessions tab. A value of ``None`` or ``""`` leaves the option
    unset (the next read returns the default ``"-"`` placeholder).
    """
    sessions = _list_sessions()
    max_attempts = 5
    for _attempt in range(max_attempts):
        session_name = _make_session_name(repo_name, purpose, repo_path=path, sessions=sessions)
        result = _run_tmux(
            [
                "new-session",
                "-d",
                "-s",
                session_name,
                *_detached_session_size_args(),
                "-c",
                str(path),
            ],
            text=True,
        )
        if result.returncode == 0:
            break

        sessions = _list_sessions()
        if session_name in sessions:
            continue

        raise TmuxError(
            "tmux new-session failed",
            args_list=list(result.args) if isinstance(result.args, list) else None,
            returncode=result.returncode,
            stderr=result.stderr,
        )
    else:
        raise TmuxError(
            f"tmux new-session failed after {max_attempts} attempts to allocate a unique name"
        )
    try:
        _protect_session(session_name)
        if repo_label is not None and repo_label.strip():
            _set_session_repo_label(session_name, repo_label)
        if description is not None and description.strip():
            _set_session_description(session_name, description)
        sync_panel_tmux_config()
        return session_name
    except Exception:
        kill_tmux_session(session_name)
        raise


def kill_tmux_session(session_name: str) -> bool:
    """Kill a tmux session by its **full exact name**. Returns True on success.

    The argument MUST be a complete session name (e.g. ``gd/repo/shell/1``).
    Anything else is rejected with ``ValueError`` — partial names, glob
    patterns, empty strings, names already prefixed with ``=``, or names
    containing the tmux target separator ``:`` would otherwise be unsafe
    to forward to ``tmux kill-session -t <target>``. tmux's ``-t`` flag
    uses prefix matching by default; without the ``=`` exact-match
    prefix, ``tmux kill-session -t gd/repo/shell/1`` would also kill
    ``gd/repo/shell/10``, ``gd/repo/shell/100``, etc.

    Failures are logged at debug level so callers can distinguish "session
    didn't exist" from "tmux server crashed" without needing to wrap the
    call. Most callers are happy with the boolean return.
    """
    _validate_session_name_for_kill(session_name)
    try:
        result = _run_tmux(["kill-session", "-t", f"={session_name}"])
        if result.returncode != 0:
            logger.debug(
                "tmux kill-session %s exited %s: %s",
                session_name,
                result.returncode,
                (result.stderr or b"").decode(errors="replace").strip()
                if isinstance(result.stderr, (bytes, bytearray))
                else (result.stderr or "").strip(),
            )
        return result.returncode == 0
    except TmuxError as exc:
        logger.debug("tmux kill-session %s failed: %s", session_name, exc)
        return False


def _validate_session_name_for_kill(session_name: str) -> None:
    """Reject inputs that could kill more sessions than intended.

    Enforced invariants:
      * non-empty string
      * already namespaced under ``gd/`` (anything else is not ours to kill)
      * has at least one path segment after ``gd/`` (i.e. not just ``gd/``)
      * does not start with ``=`` (would produce a malformed target)
      * does not contain tmux target separators (``:``, ``.``) which
        would be interpreted as ``session:window`` or session-id syntax
      * does not contain tmux glob/wildcard characters (``*``, ``?``,
        ``[``, ``]``) which would broaden the match
    """
    if not isinstance(session_name, str) or not session_name:
        raise ValueError("kill_tmux_session requires a non-empty full session name")
    if not session_name.startswith("gd/"):
        raise ValueError(f"kill_tmux_session refused non-gd session name: {session_name!r}")
    if len(session_name) <= 3 or session_name == "gd/":
        raise ValueError(
            f"kill_tmux_session refused underspecified gd session name: {session_name!r}"
        )
    if session_name.startswith("="):
        raise ValueError(f"kill_tmux_session refused already-prefixed target: {session_name!r}")
    forbidden = (":", "*", "?", "[", "]")
    for char in forbidden:
        if char in session_name:
            raise ValueError(
                f"kill_tmux_session refused session name containing {char!r}: {session_name!r}"
            )


def attach_tmux_session(
    session_name: str,
    *,
    skip_config_sync: bool = False,
) -> bool:
    """Attach to an existing tmux session, blocking until detach/exit.

    When *skip_config_sync* is true the leading ``sync_panel_tmux_config`` call
    is omitted. Callers that just created the session (and therefore triggered
    a sync moments ago) should set this to avoid a visible flicker: the sync
    re-lists every tmux session, rewrites the gd-tmux.conf file, and runs
    ``tmux source-file``, which together take long enough to expose a brief
    empty alt-screen between the manual screen clear and tmux's first redraw.
    """
    from .panels import (
        _ensure_panel_prefix_bindings,
        ensure_temp_panel_tmux_session,
    )

    target_session = session_name
    if (
        not skip_config_sync
        and session_name.startswith("gd/")
        and not _is_temp_panel_session(session_name)
    ):
        sync_panel_tmux_config()
    if _should_open_in_temp_panel(session_name):
        if not _session_exists(session_name):
            raise TmuxError(f"tmux session no longer exists: {session_name}")
        target_session = ensure_temp_panel_tmux_session(session_name)
    elif _is_persistent_panel_session(target_session):
        if not _session_exists(target_session):
            raise TmuxError(f"tmux session no longer exists: {target_session}")
        _ensure_panel_prefix_bindings()
        _ensure_panel_resize_tracking(target_session)
        reflow_panel_tmux_session(target_session)
    if os.environ.get("TMUX"):
        _run_tmux(["switch-client", "-t", f"={target_session}"], check=True, capture_output=False)
        return False
    _run_tmux(["attach-session", "-t", f"={target_session}"], check=True, capture_output=False)
    return True


def open_in_tmux(repo_name: str, path: Path) -> None:
    """Create and attach to a new tmux session rooted at *path*."""
    session_name = create_tmux_session(repo_name, path)
    try:
        attach_tmux_session(session_name, skip_config_sync=True)
    except BaseException:
        kill_tmux_session(session_name)
        raise


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
    """Return the deterministic temp panel session name for *session_name*.

    The temp session is a 1:1 wrapper around an existing inner session,
    so its name is derived directly from the inner session. Inner
    session names are already unique (repo + purpose + incrementing
    sequence), which makes the temp name unique by construction.

    The temp session is reused across attaches (see
    :func:`ensure_temp_panel_tmux_session`), so the same name must
    always map to the same wrapper session for a given inner session.
    """
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
    if not sequence.isdigit() or int(sequence) <= 0:
        return None
    return repo, purpose, sequence


def capture_pane(
    session_name: str,
    *,
    lines: int | None = None,
    full: bool = False,
) -> str | None:
    """Return the current scrollback of *session_name*'s active pane.

    Returns ``None`` when the session is not running, when tmux is not
    installed, or when the capture fails for any other reason.

    Exactly one of *lines* or *full* should be supplied. ``lines=N`` uses
    ``tmux capture-pane -p -S -N`` to grab the last N lines from the
    scrollback. ``full=True`` uses ``-S -`` to grab the entire visible
    scrollback history. With neither, tmux's default (the currently
    visible viewport) is returned.
    """
    if not _session_exists(session_name):
        return None
    cmd = ["tmux", "capture-pane", "-p", "-t", _active_pane_target(session_name)]
    if full:
        cmd.extend(["-S", "-"])
    elif lines is not None and lines > 0:
        cmd.extend(["-S", f"-{lines}"])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout


def send_key_to_session(session_name: str, key: str) -> bool:
    if not _session_exists(session_name):
        return False
    result = subprocess.run(
        ["tmux", "send-keys", "-t", _active_pane_target(session_name), key],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def send_text_to_session(session_name: str, text: str, *, enter: bool = False) -> bool:
    if not _session_exists(session_name):
        return False

    buffer_name = f"gitdirector-send-{os.getpid()}"
    load_result = subprocess.run(
        ["tmux", "load-buffer", "-b", buffer_name, "-"],
        input=text,
        capture_output=True,
        text=True,
    )
    if load_result.returncode != 0:
        return False

    paste_result = subprocess.run(
        ["tmux", "paste-buffer", "-b", buffer_name, "-t", _active_pane_target(session_name)],
        capture_output=True,
        text=True,
    )
    subprocess.run(["tmux", "delete-buffer", "-b", buffer_name], capture_output=True, text=True)
    if paste_result.returncode != 0:
        return False
    if enter:
        return send_key_to_session(session_name, "Enter")
    return True


GD_DESCRIPTION_OPTION = "@gitdirector_description"
GD_REPO_LABEL_OPTION = "@gitdirector_repo_label"
GD_DEFAULT_DESCRIPTION = "-"


def _get_session_repo_label(session_name: str) -> str | None:
    result = subprocess.run(
        [
            "tmux",
            "show-option",
            "-t",
            _session_option_target(session_name),
            "-v",
            "-q",
            GD_REPO_LABEL_OPTION,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    if not value or "\n" in value or "/" in value:
        return None
    return value


def _set_session_repo_label(session_name: str, repo_label: str) -> None:
    clean = (repo_label or "").strip()
    if not clean:
        return
    subprocess.run(
        [
            "tmux",
            "set-option",
            "-t",
            _session_option_target(session_name),
            GD_REPO_LABEL_OPTION,
            clean,
        ],
        capture_output=True,
    )


def _get_session_description(session_name: str) -> str:
    """Return the user-set description for *session_name*.

    Reads the ``@gitdirector_description`` session option. Returns the
    default placeholder ("-") when the option is unset or the session is
    unavailable.
    """
    result = subprocess.run(
        [
            "tmux",
            "show-option",
            "-t",
            _session_option_target(session_name),
            "-v",
            "-q",
            GD_DESCRIPTION_OPTION,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return GD_DEFAULT_DESCRIPTION
    value = result.stdout.strip()
    return value or GD_DEFAULT_DESCRIPTION


def _set_session_description(session_name: str, description: str) -> None:
    """Set the user-facing description for *session_name* in tmux.

    Pass an empty string to clear the description (the next read will
    return the default "-" placeholder). The change is applied directly
    to the live session; ``sync_panel_tmux_config`` does not need to be
    called.
    """
    clean = (description or "").strip()
    if clean:
        subprocess.run(
            [
                "tmux",
                "set-option",
                "-t",
                _session_option_target(session_name),
                GD_DESCRIPTION_OPTION,
                clean,
            ],
            capture_output=True,
        )
    else:
        subprocess.run(
            [
                "tmux",
                "set-option",
                "-u",
                "-t",
                _session_option_target(session_name),
                GD_DESCRIPTION_OPTION,
            ],
            capture_output=True,
        )


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
            f"set-option -t {quoted_session} mouse on",
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
    _run_tmux(["source-file", str(config_path)], check=True)
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
        "sleep 0.15; "
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

    return [entry["session_name"] for entry in entries]


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

    content = "\n".join(lines)
    changed = _LAST_SYNC_CONTENT.get(config_path) != content
    if changed:
        atomic_write_text(config_path, content)
        _LAST_SYNC_CONTENT[config_path] = content

    if changed and (live_panel_sessions or live_repo_sessions):
        try:
            _run_tmux(["source-file", str(config_path)], check=True)
        except TmuxError:
            return config_path

    return config_path


__all__ = [name for name in globals() if not name.startswith("__")]
